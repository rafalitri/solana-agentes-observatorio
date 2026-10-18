import base64
import json
import sqlite3
import struct
import time
import urllib.error
import urllib.request

from agentes.analista import ahora
from comun import db
from comun.config import (SUPERV_CANDIDATOS_S, SUPERV_DEX_LOTE, SUPERV_DEX_MAX_MIN, SUPERV_DEX_PARES_MAX, SUPERV_DEX_URL, SUPERV_DURACION_S,
                          SUPERV_MARGEN_FIN_S, SUPERV_PERIODO_S, SUPERV_RPC_LOTE, SUPERV_RPC_PAUSA_S, SUPERV_VENTANA_S,
                          SUPERV_WSOL)
from comun.solana_rpc import get_multiple_accounts, leer_curva

ERRORES_RED = (OSError, ValueError, RuntimeError)  # HTTPError (429 incluido) es OSError; JSON roto es ValueError


def precio_curva(v_sol, v_tokens):
    # SOL por token (tokens con 6 decimales, SOL con 9); None sin reservas
    return (v_sol / 1e9) / (v_tokens / 1e6) if v_sol is not None and v_tokens else None


def evaluar_superviviente(fotos, ahora_ts):
    """fotos: [(ts, v_sol, v_tokens, complete)] por ts. Solo mira hasta 30 min desde la 1.ª foto válida.
    Devuelve la fila de supervivientes (dict) o None si no sobrevive / no hay foto válida."""
    validas = [(ts, precio_curva(vs, vt)) for ts, vs, vt, c in fotos if not c and precio_curva(vs, vt) is not None]
    if not validas:
        return None
    t0, p0 = validas[0]
    fin = t0 + SUPERV_VENTANA_S
    ventana = [f for f in fotos if f[0] <= fin]
    grad = next((f[0] for f in ventana if f[3]), None)
    pv = [v for v in validas if v[0] <= fin]
    ts_ult, p30 = pv[-1]
    if grad is not None:
        motivo = "graduado"
    elif p30 >= p0:
        motivo = "curva"
    else:
        return None
    return {"mint": None, "ts_seleccion": ahora_ts, "motivo": motivo, "ts_graduacion": grad, "precio_1a_foto": p0,
            "precio_30min": p30, "ts_fin": fin + SUPERV_DURACION_S + SUPERV_MARGEN_FIN_S,
            "fuente": "dex" if grad is not None else "curva",
            "precio_ultimo_curva": p30, "ts_ultimo_curva": ts_ult,
            "precio_primero_dex": None, "ts_primero_dex": None}


def elegir_par(lista):
    """lista: pares de DexScreener de un token. Devuelve (par|None, error|None). Descarta dexId 'pumpfun' (curva congelada
    en el precio de graduación), exige cotización en SOL y liquidez en USD > 0; entre los que quedan, el de más liquidez."""
    sin_curva = [p for p in lista if p.get("dexId") != "pumpfun"]
    sol = [p for p in sin_curva if (p.get("quoteToken") or {}).get("address") == SUPERV_WSOL]
    if sin_curva and not sol:
        return None, "sin_par_sol"
    con_liq = [p for p in sol if ((p.get("liquidity") or {}).get("usd") or 0) > 0]
    if not con_liq:
        return None, "sin_par"
    return max(con_liq, key=lambda x: x["liquidity"]["usd"]), None


def parsear_dex(pares, mints):
    """pares: lista JSON de DexScreener. Devuelve {mint: (datos|None, error|None)}: por token, el par elegido por elegir_par."""
    por_mint = {m: [] for m in mints}
    for p in pares if isinstance(pares, list) else []:
        m = (p.get("baseToken") or {}).get("address")
        if m in por_mint:
            por_mint[m].append(p)
    res = {}
    for m, lista in por_mint.items():
        p, err = elegir_par(lista)
        if err:
            res[m] = (None, err)
            continue
        try:
            precio = float(p["priceNative"])
        except (KeyError, TypeError, ValueError):
            res[m] = (None, "sin_precio")
            continue
        m5 = (p.get("txns") or {}).get("m5") or {}
        res[m] = ({"precio_sol": precio, "liquidez_usd": (p.get("liquidity") or {}).get("usd"),
                   "volumen_5m": (p.get("volume") or {}).get("m5"), "compras_5m": m5.get("buys"),
                   "ventas_5m": m5.get("sells"), "fdv_usd": p.get("fdv"), "dex_id": p.get("dexId"),
                   "pair_address": p.get("pairAddress")}, None)
    return res


def llamar_dex(mints):
    # bloqueante. Lista de pares ({"pairs": [...]} en /latest); falla con OSError (incluye HTTPError 429) o ValueError
    peticion = urllib.request.Request(SUPERV_DEX_URL + ",".join(mints), headers={"User-Agent": "solana-agentes"})
    with urllib.request.urlopen(peticion, timeout=15) as r:
        datos = json.load(r)
    return (datos.get("pairs") if isinstance(datos, dict) else None) or []


def _fila_error(mint, ts, fuente, error):
    return (mint, ts, fuente, None, None, None, None, None, None, None, None, error)


def leer_dex(mints, ts, llamar=llamar_dex, avisados=None):
    filas, llamadas, avisado = [], 0, False
    lotes = [mints[i:i + SUPERV_DEX_LOTE] for i in range(0, len(mints), SUPERV_DEX_LOTE)]
    while lotes:
        lote = lotes.pop(0)
        if llamadas >= SUPERV_DEX_MAX_MIN:
            filas += [_fila_error(m, ts, "dex", "dex_tope") for m in lote]
            continue
        if llamadas:
            time.sleep(60 / SUPERV_DEX_MAX_MIN)  # reparte las llamadas: nunca más de SUPERV_DEX_MAX_MIN por minuto
        llamadas += 1
        try:
            pares = llamar(lote)
            if len(lote) > 1 and isinstance(pares, list) and len(pares) >= SUPERV_DEX_PARES_MAX:
                if not avisado:  # una vez por ciclo
                    print(f"[{ahora()}] Aviso: DexScreener devolvió {len(pares)} pares (posible corte); repito mint a mint")
                    avisado = True
                lotes = [[m] for m in lote] + lotes
                continue
            datos = parsear_dex(pares, lote)
        except ERRORES_RED as e:
            etiqueta = "dex_429" if getattr(e, "code", None) == 429 else "dex_red"
            filas += [_fila_error(m, ts, "dex", etiqueta) for m in lote]
            continue
        for m in lote:
            d, err = datos[m]
            filas.append((m, ts, "dex", *(d[k] for k in ("precio_sol", "liquidez_usd", "volumen_5m", "compras_5m",
                                                           "ventas_5m", "fdv_usd", "dex_id", "pair_address")), None) if d
                         else _fila_error(m, ts, "dex", err))
            if err == "sin_par" and avisados is not None and m not in avisados:
                avisados.add(m)  # una sola vez por token: no llenar la terminal
                print(f"[{ahora()}] Sin par válido en DexScreener (solo pumpfun o sin liquidez): {m}")
    return filas


def leer_curvas(tokens, ts, llamar=get_multiple_accounts, pausa=SUPERV_RPC_PAUSA_S):
    """tokens: [(mint, bonding_curve)]. Un getMultipleAccounts por cada SUPERV_RPC_LOTE."""
    filas = []
    for i in range(0, len(tokens), SUPERV_RPC_LOTE):
        lote = tokens[i:i + SUPERV_RPC_LOTE]
        if i:
            time.sleep(pausa)
        try:
            cuentas = llamar([bc for _, bc in lote])
        except ERRORES_RED as e:
            etiqueta = "rpc_429" if getattr(e, "code", None) == 429 else "rpc_red"
            filas += [_fila_error(m, ts, "curva", etiqueta) for m, _ in lote]
            continue
        for mint, bc in lote:
            if not cuentas.get(bc):
                filas.append(_fila_error(mint, ts, "curva", "sin_cuenta"))
                continue
            try:
                c = leer_curva(base64.b64decode(cuentas[bc]))
            except (struct.error, ValueError):
                filas.append(_fila_error(mint, ts, "curva", "curva_ilegible"))
                continue
            if c["complete"]:
                filas.append(_fila_error(mint, ts, "curva", "graduada"))  # desde la siguiente ronda se lee en dex
            else:
                filas.append((mint, ts, "curva", precio_curva(c["v_sol"], c["v_tokens"]), None, None, None, None, None, None, None,
                              None if c["v_tokens"] else "sin_precio"))
    return filas


def seleccionar(con, ahora_ts, evaluados):
    # candidatos con >= 30 min desde la decisión; `evaluados` (en memoria) evita repetir descartes
    nuevos, vistos = [], []
    for mint, _ in db.candidatos_superv(con, ahora_ts - SUPERV_CANDIDATOS_S, ahora_ts - SUPERV_VENTANA_S):
        if mint in evaluados:
            continue
        vistos.append(mint)
        fila = evaluar_superviviente(db.fotos_superv(con, mint), ahora_ts)
        if fila:
            fila["mint"] = mint
            nuevos.append(fila)
    if nuevos:
        db.guardar_supervivientes(con, nuevos)
    evaluados.update(vistos)  # después de guardar: si la BD falla, se reintenta
    return len(nuevos)


def ronda(con, ahora_ts, evaluados, rpc=get_multiple_accounts, dex=llamar_dex, avisados=None):
    """Selecciona y lee una vez. Devuelve (lecturas_ok, lecturas_con_error)."""
    seleccionar(con, ahora_ts, evaluados)
    activos = db.activos_superv(con, ahora_ts)
    filas = leer_curvas([(m, bc) for m, f, bc in activos if f == "curva"], ahora_ts, rpc)
    db.guardar_lecturas_superv(con, filas)
    filas_dex = leer_dex([m for m, f, _ in activos if f == "dex"], ahora_ts, dex, avisados)
    db.guardar_lecturas_superv(con, filas_dex)
    todas = filas + filas_dex
    malas = sum(1 for f in todas if f[-1])
    return len(todas) - malas, malas


def main():
    con = db.conectar()
    for intento in range(5):  # el ALTER TABLE de crear_tablas puede chocar con otros procesos escribiendo
        try:
            db.crear_tablas(con)
            break
        except sqlite3.OperationalError as e:
            db.deshacer(con)
            if intento == 4:
                raise
            print(f"[{ahora()}] Aviso: BD ocupada al arrancar ({e}); reintento en 2 s")
            time.sleep(2)
    print(f"Supervivientes arrancado (lecturas cada {SUPERV_PERIODO_S} s)")
    evaluados, avisados, tot = set(), set(), {"procesados": 0, "errores": 0}
    try:
        while True:
            inicio = time.time()
            try:
                ok, mal = ronda(con, inicio, evaluados, avisados=avisados)
                tot["procesados"] += ok
                tot["errores"] += mal
                estado = "ok"
                print(f"[{ahora()}] Lecturas ok: {ok} | con error: {mal}")
            except sqlite3.OperationalError as e:
                db.deshacer(con)
                tot["errores"] += 1
                estado = "ok"
                print(f"[{ahora()}] Aviso: error de BD ({e})")
            try:
                db.guardar_estado_agente(con, "supervivientes", time.time(), estado, tot["procesados"], tot["errores"])
            except sqlite3.OperationalError as e:
                db.deshacer(con)
                print(f"[{ahora()}] Aviso: error de BD al guardar estado ({e})")
            time.sleep(max(1, SUPERV_PERIODO_S - (time.time() - inicio)))
    except KeyboardInterrupt:
        db.guardar_estado_agente(con, "supervivientes", time.time(), "parado", tot["procesados"], tot["errores"])
        con.close()
        print(f"\n[{ahora()}] Supervivientes detenido con Ctrl+C.")
    except Exception:
        db.guardar_estado_agente(con, "supervivientes", time.time(), "error", tot["procesados"], tot["errores"])
        raise


if __name__ == "__main__":
    main()
