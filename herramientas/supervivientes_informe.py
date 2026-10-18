"""Informe del CRITERIO SUPERVIVIENTES (solo lectura): T1 stop que sigue al precio y T2 aguantar, sobre lecturas cada 60 s.
Criterio fijado de antemano (pieza 33): no cambiar definiciones."""
import argparse
import math
import sqlite3
import statistics
import time

from agentes.supervivientes import precio_curva
from comun.config import (ESTRATEGIA_EJECUCION, RUTA_BD, SIM_COMISION, SIM_FEE_RED_SOL, SIM_TAM_SOL, SUPERV_DURACION_S,
                          SUPERV_MARGEN_FIN_S, SUPERV_VENTANA_S, TENEDORES_MIN_N)
from herramientas.opiniones import resumen
from herramientas.resultados import parse_desde
from herramientas.salidas import dividir_mitades, fmt

HUECO_MAX_S = 1800  # más de 30 min seguidos sin lectura válida = sin dato
ESPERA_GRAD_S = 600  # tras graduarse hay que esperar 10 min
RETRASO_SELECCION_S = 120  # (a) selección más de 2 min después de la marca de 30 min
DESCUADRE_MARCA_S = 60  # control de calidad: marca por 1.ª foto vs marca por ts_fin
FROZEN_MIN, FROZEN_MAX, FROZEN_RACHA = 4.0e-7, 4.2e-7, 3
STOP = 0.7
COSTE = SIM_COMISION + ESTRATEGIA_EJECUCION  # por lado
SALIDAS = ("T1", "T2")
MIN_N = TENEDORES_MIN_N


# ---------- funciones puras ----------

def marca30(primera_foto):
    return None if primera_foto is None else primera_foto + SUPERV_VENTANA_S


def marca_por_fin(ts_fin):
    return ts_fin - SUPERV_DURACION_S - SUPERV_MARGEN_FIN_S


def validas(lecturas):
    """lecturas: [(ts, fuente, precio, dex_id, error)] -> [(ts, precio, fuente, dex_id)] sin error, con precio > 0, por ts."""
    return sorted((ts, p, f, d) for ts, f, p, d, e in lecturas if e is None and p is not None and p > 0)


def elegir_entrada(val, marca, ts_grad, retraso=0):
    """Índice en val de la 1.ª lectura con ts >= max(marca, graduación + 10 min) si ts_grad <= marca; si no, ts >= marca.
    Suma `retraso` lecturas. None si no existe."""
    umbral = marca
    if ts_grad is not None and ts_grad <= marca:
        umbral = max(marca, ts_grad + ESPERA_GRAD_S)
    i = next((k for k, v in enumerate(val) if v[0] >= umbral), None)
    if i is None or i + retraso >= len(val):
        return None
    return i + retraso


def ventana(val, i):
    """Lecturas desde la entrada hasta 24 h después de la lectura de entrada."""
    fin = val[i][0] + SUPERV_DURACION_S
    return [v for v in val[i:] if v[0] <= fin]


def hay_hueco(val, i, maximo=HUECO_MAX_S):
    """True si dentro de las 24 h desde la entrada hay > maximo s sin lectura (el hueco final hasta entrada + 24 h cuenta)."""
    ts = [v[0] for v in ventana(val, i)] + [val[i][0] + SUPERV_DURACION_S]
    return any(b - a > maximo for a, b in zip(ts, ts[1:]))


def par_congelado(vent):
    """True si hay >= 3 lecturas SEGUIDAS (solo válidas, desde la entrada) de fuente dex, dex_id NULL y precio 4,0-4,2e-7."""
    racha = 0
    for _, p, fuente, dex_id in vent:
        if fuente == "dex" and dex_id is None and FROZEN_MIN <= p <= FROZEN_MAX:
            racha += 1
            if racha >= FROZEN_RACHA:
                return True
        else:
            racha = 0
    return False


def retorno(p_ent, p_sal):
    # misma fórmula que simulador.simular_token con desliz 0: costes por lado y fee de red en compra y venta
    return (p_sal / p_ent) * (1 - COSTE) ** 2 - 1 - 2 * SIM_FEE_RED_SOL / SIM_TAM_SOL


def indice_salida_t1(precios, retraso=0):
    """Índice de la lectura de venta: la 1.ª con precio <= 0,7 x máximo desde la entrada (+ retraso lecturas, tope la última);
    si no hay, la última."""
    maximo = precios[0]
    for k, p in enumerate(precios):
        maximo = max(maximo, p)
        if k and p <= STOP * maximo:
            return min(k + retraso, len(precios) - 1)
    return len(precios) - 1


def simular(val, i, salida, retraso=0):
    """Retorno de T1 o T2 entrando en la lectura i (ya con retraso aplicado por quien llama). None si i es None."""
    if i is None:
        return None
    precios = [v[1] for v in ventana(val, i)]
    k = indice_salida_t1(precios, retraso) if salida == "T1" else len(precios) - 1
    return retorno(precios[0], precios[k])


def sin_el_mejor_1pct(rets):
    """Media tras quitar las ceil(1 % de n) mejores; None si no queda nada."""
    resto = sorted(rets, reverse=True)[math.ceil(0.01 * len(rets)):]
    return statistics.fmean(resto) if resto else None


def veredicto(st, media_sin, minimo=MIN_N):
    """st: {1: (n, media, mediana), 2: ...}; media_sin: media del día sin el 1 % mejor."""
    if any(st[h][0] < minimo for h in (1, 2)):
        return f"NO CONCLUYENTE (n < {minimo})"
    if all(st[h][1] is not None and st[h][1] > 0 for h in (1, 2)) and media_sin is not None and media_sin > 0:
        return "SI"
    return "NO"


def preparar(registros, ahora, con_retornos=True):
    """registros: dicts con mint, ts_seleccion, ts_fin, ts_graduacion, primera_foto, lecturas, top10_pct, n_insiders
    (None sin fila), previo. Devuelve {"conteos", "analizados", "sin_dato"}. Con con_retornos=False no calcula ningún retorno."""
    c = {"universo": len(registros), "sin_terminar": 0, "retrasada": 0, "congelado": 0, "sin_dato": 0,
         "descuadre_marca": 0, "sin_primera_foto": 0}
    analizados, sin_dato = [], []
    for r in registros:
        m = marca30(r["primera_foto"])
        if m is None:
            c["sin_primera_foto"] += 1
            m = marca_por_fin(r["ts_fin"])
        elif abs(m - marca_por_fin(r["ts_fin"])) > DESCUADRE_MARCA_S:
            c["descuadre_marca"] += 1
        val = validas(r["lecturas"])
        i = elegir_entrada(val, m, r["ts_graduacion"])
        terminado = (val[i][0] + SUPERV_DURACION_S + SUPERV_MARGEN_FIN_S <= ahora) if i is not None else r["ts_fin"] <= ahora
        if not terminado:
            c["sin_terminar"] += 1
        elif r["ts_seleccion"] > m + RETRASO_SELECCION_S:
            c["retrasada"] += 1
        elif i is not None and par_congelado(ventana(val, i)):
            c["congelado"] += 1
        else:
            fila = {"mint": r["mint"], "ts": r["ts_seleccion"], "top10_pct": r["top10_pct"],
                    "n_insiders": r["n_insiders"], "previo": r["previo"], "base": None, "r1": None,
                    "graduado": None if i is None else val[i][2] == "dex",
                    "ts_entrada": None if i is None else val[i][0]}
            if i is None or hay_hueco(val, i):
                c["sin_dato"] += 1
                sin_dato.append(fila)
            else:
                if con_retornos:
                    fila["base"] = {s: simular(val, i, s) for s in SALIDAS}
                    i1 = elegir_entrada(val, m, r["ts_graduacion"], 1)
                    fila["r1"] = None if i1 is None else {s: simular(val, i1, s, 1) for s in SALIDAS}
                else:
                    fila["r1"] = elegir_entrada(val, m, r["ts_graduacion"], 1) is not None  # solo existencia
                analizados.append(fila)
    mitad = dividir_mitades({f["mint"]: f["ts"] for f in analizados})
    corte = max((f["ts"] for f in analizados if mitad[f["mint"]] == 1), default=None)
    for f in analizados:
        f["mitad"] = mitad[f["mint"]]
    for f in sin_dato:
        f["mitad"] = 1 if corte is None or f["ts"] <= corte else 2
    return {"conteos": c, "analizados": analizados, "sin_dato": sin_dato}


# ---------- informe ----------

def _st(rets_por_mitad):
    return {h: resumen(rets_por_mitad[h]) for h in (1, 2)}


def _linea(nombre, st):
    return f"  {nombre} | " + " | ".join(
        f"mitad {h}: n={st[h][0]} media%={fmt(st[h][1])} mediana%={fmt(st[h][2])}" for h in (1, 2))


def _rets(filas, clave, salida):
    out = {1: [], 2: []}
    for f in filas:
        d = f[clave]
        if d:
            out[f["mitad"]].append(d[salida])
    return out


def _media_dia(rets):
    t = rets[1] + rets[2]
    return statistics.fmean(t) if t else None


def informe(res, solo_recuentos=False):
    c, an, sd = res["conteos"], res["analizados"], res["sin_dato"]
    out = [f"Universo (supervivientes con ts_seleccion en la ventana): {c['universo']}",
           f"  seguimiento sin terminar: {c['sin_terminar']}",
           f"  excluidos (a) selección retrasada > {RETRASO_SELECCION_S} s: {c['retrasada']}",
           f"  excluidos (b) par congelado: {c['congelado']}",
           f"  sin dato (entrada ausente o hueco > {HUECO_MAX_S // 60} min): {c['sin_dato']}",
           f"  analizados: {len(an)}",
           f"  control de calidad: marca 1.ª foto vs ts_fin con diferencia > {DESCUADRE_MARCA_S} s: {c['descuadre_marca']}; "
           f"sin 1.ª foto (marca por ts_fin): {c['sin_primera_foto']}",
           f"n mínimo por mitad: {MIN_N}"]
    n_mitad = lambda g: {h: sum(1 for f in g if f["mitad"] == h) for h in (1, 2)}
    out.append(f"n por mitad: analizados {n_mitad(an)}; sin dato {n_mitad(sd)}")
    if solo_recuentos:
        out.append(f"n con retraso 1 posible: {sum(1 for f in an if f['r1'])}")
        out.append("Info: graduado al entrar " + str(sum(1 for f in an if f["graduado"])) + ", en curva "
                   + str(sum(1 for f in an if f["graduado"] is False)))
        out.append("Info: creador con tokens anteriores " + str(sum(1 for f in an if f["previo"])) + ", sin "
                   + str(sum(1 for f in an if not f["previo"])))
        out.append("Info: insider (n_insiders >= 1) " + str(sum(1 for f in an if f["n_insiders"])) + ", 0 "
                   + str(sum(1 for f in an if f["n_insiders"] == 0)) + ", sin fila "
                   + str(sum(1 for f in an if f["n_insiders"] is None)))
        out.append("(--solo-recuentos: sin retornos ni veredicto)")
        return out
    for s in SALIDAS:
        rb = _rets(an, "base", s)
        st = _st(rb)
        sin = sin_el_mejor_1pct(rb[1] + rb[2])
        out += [f"\n{s}", _linea("base", st),
                f"  media% del día: {fmt(_media_dia(rb))} | sin el {math.ceil(0.01 * len(rb[1] + rb[2]))} mejor(es): {fmt(sin)}"]
        rc = {h: rb[h] + [-1.0] * n_mitad(sd)[h] for h in (1, 2)}
        out.append(_linea("sin dato = -100 %", _st(rc)))
        out.append(_linea("retraso 1", _st(_rets(an, "r1", s))))
        out.append(f"VEREDICTO_DIA_{s}: {veredicto(st, sin)}")
    out.append("\nSolo información (no decide):")
    grupos = (("graduado al entrar", lambda f: f["graduado"] is True), ("en curva al entrar", lambda f: f["graduado"] is False),
              ("creador con tokens anteriores", lambda f: bool(f["previo"])), ("creador sin tokens anteriores", lambda f: not f["previo"]),
              ("insider n_insiders >= 1", lambda f: bool(f["n_insiders"])), ("insider n_insiders = 0", lambda f: f["n_insiders"] == 0),
              ("insider sin fila", lambda f: f["n_insiders"] is None))
    for nombre, cond in grupos:
        g = [f for f in an if cond(f)]
        medias = ", ".join(f"{s} media%={fmt(_media_dia(_rets(g, 'base', s)))}" for s in SALIDAS)
        out.append(f"  {nombre}: n={len(g)} | {medias}")
    t10 = [f["top10_pct"] for f in an if f["top10_pct"] is not None]
    out.append(f"  top10_pct: n con dato={len(t10)} mediana={statistics.median(t10):.2f}" if t10 else "  top10_pct: sin datos")
    return out


# ---------- BD ----------

def cargar(con, desde, hasta, omitir=(), fin_antes=None):
    """omitir: mints que no se cargan; fin_antes: solo supervivientes con ts_fin <= fin_antes (agente contable)."""
    regs = []
    for mint, ts_sel, ts_grad, ts_fin, top10, n_ins, previo in con.execute(
            "SELECT s.mint, s.ts_seleccion, s.ts_graduacion, s.ts_fin, te.top10_pct, i.n_insiders, "
            "EXISTS (SELECT 1 FROM tokens p WHERE p.creador = t.creador AND p.ts < t.ts) "
            "FROM supervivientes s LEFT JOIN tokens t ON t.mint = s.mint LEFT JOIN tenedores te ON te.mint = s.mint "
            "LEFT JOIN insider i ON i.mint = s.mint WHERE s.ts_seleccion >= ? AND s.ts_seleccion < ? "
            "AND (? IS NULL OR s.ts_fin <= ?) ORDER BY s.ts_seleccion",
            (desde, hasta, fin_antes, fin_antes)).fetchall():
        if mint in omitir:
            continue
        # 1.ª foto VÁLIDA, mismo criterio que agentes/supervivientes.py: complete = 0 y precio calculable
        foto = next((ts for ts, vs, vt, comp in con.execute(
            "SELECT ts, v_sol, v_tokens, complete FROM fotos WHERE mint = ? ORDER BY ts", (mint,))
            if not comp and precio_curva(vs, vt) is not None), None)
        lecs = con.execute("SELECT ts, fuente, precio_sol, dex_id, error FROM lecturas_superv WHERE mint = ? ORDER BY ts",
                           (mint,)).fetchall()
        regs.append({"mint": mint, "ts_seleccion": ts_sel, "ts_graduacion": ts_grad, "ts_fin": ts_fin, "primera_foto": foto,
                     "lecturas": lecs, "top10_pct": top10, "n_insiders": n_ins, "previo": previo})
    return regs


def main():
    ap = argparse.ArgumentParser(description="Informe del criterio SUPERVIVIENTES (solo lectura)")
    ap.add_argument("--desde", required=True, help='hora local "YYYY-MM-DD HH:MM"')
    ap.add_argument("--hasta", help='hora local "YYYY-MM-DD HH:MM" (por defecto, desde + 24 h)')
    ap.add_argument("--solo-recuentos", action="store_true", help="humo: sin retornos, medias ni veredicto")
    a = ap.parse_args()
    try:
        desde = parse_desde(a.desde)
        hasta = parse_desde(a.hasta) if a.hasta else desde + 24 * 3600
    except ValueError:
        ap.error('--desde/--hasta deben tener el formato "YYYY-MM-DD HH:MM"')
    con = sqlite3.connect(f"file:{RUTA_BD}?mode=ro", uri=True)
    try:
        regs = cargar(con, desde, hasta)
    finally:
        con.close()
    print(f"Rango: {a.desde} -> {a.hasta or 'desde + 24 h'}")
    print("\n".join(informe(preparar(regs, time.time(), not a.solo_recuentos), a.solo_recuentos)))


if __name__ == "__main__":
    main()
