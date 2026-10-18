"""Pieza 22: extras del día (solo lectura). Huecos entre fotos, entrada temprana/tardía y entradas por señal.

Universo: el de salidas.py (preparar) con pasa = 1. UN solo corte de mitades (dividir_mitades) para las tres secciones.
"""
import argparse
import statistics
import time
from collections import defaultdict

from comun.config import SEGUIMIENTO_S, SIM_COMISION, SIM_FEE_RED_SOL, SIM_TAM_SOL
from herramientas.resultados import parse_desde
from herramientas.salidas import dividir_mitades, leer, preparar, tramo_compra
from herramientas.simulador import LAMPORTS, simular_token

EXTRAS_IMPULSO_SOL = 2  # v_sol sube al menos esto...
EXTRAS_IMPULSO_S = 120  # ...en como mucho estos segundos
EXTRAS_CERCA_GRAD_SOL = 50  # real_sol mínimo para "cerca de graduar"
HUECO_LARGO_S = 15
GRUPOS_SEG = (("<20 s", 0, 20), ("20-60 s", 20, 60), (">=60 s", 60, float("inf")))
TRAMOS_B = (("0.1-1", ("0.1-1",)), (">=1", ("1-5", ">=5")))
SALIDAS = (("obj1.2/stop0.3", 1.2, 0.3), ("aguantar", None, None))


def fotos_hasta_graduar(fotos):
    # fotos del dict hasta la primera con complete (sin incluirla); graduó = hubo alguna con complete
    out = []
    for f in fotos:
        if f["complete"]:
            return out, True
        out.append(f)
    return out, False


def huecos(segundos):
    return [b - a for a, b in zip(segundos, segundos[1:])]


def f1(x):
    return "-" if x is None else f"{x:.1f}"


def percentil(valores, p):
    # p en 0..100; con pocos datos devuelve el extremo más cercano
    if not valores:
        return None
    v = sorted(valores)
    return v[min(len(v) - 1, max(0, round(p / 100 * (len(v) - 1))))]


def fila_impulso(fotos):
    """Índice de la 1.ª foto con v_sol >= antes + EXTRAS_IMPULSO_SOL dentro de EXTRAS_IMPULSO_S, o None."""
    for i, f in enumerate(fotos):
        if f["v_sol"] is None:
            continue
        for j in range(i - 1, -1, -1):
            if f["segundos"] - fotos[j]["segundos"] > EXTRAS_IMPULSO_S:
                break
            v = fotos[j]["v_sol"]
            if v is not None and (f["v_sol"] - v) / LAMPORTS >= EXTRAS_IMPULSO_SOL:
                return i
    return None


def fila_cerca_graduar(fotos):
    for i, f in enumerate(fotos):
        if f["real_sol"] is not None and not f["complete"] and f["real_sol"] / LAMPORTS >= EXTRAS_CERCA_GRAD_SOL:
            return i
    return None


def retornos(fotos, tam):
    return {n: (lambda r: None if r is None else r["retorno"])(
        simular_token(fotos, tam, obj, stop, 0, 0.0, SIM_COMISION, SIM_FEE_RED_SOL)) for n, obj, stop in SALIDAS}


def cifras(x):
    if not x:
        return "n=0"
    return f"n={len(x)} media={100 * statistics.fmean(x):.1f}% mediana={100 * statistics.median(x):.1f}%"


def lineas_retorno(titulo, items):
    # items: [(mitad, {salida: retorno|None})]
    out = [f"  {titulo}"]
    for h in (1, 2):
        for nombre, _, _ in SALIDAS:
            rs = [r[nombre] for m, r in items if m == h and r[nombre] is not None]
            out.append(f"    mitad {h} {nombre}: {cifras(rs)}")
    return out


def seccion_a(validos, mitad):
    por = {1: defaultdict(list), 2: defaultdict(list)}  # mitad -> hora -> huecos
    fotos_hora = {1: defaultdict(int), 2: defaultdict(int)}
    fotos_tok = {1: [], 2: []}
    for mint, ts, _, _, fd in validos:
        h = mitad[mint]
        util, graduo = fotos_hasta_graduar(fd)
        for f in util:
            fotos_hora[h][time.strftime("%Y-%m-%d %H:00", time.localtime(ts + f["segundos"]))] += 1
        for f, g in zip(util[1:], huecos([f["segundos"] for f in util])):
            por[h][time.strftime("%Y-%m-%d %H:00", time.localtime(ts + f["segundos"]))].append(g)
        if not graduo and util:
            fotos_tok[h].append(len(util))
    out = ["== a) Huecos entre fotos por hora (solo hasta la graduacion; hora local de la foto) =="]
    for h in (1, 2):
        out.append(f"  mitad {h}:")
        for hora in sorted(fotos_hora[h]):
            g = por[h][hora]
            larg = f"{100 * sum(x > HUECO_LARGO_S for x in g) / len(g):.1f}" if g else "-"
            out.append(f"    {hora} fotos={fotos_hora[h][hora]} huecos n={len(g)} media={f1(statistics.fmean(g) if g else None)}"
                       f" p50={f1(percentil(g, 50))} p90={f1(percentil(g, 90))} p99={f1(percentil(g, 99))}"
                       f" max={f1(max(g) if g else None)} >{HUECO_LARGO_S}s={larg}%")
        ft = fotos_tok[h]
        out.append(f"    fotos por token no graduado: n={len(ft)} media={f1(statistics.fmean(ft) if ft else None)}"
                   f" p10={f1(percentil(ft, 10))} p50={f1(percentil(ft, 50))} p90={f1(percentil(ft, 90))}")
    return out


def seccion_b(validos, mitad, tam):
    out = ["== b) Entrada temprana vs tardia (1a foto valida; segundos desde el nacimiento) =="]
    filas = []
    for mint, _, sol, _, fd in validos:
        val = [f for f in fd if f["v_sol"] is not None]
        if val:
            filas.append((mitad[mint], val[0]["segundos"], tramo_compra(sol), retornos(fd, tam)))
    for nombre_t, tramos in (("todos", None),) + TRAMOS_B:
        for nombre_g, lo, hi in GRUPOS_SEG:
            items = [(m, r) for m, s, t, r in filas if lo <= s < hi and (tramos is None or t in tramos)]
            out += lineas_retorno(f"tramo compra creador {nombre_t} | primera foto {nombre_g}", items)
    return out


def seccion_c(validos, mitad, tam):
    out = ["== c) Entradas por senal (retraso 0; se entra en la foto que cumple la senal) =="]
    for nombre, fn in ((f"impulso (+{EXTRAS_IMPULSO_SOL} SOL en <= {EXTRAS_IMPULSO_S} s)", fila_impulso),
                       (f"cerca de graduar (real_sol >= {EXTRAS_CERCA_GRAD_SOL} SOL)", fila_cerca_graduar)):
        items, cumplen = [], 0
        for mint, _, _, _, fd in validos:
            i = fn(fd)
            if i is None:
                continue
            cumplen += 1
            items.append((mitad[mint], retornos(fd[i:], tam)))
        lineas = lineas_retorno(nombre, items)
        out += lineas[:1] + [f"  tokens que cumplen la senal: {cumplen} de {len(validos)}"] + lineas[1:]
    return out


def informe(validos, tam):
    mitad = dividir_mitades({m: ts for m, ts, _, _, _ in validos})
    n = [sum(v == h for v in mitad.values()) for h in (1, 2)]
    return "\n".join([f"Universo: {len(validos)} tokens (mitad 1 = {n[0]}, mitad 2 = {n[1]}; un solo corte)"]
                     + seccion_a(validos, mitad) + seccion_b(validos, mitad, tam) + seccion_c(validos, mitad, tam))


def main():
    ap = argparse.ArgumentParser(description="Extras del dia (solo lectura)")
    ap.add_argument("--desde", required=True, help='hora local "YYYY-MM-DD HH:MM"')
    ap.add_argument("--hasta", required=True, help='hora local "YYYY-MM-DD HH:MM"')
    ap.add_argument("--tam", type=float, default=SIM_TAM_SOL)
    a = ap.parse_args()
    try:
        desde, hasta_usuario = parse_desde(a.desde), parse_desde(a.hasta)
    except ValueError:
        ap.error('--desde/--hasta deben tener el formato "YYYY-MM-DD HH:MM"')
    hasta = min(hasta_usuario, time.time() - SEGUIMIENTO_S)
    toks, fotos, _, _ = leer(desde, hasta)
    validos, exc = preparar(toks, fotos)
    validos = [v for v in validos if v[3] == 1]
    print(f"Rango: {a.desde} -> {a.hasta} (tope efectivo: {time.strftime('%Y-%m-%d %H:%M', time.localtime(hasta))})")
    print(f"Excluidos por preparar (sobre todos los tokens, antes de filtrar pasa = 1): {exc}")
    print(informe(validos, a.tam) if validos else "Sin datos en la ventana.")


if __name__ == "__main__":
    main()
