"""Informe de tenedores (solo lectura): ¿la concentración o el creador grande empeoran S1 de la v0? Criterio fijado de antemano."""
import argparse
import sqlite3
import time

from comun.config import (RUTA_BD, SEGUIMIENTO_S, SIM_FEE_RED_SOL, SIM_TAM_SOL, TENEDORES_MIN_N)
from comun.db import fotos_token
from herramientas.estrategia_v0 import (COLS, COMISION, CONSULTA, _valida, elegir_entrada, estadisticas,
                                        percentil)
from herramientas.rebotes import DESDE_DEFECTO
from herramientas.resultados import parse_desde
from herramientas.salidas import dividir_mitades, fmt
from herramientas.simulador import simular_token

PERCENTIL_TOP10 = 67
UMBRAL_CREADOR = 5  # % del supply
MEDIDAS = ("A", "B")
NOMBRE_MEDIDA = {"A": "concentración alta (top10_pct >= UMBRAL_TOP10)", "B": "creador_pct > 5"}
CONSULTA_T = CONSULTA.replace("te.seg_desde_primera_foto ", "te.seg_desde_primera_foto, te.mint ")
COLS_T = COLS + ("mint_ten",)


# ---------- funciones puras ----------

def consultada_a_tiempo(seg_ten, seg_entrada_rel):
    """seg_ten = seg_desde_primera_foto de la consulta; seg_entrada_rel = segundos de entrada - segundos de la 1.ª foto."""
    return seg_ten is not None and seg_ten <= seg_entrada_rel


def mult_max(fotos_desde_entrada):
    """Máximo precio válido (v_sol / v_tokens) desde la foto de entrada incluida, entre el precio de entrada."""
    precios = [f["v_sol"] / f["v_tokens"] for f in fotos_desde_entrada if _valida(f)]
    return max(precios) / precios[0] if precios else None


def preparar(registros, fotos_de):
    """Devuelve (filas del universo, descartes {motivo: n}). fotos_de(mint) inyectable (tests sin BD)."""
    descartes = {"sin fila": 0, "con error": 0, "sin entrada": 0, "consultada tarde": 0, "sin retorno S1": 0}
    filas = []
    for r in registros:
        if r["mint_ten"] is None:
            descartes["sin fila"] += 1
        elif r["error_ten"] is not None or r["top10_pct"] is None or r["creador_pct"] is None:
            descartes["con error"] += 1
        else:
            fotos = fotos_de(r["mint"])
            i, _ = elegir_entrada(fotos, 0)
            if i is None:
                descartes["sin entrada"] += 1
            elif not consultada_a_tiempo(r["seg_ten"], fotos[i]["segundos"] - fotos[0]["segundos"]):
                descartes["consultada tarde"] += 1
            else:
                s1 = simular_token(fotos[i:], SIM_TAM_SOL, 1.2, 0.3, 0, 0, COMISION, SIM_FEE_RED_SOL)
                if s1 is None:
                    descartes["sin retorno S1"] += 1
                else:
                    filas.append({"mint": r["mint"], "ts": r["ts"], "top10_pct": r["top10_pct"],
                                  "creador_pct": r["creador_pct"], "ret": s1["retorno"],
                                  "mult": mult_max(fotos[i:]), "ts_entrada": fotos[i].get("ts")})
    mitad = dividir_mitades({f["mint"]: f["ts"] for f in filas})
    for f in filas:
        f["mitad"] = mitad[f["mint"]]
    return filas, descartes


def agrupar(filas, medida, umbral_top10):
    """(riesgo, resto) de la medida A o B. A: top10_pct >= umbral. B: creador_pct > 5."""
    if medida == "A":
        riesgo = lambda f: f["top10_pct"] >= umbral_top10
    else:
        riesgo = lambda f: f["creador_pct"] > UMBRAL_CREADOR
    return [f for f in filas if riesgo(f)], [f for f in filas if not riesgo(f)]


def stats_mitades(grupo):
    """{1: (n, media ret, mediana ret, media mult), 2: ...}."""
    out = {}
    for h in (1, 2):
        g = [f for f in grupo if f["mitad"] == h]
        n, media, mediana = estadisticas([f["ret"] for f in g])
        out[h] = (n, media, mediana, estadisticas([f["mult"] for f in g])[1])
    return out


def peor_en_ambas_mitades(riesgo, resto, minimo=TENEDORES_MIN_N):
    """riesgo/resto: salida de stats_mitades. (True, motivo) solo si en las DOS mitades ambos grupos tienen
    n >= minimo y la media del grupo de riesgo es estrictamente menor que la del resto."""
    fallos = []
    for h in (1, 2):
        (nr, mr), (ns, ms) = riesgo[h][:2], resto[h][:2]
        if nr < minimo or ns < minimo:
            fallos.append(f"n insuficiente en mitad {h} (riesgo {nr}, resto {ns}, mínimo {minimo})")
        elif mr is None or ms is None or not mr < ms:
            fallos.append(f"el riesgo no es peor en mitad {h}")
    return (not fallos), ("; ".join(fallos) if fallos else "peor en las dos mitades")


def veredicto_dia1(aporta):
    """aporta: {medida: bool}. Devuelve (medidas que pasan a día 2, texto)."""
    pasan = [m for m in MEDIDAS if aporta.get(m)]
    return pasan, "PASA_A_DIA2: " + (",".join(pasan) if pasan else "ninguna")


def veredicto_dia2(se_repite, medidas):
    """Solo cuentan las medidas pedidas (las que pasaron el día 1). Devuelve (medidas que aportan, texto)."""
    ok = [m for m in MEDIDAS if m in medidas and se_repite.get(m)]
    return ok, "VEREDICTO: " + ("APORTA: " + ",".join(ok) if ok else "no aporta")


def parse_medidas(texto):
    ms = [m.strip() for m in texto.split(",")]
    if any(m not in MEDIDAS for m in ms):
        raise ValueError(texto)
    return ms


# ---------- informe ----------

def _linea(nombre, st):
    partes = [f"mitad {h}: n={st[h][0]} media%={fmt(st[h][1])} mediana%={fmt(st[h][2])} mult={fmt(st[h][3], 1, 2)}"
              for h in (1, 2)]
    return f"  {nombre} | " + " | ".join(partes)


def informe(filas, descartes, modo, umbral=None, medidas=MEDIDAS, minimo=TENEDORES_MIN_N):
    out = [f"Tokens en el universo: {len(filas)}"]
    out += [f"  fuera por {m}: {n}" for m, n in descartes.items()]
    if not filas:
        return out + ["Universo vacío."]
    if modo == "dia1":
        umbral = percentil([f["top10_pct"] for f in filas], PERCENTIL_TOP10)
        out.append(f"UMBRAL_TOP10 congelado: {umbral:.4f}")
    resultado = {}
    for m in MEDIDAS:
        riesgo, resto = agrupar(filas, m, umbral)
        sr, ss = stats_mitades(riesgo), stats_mitades(resto)
        resultado[m] = peor_en_ambas_mitades(sr, ss, minimo)
        out += [f"\n{m}) {NOMBRE_MEDIDA[m]}", _linea("riesgo", sr), _linea("resto ", ss)]
    etiqueta = "APORTA_DIA1" if modo == "dia1" else "SE_REPITE"
    out.append("")
    for m, (ok, motivo) in resultado.items():
        info = " [solo información]" if modo == "dia2" and m not in medidas else ""
        out.append(f"{etiqueta} {m}: {'sí' if ok else 'no'} ({motivo}){info}")
    aporta = {m: r[0] for m, r in resultado.items()}
    out.append(veredicto_dia1(aporta)[1] if modo == "dia1" else veredicto_dia2(aporta, medidas)[1])
    return out


def main():
    ap = argparse.ArgumentParser(description="Informe de tenedores (solo lectura)")
    ap.add_argument("--modo", required=True, choices=("dia1", "dia2"))
    ap.add_argument("--desde", default=DESDE_DEFECTO, help='hora local "YYYY-MM-DD HH:MM"')
    ap.add_argument("--hasta", help='hora local "YYYY-MM-DD HH:MM" (por defecto, desde + 24 h)')
    ap.add_argument("--umbral-top10", type=float)
    ap.add_argument("--medidas", help="A, B o A,B (las que pasaron el día 1)")
    a = ap.parse_args()
    medidas = MEDIDAS
    if a.modo == "dia2":
        if a.umbral_top10 is None or a.medidas is None:
            ap.error("dia2 exige --umbral-top10 y --medidas")
        try:
            medidas = parse_medidas(a.medidas)
        except ValueError:
            ap.error("--medidas debe ser A, B o A,B")
    try:
        desde = parse_desde(a.desde)
        hasta_usuario = parse_desde(a.hasta) if a.hasta else desde + 24 * 3600
    except ValueError:
        ap.error('--desde/--hasta deben tener el formato "YYYY-MM-DD HH:MM"')
    hasta = min(hasta_usuario, time.time() - SEGUIMIENTO_S)
    con = sqlite3.connect(f"file:{RUTA_BD}?mode=ro", uri=True)
    try:
        registros = [dict(zip(COLS_T, fila)) for fila in con.execute(CONSULTA_T, (desde, hasta))]
        print(f"Rango: {a.desde} -> {a.hasta or 'desde + 24 h'}; "
              f"tope efectivo: {time.strftime('%Y-%m-%d %H:%M', time.localtime(hasta))}")
        filas, descartes = preparar(registros, lambda m: fotos_token(con, m))
    finally:
        con.close()
    print(f"n mínimo por grupo y mitad: {TENEDORES_MIN_N}")
    print("\n".join(informe(filas, descartes, a.modo, a.umbral_top10, medidas)))


if __name__ == "__main__":
    main()
