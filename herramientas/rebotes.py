"""Informe de rebotes: ¿aporta entrar justo después de un susto (caída del 50 % / 70 %)? (solo lectura, solo tabla resumen)"""
import argparse
import sqlite3
import statistics
import time

from comun.config import REBOTES_MIN_N, RUTA_BD, SEGUIMIENTO_S, UMBRALES_SUSTO
from herramientas.opiniones import resumen
from herramientas.resultados import parse_desde
from herramientas.salidas import dividir_mitades

UMBRALES = tuple(f"{round(u * 100)}" for u in UMBRALES_SUSTO)
SALIDAS = ("obj", "fin")
NOMBRE_SALIDA = {"obj": "objetivo x1,2/stop -30%", "fin": "aguantar"}
DESDE_DEFECTO = "2026-10-16 18:39"
COLUMNAS = ("mint", "ts") + tuple(
    c for u in UMBRALES for c in (f"seg_susto{u}", *(f"ret_s{u}_r{r}_{s}" for r in (0, 1) for s in SALIDAS)))

# mayhem NULL (PumpPortal sin el campo) cuenta como no mayhem
CONSULTA = (
    "SELECT d.mint, d.ts, " + ", ".join(f"r.{c}" for c in COLUMNAS[2:]) + " FROM decisiones d "
    "JOIN resumen r ON r.mint = d.mint JOIN tokens t ON t.mint = d.mint "
    "WHERE d.pasa = 1 AND d.ts >= ? AND d.ts <= ? AND r.error IS NULL "
    "AND r.grad_inst = 0 AND r.curva_atipica = 0 AND COALESCE(t.mayhem, 0) = 0")


def columna(umbral, retraso, salida):
    return f"ret_s{umbral}_r{retraso}_{salida}"


def hora_corte(registros):
    # mismo corte que dividir_mitades: mediana de ts de TODOS los tokens que entran
    return statistics.median(r["ts"] for r in registros) if registros else None


def construir_filas(registros):
    mitad = dividir_mitades({r["mint"]: r["ts"] for r in registros})
    return [{**r, "mitad": mitad[r["mint"]]} for r in registros]


def estadisticas(valores):
    # (n, media, mediana, fracción con retorno > 0); ignora None; vacío -> (0, None, None, None)
    v = [x for x in valores if x is not None]
    n, media, mediana = resumen(v)
    return n, media, mediana, (sum(x > 0 for x in v) / n if n else None)


def de_mitad(filas, mitad):
    return [f for f in filas if mitad is None or f["mitad"] == mitad]


def valores(filas, mitad, col):
    return [f[col] for f in de_mitad(filas, mitad)]


def veredicto(filas, minimo=REBOTES_MIN_N):
    # solo retraso 0; APORTA si alguna combinación umbral x salida tiene media > 0 y n >= minimo en las dos mitades
    motivos, aporta = [], False
    for u in UMBRALES:
        for s in SALIDAS:
            col, fallos = columna(u, 0, s), []
            for h in (1, 2):
                n, media, _, _ = estadisticas(valores(filas, h, col))
                if n < minimo:
                    fallos.append(f"n={n} < {minimo} en mitad {h}")
                elif media <= 0:
                    fallos.append(f"media {media * 100:.1f}% no positiva en mitad {h}")
            aporta = aporta or not fallos
            motivos.append(f"susto{u} {NOMBRE_SALIDA[s]}: " + ("cumple" if not fallos else "; ".join(fallos)))
    return ("APORTA" if aporta else "NO APORTA"), motivos


def _p(x, dec=1):
    return "-" if x is None else f"{x * 100:.{dec}f}"


def informe(filas, minimo=REBOTES_MIN_N):
    out = ["== Tokens y sustos =="]
    for nombre, h in (("mitad 1", 1), ("mitad 2", 2), ("total", None)):
        fs = de_mitad(filas, h)
        linea = f"{nombre}: {len(fs)} tokens"
        for u in UMBRALES:
            k = sum(f[f"seg_susto{u}"] is not None for f in fs)
            linea += f" | susto{u}: {k} ({_p(k / len(fs) if fs else None)}%)"
        out.append(linea)
    out.append("\numbral | retraso | salida | grupo | tokens_susto | n | media% | mediana% | %>0")
    for u in UMBRALES:
        for r in (0, 1):
            for s in SALIDAS:
                for nombre, h in (("mitad 1", 1), ("mitad 2", 2), ("total", None)):
                    fs = de_mitad(filas, h)
                    k = sum(f[f"seg_susto{u}"] is not None for f in fs)
                    n, media, mediana, pos = estadisticas(valores(filas, h, columna(u, r, s)))
                    out.append(f"{u} | {r} | {NOMBRE_SALIDA[s]} | {nombre} | {k} | {n} | "
                               f"{_p(media)} | {_p(mediana)} | {_p(pos)}")
    texto, motivos = veredicto(filas, minimo)
    out.append(f"\nVEREDICTO (retraso 0, n >= {minimo} y media > 0 en las dos mitades): {texto}")
    out += [f"  - {m}" for m in motivos]
    return "\n".join(out)


def leer_registros(desde, hasta):
    con = sqlite3.connect(f"file:{RUTA_BD}?mode=ro", uri=True)
    try:
        return [dict(zip(COLUMNAS, fila)) for fila in con.execute(CONSULTA, (desde, hasta))]
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser(description="Informe de rebotes (solo lectura, solo tabla resumen)")
    ap.add_argument("--desde", default=DESDE_DEFECTO, help='hora local "YYYY-MM-DD HH:MM"')
    ap.add_argument("--hasta", help='hora local "YYYY-MM-DD HH:MM" (por defecto, desde + 24 h)')
    a = ap.parse_args()
    try:
        desde = parse_desde(a.desde)
        hasta_usuario = parse_desde(a.hasta) if a.hasta else desde + 24 * 3600
    except ValueError:
        ap.error('--desde/--hasta deben tener el formato "YYYY-MM-DD HH:MM"')
    hasta = min(hasta_usuario, time.time() - SEGUIMIENTO_S)  # seguimiento de 30 min terminado
    registros = leer_registros(desde, hasta)
    print(f"Rango: {a.desde} -> {a.hasta or 'desde + 24 h'}; "
          f"tope efectivo: {time.strftime('%Y-%m-%d %H:%M', time.localtime(hasta))}")
    if not registros:
        print("Sin tokens en la ventana.")
        return
    print(f"Corte de mitades (mediana de decisiones.ts): "
          f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(hora_corte(registros)))}")
    print(informe(construir_filas(registros)))


if __name__ == "__main__":
    main()
