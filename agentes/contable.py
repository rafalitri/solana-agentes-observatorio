"""Pieza 34a: agente contable. Calcula operaciones simuladas (en papel) de 4 mesas con reglas ya fijadas y las guarda en
mesas_ops. Solo hacia delante; no decide nada. Reutiliza las funciones de los informes: no copia su lógica."""
import argparse
import sqlite3
import time

from agentes.analista import ahora
from comun import db
from comun.config import (MESAS_DESDE, MESAS_ESPERA_S, MESAS_LATIDO_S, MESAS_LOTE, MESAS_VISTO_S, JUEZ_UMBRAL_TOP10, RUTA_BD, SEGUIMIENTO_S,
                          SIM_FEE_RED_SOL, SIM_TAM_SOL)
from herramientas.extras_dia import fila_cerca_graduar
from herramientas.juez_informe import CONSULTA_JUICIOS, unir_juicios
from herramientas.resultados import parse_desde
from herramientas.simulador import simular_token
from herramientas.supervivientes_informe import cargar, preparar as preparar_superv
# COMISION: las 4 mesas pagan el MISMO coste por lado (SIM_COMISION + ESTRATEGIA_EJECUCION) más fee de red, para que
# se puedan comparar entre sí. CERCA_GRADUAR no usa el coste del apartado c) de extras_dia (solo SIM_COMISION).
from herramientas.estrategia_v0 import COMISION
from herramientas.tenedores_informe import COLS_T, CONSULTA_T, preparar

MESAS = ("BASE", "JUEZ_VIGILAR", "CERCA_GRADUAR", "SUPERV_T1")
COLS_JUICIO = ("mint", "version_juez", "b1", "b2", "b4", "b5", "b6")


# ---------- funciones puras ----------

def fila(mesa, mint, ts_entrada, ret, estado, ts_calculo):
    return {"mesa": mesa, "mint": mint, "ts_entrada": ts_entrada, "ret": ret, "estado": estado, "ts_calculo": ts_calculo}


def juicios_de(con, mints, trozo=500):
    """{mint: dict COLS_JUICIO} solo de esos mints (no todos los juicios de la BD)."""
    mints, out = list(mints), {}
    for i in range(0, len(mints), trozo):
        lote = mints[i:i + trozo]
        for r in con.execute(f"{CONSULTA_JUICIOS} AND mint IN ({', '.join('?' * len(lote))})", lote):
            out[r[0]] = dict(zip(COLS_JUICIO, r))
    return out


def ret_cerca_graduar(fotos):
    """(ts_entrada, retorno) de entrar en la foto con real_sol >= 50 SOL, x1,2 / -30 %, retraso 0; None si no se puede."""
    i = fila_cerca_graduar(fotos)
    if i is None:
        return None
    r = simular_token(fotos[i:], SIM_TAM_SOL, 1.2, 0.3, 0, 0, COMISION, SIM_FEE_RED_SOL)
    return None if r is None else (fotos[i]["ts"], r["retorno"])


# ---------- cálculo ----------

def calcular(con, ahora_ts, vistos, desde, umbral_top10=JUEZ_UMBRAL_TOP10):
    """({mesa: [filas nuevas]}, {mesa: mints evaluados}). vistos: {mesa: mints ya evaluados en este proceso} (los que no
    entran en una mesa no se recalculan cada ciclo). No escribe ni modifica vistos."""
    hasta = ahora_ts - SEGUIMIENTO_S
    hechos = {m: db.mesas_hechas(con, m) | vistos[m] for m in MESAS}
    registros = [dict(zip(COLS_T, f)) for f in con.execute(CONSULTA_T, (desde, hasta))]
    cache = {}

    def fotos_de(mint):
        if mint not in cache:
            cache[mint] = db.fotos_token_ts(con, mint)
        return cache[mint]

    pend = {m: [r for r in registros if r["mint"] not in hechos[m]] for m in ("BASE", "JUEZ_VIGILAR", "CERCA_GRADUAR")}
    if umbral_top10 is None:
        pend["JUEZ_VIGILAR"] = []  # sin umbral no se calcula: queda pendiente
    out = {m: [] for m in MESAS}
    por_mint = {r["mint"]: r for r in pend["BASE"] + pend["JUEZ_VIGILAR"]}
    filas, _ = preparar(list(por_mint.values()), fotos_de)
    por_fila = {f["mint"]: f for f in filas}
    for r in pend["BASE"]:
        f = por_fila.get(r["mint"])
        if f:
            out["BASE"].append(fila("BASE", f["mint"], f["ts_entrada"], f["ret"], "ok", ahora_ts))
    if pend["JUEZ_VIGILAR"]:
        mints = {r["mint"] for r in pend["JUEZ_VIGILAR"]}
        propias = [f for f in filas if f["mint"] in mints]
        unidas, _ = unir_juicios(propias, juicios_de(con, [f["mint"] for f in propias]), umbral_top10)
        out["JUEZ_VIGILAR"] = [fila("JUEZ_VIGILAR", f["mint"], f["ts_entrada"], f["ret"], "ok", ahora_ts)
                               for f in unidas if f["n_banderas"] == 0]
    for r in pend["CERCA_GRADUAR"]:
        x = ret_cerca_graduar(fotos_de(r["mint"]))
        if x:
            out["CERCA_GRADUAR"].append(fila("CERCA_GRADUAR", r["mint"], x[0], x[1], "ok", ahora_ts))
    # SUPERV_T1 mantiene desliz 0 (compra al precio de la lectura, sin impacto de precio) por fidelidad a su criterio;
    # las otras mesas sí lo pagan en simular_token. Al comparar mesas hay que tenerlo en cuenta.
    regs = cargar(con, desde, ahora_ts, hechos["SUPERV_T1"], ahora_ts)
    res = preparar_superv(regs, ahora_ts)
    out["SUPERV_T1"] = ([fila("SUPERV_T1", f["mint"], f["ts_entrada"], f["base"]["T1"], "ok", ahora_ts)
                         for f in res["analizados"]]
                        + [fila("SUPERV_T1", f["mint"], f["ts_entrada"], None, "sin_dato", ahora_ts)
                           for f in res["sin_dato"]])
    # un descartado solo es "visto" si es lo bastante antiguo: lo reciente puede completarse (juicio, tenedores...)
    viejo = ahora_ts - MESAS_VISTO_S
    evaluados = {m: {r["mint"] for r in pend[m] if r["ts"] <= viejo} for m in pend}
    # excluidos (a)/(b) o sin_terminar de SUPERV: con su fin ya pasado y terminados su clasificación ya no cambia
    hechas = {f["mint"] for f in res["analizados"] + res["sin_dato"]}
    evaluados["SUPERV_T1"] = {r["mint"] for r in regs if r["mint"] not in hechas and r["ts_fin"] <= ahora_ts
                              and preparar_superv([r], ahora_ts, False)["conteos"]["sin_terminar"] == 0}
    return out, evaluados


def ciclo(con, ahora_ts, vistos, desde, escribir=True, umbral_top10=JUEZ_UMBRAL_TOP10):
    out, evaluados = calcular(con, ahora_ts, vistos, desde, umbral_top10)
    if escribir:
        for filas in out.values():
            for i in range(0, len(filas), MESAS_LOTE):
                db.guardar_mesas_ops(con, filas[i:i + MESAS_LOTE])
        for m, mints in evaluados.items():  # solo tras guardar: si la escritura falla, el ciclo siguiente lo repite
            vistos[m] |= mints
    return out


def resumen_linea(out, umbral_top10=JUEZ_UMBRAL_TOP10):
    txt = " | ".join(f"{m}: {len(out[m])}" for m in MESAS)  # solo recuentos, nunca retornos
    return txt + ("" if umbral_top10 is not None else " | JUEZ_VIGILAR pendiente: JUEZ_UMBRAL_TOP10 sin fijar")


# ---------- proceso ----------

def prueba(desde, ruta=RUTA_BD):
    # solo lectura (mode=ro): no crea tablas, ni filas, ni latido
    con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    try:
        out = ciclo(con, time.time(), {m: set() for m in MESAS}, desde, escribir=False)
    finally:
        con.close()
    print(f"[{ahora()}] --prueba calculables: {resumen_linea(out)} (no se ha escrito nada)")
    return out


def guardar_estado(con, estado, tot):
    try:
        db.guardar_estado_agente(con, "contable", time.time(), estado, tot["procesados"], tot["errores"])
    except sqlite3.OperationalError as e:
        db.deshacer(con)
        tot["errores"] += 1
        print(f"[{ahora()}] Aviso: error de BD al guardar estado ({e})")


def esperar(con, tot, espera_s, latido_s, dormir=time.sleep):
    """Espera espera_s en tramos de latido_s, escribiendo el latido tras cada uno (el ciclo dura más que LATIDO_MAX_S)."""
    pasado = 0
    while pasado < espera_s:
        paso = min(latido_s, espera_s - pasado)
        dormir(paso)
        pasado += paso
        guardar_estado(con, "ok", tot)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prueba", action="store_true", help="cuenta lo calculable por mesa y no escribe nada")
    ap.add_argument("--desde", default=MESAS_DESDE, help='hora local "YYYY-MM-DD HH:MM"')
    args = ap.parse_args()
    try:
        desde = parse_desde(args.desde)
    except ValueError:
        ap.error('--desde debe tener el formato "YYYY-MM-DD HH:MM"')
    if args.prueba:
        prueba(desde)
        return
    con = db.conectar()
    db.crear_tablas(con)
    print("Contable arrancado")
    vistos = {m: set() for m in MESAS}
    tot = {"procesados": 0, "errores": 0}
    try:
        while True:
            try:
                out = ciclo(con, time.time(), vistos, desde)
                tot["procesados"] += sum(len(f) for f in out.values())
                print(f"[{ahora()}] Nuevas: {resumen_linea(out)}")
            except sqlite3.OperationalError as e:
                db.deshacer(con)
                tot["errores"] += 1
                print(f"[{ahora()}] Aviso: error de BD ({e})")
            guardar_estado(con, "ok", tot)
            esperar(con, tot, MESAS_ESPERA_S, MESAS_LATIDO_S)
    except KeyboardInterrupt:
        guardar_estado(con, "parado", tot)
        con.close()
        print(f"\n[{ahora()}] Contable detenido con Ctrl+C.")
    except Exception:
        guardar_estado(con, "error", tot)
        raise


if __name__ == "__main__":
    main()
