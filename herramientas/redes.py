"""Descarga por lotes (a mano, no es agente) de las redes (twitter/telegram/web) de los metadatos IPFS de los tokens que pasan."""
import argparse
import http.client
import sqlite3
import time

from agentes.analista import ERRORES_RED
from agentes.analista_v2 import _bajar
from agentes.opinion import parsear_metadatos
from comun import db
from comun.config import REDES_MAX_FALLOS_SEGUIDOS, REDES_PASARELAS, REDES_PAUSA_S, SEGUIMIENTO_S
from herramientas.resultados import parse_desde

LOTE = 50
PROGRESO_CADA = 100


def clasificar(uri, bajar=_bajar):
    # -> (ok, motivo, twitter, telegram, web). Solo IPFS: el contenido de un CID no cambia. Con ok = 0 las redes son None.
    cid = uri.split("/ipfs/", 1)[1] if "/ipfs/" in (uri or "") else ""
    urls = [p + cid for p in REDES_PASARELAS] if cid else []
    if not urls:
        return 0, "no_ipfs", None, None, None
    for url in urls:
        try:
            texto = bajar(url)
        except (*ERRORES_RED, http.client.HTTPException, ValueError):
            continue
        meta = parsear_metadatos(texto)  # la primera pasarela que responde manda, como en analista_v2
        if meta["error"] is not None:
            return 0, "json", None, None, None
        return 1, None, int(meta["twitter"]), int(meta["telegram"]), int(meta["web"])
    return 0, "descarga", None, None, None


def procesar(con, pendientes, bajar=_bajar, pausa=REDES_PAUSA_S, reemplazar=False):
    # Descarga SIN escritura pendiente: los resultados se acumulan en memoria y se escriben de golpe (executemany + commit).
    c = {"hechos": 0, "ok": 0, "no_ipfs": 0, "fallos": 0, "parado": False}
    buffer = []
    seguidos = 0  # fallos de descarga consecutivos; no_ipfs no descarga, así que ni suma ni reinicia

    def volcar():
        if not buffer:
            return
        try:
            db.guardar_redes(con, buffer, reemplazar)
            buffer.clear()
        except sqlite3.OperationalError as e:
            db.deshacer(con)
            print(f"Aviso: no se pudo escribir ({e}); se reintenta en el próximo lote")

    try:
        for mint, uri in pendientes:
            ok, motivo, tw, tg, web = clasificar(uri, bajar)
            buffer.append((mint, time.time(), ok, motivo, tw, tg, web))
            c["hechos"] += 1
            c["ok" if ok else "no_ipfs" if motivo == "no_ipfs" else "fallos"] += 1
            if motivo == "descarga":
                seguidos += 1
            elif motivo != "no_ipfs":
                seguidos = 0
            if len(buffer) >= LOTE:
                volcar()
            if c["hechos"] % PROGRESO_CADA == 0:
                print(f"Hechos {c['hechos']} de {len(pendientes)} | ok {c['ok']} | no_ipfs {c['no_ipfs']} | fallos {c['fallos']}")
            if ok or motivo != "no_ipfs":
                time.sleep(pausa)  # solo pausa si hubo descarga
            if seguidos >= REDES_MAX_FALLOS_SEGUIDOS:
                c["parado"] = True
                print(f"PARADO: {REDES_MAX_FALLOS_SEGUIDOS} fallos seguidos (posible bloqueo de pasarela)")
                break
    except KeyboardInterrupt:
        print("Interrumpido: se guarda lo hecho.")
    volcar()
    return c


def main():
    ap = argparse.ArgumentParser(description="Descarga las redes de los metadatos IPFS (escribe en la tabla redes)")
    ap.add_argument("--desde", required=True, help='hora local "YYYY-MM-DD HH:MM"')
    ap.add_argument("--hasta", help='hora local "YYYY-MM-DD HH:MM" (por defecto, desde + 24 h)')
    ap.add_argument("--pausa", type=float, default=REDES_PAUSA_S, help="segundos entre descargas")
    ap.add_argument("--reintentar", action="store_true", help="repite también las filas con motivo 'descarga' (las sobrescribe)")
    a = ap.parse_args()
    try:
        desde = parse_desde(a.desde)
        hasta_usuario = parse_desde(a.hasta) if a.hasta else desde + 24 * 3600
    except ValueError:
        ap.error('--desde/--hasta deben tener el formato "YYYY-MM-DD HH:MM"')
    hasta = min(hasta_usuario, time.time() - SEGUIMIENTO_S)
    con = db.conectar()
    try:
        db.crear_tablas(con)
        pendientes = db.pendientes_redes(con, desde, hasta, a.reintentar)
        print(f"Pendientes: {len(pendientes)}")
        c = procesar(con, pendientes, pausa=a.pausa, reemplazar=a.reintentar)
    finally:
        con.close()
    print(f"RESUMEN: hechos {c['hechos']} de {len(pendientes)} | ok {c['ok']} | no_ipfs {c['no_ipfs']} | fallos {c['fallos']}")


if __name__ == "__main__":
    main()
