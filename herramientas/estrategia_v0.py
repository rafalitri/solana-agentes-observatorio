"""Estrategia v0: aplica un criterio fijado de antemano (solo lectura). Modos: desarrollo y validacion."""
import argparse
import math
import sqlite3
import time

from comun.config import (ESTRATEGIA_EJECUCION, ESTRATEGIA_MIN_N, RUTA_BD, SEGUIMIENTO_S, SIM_COMISION,
                          SIM_FEE_RED_SOL, SIM_TAM_SOL)
from comun.db import fotos_token
from herramientas.opiniones import resumen
from herramientas.rebotes import DESDE_DEFECTO
from herramientas.resultados import parse_desde
from herramientas.salidas import dividir_mitades, fmt
from herramientas.simulador import LAMPORTS, simular_escalera, simular_token

ESPERA_ENTRADA_S = 60
PERCENTIL_VEL = 67
COMISION = SIM_COMISION + ESTRATEGIA_EJECUCION
SALIDAS = ("S1", "S2", "S3")
NOMBRE_SALIDA = {"S1": "objetivo 1,2/stop -30%", "S2": "escalera x2", "S3": "aguantar"}
VARIANTES = ("A", "B")

# mayhem NULL cuenta como no mayhem. Sin filtrar por resumen.ts: la ventana es sobre decisiones.ts
CONSULTA = (
    "SELECT d.mint, d.ts, t.creador, a.meta_ok, rd.ok, rd.twitter, rd.telegram, rd.web, "
    "te.top10_pct, te.top10_circ_pct, te.creador_pct, te.error, te.seg_desde_primera_foto "
    "FROM decisiones d JOIN resumen r ON r.mint = d.mint JOIN tokens t ON t.mint = d.mint "
    "LEFT JOIN analisis_v2 a ON a.mint = d.mint LEFT JOIN redes rd ON rd.mint = d.mint "
    "LEFT JOIN tenedores te ON te.mint = d.mint "
    "WHERE d.pasa = 1 AND d.ts >= ? AND d.ts <= ? AND r.error IS NULL "
    "AND r.grad_inst = 0 AND r.curva_atipica = 0 AND COALESCE(t.mayhem, 0) = 0 ORDER BY d.ts")
COLS = ("mint", "ts", "creador", "meta_ok", "redes_ok", "twitter", "telegram", "web",
        "top10_pct", "top10_circ_pct", "creador_pct", "error_ten", "seg_ten")


# ---------- funciones puras ----------

def _valida(f):
    return f["v_sol"] is not None and not f["complete"] and f["v_tokens"] is not None and f["v_tokens"] > 0


def elegir_entrada(fotos, retraso=0):
    """Índices (decisión, ejecución) en fotos. Decisión = 1.ª foto válida con segundos >= 1.ª foto + 60;
    ejecución = la decisión, o la siguiente foto válida si retraso = 1. (None, None) si no hay decisión;
    (i, None) si no hay siguiente foto válida."""
    if not fotos:
        return None, None
    minimo = fotos[0]["segundos"] + ESPERA_ENTRADA_S
    validas = [i for i, f in enumerate(fotos) if _valida(f)]
    for k, i in enumerate(validas):
        if fotos[i]["segundos"] >= minimo:
            return i, (validas[k + retraso] if k + retraso < len(validas) else None)
    return None, None


def calcular_vel(fotos, i):
    """SOL ganados en v_sol entre la 1.ª foto con v_sol y la foto i (solo mira fotos hasta i)."""
    primera = next((f for f in fotos[:i + 1] if f["v_sol"] is not None), None)
    return None if primera is None else (fotos[i]["v_sol"] - primera["v_sol"]) / LAMPORTS


def percentil(valores, p):
    v = sorted(valores)
    if not v:
        return None
    pos = (len(v) - 1) * p / 100
    lo = math.floor(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def media_sin_mejores(valores, frac=0.01):
    """Media quitando los ceil(frac * n) mejores; None si no queda nada."""
    v = sorted(valores)
    resto = v[:len(v) - math.ceil(round(frac * len(v), 9))]
    return sum(resto) / len(resto) if resto else None


def estadisticas(valores):
    n, media, mediana = resumen(valores)
    return n, media, mediana


def elegir_combinacion(tabla, minimo=ESTRATEGIA_MIN_N):
    """tabla: {(variante, salida): {1: (n, media), 2: (n, media), None: (n, media)}} con retraso 0.
    Gana la de mayor media total entre las que tienen media > 0 y n >= minimo en las dos mitades; None si ninguna."""
    ok = [k for k, t in tabla.items()
          if all(t[h][0] >= minimo and t[h][1] is not None and t[h][1] > 0 for h in (1, 2))]
    return max(ok, key=lambda k: tabla[k][None][1]) if ok else None


def veredicto_validacion(por_mitad, media_sin_1pct, minimo=ESTRATEGIA_MIN_N):
    """por_mitad: {1: (n, media), 2: (n, media)}. Devuelve (vale, motivo)."""
    fallos = []
    for h in (1, 2):
        n, media = por_mitad[h]
        if n < minimo:
            fallos.append(f"n={n} < {minimo} en mitad {h}")
        elif media is None or media <= 0:
            fallos.append(f"media no positiva en mitad {h}")
    if media_sin_1pct is None or media_sin_1pct <= 0:
        fallos.append("media sin el 1 % mejor no positiva")
    return (not fallos), ("; ".join(fallos) if fallos else "cumple todo")


def senal_ok(variante, vel, umbral, meta_ok, redes_ok, n_redes):
    if vel is None or vel < umbral:
        return False
    return variante == "A" or (meta_ok == 1 and redes_ok == 1 and n_redes >= 1)


def grupo_reputacion(previos, ts_decision):
    """previos: [(ts, graduado, seg_graduado)] de tokens anteriores del creador. Un previo solo cuenta como
    graduado si se graduó antes de ts_decision (ts del previo + seg_graduado <= ts_decision)."""
    if not previos:
        return "0 previos"
    if any(g == 1 and sg is not None and ts + sg <= ts_decision for ts, g, sg in previos):
        return ">=1 graduado"
    return ">=1 previos sin graduados"


def grupo_tenedores(fila, columna_top, seg_decision_rel):
    """pasa / no pasa / sin dato a tiempo. seg_decision_rel = segundos de entrada - segundos de la 1.ª foto."""
    if fila["error_ten"] is not None or fila[columna_top] is None or fila["creador_pct"] is None \
            or fila["seg_ten"] is None or fila["seg_ten"] > seg_decision_rel:
        return "sin dato a tiempo"
    return "pasa filtro" if fila[columna_top] <= 70 and fila["creador_pct"] <= 5 else "no pasa"


# ---------- cálculo por token ----------

def retornos(fotos, i_dec, i_ejec):
    """{salida: retorno o None} ejecutando en i_ejec (None = no opera)."""
    if i_ejec is None:
        return {s: None for s in SALIDAS}
    f = fotos[i_ejec:]
    args = (SIM_TAM_SOL, COMISION, SIM_FEE_RED_SOL)
    r1 = simular_token(f, SIM_TAM_SOL, 1.2, 0.3, 0, 0, COMISION, SIM_FEE_RED_SOL)
    r2 = simular_escalera(f, *args)
    r3 = simular_token(f, SIM_TAM_SOL, None, None, 0, 0, COMISION, SIM_FEE_RED_SOL)
    return {s: (r["retorno"] if r else None) for s, r in zip(SALIDAS, (r1, r2, r3))}


def preparar_filas(registros, fotos_de, previos_de):
    """Una fila por token del universo. fotos_de(mint) y previos_de(creador, ts) inyectables (tests sin BD)."""
    filas = []
    for r in registros:
        fotos = fotos_de(r["mint"])
        i, e0 = elegir_entrada(fotos, 0)
        fila = {**r, "opera": i is not None, "vel": None, "ret": {0: {}, 1: {}}, "seg_rel": None}
        if i is not None:
            _, e1 = elegir_entrada(fotos, 1)
            fila["vel"] = calcular_vel(fotos, i)
            fila["seg_rel"] = fotos[i]["segundos"] - fotos[0]["segundos"]
            fila["ret"] = {0: retornos(fotos, i, e0), 1: retornos(fotos, i, e1)}
            fila["rep"] = grupo_reputacion(previos_de(r["creador"], r["ts"]), r["ts"] + fotos[i]["segundos"])
        filas.append(fila)
    mitad = dividir_mitades({f["mint"]: f["ts"] for f in filas if f["opera"]})
    for f in filas:
        f["mitad"] = mitad.get(f["mint"])
    return filas


def n_redes(f):
    return (f["twitter"] or 0) + (f["telegram"] or 0) + (f["web"] or 0)


def retornos_combinacion(filas, umbral, variante, salida, retraso, mitad=None):
    return [f["ret"][retraso][salida] for f in filas
            if f["opera"] and (mitad is None or f["mitad"] == mitad)
            and senal_ok(variante, f["vel"], umbral, f["meta_ok"], f["redes_ok"], n_redes(f))
            and f["ret"][retraso][salida] is not None]


# ---------- informe ----------

def informe_descriptivo(filas, umbral):
    op = [f for f in filas if f["opera"]]
    return [f"Tokens en el universo: {len(filas)}",
            f"  sin entrada (sin foto válida desde 1.ª + {ESPERA_ENTRADA_S} s): {len(filas) - len(op)}",
            f"  con vel >= umbral: {sum(f['vel'] >= umbral for f in op)}",
            f"  con meta_ok = 1: {sum(f['meta_ok'] == 1 for f in op)}",
            f"  con redes.ok = 1 y redes >= 1: {sum(f['redes_ok'] == 1 and n_redes(f) >= 1 for f in op)}",
            f"  con vel >= umbral pero sin siguiente foto válida (no opera en retraso 1): "
            f"{sum(f['vel'] >= umbral and f['ret'][1]['S3'] is None for f in op)}"]


def linea(nombre, vals):
    n, media, mediana = estadisticas(vals)
    return f"{nombre}: n={n} media%={fmt(media)} mediana%={fmt(mediana)}"


def desarrollo(filas, minimo=ESTRATEGIA_MIN_N):
    op = [f for f in filas if f["opera"]]
    if not op:
        return ["Sin tokens con entrada."], None
    umbral = percentil([f["vel"] for f in op], PERCENTIL_VEL)
    out = [f"UMBRAL_VEL congelado: {umbral:.6f}", ""] + informe_descriptivo(filas, umbral)
    tabla = {}
    for r in (0, 1):
        out.append(f"\n== Retraso {r} ==")
        for v in VARIANTES:
            for s in SALIDAS:
                partes = []
                for nombre, h in (("mitad 1", 1), ("mitad 2", 2), ("total", None)):
                    vals = retornos_combinacion(filas, umbral, v, s, r, h)
                    partes.append(linea(nombre, vals))
                    if r == 0:
                        n, media, _ = estadisticas(vals)
                        tabla.setdefault((v, s), {})[h] = (n, media)
                out.append(f"{v}/{s} ({NOMBRE_SALIDA[s]}) | " + " | ".join(partes))
    ganadora = elegir_combinacion(tabla, minimo)
    out.append("\nVEREDICTO: " + (f"ELEGIDA {ganadora[0]}/{ganadora[1]}" if ganadora else "v0 DESCARTADA"))
    return out, umbral


def info_grupos(filas, umbral, variante, salida):
    sel = [f for f in filas if f["opera"] and senal_ok(variante, f["vel"], umbral, f["meta_ok"], f["redes_ok"], n_redes(f))
           and f["ret"][0][salida] is not None]
    out = ["\n== SOLO INFORMACIÓN (no influye en el veredicto) ==", "(a) Por tenedores (retraso 0):"]
    for col in ("top10_pct", "top10_circ_pct"):
        for g in ("pasa filtro", "no pasa", "sin dato a tiempo"):
            out.append("  " + linea(f"{col} | {g}", [f["ret"][0][salida] for f in sel
                                                      if grupo_tenedores(f, col, f["seg_rel"]) == g]))
    out.append("(b) Por reputación del creador (retraso 0):")
    for g in ("0 previos", ">=1 previos sin graduados", ">=1 graduado"):
        out.append("  " + linea(g, [f["ret"][0][salida] for f in sel if f["rep"] == g]))
    return out


def validacion(filas, umbral, variante, salida, minimo=ESTRATEGIA_MIN_N):
    out = [f"Combinación {variante}/{salida} ({NOMBRE_SALIDA[salida]}), UMBRAL_VEL {umbral:.6f}", ""]
    out += informe_descriptivo(filas, umbral)
    por_mitad = {}
    for r in (0, 1):
        out.append(f"\n== Retraso {r} ==")
        for h in (1, 2):
            vals = retornos_combinacion(filas, umbral, variante, salida, r, h)
            out.append(linea(f"mitad {h}", vals))
            if r == 0:
                por_mitad[h] = estadisticas(vals)[:2]
        todos = retornos_combinacion(filas, umbral, variante, salida, r)
        out.append(linea("total", todos))
        sin = media_sin_mejores(todos)
        out.append(f"media% sin el 1 % mejor: {fmt(sin)}")
        if r == 0:
            sin0 = sin
    vale, motivo = veredicto_validacion(por_mitad, sin0, minimo)
    out += info_grupos(filas, umbral, variante, salida)
    out.append("\nVEREDICTO: VALE" if vale else f"\nVEREDICTO: NO VALE ({motivo})")
    return out


# ---------- BD (solo lectura) ----------

def main():
    ap = argparse.ArgumentParser(description="Estrategia v0 (solo lectura)")
    ap.add_argument("--modo", required=True, choices=("desarrollo", "validacion"))
    ap.add_argument("--desde", default=DESDE_DEFECTO, help='hora local "YYYY-MM-DD HH:MM"')
    ap.add_argument("--hasta", help='hora local "YYYY-MM-DD HH:MM" (por defecto, desde + 24 h)')
    ap.add_argument("--umbral-vel", type=float)
    ap.add_argument("--variante", choices=VARIANTES)
    ap.add_argument("--salida", choices=SALIDAS)
    a = ap.parse_args()
    if a.modo == "validacion" and None in (a.umbral_vel, a.variante, a.salida):
        ap.error("validacion exige --umbral-vel, --variante y --salida")
    try:
        desde = parse_desde(a.desde)
        hasta_usuario = parse_desde(a.hasta) if a.hasta else desde + 24 * 3600
    except ValueError:
        ap.error('--desde/--hasta deben tener el formato "YYYY-MM-DD HH:MM"')
    hasta = min(hasta_usuario, time.time() - SEGUIMIENTO_S)
    con = sqlite3.connect(f"file:{RUTA_BD}?mode=ro", uri=True)
    try:
        registros = [dict(zip(COLS, fila)) for fila in con.execute(CONSULTA, (desde, hasta))]
        print(f"Rango: {a.desde} -> {a.hasta or 'desde + 24 h'}; "
              f"tope efectivo: {time.strftime('%Y-%m-%d %H:%M', time.localtime(hasta))}")
        if not registros:
            print("Sin tokens en la ventana.")
            return

        def previos_de(creador, ts):
            if not creador:
                return []
            return con.execute(
                "SELECT t.ts, r.graduado, r.seg_graduado FROM tokens t LEFT JOIN resumen r ON r.mint = t.mint "
                "WHERE t.creador = ? AND t.ts < ?", (creador, ts)).fetchall()

        filas = preparar_filas(registros, lambda m: fotos_token(con, m), previos_de)
    finally:
        con.close()
    if not any(f["opera"] for f in filas):
        print("Ningún token con entrada.")
        return
    salida = desarrollo(filas)[0] if a.modo == "desarrollo" else validacion(filas, a.umbral_vel, a.variante, a.salida)
    print(f"n mínimo por mitad: {ESTRATEGIA_MIN_N}")
    print("\n".join(salida))


if __name__ == "__main__":
    main()
