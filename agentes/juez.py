import argparse
import sqlite3
import time

from agentes.analista import ahora
from comun import db
from comun.config import (JUEZ_ESPERA_S, JUEZ_LOTE, JUEZ_MAX_S, JUEZ_PLAZO_S, JUEZ_UMBRAL_TOP10, JUEZ_VERSION,
                          RUTA_BD)
from herramientas.arana_informe import contar_previos, monederos_activos

BANDERAS = ("b1", "b2", "b3", "b4", "b5", "b6")


# ---------- funciones puras ----------

def listo(d, ahora_ts, plazo=JUEZ_PLAZO_S):
    """Se juzga con tenedores Y analisis_v2, o con lo que haya cuando pasa el plazo desde la decisión."""
    return bool((d["hay_ten"] and d["hay_a2"]) or ahora_ts >= d["ts_decision"] + plazo)


def calcular_banderas(d, n_rep, umbral_top10=JUEZ_UMBRAL_TOP10):
    """d: fila de db.pendientes_juez; n_rep: monederos repetidos del token (None si no hay dato). 1 / 0 / None."""
    sin_ten = not d["hay_ten"] or d["ten_error"] is not None
    cp, ib, top10 = d["creador_pct"], d["initial_buy"], d["top10_pct"]
    b1 = b2 = b3 = b4 = None
    if not sin_ten:
        if cp is not None:
            b2 = int(cp > 5)
            if ib is not None:
                b1 = int(cp < 0.5 * (ib / 1e9 * 100))
        if umbral_top10 is not None and top10 is not None:
            b3 = int(top10 >= umbral_top10)
        if n_rep is not None:
            b4 = int(n_rep >= 1)
    redes = (d["twitter"], d["telegram"], d["web"])
    if not d["hay_a2"]:
        b5 = None
    elif 1 in redes:
        b5 = 0
    else:
        b5 = 1 if all(r == 0 for r in redes) else None
    b6 = int(bool(d["previo"])) if d["creador"] is not None else None
    return dict(zip(BANDERAS, (b1, b2, b3, b4, b5, b6)))


def calcular_veredicto(banderas, umbral_top10=JUEZ_UMBRAL_TOP10):
    """n_banderas (las que valen 1), sin_dato ("b3,b5") y veredicto: descartar / sin_dato / vigilar.
    b3 None solo es dato que falta si el umbral está fijado."""
    n = sum(1 for b in BANDERAS if banderas[b] == 1)
    faltan = [b for b in BANDERAS if banderas[b] is None]
    if n >= 1:
        v = "descartar"
    elif any(b != "b3" or umbral_top10 is not None for b in faltan):
        v = "sin_dato"
    else:
        v = "vigilar"
    return {"n_banderas": n, "sin_dato": ",".join(faltan), "veredicto": v}


def juicio(d, previos, ahora_ts, umbral_top10=JUEZ_UMBRAL_TOP10):
    bs = calcular_banderas(d, previos.get(d["mint"]), umbral_top10)
    return {"mint": d["mint"], "momento": 1, "ts_juicio": ahora_ts, "ts_decision": d["ts_decision"],
            "version_juez": JUEZ_VERSION, **bs, "top10_pct": d["top10_pct"], **calcular_veredicto(bs, umbral_top10)}


# ---------- recuento de la araña en memoria ----------

class Recuento:
    """items = [(mint, ts_consulta, monederos activos)] de TODAS las consultas de tenedores (como el informe).
    shortcut: crece sin límite durante la vida del proceso y no ve filas con ts_consulta menor que la última
    leída; con miles de tokens al día basta, revisar si pasa de decenas de miles."""

    def __init__(self):
        self.items, self.mints, self.ultimo = [], set(), 0.0

    def actualizar(self, con):
        # >= (no >): dos filas con el mismo ts_consulta no se pierden; las ya vistas se descartan por mint
        for mint, ts, raw, curva, creador in db.tenedores_con_raw(con, self.ultimo):
            self.ultimo = max(self.ultimo, ts)
            if mint in self.mints:
                continue
            self.mints.add(mint)
            try:
                self.items.append((mint, ts, monederos_activos(raw, curva, creador)))
            except (ValueError, KeyError, TypeError):
                pass  # raw ilegible: ese token queda sin b4 (NULL)


# ---------- proceso ----------

def juzgar(con, rec, ahora_ts, escribir=True, limite=None, umbral_top10=JUEZ_UMBRAL_TOP10):
    rec.actualizar(con)
    pend = [d for d in db.pendientes_juez(con, ahora_ts - JUEZ_MAX_S) if listo(d, ahora_ts)]
    if limite is not None:
        pend = pend[:limite]
    previos = contar_previos(rec.items) if pend else {}
    filas = [juicio(d, previos, ahora_ts, umbral_top10) for d in pend]
    if escribir:
        for i in range(0, len(filas), JUEZ_LOTE):
            db.guardar_juicios(con, filas[i:i + JUEZ_LOTE])
    return filas


def linea(f):
    bs = " ".join(f"{b}={'-' if f[b] is None else f[b]}" for b in BANDERAS)
    return f"{f['mint'][:8]} {bs} n={f['n_banderas']} sin_dato=[{f['sin_dato']}] {f['veredicto']}"


def prueba(n, ruta=RUTA_BD):
    # solo lectura (mode=ro): ni juicios ni latido ni crear tablas
    con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    try:
        filas = juzgar(con, Recuento(), time.time(), escribir=False, limite=n)
    finally:
        con.close()
    for f in filas:
        print(linea(f))
    print(f"[{ahora()}] --prueba: {len(filas)} juicios (no se ha escrito nada)")
    return filas


def guardar_estado(con, estado, tot, c):
    try:
        db.guardar_estado_agente(con, "juez", time.time(), estado, tot["procesados"], tot["errores"])
    except sqlite3.OperationalError as e:
        db.deshacer(con)
        c["bd"] += 1
        tot["errores"] += 1
        print(f"[{ahora()}] Aviso: error de BD al guardar estado ({e})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prueba", type=int, metavar="N", help="juzga hasta N pendientes, los imprime y no escribe nada")
    args = ap.parse_args()
    if args.prueba is not None:
        prueba(args.prueba)
        return
    con = db.conectar()
    db.crear_tablas(con)
    print("Juez arrancado")
    rec = Recuento()
    c = {"juzgados": 0, "vigilar": 0, "descartar": 0, "sin_dato": 0, "bd": 0}  # contadores del último minuto
    tot = {"procesados": 0, "errores": 0}
    proximo_informe = time.time() + 60
    try:
        while True:
            try:
                filas = juzgar(con, rec, time.time())
                c["juzgados"] += len(filas)
                tot["procesados"] += len(filas)
                for f in filas:
                    c[f["veredicto"]] += 1
            except sqlite3.OperationalError as e:
                db.deshacer(con)
                c["bd"] += 1
                tot["errores"] += 1
                print(f"[{ahora()}] Aviso: error de BD ({e})")
            if time.time() >= proximo_informe:
                guardar_estado(con, "ok", tot, c)
                print(f"[{ahora()}] Juzgados ultimo minuto: {c['juzgados']} | vigilar: {c['vigilar']}"
                      f" | descartar: {c['descartar']} | con sin_dato: {c['sin_dato']} | errores bd: {c['bd']}")
                c.update(juzgados=0, vigilar=0, descartar=0, sin_dato=0, bd=0)
                proximo_informe = time.time() + 60
            time.sleep(JUEZ_ESPERA_S)
    except KeyboardInterrupt:
        guardar_estado(con, "parado", tot, c)
        con.close()
        print(f"\n[{ahora()}] Juez detenido con Ctrl+C.")
    except Exception:
        guardar_estado(con, "error", tot, c)
        raise


if __name__ == "__main__":
    main()
