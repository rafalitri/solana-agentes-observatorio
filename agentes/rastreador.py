import asyncio
import base64
import json
import random
import sqlite3
import sys
import time
from datetime import datetime

import websockets

from agentes import riesgo, seguimiento
from comun import db, solana_rpc
from comun.config import (
    ESPERA_MAX, ESPERA_MIN, FOTO_S, MAX_SEGUIDOS, PORCENTAJE_CONTROL, RUTA_BD, SEGUIMIENTO_S, URL,
)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def ahora():
    return datetime.now().astimezone().isoformat(timespec="seconds")


async def fotografiar(con, seguidos, contador):
    # cada FOTO_S: quita caducados y guarda una foto de la bonding curve de cada token seguido
    con_foto = set()  # mints con foto ya guardada (tras un reinicio se pierde: un raw extra, sin importancia)
    intentos = {}  # mint -> veces que se ha intentado leer su curva
    proximo = time.monotonic()
    while True:
        proximo += FOTO_S
        await asyncio.sleep(max(0, proximo - time.monotonic()))  # ritmo fijo: descuenta lo que tardó el ciclo
        proximo = max(proximo, time.monotonic())  # si el ciclo se alargó, no se recupera a ráfagas
        inicios = {mint: ts for mint, (ts, _) in seguidos.items()}
        for mint in seguimiento.caducados(inicios, time.time(), SEGUIMIENTO_S):
            seguidos.pop(mint, None)
        for mint in [m for m in intentos if m not in seguidos]:
            del intentos[mint]
        lista = list(seguidos.items())  # copia: `seguidos` puede cambiar mientras esperamos al RPC
        if not lista:
            continue
        try:
            cuentas = await asyncio.to_thread(solana_rpc.get_multiple_accounts, [c for _, (_, c) in lista])
        except Exception as e:  # el RPC falla a veces: se reintenta en la siguiente vuelta
            print(f"[{ahora()}] AVISO: RPC falló ({e!r})")
            contador["errores_rpc"] += 1
            contador["errores_total"] += 1
            continue
        ts = time.time()
        filas = []
        for mint, (inicio, curva) in lista:
            raw = cuentas.get(curva)
            if raw is None:  # la cuenta no existe (aún o ya)
                continue
            intentos[mint] = intentos.get(mint, 0) + 1
            try:
                c = solana_rpc.leer_curva(base64.b64decode(raw))
            except Exception as e:
                if seguimiento.imprimir_ilegible(intentos[mint]):
                    print(f"[{ahora()}] Curva ilegible de {mint} ({e!r})")
                else:
                    contador["pendientes"] += 1
                continue
            filas.append((mint, ts, int(ts - inicio), c["v_sol"], c["v_tokens"], c["real_sol"],
                          int(c["complete"]), c["market_cap_sol"],
                          seguimiento.raw_para(mint, con_foto, raw)))
        try:
            db.guardar_fotos(con, filas)
        except sqlite3.OperationalError as e:  # se pierden las fotos de este ciclo; la tarea sigue
            db.deshacer(con)
            print(f"[{ahora()}] Aviso: error de BD al guardar fotos ({e})")
            continue
        seguimiento.actualizar_con_foto(con_foto, filas, seguidos)
        for mint in seguimiento.graduados(filas):  # curva completa: ya no hay nada que seguir
            seguidos.pop(mint, None)
        contador["fotos"] += len(filas)


def guardar_estado(con, estado, contador):
    try:
        db.guardar_estado_agente(con, "rastreador", time.time(), estado,
                                 contador["recibidos"], contador["errores_total"])
    except sqlite3.OperationalError as e:
        db.deshacer(con)
        print(f"[{ahora()}] Aviso: error de BD al guardar estado ({e})")


async def informar(con, seguidos, contador):
    # el latido sigue aunque el WebSocket esté caído: esta tarea no depende de él
    while True:
        await asyncio.sleep(60)
        print(f"[{ahora()}] Seguidos: {len(seguidos)} | fotos último minuto: {contador['fotos']}"
              f" | errores RPC: {contador['errores_rpc']} | curvas pendientes: {contador['pendientes']}")
        estado = seguimiento.estado_latido(contador["ws_ok"], len(seguidos), contador["fotos"])
        contador["fotos"] = contador["errores_rpc"] = contador["pendientes"] = 0
        guardar_estado(con, estado, contador)


async def main():
    con = db.conectar()
    db.crear_tablas(con)
    seguidos = {}  # mint -> (ts de creación, bonding curve)
    contador = {"fotos": 0, "errores_rpc": 0, "pendientes": 0, "recibidos": 0, "errores_total": 0,
                "ws_ok": False}
    # referencias guardadas para que Python no borre las tareas; no dependen del WebSocket
    tareas = [asyncio.create_task(informar(con, seguidos, contador)),
              asyncio.create_task(fotografiar(con, seguidos, contador))]
    try:
        await recibir(con, seguidos, contador)
    except (asyncio.CancelledError, KeyboardInterrupt):  # parada limpia (Ctrl+C)
        guardar_estado(con, "parado", contador)
        raise
    except Exception:
        guardar_estado(con, "error", contador)
        raise


async def recibir(con, seguidos, contador):
    espera = ESPERA_MIN
    while True:
        try:
            async with websockets.connect(URL) as ws:
                espera = ESPERA_MIN  # conexión lograda: se reinicia la espera
                contador["ws_ok"] = True
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                # al (re)conectar se recupera de la BD lo que seguía en marcha; los más nuevos si no caben
                recuperados = db.mints_en_seguimiento(con, time.time() - SEGUIMIENTO_S)
                seguidos.clear()
                seguidos.update(list(recuperados.items())[-MAX_SEGUIDOS:])
                while True:
                    mensaje = await ws.recv()  # fuera del try interno: si cae la conexión, se reconecta
                    try:
                        datos = json.loads(mensaje)
                        if "mint" not in datos:  # p. ej. el mensaje de confirmación
                            print(f"[{ahora()}] SERVIDOR: {mensaje[:300]}")
                            continue
                        datos["ts"] = time.time()
                        datos["hora"] = ahora()
                        if datos.get("txType") != "create":  # migraciones
                            db.guardar_operacion(con, datos)
                            continue
                        db.guardar_token(con, datos)  # commit al instante: no se pierde nada si se corta
                        contador["recibidos"] += 1
                        decision = riesgo.evaluar(datos, db.contexto_token(con, datos))
                        decision["control"] = not decision["pasa"] and random.random() < PORCENTAJE_CONTROL
                        decision["seguir"] = decision["pasa"] or decision["control"]
                        db.guardar_decision(con, datos["mint"], decision)
                        estado = "OK" if decision["pasa"] else "CONTROL" if decision["control"] else "NO"
                        print(datos["hora"], datos.get("symbol", ""), datos.get("solAmount", ""),
                              estado, ",".join(decision["motivos"]))
                        if not datos.get("bondingCurveKey"):
                            print(f"[{ahora()}] AVISO: {datos['mint']} sin bondingCurveKey, no se sigue")
                        elif len(seguidos) >= MAX_SEGUIDOS:
                            print(f"[{ahora()}] AVISO: MAX_SEGUIDOS ({MAX_SEGUIDOS}) lleno, no se sigue {datos['mint']}")
                        else:
                            seguidos[datos["mint"]] = (datos["ts"], datos["bondingCurveKey"])
                    except Exception as e:
                        db.deshacer(con)
                        print(f"[{ahora()}] Mensaje ignorado ({e!r})")
        except Exception as e:  # Ctrl+C no es Exception, así que pasa de largo
            contador["ws_ok"] = False
            print(f"[{ahora()}] Conexión caída o error ({e!r}). Reconectando en {espera} s...")
            await asyncio.sleep(espera)
            espera = min(espera * 2, ESPERA_MAX)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n[{ahora()}] Detenido con Ctrl+C. Datos guardados en {RUTA_BD}.")
