"""Informe del CRITERIO INSIDER para UN día (solo lectura): ¿los tokens con insider rinden peor en S1 de la v0?"""
import argparse
import sqlite3
import statistics
import time

from comun.config import RUTA_BD, SEGUIMIENTO_S, SIM_FEE_RED_SOL, SIM_TAM_SOL, TENEDORES_MIN_N
from comun.db import fotos_token
from herramientas.estrategia_v0 import COMISION, CONSULTA, elegir_entrada, percentil
from herramientas.rebotes import DESDE_DEFECTO
from herramientas.resultados import parse_desde
from herramientas.salidas import dividir_mitades, fmt
from herramientas.simulador import simular_token
from herramientas.tenedores_informe import mult_max, peor_en_ambas_mitades, stats_mitades

# mismo universo y filtros que CONSULTA (v0); solo cambian las columnas y el LEFT JOIN
CONSULTA_I = ("SELECT d.mint, d.ts, i.mint, i.n_insiders, i.n_compradores_slot0, i.error, i.n_hermanos, i.madre, t.creador "
              + CONSULTA[CONSULTA.index(" FROM "):].replace("LEFT JOIN tenedores te ON te.mint = d.mint",
                                                            "LEFT JOIN insider i ON i.mint = d.mint"))
COLS_I = ("mint", "ts", "mint_ins", "n_insiders", "n_compradores", "error_ins", "n_hermanos", "madre", "creador")


# ---------- funciones puras ----------

def preparar(registros, fotos_de):
    """Devuelve (filas del universo, descartes). fotos_de(mint) inyectable (tests sin BD)."""
    descartes = {"sin fila": 0, "con error": 0, "sin entrada": 0, "sin retorno S1": 0}
    filas = []
    for r in registros:
        if r["mint_ins"] is None:
            descartes["sin fila"] += 1
        elif r["error_ins"] is not None or r["n_insiders"] is None:
            descartes["con error"] += 1
        else:
            fotos = fotos_de(r["mint"])
            i, _ = elegir_entrada(fotos, 0)
            if i is None:
                descartes["sin entrada"] += 1
                continue
            s1 = simular_token(fotos[i:], SIM_TAM_SOL, 1.2, 0.3, 0, 0, COMISION, SIM_FEE_RED_SOL)
            if s1 is None:
                descartes["sin retorno S1"] += 1
            else:
                filas.append({"mint": r["mint"], "ts": r["ts"], "n_insiders": r["n_insiders"],
                              "n_compradores": r["n_compradores"], "n_hermanos": r["n_hermanos"],  # None = fallo de hermanos
                              "madre": r["madre"], "creador": r["creador"], "ret": s1["retorno"], "mult": mult_max(fotos[i:])})
    mitad = dividir_mitades({f["mint"]: f["ts"] for f in filas})
    for f in filas:
        f["mitad"] = mitad[f["mint"]]
    return filas, descartes


def veredicto_dia(insider, resto, minimo=TENEDORES_MIN_N, etiqueta="VEREDICTO_DIA", nombre="insider"):
    """insider/resto: salida de stats_mitades (grupo de riesgo y resto). Texto de la línea de veredicto."""
    if any(g[h][0] < minimo for g in (insider, resto) for h in (1, 2)):
        return f"{etiqueta}: NO CONCLUYENTE (n < {minimo} en algún grupo y mitad)"
    peor, _ = peor_en_ambas_mitades(insider, resto, minimo)
    return f"{etiqueta}: {nombre} PEOR en las dos mitades" if peor else f"{etiqueta}: NO"


def madres_resumen(filas, top=5):
    """(madres distintas, madres que financian a >= 2 creadores distintos, top [(madre en 6 caracteres, n creadores)])."""
    creadores = {}
    for f in filas:
        if f["madre"]:
            creadores.setdefault(f["madre"], set()).add(f["creador"])
    orden = sorted(creadores.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return len(creadores), sum(len(c) >= 2 for c in creadores.values()), [(m[:6], len(c)) for m, c in orden[:top]]


# ---------- informe ----------

def _linea(nombre, st):
    partes = [f"mitad {h}: n={st[h][0]} media%={fmt(st[h][1])} mediana%={fmt(st[h][2])} mult={fmt(st[h][3], 1, 2)}"
              for h in (1, 2)]
    return f"  {nombre} | " + " | ".join(partes)


def informe(filas, descartes, minimo=TENEDORES_MIN_N):
    out = [f"Tokens en el universo: {len(filas)}"]
    out += [f"  fuera por {m}: {n}" for m, n in descartes.items()]
    if not filas:
        return out + ["Universo vacío."]
    comp = [f["n_compradores"] for f in filas]
    n_madres, n_multi, top = madres_resumen(filas)
    out += ["", "Información (no entra en el veredicto):",
            f"  compradores en el slot 0: media={statistics.fmean(comp):.2f} p50={percentil(comp, 50):.1f} "
            f"p90={percentil(comp, 90):.1f}",
            f"  madres distintas: {n_madres}; financian a >= 2 creadores del día: {n_multi}",
            "  top 5 madres por nº de creadores: " + (", ".join(f"{m}={n}" for m, n in top) or "-")]
    for clave, nombre, etiqueta in (("n_insiders", "insider", "VEREDICTO_DIA"), ("n_hermanos", "hermanos", "VEREDICTO_DIA_HERMANOS")):
        validas = [f for f in filas if f[clave] is not None]  # hermanos: se excluyen los tokens con error_hermanos
        con_g = [f for f in validas if f[clave] >= 1]
        sin_g = [f for f in validas if f[clave] < 1]
        sg, ss = stats_mitades(con_g), stats_mitades(sin_g)
        out += ["", f"{nombre}: {100 * len(con_g) / len(validas) if validas else 0:.1f} % de tokens con >= 1 "
                    f"({len(con_g)} de {len(validas)}; fuera por error: {len(filas) - len(validas)})",
                "S1 neto de la v0 (x1,2 / -30 %, entrada a 60 s):", _linea(f"{nombre:<11}", sg), _linea("sin " + nombre, ss),
                veredicto_dia(sg, ss, minimo, etiqueta, nombre)]
    return out


def main():
    ap = argparse.ArgumentParser(description="Informe del criterio INSIDER, un día (solo lectura)")
    ap.add_argument("--desde", default=DESDE_DEFECTO, help='hora local "YYYY-MM-DD HH:MM"')
    ap.add_argument("--hasta", help='hora local "YYYY-MM-DD HH:MM" (por defecto, desde + 24 h)')
    a = ap.parse_args()
    try:
        desde = parse_desde(a.desde)
        hasta_usuario = parse_desde(a.hasta) if a.hasta else desde + 24 * 3600
    except ValueError:
        ap.error('--desde/--hasta deben tener el formato "YYYY-MM-DD HH:MM"')
    hasta = min(hasta_usuario, time.time() - SEGUIMIENTO_S)
    con = sqlite3.connect(f"file:{RUTA_BD}?mode=ro", uri=True)
    try:
        registros = [dict(zip(COLS_I, fila)) for fila in con.execute(CONSULTA_I, (desde, hasta))]
        print(f"Rango: {a.desde} -> {a.hasta or 'desde + 24 h'}; "
              f"tope efectivo: {time.strftime('%Y-%m-%d %H:%M', time.localtime(hasta))}")
        filas, descartes = preparar(registros, lambda m: fotos_token(con, m))
    finally:
        con.close()
    print(f"n mínimo por grupo y mitad: {TENEDORES_MIN_N}")
    print("\n".join(informe(filas, descartes)))


if __name__ == "__main__":
    main()
