"""Informe del criterio Qwen: ¿separa la opinión tokens mejores de peores? (solo lectura)"""
import argparse
import sqlite3
import statistics
import time

from comun.config import RUTA_BD, SEGUIMIENTO_S, SIM_COMISION, SIM_FEE_RED_SOL, SIM_TAM_SOL, VERSION_PROMPT
from herramientas.resultados import calcular_token, parse_desde
from herramientas.salidas import dividir_mitades, leer, preparar
from herramientas.simulador import simular_token

OBJETIVO, STOP = 1.2, 0.3
COMPARACIONES = [("punt>=7", "punt<=5"), ("otro", "meme")]
TABLAS = ("analisis", "analisis_v2")
TRAMOS = ("0.1-1", ">=1")


def clasificar(etiqueta, punt, alto=7, bajo=5):
    # grupos del criterio (el valor intermedio no entra) + filas descriptivas
    g = []
    if punt is not None and punt >= alto:
        g.append(f"punt>={alto}")
    if punt is not None and punt <= bajo:
        g.append(f"punt<={bajo}")
    if etiqueta in ("otro", "meme"):
        g.append(etiqueta)
    g += [f"punt={punt}", f"etq={etiqueta}"]
    return g


def resumen(valores):
    # (n, media, mediana); lista vacía -> (0, None, None). Ignora None.
    v = [x for x in valores if x is not None]
    return (len(v), statistics.fmean(v), statistics.median(v)) if v else (0, None, None)


def misma_direccion(d1, d2):
    # True solo si ambas diferencias existen, no son 0 y tienen el mismo signo
    return d1 is not None and d2 is not None and d1 != 0 and d2 != 0 and (d1 > 0) == (d2 > 0)


def tramo_compra(sol_amount):
    # compra del creador en SOL; límites semiabiertos (0.1 y 1 caen arriba). None si no hay dato o < 0.1
    if sol_amount is None or sol_amount < 0.1:
        return None
    return "0.1-1" if sol_amount < 1 else ">=1"


def consulta_opiniones(tabla):
    # el nombre de tabla no admite parámetros SQL: lista blanca
    if tabla not in TABLAS:
        raise ValueError(f"tabla desconocida: {tabla}")
    meta = " AND a.meta_ok = 1" if tabla == "analisis_v2" else ""
    return (f"SELECT a.mint, a.etiqueta, a.puntuacion FROM {tabla} a JOIN tokens t ON t.mint = a.mint "
            f"WHERE a.error IS NULL AND a.version_prompt = ?{meta} AND t.ts >= ? AND t.ts <= ? "
            "AND EXISTS (SELECT 1 FROM decisiones d WHERE d.mint = a.mint AND d.pasa = 1)")


def leer_opiniones(desde, hasta, version, tabla="analisis"):
    con = sqlite3.connect(f"file:{RUTA_BD}?mode=ro", uri=True)
    try:
        return {m: (e, p) for m, e, p in con.execute(consulta_opiniones(tabla), (version, desde, hasta))}
    finally:
        con.close()


def medidas(filas, tam):
    # filas de foto: (segundos, mc, complete, v_sol, v_tokens, real_sol); desde la 1.ª foto con mc válido
    mc0 = next((f[1] for f in filas if f[1] is not None), None)
    m = calcular_token(mc0, [(f[0], f[1], f[2]) for f in filas])["mult_max"] if mc0 else None
    fd = [{"segundos": f[0], "v_sol": f[3], "v_tokens": f[4], "real_sol": f[5], "complete": f[2]}
          for f in filas]
    r = simular_token(fd, tam, OBJETIVO, STOP, 0, 0.0, SIM_COMISION, SIM_FEE_RED_SOL)
    return m, None if r is None else r["retorno"]


def construir(validos, fotos, opiniones, tam):
    # un único corte de mitades sobre todos los tokens incluidos
    inc = [(mint, ts, sol) for mint, ts, sol, _, _ in validos if mint in opiniones]
    mitad = dividir_mitades({mint: ts for mint, ts, _ in inc})
    filas = []
    for mint, _, sol in inc:
        m, r = medidas(fotos[mint], tam)
        etq, punt = opiniones[mint]
        filas.append({"mitad": mitad[mint], "grupos": clasificar(etq, punt), "mult": m, "ret": r,
                      "tramo": tramo_compra(sol)})
    return filas


def valores(filas, grupo, mitad, clave):
    return [f[clave] for f in filas if grupo in f["grupos"] and f["mitad"] == mitad]


def _f(x, mult=1, dec=2):
    return "-" if x is None else f"{x * mult:.{dec}f}"


def informe(filas):
    grupos = [g for c in COMPARACIONES for g in c]
    resto = sorted({g for f in filas for g in f["grupos"]} - set(grupos))
    out = ["grupo | mitad | n_mult media_mult mediana_mult | n_ret media_ret% mediana_ret%"]
    for g in grupos + resto:
        for h in (1, 2):
            nm, mm, md = resumen(valores(filas, g, h, "mult"))
            nr, rm, rd = resumen(valores(filas, g, h, "ret"))
            out.append(f"{g} | {h} | {nm} {_f(mm)} {_f(md)} | {nr} {_f(rm, 100, 1)} {_f(rd, 100, 1)}")
    out.append("\ncomparacion | medida | dif mitad1 | dif mitad2 | misma_direccion")
    for a, b in COMPARACIONES:
        for nombre, clave, k in (("media mult", "mult", 1), ("media ret %", "ret", 100)):
            d = []
            for h in (1, 2):
                ma, mb = resumen(valores(filas, a, h, clave))[1], resumen(valores(filas, b, h, clave))[1]
                d.append(None if ma is None or mb is None else ma - mb)
            out.append(f"{a} vs {b} | {nombre} | {_f(d[0], k)} | {_f(d[1], k)} | "
                       f"{'si' if misma_direccion(*d) else 'no'}")
    return "\n".join(out)


def informe_tramos(filas):
    # punt>=7 vs punt<=5 dentro de cada tramo de compra del creador; mismas mitades que el informe general
    a, b = COMPARACIONES[0]
    out = ["\ntramo | medida | n(" + a + "/" + b + ") mitad1 | n mitad2 | dif mitad1 | dif mitad2 | misma_direccion"]
    for tramo in TRAMOS:
        sub = [f for f in filas if f["tramo"] == tramo]
        for nombre, clave, k in (("media mult", "mult", 1), ("media ret %", "ret", 100)):
            d, ns = [], []
            for h in (1, 2):
                va, vb = valores(sub, a, h, clave), valores(sub, b, h, clave)
                ns.append(f"{len(va)}/{len(vb)}")
                ma, mb = resumen(va)[1], resumen(vb)[1]
                d.append(None if ma is None or mb is None else ma - mb)
            out.append(f"{tramo} | {nombre} | {ns[0]} | {ns[1]} | {_f(d[0], k)} | {_f(d[1], k)} | "
                       f"{'si' if misma_direccion(*d) else 'no'}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Informe del criterio Qwen (solo lectura)")
    ap.add_argument("--desde", required=True, help='hora local "YYYY-MM-DD HH:MM"')
    ap.add_argument("--hasta", help='hora local "YYYY-MM-DD HH:MM" (por defecto, ahora)')
    ap.add_argument("--version-prompt", type=int, default=None,
                    help="por defecto: VERSION_PROMPT con analisis, 2 con analisis_v2")
    ap.add_argument("--tabla", choices=TABLAS, default="analisis")
    ap.add_argument("--tramos", action="store_true", help="compara también dentro de cada tramo de compra")
    a = ap.parse_args()
    if a.version_prompt is None:
        a.version_prompt = 2 if a.tabla == "analisis_v2" else VERSION_PROMPT
    try:
        desde = parse_desde(a.desde)
        hasta_usuario = parse_desde(a.hasta) if a.hasta else time.time()
    except ValueError:
        ap.error('--desde/--hasta deben tener el formato "YYYY-MM-DD HH:MM"')
    hasta = min(hasta_usuario, time.time() - SEGUIMIENTO_S)  # seguimiento de 30 min terminado
    toks, fotos, _, _ = leer(desde, hasta)
    validos, exc = preparar(toks, fotos)
    filas = construir(validos, fotos, leer_opiniones(desde, hasta, a.version_prompt, a.tabla), SIM_TAM_SOL)
    extra = f"tabla={a.tabla}; " if a.tabla != "analisis" else ""
    print(f"Rango: {a.desde} -> {a.hasta or 'ahora'}; {extra}version_prompt={a.version_prompt}; "
          f"tope efectivo: {time.strftime('%Y-%m-%d %H:%M', time.localtime(hasta))}")
    print(f"Tokens incluidos: {len(filas)} (excluidos: {exc}); objetivo={OBJETIVO}, stop={STOP}, tam={SIM_TAM_SOL}")
    if filas:
        print(informe(filas))
        if a.tramos:
            print(informe_tramos(filas))


if __name__ == "__main__":
    main()
