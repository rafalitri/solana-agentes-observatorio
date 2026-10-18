import json
import sqlite3
import time
import urllib.error
import urllib.request

from agentes.opinion import construir_peticion, extraer_texto, parsear_respuesta
from comun import db
from comun.config import (ANALISTA_ESPERA_S, ANALISTA_LOTE, ANALISTA_REINTENTO_S, ANALISTA_TIMEOUT_S,
                          ANALISTA_VENTANA_S, MODELO_ANALISTA, OLLAMA_URL, VERSION_PROMPT)

ERRORES_RED = (urllib.error.URLError, TimeoutError, ConnectionError, OSError)


def ahora():
    return time.strftime("%H:%M:%S")


def llamar_ollama(peticion):
    req = urllib.request.Request(OLLAMA_URL, data=json.dumps(peticion).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=ANALISTA_TIMEOUT_S) as resp:
        return resp.read().decode("utf-8")


def guardar_estado(con, estado, tot, c):
    try:
        db.guardar_estado_agente(con, "analista", time.time(), estado, tot["procesados"], tot["errores"])
    except sqlite3.OperationalError as e:
        db.deshacer(con)
        c["bd"] += 1
        tot["errores"] += 1
        print(f"[{ahora()}] Aviso: error de BD al guardar estado ({e})")


def main():
    con = db.conectar()
    db.crear_tablas(con)
    print(f"Analista arrancado ({MODELO_ANALISTA}, prompt v{VERSION_PROMPT})")
    c = {"ok": 0, "parseo": 0, "red": 0, "bd": 0}  # contadores del último minuto
    tot = {"procesados": 0, "errores": 0}
    estado = "ok"
    proximo_informe = time.time() + 60
    try:
        while True:
            try:
                pendientes = db.pendientes_analisis(con, time.time() - ANALISTA_VENTANA_S, ANALISTA_LOTE)
            except sqlite3.OperationalError as e:
                db.deshacer(con)
                c["bd"] += 1
                tot["errores"] += 1
                print(f"[{ahora()}] Aviso: error de BD al leer pendientes ({e})")
                pendientes = []
            for mint, name, symbol in pendientes:
                inicio = time.time()
                try:
                    cuerpo = llamar_ollama(construir_peticion(name, symbol))
                except ERRORES_RED as e:
                    c["red"] += 1
                    tot["errores"] += 1
                    estado = "sin_ollama"
                    print(f"[{ahora()}] Aviso: Ollama no responde ({type(e).__name__}). Reintento en {ANALISTA_REINTENTO_S} s")
                    time.sleep(ANALISTA_REINTENTO_S)
                    break  # sale del lote
                duracion = time.time() - inicio
                estado = "ok"
                try:
                    raw = extraer_texto(cuerpo)
                    r = parsear_respuesta(raw)
                except ValueError:
                    raw = None
                    r = {"etiqueta": None, "puntuacion": None, "razon": None, "error": "ollama_cuerpo"}
                try:
                    db.guardar_analisis(con, mint, time.time(), VERSION_PROMPT, MODELO_ANALISTA,
                                        r["etiqueta"], r["puntuacion"], r["razon"], duracion, r["error"], raw)
                except sqlite3.OperationalError as e:
                    db.deshacer(con)
                    c["bd"] += 1
                    tot["errores"] += 1
                    print(f"[{ahora()}] Aviso: error de BD al guardar análisis ({e})")
                    continue
                c["ok"] += 1
                tot["procesados"] += 1
                if r["error"]:
                    c["parseo"] += 1
                    tot["errores"] += 1
            else:
                if not pendientes:
                    time.sleep(ANALISTA_ESPERA_S)
            if time.time() >= proximo_informe:
                guardar_estado(con, estado, tot, c)
                print(f"[{ahora()}] Analizados ultimo minuto: {c['ok']} | errores parseo: {c['parseo']}"
                      f" | errores red: {c['red']} | errores bd: {c['bd']}")
                c.update(ok=0, parseo=0, red=0, bd=0)
                proximo_informe = time.time() + 60
    except KeyboardInterrupt:
        pass
    guardar_estado(con, "parado", tot, c)
    con.close()
    print(f"\n[{ahora()}] Analista detenido con Ctrl+C.")


if __name__ == "__main__":
    main()
