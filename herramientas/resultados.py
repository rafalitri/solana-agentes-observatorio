import argparse
import json
import sqlite3
import statistics
import time
from collections import defaultdict
from datetime import datetime

from comun.config import BASE_SOL_ESTANDAR, RUTA_BD, SEGUIMIENTO_S, TOLERANCIA_BASE_SOL

MINUTOS = (5, 15, 30)
TOLERANCIA_S = 120  # una foto vale para el minuto N si está a menos de esto de N*60


def parse_desde(texto):
    # hora local "YYYY-MM-DD HH:MM" -> epoch; ValueError si el formato no encaja
    return datetime.strptime(texto, "%Y-%m-%d %H:%M").timestamp()


def mult(mc, mc_inicial):
    if mc is None or not mc_inicial:
        return None
    return mc / mc_inicial


def calcular_token(mc_inicial, fotos):
    # fotos: (segundos, market_cap_sol, complete) por orden de tiempo.
    # segundos = fotos.ts - tokens.ts (verificado con datos reales)
    mcs = [mc for _, mc, _ in fotos if mc is not None]
    mults = {}
    for n in MINUTOS:
        limite = n * 60
        if any(c and s <= limite for s, _, c in fotos):
            mults[n] = "graduado"
            continue
        previas = [(s, mc) for s, mc, _ in fotos if s <= limite]
        if not previas or limite - previas[-1][0] > TOLERANCIA_S:
            mults[n] = None
        else:
            mults[n] = mult(previas[-1][1], mc_inicial)
    return {
        "mult_max": mult(max(mcs), mc_inicial) if mcs else None,
        "mults": mults,
        "graduado": any(c for _, _, c in fotos),
        "grad_inst": grad_inst(fotos),
    }


def grad_inst(fotos):
    # fotos: (segundos, market_cap_sol, complete, ...); instantáneo = la 1.ª foto ya está graduada
    return bool(fotos) and bool(fotos[0][2])


def grupos_de(pasa, motivos, marcas, control):
    grupos = ["pasan" if pasa else "descartados"]
    grupos += [f"regla:{m}" for m in motivos]
    grupos += [f"marca:{m}" for m in marcas]
    if control:
        grupos.append("control")
    return grupos


def curva_atipica(mayhem, curva):
    # curva: (v_sol, real_sol, v_tokens) por foto, en lamports. En mayhem la base varía: no aplica.
    if mayhem:
        return False
    for v_sol, real_sol, _ in curva:
        if v_sol is not None:
            base = (v_sol - (real_sol or 0)) / 1e9
            return abs(base - BASE_SOL_ESTANDAR) > TOLERANCIA_BASE_SOL
    return False


def agrupar(tokens):
    # tokens: (mc_inicial, fotos, decision, mayhem, curva) con decision = (pasa, motivos, marcas, control) o None
    # devuelve (grupos: nombre -> lista de calcular_token, n_instantaneos, n_sin_decision,
    #           n_curva_atipica, n_curva_atipica_con_operaciones)
    grupos, inst, sin_dec, atip, atip_ops = defaultdict(list), 0, 0, 0, 0
    for mc_inicial, fotos, decision, mayhem, curva in tokens:
        r = calcular_token(mc_inicial, fotos)
        if r["grad_inst"]:
            inst += 1
            continue
        if curva_atipica(mayhem, curva):
            atip += 1
            atip_ops += len({vt for _, _, vt in curva if vt is not None}) > 1
            continue
        grupos["todos"].append(r)
        if mayhem:
            grupos["mayhem"].append(r)
        if decision is None:
            sin_dec += 1
            continue
        for g in grupos_de(*decision):
            grupos[g].append(r)
    return grupos, inst, sin_dec, atip, atip_ops


def _pct(cuantos, n):
    return 100 * cuantos / n if n else None


def _mediana(valores):
    return statistics.median(valores) if valores else None


def resumir(rs):
    n = len(rs)
    maxs = [r["mult_max"] for r in rs if r["mult_max"] is not None]
    res = {
        "n": n,
        "pct_grad": _pct(sum(r["graduado"] for r in rs), n),
        "med_max": _mediana(maxs),
        "pct_2": _pct(sum(m >= 2 for m in maxs), n),
        "pct_5": _pct(sum(m >= 5 for m in maxs), n),
    }
    for m in MINUTOS:
        vals = [r["mults"][m] for r in rs if isinstance(r["mults"][m], float)]
        res[f"med_{m}"] = (_mediana(vals), len(vals))
    return res


def _f(x, fmt):
    return "-" if x is None else format(x, fmt)


def formatear(grupos, inst, sin_dec, atip, atip_ops):
    orden = ["todos", "pasan", "descartados"]
    orden += sorted(g for g in grupos if g.startswith("regla:"))
    orden += sorted(g for g in grupos if g.startswith("marca:"))
    orden += ["control", "mayhem"]
    cab = f"{'grupo':<28}{'n':>6}{'%grad':>7}{'med_max':>9}{'%>=2x':>7}{'%>=5x':>7}" + "".join(
        f"{'med_' + str(m) + 'm (n)':>14}" for m in MINUTOS)
    lineas = [cab, "-" * len(cab)]
    for g in orden:
        r = resumir(grupos.get(g, []))
        celdas = "".join(
            f"{_f(v, '.2f') + ' (' + str(k) + ')':>14}" for v, k in (r[f"med_{m}"] for m in MINUTOS))
        lineas.append(
            f"{g:<28}{r['n']:>6}{_f(r['pct_grad'], '.1f'):>7}{_f(r['med_max'], '.2f'):>9}"
            f"{_f(r['pct_2'], '.1f'):>7}{_f(r['pct_5'], '.1f'):>7}{celdas}")
    lineas += ["", f"Graduados instantaneos (excluidos de todos los grupos): {inst}",
               f"Curva atipica (excluidos de todos los grupos): {atip} (con operaciones: {atip_ops})"]
    if sin_dec:
        lineas.append(f"Tokens sin fila en decisiones (solo cuentan en 'todos'): {sin_dec}")
    return "\n".join(lineas)


def leer(desde_ts, hasta_ts):
    con = sqlite3.connect(f"file:{RUTA_BD}?mode=ro", uri=True)
    try:
        toks = con.execute(
            "SELECT mint, market_cap_sol, mayhem FROM tokens WHERE ts >= ? AND ts <= ? "
            "AND EXISTS (SELECT 1 FROM fotos WHERE fotos.mint = tokens.mint)",
            (desde_ts, hasta_ts)).fetchall()
        fotos, curvas = defaultdict(list), defaultdict(list)
        for mint, s, mc, c, vs, rs, vt in con.execute(
                "SELECT f.mint, f.ts - t.ts, f.market_cap_sol, f.complete, f.v_sol, f.real_sol, f.v_tokens "
                "FROM fotos f JOIN tokens t ON t.mint = f.mint WHERE t.ts >= ? AND t.ts <= ? "
                "ORDER BY f.mint, f.ts",
                (desde_ts, hasta_ts)):
            fotos[mint].append((s, mc, c))
            curvas[mint].append((vs, rs, vt))
        decs = {m: (bool(p), json.loads(mo), json.loads(ma), bool(c)) for m, p, mo, ma, c in con.execute(
            "SELECT mint, pasa, motivos, marcas, control FROM decisiones")}
    finally:
        con.close()
    return [(mc, fotos[m], decs.get(m), bool(my), curvas[m]) for m, mc, my in toks]


def main():
    ap = argparse.ArgumentParser(description="Informe de resultados (solo lectura)")
    ap.add_argument("--desde", required=True, help='hora local "YYYY-MM-DD HH:MM"')
    args = ap.parse_args()
    try:
        desde = parse_desde(args.desde)
    except ValueError:
        ap.error('--desde debe tener el formato "YYYY-MM-DD HH:MM"')
    tokens = leer(desde, time.time() - SEGUIMIENTO_S)
    print(formatear(*agrupar(tokens)))


if __name__ == "__main__":
    main()
