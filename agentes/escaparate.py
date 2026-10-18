import hashlib
import json
import sqlite3
import sys
import time
import urllib.request

from agentes.analista import ahora
from comun import db
from comun.config import ESCAPARATE_PERIODO_S, ESCAPARATE_RAW_MAX, ESCAPARATE_RESUMEN_CADA, ESCAPARATE_URLS

ERRORES_RED = (OSError, ValueError, RuntimeError)  # HTTPError es OSError; JSON roto es ValueError
CAMPOS_IMAGEN = ("icon", "header", "openGraph")


def solo_solana(lista):
    return [e for e in lista if isinstance(e, dict) and e.get("chainId") == "solana" and e.get("tokenAddress")]


def clave_de(tipo, el):
    if tipo == "cto":
        return str(el.get("claimDate"))
    if tipo == "perfil":
        sin_img = {k: v for k, v in el.items() if k not in CAMPOS_IMAGEN}
        return hashlib.sha1(json.dumps(sin_img, sort_keys=True).encode()).hexdigest()[:12]
    total = el.get("totalAmount")
    return str(total if total is not None else el.get("amount"))


def contar_links(el):
    links = el.get("links")
    return len(links) if isinstance(links, list) else 0


def fila_de(tipo, el, ts, en_bd):
    return {"tipo": tipo, "mint": el["tokenAddress"], "clave": clave_de(tipo, el), "ts_visto": ts,
            "claim_date": el.get("claimDate") if tipo == "cto" else None,
            "amount": el.get("amount") if tipo == "boost" else None,
            "total_amount": el.get("totalAmount") if tipo == "boost" else None,
            "n_links": contar_links(el), "en_bd": 1 if en_bd else 0,
            "raw": json.dumps(el, ensure_ascii=False)[:ESCAPARATE_RAW_MAX]}


def pedir(url):
    peticion = urllib.request.Request(url, headers={"User-Agent": "solana-agentes"})
    with urllib.request.urlopen(peticion, timeout=15) as r:
        datos = json.load(r)
    if not isinstance(datos, list):
        raise ValueError("la respuesta no es una lista")
    return datos


def ciclo(con, ts, pedir=pedir):
    """Pide los 3 endpoints y guarda lo nuevo. Devuelve ({tipo: nuevos}, en_bd de los nuevos, errores)."""
    elementos, errores = [], 0
    for tipo, url in ESCAPARATE_URLS.items():
        try:
            elementos += [(tipo, e) for e in solo_solana(pedir(url))]
        except ERRORES_RED as e:
            errores += 1
            print(f"[{ahora()}] Aviso: {tipo} falló ({type(e).__name__}: {str(e)[:80]})")
    en_bd = db.mints_en_tokens(con, {e["tokenAddress"] for _, e in elementos})
    db.guardar_escaparate(con, [fila_de(t, e, ts, e["tokenAddress"] in en_bd) for t, e in elementos])
    # INSERT OR IGNORE no toca las repetidas: las filas con este ts_visto son exactamente las nuevas
    nuevos = {t: 0 for t in ESCAPARATE_URLS}
    n_bd = 0
    for tipo, n, b in con.execute(
            "SELECT tipo, COUNT(*), SUM(en_bd) FROM escaparate WHERE ts_visto = ? GROUP BY tipo", (ts,)):
        nuevos[tipo], n_bd = n, n_bd + (b or 0)
    return nuevos, n_bd, errores


def main():
    una_vez = "--una-vez" in sys.argv
    con = db.conectar()
    db.crear_tablas(con)
    print("Escaparate arrancado")
    tot = {"procesados": 0, "errores": 0}
    ciclos = 0
    try:
        while True:
            inicio = time.time()
            ciclos += 1
            try:
                nuevos, n_bd, err = ciclo(con, inicio)
                tot["procesados"] += sum(nuevos.values())
                tot["errores"] += err
                if any(nuevos.values()):
                    print(f"[{ahora()}] nuevos: cto {nuevos['cto']}, perfil {nuevos['perfil']}, boost {nuevos['boost']} "
                          f"(en_bd {n_bd})")
                elif ciclos % ESCAPARATE_RESUMEN_CADA == 0 or una_vez:
                    print(f"[{ahora()}] sin novedades | ciclos {ciclos} | guardados {tot['procesados']} | errores {tot['errores']}")
            except sqlite3.OperationalError as e:
                db.deshacer(con)
                tot["errores"] += 1
                print(f"[{ahora()}] Aviso: error de BD ({e})")
            try:
                db.guardar_estado_agente(con, "escaparate", time.time(), "ok", tot["procesados"], tot["errores"])
            except sqlite3.OperationalError as e:
                db.deshacer(con)
                print(f"[{ahora()}] Aviso: error de BD al guardar estado ({e})")
            if una_vez:
                con.close()
                return
            time.sleep(max(1, ESCAPARATE_PERIODO_S - (time.time() - inicio)))
    except KeyboardInterrupt:
        db.guardar_estado_agente(con, "escaparate", time.time(), "parado", tot["procesados"], tot["errores"])
        con.close()
        print(f"\n[{ahora()}] Escaparate detenido con Ctrl+C.")
    except Exception:
        db.guardar_estado_agente(con, "escaparate", time.time(), "error", tot["procesados"], tot["errores"])
        raise


if __name__ == "__main__":
    main()
