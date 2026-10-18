"""Informe del simulador de salidas (solo lectura). Uso:
python -m herramientas.salidas --desde "AAAA-MM-DD HH:MM" [--hasta "..."] [--tam 0.1] [--retraso 0|1] [--desliz 0.0]
"""
import argparse
import sqlite3
import statistics
import time
from collections import defaultdict
from itertools import product

from comun.config import RUTA_BD, SEGUIMIENTO_S, SIM_COMISION, SIM_FEE_RED_SOL, SIM_TAM_SOL
from herramientas.resultados import curva_atipica, grad_inst, parse_desde
from herramientas.simulador import simular_token

OBJETIVOS = [1.2, 1.3, 1.5, 2, 3, None]
STOPS = [0.3, 0.5, None]
COMBOS = list(product(OBJETIVOS, STOPS))
TRAMOS = ["0", "<0.01", "0.01-0.1", "0.1-1", "1-5", ">=5"]
MOTIVOS = ["objetivo", "stop", "tiempo", "graduacion"]


def tramo_compra(sol_amount):
    # límites semiabiertos: 0.1 cae en "0.1-1". None cuenta como 0.
    s = sol_amount or 0
    if s <= 0:
        return "0"
    for tope, nombre in ((0.01, "<0.01"), (0.1, "0.01-0.1"), (1, "0.1-1"), (5, "1-5")):
        if s < tope:
            return nombre
    return ">=5"


def dividir_mitades(ts_por_mint):
    # mitad 1 = los más antiguos (ts <= mediana); los empates con la mediana van a la mitad 1
    if not ts_por_mint:
        return {}
    med = statistics.median(ts_por_mint.values())
    return {m: 1 if ts <= med else 2 for m, ts in ts_por_mint.items()}


def metricas(items, tam):
    # items: (retorno como fracción, motivo_salida, mitad 1|2)
    n = len(items)
    r1 = [r for r, _, h in items if h == 1]
    r2 = [r for r, _, h in items if h == 2]
    rs = [r for r, _, _ in items]
    media = lambda x: statistics.fmean(x) if x else None
    return {
        "n": n, "n1": len(r1), "n2": len(r2),
        "media1": media(r1), "media2": media(r2), "media": media(rs),
        "mediana": statistics.median(rs) if rs else None,
        "pct_pos": 100 * sum(r > 0 for r in rs) / n if n else None,
        "peor": min(rs) if rs else None,
        "pct_motivo": {m: 100 * sum(x == m for _, x, _ in items) / n if n else None for m in MOTIVOS},
        "sol_neto": sum(rs) * tam,
    }


def leer(desde, hasta):
    con = sqlite3.connect(f"file:{RUTA_BD}?mode=ro", uri=True)
    try:
        sin_mayhem = "t.ts >= ? AND t.ts <= ? AND COALESCE(t.mayhem, 0) = 0"
        toks = con.execute(
            "SELECT t.mint, t.ts, t.sol_amount, MAX(d.pasa) FROM tokens t "
            f"LEFT JOIN decisiones d ON d.mint = t.mint WHERE {sin_mayhem} GROUP BY t.mint",
            (desde, hasta)).fetchall()
        n_mayhem = con.execute(
            "SELECT COUNT(*) FROM tokens t WHERE t.ts >= ? AND t.ts <= ? AND t.mayhem = 1",
            (desde, hasta)).fetchone()[0]
        n_multi = con.execute(
            "SELECT COUNT(*) FROM (SELECT d.mint FROM decisiones d JOIN tokens t ON t.mint = d.mint "
            "WHERE t.ts >= ? AND t.ts <= ? GROUP BY d.mint HAVING COUNT(*) > 1)", (desde, hasta)).fetchone()[0]
        fotos = defaultdict(list)
        for mint, *fila in con.execute(
                "SELECT f.mint, f.ts - t.ts, f.market_cap_sol, f.complete, f.v_sol, f.v_tokens, f.real_sol "
                f"FROM fotos f JOIN tokens t ON t.mint = f.mint WHERE {sin_mayhem} ORDER BY f.mint, f.ts",
                (desde, hasta)):
            fotos[mint].append(tuple(fila))
    finally:
        con.close()
    return toks, fotos, n_mayhem, n_multi


def contar_fuera_ventana(desde, hasta_usuario, hasta_ef):
    if hasta_usuario <= hasta_ef:
        return 0
    con = sqlite3.connect(f"file:{RUTA_BD}?mode=ro", uri=True)
    try:
        return con.execute("SELECT COUNT(*) FROM tokens WHERE ts >= ? AND ts <= ? AND ts > ?",
                           (desde, hasta_usuario, hasta_ef)).fetchone()[0]
    finally:
        con.close()


def preparar(toks, fotos):
    # fila de foto: (segundos, mc, complete, v_sol, v_tokens, real_sol)
    # devuelve tokens válidos [(mint, ts, sol_amount, pasa, fotos_dict)] y contadores de exclusión
    exc = {"graduado instantaneo": 0, "curva atipica": 0}
    validos = []
    for mint, ts, sol, pasa in toks:
        filas = fotos.get(mint, [])
        if grad_inst(filas):
            exc["graduado instantaneo"] += 1
            continue
        if curva_atipica(False, [(f[3], f[5], f[4]) for f in filas]):
            exc["curva atipica"] += 1
            continue
        fd = [{"segundos": f[0], "v_sol": f[3], "v_tokens": f[4], "real_sol": f[5], "complete": f[2]}
              for f in filas]
        validos.append((mint, ts, sol, pasa, fd))
    return validos, exc


def simular_todo(validos, tam, retraso, desliz):
    # cada token se simula una vez por combinación; devuelve (tokens con entrada, {combo: [resultado]})
    con_entrada, res = [], {c: [] for c in COMBOS}
    for tok in validos:
        r0 = simular_token(tok[4], tam, *COMBOS[0], retraso, desliz, SIM_COMISION, SIM_FEE_RED_SOL)
        if r0 is None:
            continue
        con_entrada.append(tok)
        for c in COMBOS:
            res[c].append(r0 if c == COMBOS[0] else
                          simular_token(tok[4], tam, *c, retraso, desliz, SIM_COMISION, SIM_FEE_RED_SOL))
    return con_entrada, res


def fmt(x, mult=100, dec=1):
    return "-" if x is None else f"{x * mult:.{dec}f}"


def informe(tokens, res, tam):
    mitad = dividir_mitades({t[0]: t[1] for t in tokens})
    grupos = defaultdict(list)  # nombre -> posiciones en `tokens`
    for i, (mint, _, sol, pasa, _) in enumerate(tokens):
        grupos["todos"].append(i)
        if pasa:
            grupos["pasan"].append(i)
        grupos["compra creador " + tramo_compra(sol)].append(i)
    cab = (f"{'objetivo':>8} {'stop':>5} {'n':>5} {'n1':>5} {'n2':>5} {'media1%':>8} {'media2%':>8} "
           f"{'media%':>8} {'mediana%':>9} {'>0 %':>6} {'peor%':>8} {'obj%':>6} {'stop%':>6} "
           f"{'tiempo%':>7} {'grad%':>6} {'SOL neto':>9}")
    out = []
    for nombre in ["todos", "pasan"] + ["compra creador " + t for t in TRAMOS]:
        idx = grupos.get(nombre, [])
        out += ["", f"== {nombre} ({len(idx)} tokens) =="]
        if not idx:
            out.append("sin tokens")
            continue
        filas = []
        for c in COMBOS:
            m = metricas([(res[c][i]["retorno"], res[c][i]["motivo_salida"], mitad[tokens[i][0]]) for i in idx], tam)
            filas.append((c, m))
        filas.sort(key=lambda f: float("-inf") if f[1]["media1"] is None else f[1]["media1"], reverse=True)
        out.append(cab)
        for (obj, stop), m in filas:
            pm = m["pct_motivo"]
            out.append(
                f"{'-' if obj is None else obj:>8} {'-' if stop is None else stop:>5} {m['n']:>5} {m['n1']:>5} "
                f"{m['n2']:>5} {fmt(m['media1']):>8} {fmt(m['media2']):>8} {fmt(m['media']):>8} "
                f"{fmt(m['mediana']):>9} {fmt(m['pct_pos'], 1):>6} {fmt(m['peor']):>8} "
                f"{fmt(pm['objetivo'], 1):>6} {fmt(pm['stop'], 1):>6} {fmt(pm['tiempo'], 1):>7} "
                f"{fmt(pm['graduacion'], 1):>6} {m['sol_neto']:>9.3f}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Informe del simulador de salidas (solo lectura)")
    ap.add_argument("--desde", required=True, help='hora local "YYYY-MM-DD HH:MM"')
    ap.add_argument("--hasta", help='hora local "YYYY-MM-DD HH:MM" (por defecto, ahora)')
    ap.add_argument("--tam", type=float, default=SIM_TAM_SOL)
    ap.add_argument("--retraso", type=int, choices=[0, 1], default=0)
    ap.add_argument("--desliz", type=float, default=0.0)
    a = ap.parse_args()
    try:
        desde = parse_desde(a.desde)
        hasta_usuario = parse_desde(a.hasta) if a.hasta else time.time()
    except ValueError:
        ap.error('--desde/--hasta deben tener el formato "YYYY-MM-DD HH:MM"')
    hasta = min(hasta_usuario, time.time() - SEGUIMIENTO_S)  # ventana de 30 min completa
    t0 = time.time()
    toks, fotos, n_mayhem, n_multi = leer(desde, hasta)
    fuera = contar_fuera_ventana(desde, hasta_usuario, hasta)
    validos, exc = preparar(toks, fotos)
    tokens, res = simular_todo(validos, a.tam, a.retraso, a.desliz)
    print(f"Rango: {a.desde} -> {a.hasta or 'ahora'} (tope efectivo por ventana: "
          f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(hasta))})")
    print(f"Parametros: tam={a.tam} SOL, retraso={a.retraso}, desliz={a.desliz}, "
          f"comision={SIM_COMISION} por lado, fee red={SIM_FEE_RED_SOL} SOL por tx")
    print(f"Tokens incluidos: {len(tokens)}")
    print(f"Excluidos: fuera de ventana={fuera}, mayhem={n_mayhem}, graduado instantaneo="
          f"{exc['graduado instantaneo']}, curva atipica={exc['curva atipica']}, "
          f"sin entrada valida={len(validos) - len(tokens)}")
    print(f"Tokens con mas de una decision: {n_multi}")
    if tokens:
        print(informe(tokens, res, a.tam))
    print(f"\nTiempo: {time.time() - t0:.1f} s")


if __name__ == "__main__":
    main()
