"""Informe del juez (solo lectura): CRITERIO JUEZ v1, ¿los tokens con 0 banderas rinden mejor que los de >= 1? Fijado de antemano."""
import argparse
import sqlite3
import time

from comun.config import RUTA_BD, SEGUIMIENTO_S, TENEDORES_MIN_N
from comun.db import fotos_token
from herramientas.resultados import parse_desde
from herramientas.tenedores_informe import (COLS_T, CONSULTA_T, _linea, peor_en_ambas_mitades, preparar,
                                            stats_mitades)

VERSION_JUEZ_CRITERIO = 1  # fija en el criterio: no sigue a JUEZ_VERSION
BANDERAS = ("b1", "b2", "b3", "b4", "b5", "b6")
SIN_DATO_VALE = ("b1", "b2", "b4", "b5", "b6")  # b3 se recalcula, nunca falta
CONSULTA_JUICIOS = "SELECT mint, version_juez, b1, b2, b4, b5, b6 FROM juicios WHERE momento = 1"


# ---------- funciones puras ----------

def unir_juicios(filas, juicios, umbral_top10):
    """juicios: {mint: dict con version_juez, b1, b2, b4, b5, b6}. Devuelve (filas con b1..b6 y n_banderas, descartes).
    b3 se recalcula con el top10_pct de la fila; el b3 guardado no se usa."""
    descartes = {"sin juicio": 0, "otra version": 0, "sin dato": 0}
    out = []
    for f in filas:
        j = juicios.get(f["mint"])
        if j is None:
            descartes["sin juicio"] += 1
        elif j["version_juez"] != VERSION_JUEZ_CRITERIO:
            descartes["otra version"] += 1
        elif any(j[b] is None for b in SIN_DATO_VALE):
            descartes["sin dato"] += 1
        else:
            bs = {b: j[b] for b in SIN_DATO_VALE}
            bs["b3"] = int(f["top10_pct"] >= umbral_top10)
            out.append({**f, **bs, "n_banderas": sum(bs.values())})
    return out, descartes


def agrupar_banderas(filas):
    """(>= 1 bandera, 0 banderas)."""
    return [f for f in filas if f["n_banderas"] >= 1], [f for f in filas if f["n_banderas"] == 0]


def por_n_banderas(filas):
    """{"0": ..., "1": ..., "2": ..., "3+": ...} (solo información)."""
    return {k: [f for f in filas if (str(f["n_banderas"]) if f["n_banderas"] < 3 else "3+") == k]
            for k in ("0", "1", "2", "3+")}


def por_bandera(filas, b):
    """(con la bandera, sin ella) (solo información)."""
    return [f for f in filas if f[b] == 1], [f for f in filas if f[b] == 0]


def veredicto(riesgo, resto, minimo=TENEDORES_MIN_N):
    """riesgo/resto: salida de stats_mitades. SI / NO / NO CONCLUYENTE (n < minimo en algún grupo y mitad)."""
    if any(g[h][0] < minimo for g in (riesgo, resto) for h in (1, 2)):
        return f"NO CONCLUYENTE (n < {minimo})"
    ok, _ = peor_en_ambas_mitades(riesgo, resto, minimo)
    return "SI (0 banderas mejor en las dos mitades)" if ok else "NO"


def _recuentos(nombre, grupo):
    return f"  {nombre} | " + " | ".join(f"mitad {h}: n={sum(1 for f in grupo if f['mitad'] == h)}" for h in (1, 2))


# ---------- informe ----------

def informe(filas, descartes, solo_recuentos=False, minimo=TENEDORES_MIN_N):
    """filas: salida de unir_juicios; descartes: los de preparar + los de unir_juicios."""
    out = [f"Tokens en el universo: {len(filas)}"]
    out += [f"  fuera por {m}: {n}" for m, n in descartes.items()]
    riesgo, resto = agrupar_banderas(filas)
    if solo_recuentos:
        return out + [_recuentos(">= 1 bandera", riesgo), _recuentos("0 banderas ", resto)]
    sr, ss = stats_mitades(riesgo), stats_mitades(resto)
    out += ["", _linea(">= 1 bandera", sr), _linea("0 banderas ", ss), "",
            "VEREDICTO_DIA: " + veredicto(sr, ss, minimo), "", "Solo información (no decide):"]
    for k, g in por_n_banderas(filas).items():
        out.append(_linea(f"{k} banderas", stats_mitades(g)))
    for b in BANDERAS:
        con, sin = por_bandera(filas, b)
        out += [_linea(f"{b} con", stats_mitades(con)), _linea(f"{b} sin", stats_mitades(sin))]
    return out


def main():
    ap = argparse.ArgumentParser(description="Informe del juez (solo lectura)")
    ap.add_argument("--desde", required=True, help='hora local "YYYY-MM-DD HH:MM"')
    ap.add_argument("--hasta", help='hora local "YYYY-MM-DD HH:MM" (por defecto, desde + 24 h)')
    ap.add_argument("--umbral-top10", type=float, required=True)
    ap.add_argument("--solo-recuentos", action="store_true", help="sin retornos, medias ni mult")
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
        filas, descartes = preparar(registros, lambda m: fotos_token(con, m))
        cols = ("mint", "version_juez", "b1", "b2", "b4", "b5", "b6")
        juicios = {r[0]: dict(zip(cols, r)) for r in con.execute(CONSULTA_JUICIOS)}
    finally:
        con.close()
    filas, d2 = unir_juicios(filas, juicios, a.umbral_top10)
    print(f"umbral top10 (b3 recalculado): {a.umbral_top10}; n mínimo por grupo y mitad: {TENEDORES_MIN_N}")
    print("\n".join(informe(filas, {**descartes, **d2}, a.solo_recuentos)))


if __name__ == "__main__":
    main()
