import http.client
import sqlite3
import time
import urllib.request

from agentes.analista import ERRORES_RED, ahora, llamar_ollama
from agentes.opinion import (construir_peticion_v2, extraer_texto, parsear_metadatos, parsear_respuesta,
                             redes_de, urls_metadatos)
from comun import db
from comun.config import (ANALISTA_ESPERA_S, ANALISTA_LOTE, ANALISTA_REINTENTO_S, ANALISTA_VENTANA_S,
                          METADATOS_TIMEOUT_S, MODELO_ANALISTA)

VERSION_PROMPT_V2 = 2


def _bajar(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=METADATOS_TIMEOUT_S) as resp:
        return resp.read().decode("utf-8")


def descargar_metadatos(uri):
    # -> (meta o None, meta_ok 0/1, segundos). Un fallo aquí nunca es error del token.
    inicio = time.time()
    for url in urls_metadatos(uri):
        if not url.startswith(("http://", "https://")):  # la uri no es fiable: nada de file:// ni similares
            continue
        try:
            texto = _bajar(url)
        except (*ERRORES_RED, http.client.HTTPException, ValueError):
            continue
        meta = parsear_metadatos(texto)  # la primera que responde manda; si su JSON falla, no se sigue
        ok = meta["error"] is None
        return (meta if ok else None), int(ok), time.time() - inicio
    return None, 0, time.time() - inicio


def guardar_estado(con, estado, tot, c):
    try:
        db.guardar_estado_agente(con, "analista_v2", time.time(), estado, tot["procesados"], tot["errores"])
    except sqlite3.OperationalError as e:
        db.deshacer(con)
        c["bd"] += 1
        tot["errores"] += 1
        print(f"[{ahora()}] Aviso: error de BD al guardar estado ({e})")


def main():
    con = db.conectar()
    db.crear_tablas(con)
    print(f"Analista v2 arrancado ({MODELO_ANALISTA}, prompt v{VERSION_PROMPT_V2})")
    c = {"ok": 0, "parseo": 0, "red": 0, "bd": 0, "meta": 0}  # contadores del último minuto
    tot = {"procesados": 0, "errores": 0}
    estado = "ok"
    proximo_informe = time.time() + 60
    try:
        while True:
            try:
                pendientes = db.pendientes_analisis_v2(con, time.time() - ANALISTA_VENTANA_S, ANALISTA_LOTE)
            except sqlite3.OperationalError as e:
                db.deshacer(con)
                c["bd"] += 1
                tot["errores"] += 1
                print(f"[{ahora()}] Aviso: error de BD al leer pendientes ({e})")
                pendientes = []
            for mint, name, symbol, uri in pendientes:
                meta, meta_ok, meta_s = descargar_metadatos(uri)
                inicio = time.time()
                try:
                    cuerpo = llamar_ollama(construir_peticion_v2(name, symbol, meta))
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
                    db.guardar_analisis_v2(con, mint, time.time(), VERSION_PROMPT_V2, MODELO_ANALISTA,
                                           r["etiqueta"], r["puntuacion"], r["razon"], duracion, r["error"], raw,
                                           meta_ok, meta_s, *redes_de(meta))
                except sqlite3.OperationalError as e:
                    db.deshacer(con)
                    c["bd"] += 1
                    tot["errores"] += 1
                    print(f"[{ahora()}] Aviso: error de BD al guardar análisis ({e})")
                    continue
                c["ok"] += 1
                c["meta"] += meta_ok
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
                      f" | errores red: {c['red']} | errores bd: {c['bd']}"
                      f" | metadatos ok: {c['meta']} de {c['ok']}")
                c.update(ok=0, parseo=0, red=0, bd=0, meta=0)
                proximo_informe = time.time() + 60
    except KeyboardInterrupt:
        pass
    guardar_estado(con, "parado", tot, c)
    con.close()
    print(f"\n[{ahora()}] Analista v2 detenido con Ctrl+C.")


if __name__ == "__main__":
    main()
