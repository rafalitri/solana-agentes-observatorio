import argparse
import sqlite3
import time
import traceback

from agentes.analista import ahora
from agentes.resumen import resumir_token
from comun import db
from comun.config import (ARCHIVISTA_DESDE, ARCHIVISTA_ESPERA_S, ARCHIVISTA_LOTE, ARCHIVISTA_MARGEN_S,
                          ARCHIVISTA_PAUSA_S, SEGUIMIENTO_S, VERSION_RESUMEN)
from herramientas.resultados import parse_desde


def guardar_estado(con, estado, tot, c):
    try:
        db.guardar_estado_agente(con, "archivista", time.time(), estado, tot["procesados"], tot["errores"])
    except sqlite3.OperationalError as e:
        db.deshacer(con)
        c["bd"] += 1
        tot["errores"] += 1
        print(f"[{ahora()}] Aviso: error de BD al guardar estado ({e})")


def resumir_lote(con, pendientes, c, tot):
    filas = []
    for mint, ts, mayhem in pendientes:
        fotos = db.fotos_token(con, mint)
        try:
            fila = resumir_token(fotos, mayhem)
            fila["error"] = None
        except Exception:
            print(f"[{ahora()}] Excepcion al resumir {mint}:")
            traceback.print_exc()
            fila = {"version_resumen": VERSION_RESUMEN, "n_fotos": len(fotos), "error": "excepcion"}
            c["excepciones"] += 1
            tot["errores"] += 1
        fila.update(mint=mint, ts_token=ts, ts_resumen=time.time())
        filas.append(fila)
    db.guardar_resumenes(con, filas)
    c["ok"] += len(filas)
    tot["procesados"] += len(filas)


def main(desde_ts):
    con = db.conectar()
    db.crear_tablas(con)
    desde_txt = time.strftime("%Y-%m-%d %H:%M", time.localtime(desde_ts))
    print(f"Archivista arrancado (resumen v{VERSION_RESUMEN}, desde {desde_txt})")
    c = {"ok": 0, "excepciones": 0, "bd": 0}  # contadores del último minuto
    tot = {"procesados": 0, "errores": 0}
    proximo_informe = time.time() + 60
    try:
        while True:
            pausa = ARCHIVISTA_ESPERA_S
            try:
                hasta_ts = time.time() - SEGUIMIENTO_S - ARCHIVISTA_MARGEN_S
                pendientes = db.pendientes_resumen(con, desde_ts, hasta_ts, ARCHIVISTA_LOTE)
                resumir_lote(con, pendientes, c, tot)
                if len(pendientes) >= ARCHIVISTA_LOTE:
                    pausa = ARCHIVISTA_PAUSA_S
            except sqlite3.OperationalError as e:
                db.deshacer(con)
                c["bd"] += 1
                tot["errores"] += 1
                print(f"[{ahora()}] Aviso: error de BD ({e})")
            if time.time() >= proximo_informe:
                guardar_estado(con, "ok", tot, c)
                print(f"[{ahora()}] Resumidos ultimo minuto: {c['ok']} | excepciones: {c['excepciones']}"
                      f" | errores bd: {c['bd']}")
                c.update(ok=0, excepciones=0, bd=0)
                proximo_informe = time.time() + 60
            time.sleep(pausa)
    except KeyboardInterrupt:
        guardar_estado(con, "parado", tot, c)
        con.close()
        print(f"\n[{ahora()}] Archivista detenido con Ctrl+C.")
    except Exception:
        guardar_estado(con, "error", tot, c)
        raise


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default=ARCHIVISTA_DESDE, help='hora local "YYYY-MM-DD HH:MM"')
    main(parse_desde(ap.parse_args().desde))
