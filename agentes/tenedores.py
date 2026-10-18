import json
import sqlite3
import time

from agentes.analista import ahora
from comun import db
from comun.config import (TENEDORES_429_ESPERA_S, TENEDORES_ENV_CLAVE, TENEDORES_ESPERA_S, TENEDORES_INTENTOS,
                          TENEDORES_LOTE, TENEDORES_MAX_S, TENEDORES_PAUSA_S, TENEDORES_SUPPLY)
from comun.entorno import leer_env
from comun.solana_rpc import RpcLimite, duenos_de_cuentas, tokens_mayores

ERRORES_CONSULTA = (OSError, ValueError, RuntimeError, KeyError, TypeError)  # red, JSON, "error" del RPC, forma rara


def calcular_tenedores(cuentas, duenos, bonding_curve, creador, supply):
    # cuentas: [(direccion, cantidad cruda)]; duenos: owner de cada cuenta (mismo orden); supply en unidades crudas
    por_dueno = {}
    for (_, cantidad), dueno in zip(cuentas, duenos):
        por_dueno[dueno] = por_dueno.get(dueno, 0) + cantidad
    curva = por_dueno.pop(bonding_curve, 0)
    top10 = sum(sorted(por_dueno.values(), reverse=True)[:10])
    circulante = supply - curva
    return {
        "curva_pct": 100 * curva / supply,
        "top10_pct": 100 * top10 / supply,
        "top10_circ_pct": 100 * top10 / circulante if circulante > 0 else 0.0,
        "creador_pct": 100 * por_dueno.get(creador, 0) / supply,
        "creador_en_top": int(creador in por_dueno),
        "n_cuentas": len(cuentas),
        "raw": json.dumps([{"direccion": d, "dueno": o, "cantidad": q} for (d, q), o in zip(cuentas, duenos)]),
    }


def consultar_rpc(mint, url):
    # 2 llamadas RPC: (cuentas, duenos, decimales)
    cuentas, decimales = tokens_mayores(mint, url)
    return cuentas, duenos_de_cuentas([d for d, _ in cuentas], url), decimales


def guardar_estado(con, estado, tot, c):
    try:
        db.guardar_estado_agente(con, "tenedores", time.time(), estado, tot["procesados"], tot["errores"])
    except sqlite3.OperationalError as e:
        db.deshacer(con)
        c["bd"] += 1
        tot["errores"] += 1
        print(f"[{ahora()}] Aviso: error de BD al guardar estado ({e})")


def procesar_ciclo(con, desde_ts, intentos, c, tot, consultar, dormir=time.sleep):
    # intentos: {mint: fallos}. Un 429 no cuenta: espera y corta el ciclo con el token aún pendiente.
    pendientes = db.pendientes_tenedores(con, desde_ts, TENEDORES_LOTE)
    c["pendientes"] = len(pendientes)
    for mint, ts_decision, curva, creador in pendientes:
        fila = {"mint": mint, "ts_decision": ts_decision}
        try:
            cuentas, duenos, decimales = consultar(mint)
            ahora_ts = time.time()
            fila.update(calcular_tenedores(cuentas, duenos, curva, creador, TENEDORES_SUPPLY * 10 ** decimales))
            fila["ts_consulta"] = ahora_ts
            primera = db.primera_foto_ts(con, mint)
            fila["seg_desde_primera_foto"] = ahora_ts - primera if primera is not None else None
        except RpcLimite:
            c["rpc"] += 1
            tot["errores"] += 1
            print(f"[{ahora()}] RPC 429: espero {TENEDORES_429_ESPERA_S} s")
            dormir(TENEDORES_429_ESPERA_S)
            return
        except ERRORES_CONSULTA as e:
            c["rpc"] += 1
            tot["errores"] += 1
            intentos[mint] = intentos.get(mint, 0) + 1
            # el mensaje ya viene sin URL (solana_rpc la sustituye por <rpc>), igual que en la columna error
            print(f"[{ahora()}] fallo RPC {type(e).__name__}: {str(e)[:120]} (intento {intentos[mint]}/{TENEDORES_INTENTOS})")
            if intentos[mint] < TENEDORES_INTENTOS:
                dormir(TENEDORES_PAUSA_S)
                continue
            fila.update(ts_consulta=time.time(), error=f"rpc: {e}"[:200])
        db.guardar_tenedores(con, fila)
        intentos.pop(mint, None)
        c["ok"] += 1
        tot["procesados"] += 1
        dormir(TENEDORES_PAUSA_S)


def main():
    url = leer_env(TENEDORES_ENV_CLAVE)
    if not url:  # nunca se usa el RPC público de respaldo
        raise SystemExit(f"Falta {TENEDORES_ENV_CLAVE} en .env")
    consultar = lambda mint: consultar_rpc(mint, url)
    con = db.conectar()
    db.crear_tablas(con)
    print("Tenedores arrancado (RPC propio)")
    c = {"ok": 0, "rpc": 0, "bd": 0, "pendientes": 0}  # contadores del último minuto
    tot = {"procesados": 0, "errores": 0}
    intentos = {}
    proximo_informe = time.time() + 60
    try:
        while True:
            try:
                procesar_ciclo(con, time.time() - TENEDORES_MAX_S, intentos, c, tot, consultar)
            except sqlite3.OperationalError as e:
                db.deshacer(con)
                c["bd"] += 1
                tot["errores"] += 1
                print(f"[{ahora()}] Aviso: error de BD ({e})")
            if time.time() >= proximo_informe:
                guardar_estado(con, "ok", tot, c)
                print(f"[{ahora()}] Consultados ultimo minuto: {c['ok']} | errores rpc: {c['rpc']}"
                      f" | errores bd: {c['bd']} | pendientes: {c['pendientes']}")
                c.update(ok=0, rpc=0, bd=0)
                proximo_informe = time.time() + 60
            time.sleep(TENEDORES_ESPERA_S)
    except KeyboardInterrupt:
        guardar_estado(con, "parado", tot, c)
        con.close()
        print(f"\n[{ahora()}] Tenedores detenido con Ctrl+C.")
    except Exception:
        guardar_estado(con, "error", tot, c)
        raise


if __name__ == "__main__":
    main()
