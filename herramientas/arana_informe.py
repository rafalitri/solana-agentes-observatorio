"""Informe de la araña (solo lectura): ¿los tokens con monederos activos repetidos rinden peor en S1? Criterio fijado de antemano."""
import argparse
import json
import sqlite3
import time

from comun.config import (ARANA_DECIMALES, ARANA_MIN_PCT, ARANA_MIN_PREVIOS, RUTA_BD, SEGUIMIENTO_S, TENEDORES_MIN_N,
                          TENEDORES_SUPPLY)
from comun.db import fotos_token
from herramientas.rebotes import DESDE_DEFECTO
from herramientas.resultados import parse_desde
from herramientas.tenedores_informe import (COLS_T, CONSULTA_T, _linea, peor_en_ambas_mitades, preparar,
                                            stats_mitades)

SUPPLY = TENEDORES_SUPPLY * 10 ** ARANA_DECIMALES
CONSULTA_RAW = ("SELECT te.mint, te.ts_consulta, te.raw, t.bonding_curve, t.creador FROM tenedores te "
                "JOIN tokens t ON t.mint = te.mint WHERE te.error IS NULL AND te.raw IS NOT NULL "
                "ORDER BY te.ts_consulta")


# ---------- funciones puras ----------

def monederos_activos(raw_json, curva, creador, supply=SUPPLY, min_pct=ARANA_MIN_PCT):
    """Dueños del raw de un token (sin la curva ni el creador) con cantidad >= min_pct % del supply."""
    por_dueno = {}
    for c in json.loads(raw_json):
        por_dueno[c["dueno"]] = por_dueno.get(c["dueno"], 0) + c["cantidad"]
    return {d for d, q in por_dueno.items() if d not in (curva, creador) and q * 100 >= min_pct * supply}


def contar_previos(items, minimo=ARANA_MIN_PREVIOS):
    """items: [(mint, ts_consulta, monederos activos)] en cualquier orden (se ordena aquí). Devuelve {mint: n
    monederos repetidos}: los que ya estaban activos en >= minimo tokens con ts_consulta estrictamente menor.
    Los empates de ts_consulta se procesan en bloque (ninguno cuenta como previo de otro del mismo bloque)."""
    items = sorted(items, key=lambda it: it[1])
    visto, out, i = {}, {}, 0
    while i < len(items):
        j = i
        while j < len(items) and items[j][1] == items[i][1]:
            j += 1
        for mint, _, ws in items[i:j]:
            out[mint] = sum(1 for w in ws if visto.get(w, 0) >= minimo)
        for _, _, ws in items[i:j]:
            for w in ws:
                visto[w] = visto.get(w, 0) + 1
        i = j
    return out


def agrupar(filas):
    """(con >= 1 monedero activo repetido, con 0)."""
    return [f for f in filas if f["n_rep"] >= 1], [f for f in filas if f["n_rep"] == 0]


def distribucion(filas):
    """{'0': n, '1': n, '2': n, '3+': n}."""
    d = {"0": 0, "1": 0, "2": 0, "3+": 0}
    for f in filas:
        d[str(f["n_rep"]) if f["n_rep"] < 3 else "3+"] += 1
    return d


def veredicto_dia1(aporta):
    return "VEREDICTO: " + ("pasa a día 2" if aporta else "no aporta")


def veredicto_dia2(aporta):
    return "VEREDICTO: " + ("APORTA" if aporta else "no aporta")


# ---------- informe ----------

def informe(filas, descartes, modo, minimo=TENEDORES_MIN_N):
    out = [f"Tokens en el universo: {len(filas)}"]
    out += [f"  fuera por {m}: {n}" for m, n in descartes.items()]
    if not filas:
        return out + ["Universo vacío."]
    con, sin = agrupar(filas)
    out.append(f"Con >= 1 monedero activo repetido: {len(con)}; distribución {distribucion(filas)}")
    sc, ss = stats_mitades(con), stats_mitades(sin)
    out += [_linea(">= 1", sc), _linea("0    ", ss), ""]
    ok, motivo = peor_en_ambas_mitades(sc, ss, minimo)
    out.append(f"{'APORTA_DIA1' if modo == 'dia1' else 'SE_REPITE'}: {'sí' if ok else 'no'} ({motivo})")
    out.append(veredicto_dia1(ok) if modo == "dia1" else veredicto_dia2(ok))
    return out


def main():
    ap = argparse.ArgumentParser(description="Informe de la araña (solo lectura)")
    ap.add_argument("--modo", required=True, choices=("dia1", "dia2"))
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
        registros = [dict(zip(COLS_T, fila)) for fila in con.execute(CONSULTA_T, (desde, hasta))]
        print(f"Rango: {a.desde} -> {a.hasta or 'desde + 24 h'}; "
              f"tope efectivo: {time.strftime('%Y-%m-%d %H:%M', time.localtime(hasta))}")
        # toda la tabla tenedores, en orden de ts_consulta: cada token solo ve lo anterior a su consulta
        items = []
        for mint, ts, raw, curva, creador in con.execute(CONSULTA_RAW):
            ws = monederos_activos(raw, curva, creador)
            if ws:  # sin monederos activos no aporta ni recibe; n_rep = 0 por defecto
                items.append((mint, ts, ws))
        n_rep = contar_previos(items)
        filas, descartes = preparar(registros, lambda m: fotos_token(con, m))
    finally:
        con.close()
    for f in filas:
        f["n_rep"] = n_rep.get(f["mint"], 0)
    print(f"n mínimo por grupo y mitad: {TENEDORES_MIN_N}")
    print("\n".join(informe(filas, descartes, a.modo)))


if __name__ == "__main__":
    main()
