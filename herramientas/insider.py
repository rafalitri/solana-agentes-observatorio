"""Herramienta por lotes (NO es agente): para cada token del universo v0 busca los compradores del slot de creación
y marca como insider al que recibió SOL directamente del creador (un salto) en las últimas transacciones de este. Escribe en la tabla insider (Helius)."""
import argparse
import json
import re
import sqlite3
import time
from collections import Counter

from comun import db
from comun.config import (HERMANOS_PREVIAS, INSIDER_MIN_SOL, INSIDER_429_REINTENTOS, INSIDER_ESPERA_S, INSIDER_INTENTOS,
                          INSIDER_MAX_FALLOS_SEGUIDOS, INSIDER_MAX_PETICIONES, INSIDER_PETICIONES_S, INSIDER_PREVIAS,
                          SEGUIMIENTO_S, TENEDORES_429_ESPERA_S, TENEDORES_ENV_CLAVE)
from comun.entorno import leer_env
from comun.solana_rpc import RpcLimite, _rpc
from herramientas.resultados import parse_desde

LOTE = 50
PROGRESO_CADA = 100
CODIGOS_FIJOS = {-32600, -32601, -32602, -32009, -32007, -32015}  # petición inválida / slot saltado / versión tx
TRANSFERENCIAS = ("transfer", "transferWithSeed")
OPCIONES_TX = {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1, "commitment": "finalized"}
OPCIONES_BLOQUE = {"transactionDetails": "accounts", "rewards": False, "maxSupportedTransactionVersion": 1,
                   "commitment": "finalized"}


# ---------- funciones puras ----------

def pagador(tx):
    """Primera cuenta firmante (la que paga) de una transacción de getBlock accounts; None si no consta."""
    for k in tx["transaction"].get("accountKeys", []):
        if isinstance(k, dict) and k.get("signer"):
            return k.get("pubkey")
    return None


def compradores_slot0(bloque, mint, excluidos, con_pagador=False):
    """bloque: resultado de getBlock (transactionDetails=accounts). Devuelve [(owner, firma)]: owners (campo owner de los
    token balances) cuyo saldo del mint sube en una transacción OK del bloque, sin los `excluidos` (creador, curva).
    La firma es la de la primera compra de ese owner en el bloque. con_pagador: devuelve (owner, firma, pagador)."""
    vistos = {}
    for tx in (bloque or {}).get("transactions", []):
        meta = tx.get("meta") or {}
        if meta.get("err") is not None:
            continue
        saldo = Counter()  # owner -> variación del mint en la transacción
        for clave, signo in (("postTokenBalances", 1), ("preTokenBalances", -1)):
            for b in meta.get(clave) or []:
                if b.get("mint") == mint and b.get("owner"):
                    saldo[b["owner"]] += signo * int(b["uiTokenAmount"]["amount"])
        firmas = tx["transaction"]["signatures"]
        for owner, delta in saldo.items():
            if delta > 0 and owner not in excluidos and owner not in vistos:
                vistos[owner] = (firmas[0], pagador(tx))
    return [(o, *v) if con_pagador else (o, v[0]) for o, v in vistos.items()]


def transferencias_sol(tx):
    """tx: getTransaction jsonParsed. [(origen, destino, lamports)] de las transferencias de SOL del programa System
    (transfer o transferWithSeed), en las instrucciones de arriba y en innerInstructions, de al menos INSIDER_MIN_SOL
    (el polvo no cuenta para insider, madre ni hermano). Transacción fallida o None: []."""
    if not tx or (tx.get("meta") or {}).get("err") is not None:
        return []
    instrucciones = list(tx["transaction"]["message"]["instructions"])
    for grupo in (tx.get("meta") or {}).get("innerInstructions") or []:
        instrucciones += grupo.get("instructions", [])
    salida = []
    for ins in instrucciones:
        parsed = ins.get("parsed")
        if ins.get("program") != "system" or not isinstance(parsed, dict) or parsed.get("type") not in TRANSFERENCIAS:
            continue
        info = parsed.get("info") or {}
        lamports = info.get("lamports")
        if lamports is not None and lamports >= round(INSIDER_MIN_SOL * 1e9):
            salida.append((info.get("source"), info.get("destination"), lamports))
    return salida


def transferencias_del_creador(tx, creador):
    """[(destino, lamports)] de las transferencias cuyo origen es `creador`."""
    return [(d, lam) for o, d, lam in transferencias_sol(tx) if o == creador]


def es_transferencia_del_creador(tx, creador, comprador):
    """True si el creador transfiere SOL a `comprador` en esa transacción (arriba o en innerInstructions)."""
    return any(d == comprador for d, _ in transferencias_del_creador(tx, creador))


def elegir_madre(recibidas, creador):
    """recibidas: [(origen, lamports)] de las 10 previas del creador, de la más reciente a la más antigua. Madre = la
    cartera (distinta del creador) con más SOL enviado al creador en total; empate -> la que envió más recientemente."""
    total, orden = {}, {}
    for i, (origen, lam) in enumerate(recibidas):
        if origen and origen != creador:
            total[origen] = total.get(origen, 0) + (lam or 0)
            orden.setdefault(origen, i)
    return min(total, key=lambda o: (-total[o], orden[o])) if total else None


def marcar_hermanos(compradores, destinos, excluidos):
    """compradores: [(owner, firma, pagador)]. Hermano = owner O pagador (no excluido) que está en `destinos`
    (el caller incluye a la madre: si compró, cuenta).
    Devuelve el conjunto de owners hermanos."""
    return {o for o, _, f in compradores
            if (o in destinos and o not in excluidos) or (f in destinos and f not in excluidos)}


def es_determinista(mensaje):
    """True si el error del RPC no se arregla reintentando (parámetros inválidos, versión no soportada, slot saltado...)."""
    m = re.search(r"'code': (-?\d+)", mensaje)
    return bool(m) and int(m.group(1)) in CODIGOS_FIJOS


# ---------- Helius ----------

class Cliente:
    """Ritmo máximo INSIDER_PETICIONES_S, reintentos y contador por método. La URL lleva clave: nunca se imprime."""

    def __init__(self, url, sueño=time.sleep, reloj=time.monotonic):
        self.url, self.sueño, self.reloj = url, sueño, reloj
        self.por_metodo = Counter()
        self.ultimo = 0.0

    @property
    def total(self):
        return sum(self.por_metodo.values())

    def llamar(self, metodo, params):
        intentos = limites = 0
        while True:
            espera = self.ultimo + 1 / INSIDER_PETICIONES_S - self.reloj()
            if espera > 0:
                self.sueño(espera)
            self.ultimo = self.reloj()
            self.por_metodo[metodo] += 1
            try:
                return _rpc(metodo, params, self.url)
            except RpcLimite:
                limites += 1
                if limites > INSIDER_429_REINTENTOS:
                    raise RuntimeError("429 repetido") from None
                self.sueño(TENEDORES_429_ESPERA_S)
            except RuntimeError as e:
                intentos += 1
                if es_determinista(str(e)) or intentos >= INSIDER_INTENTOS:
                    raise
                self.sueño(INSIDER_ESPERA_S)


def procesar_token(cl, mint, firma, creador, curva):
    """-> dict(slot, compradores [(owner, es_insider, es_hermano)], madre, error_hermanos, kb). Lanza si falla el insider.
    Insider = comprador del slot 0 (owner) que recibió SOL del creador en alguna de sus INSIDER_PREVIAS transacciones
    anteriores a la creación. Hermano = comprador (owner o pagador) que recibió SOL de la madre (la cartera que más SOL
    envió al creador en esas mismas transacciones) en sus HERMANOS_PREVIAS últimas, o la propia madre si compró. Un fallo solo de hermanos se
    guarda en error_hermanos y no invalida el insider."""
    tx = cl.llamar("getTransaction", [firma, OPCIONES_TX])
    if tx is None:
        raise RuntimeError("tx_creacion_no_encontrada")
    slot = tx["slot"]
    bloque = cl.llamar("getBlock", [slot, OPCIONES_BLOQUE])
    if bloque is None:
        raise RuntimeError("bloque_no_disponible")
    kb = len(json.dumps(bloque)) / 1024  # aprox.: el JSON reserializado, no los bytes de red
    excluidos = {creador, curva}
    comp = compradores_slot0(bloque, mint, excluidos, con_pagador=True)
    duenos = [o for o, _, _ in comp]
    insiders, recibidas, madre, hermanos, error_h = set(), [], None, set(), None
    if duenos:  # sin compradores no hace falta mirar al creador
        previas = cl.llamar("getSignaturesForAddress", [creador, {"before": firma, "limit": INSIDER_PREVIAS,
                                                                  "commitment": "finalized"}])
        for p in previas:
            if p.get("err") is None:
                t = cl.llamar("getTransaction", [p["signature"], OPCIONES_TX])
                insiders |= {o for o in duenos if es_transferencia_del_creador(t, creador, o)}
                recibidas += [(o, lam) for o, d, lam in transferencias_sol(t) if d == creador]
        madre = elegir_madre(recibidas, creador)
    pruebas = []
    if madre:
        try:
            recibido = {}  # destino -> (firma del envío, lamports) de la madre; la propia madre cuenta como hermana
            hermanos = marcar_hermanos(comp, {madre}, excluidos)
            if len(hermanos) < len(duenos):
                for p in cl.llamar("getSignaturesForAddress", [madre, {"before": firma, "limit": HERMANOS_PREVIAS,
                                                                        "commitment": "finalized"}]):
                    if p.get("err") is None:
                        t = cl.llamar("getTransaction", [p["signature"], OPCIONES_TX])
                        for o, d, lam in transferencias_sol(t):
                            if o == madre:
                                recibido.setdefault(d, (p["signature"], lam))
                        hermanos = marcar_hermanos(comp, set(recibido) | {madre}, excluidos)
                        if len(hermanos) == len(duenos):
                            break  # ya son todos
            for o, firma_compra, pag in comp:
                if o in hermanos:
                    via = next((x for x in (o, pag) if x in recibido), madre)  # madre = ella misma compró
                    pruebas.append({"hermano": o, "via": via, "firma_envio": recibido.get(via, (None, None))[0],
                                    "lamports": recibido.get(via, (None, None))[1], "firma_compra": firma_compra,
                                    "pagador": pag})
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as e:
            hermanos, pruebas, error_h = set(), [], f"{type(e).__name__}: {e}"[:200]
    return {"slot": slot, "compradores": [(o, o in insiders, o in hermanos) for o in duenos], "madre": madre,
            "error_hermanos": error_h, "pruebas": pruebas, "kb": kb}


def diagnostico(cl, pendientes, max_peticiones):
    c = Counter()
    for mint, firma, creador, curva in pendientes:
        if cl.total >= max_peticiones:
            print(f"PARADO: tope de {max_peticiones} peticiones")
            break
        antes = Counter(cl.por_metodo)
        try:
            r = procesar_token(cl, mint, firma, creador, curva)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as e:
            c["fallos"] += 1
            print(f"{mint[:8]} error={type(e).__name__}: {e}"[:200])
            continue
        ins, her = sum(i for _, i, _ in r["compradores"]), sum(h for _, _, h in r["compradores"])
        c["tokens"] += 1
        c["con_compradores"] += bool(r["compradores"])
        c["con_madre"] += r["madre"] is not None
        c["con_insider"] += ins >= 1
        c["con_hermano"] += her >= 1
        c["error_hermanos"] += r["error_hermanos"] is not None
        print(f"{mint[:8]} slot={r['slot']} compradores={len(r['compradores'])} insiders={ins} "
              f"madre={(r['madre'] or '-')[:6]} hermanos={her} error_hermanos={r['error_hermanos']} "
              f"peticiones={dict(cl.por_metodo - antes)}")
        for q in r["pruebas"]:  # direcciones completas, para comprobarlas en Solscan
            print(f"  HERMANO mint={mint} creador={creador} madre={r['madre']} hermano={q['hermano']} "
                  f"recibio_en={q['via']} firma_envio={q['firma_envio']} "
                  f"importe_SOL={(q['lamports'] or 0) / 1e9:.6f} firma_compra_slot0={q['firma_compra']} pagador={q['pagador']}")
    print(f"DIAGNOSTICO: {dict(c)} | peticiones {cl.total} {dict(cl.por_metodo)}")


def fila_insider(mint, r, error=None):
    if error is not None:
        return (mint, time.time(), None, None, None, error, None, None, None, None)
    raw = json.dumps([{"o": o, "ins": int(i), "her": int(h)} for o, i, h in r["compradores"]], separators=(",", ":"))
    n_her = None if r["error_hermanos"] else sum(h for _, _, h in r["compradores"])
    return (mint, time.time(), r["slot"], len(r["compradores"]), sum(i for _, i, _ in r["compradores"]), None, raw,
            r["madre"], n_her, r["error_hermanos"])


def procesar(con, cl, pendientes, max_peticiones, seco=False):
    c = {"hechos": 0, "ok": 0, "con_insider": 0, "fallos": 0, "parado": False}
    buffer, seguidos = [], 0

    def volcar():
        if not buffer or seco:
            buffer.clear()
            return
        try:
            db.guardar_insider(con, buffer)
            buffer.clear()
        except sqlite3.OperationalError as e:
            db.deshacer(con)
            print(f"Aviso: no se pudo escribir ({e}); se reintenta en el próximo lote")

    try:
        for mint, firma, creador, curva in pendientes:
            if cl.total >= max_peticiones:
                c["parado"] = True
                print(f"PARADO: tope de {max_peticiones} peticiones; el resto queda pendiente")
                break
            antes, t0 = Counter(cl.por_metodo), time.time()
            try:
                r = procesar_token(cl, mint, firma, creador, curva)
                fila, seguidos = fila_insider(mint, r), 0
                c["ok"] += 1
                c["con_insider"] += fila[4] >= 1
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as e:
                r, fila = None, fila_insider(mint, None, f"{type(e).__name__}: {e}"[:200])
                c["fallos"] += 1
                seguidos += 1
            buffer.append(fila)
            c["hechos"] += 1
            if seco:
                gastado = dict(cl.por_metodo - antes)
                print(f"{mint[:8]} slot={fila[2]} compradores={fila[3]} insiders={fila[4]} error={fila[5]} "
                      f"peticiones={gastado} bloque_KB={r['kb']:.0f}" if r else
                      f"{mint[:8]} error={fila[5]} peticiones={gastado}", f"| {time.time() - t0:.1f} s")
            if len(buffer) >= LOTE:
                volcar()
            if c["hechos"] % PROGRESO_CADA == 0:
                print(f"Hechos {c['hechos']} de {len(pendientes)} | ok {c['ok']} | con_insider {c['con_insider']} "
                      f"| fallos {c['fallos']} | peticiones {cl.total}")
            if seguidos >= INSIDER_MAX_FALLOS_SEGUIDOS:
                c["parado"] = True
                print(f"PARADO: {INSIDER_MAX_FALLOS_SEGUIDOS} fallos seguidos (posible límite o caída de Helius)")
                break
    except KeyboardInterrupt:
        print("Interrumpido: se guarda lo hecho.")
    volcar()
    return c


def main():
    ap = argparse.ArgumentParser(description="Compradores del slot de creación y insiders (escribe en la tabla insider)")
    ap.add_argument("--desde", required=True, help='hora local "YYYY-MM-DD HH:MM"')
    ap.add_argument("--hasta", help='hora local "YYYY-MM-DD HH:MM" (por defecto, desde + 24 h)')
    ap.add_argument("--reintentar", action="store_true", help="repite también las filas con error")
    ap.add_argument("--limite", type=int, help="máximo de tokens a procesar (prueba de humo)")
    ap.add_argument("--seco", action="store_true", help="no escribe en la BD; imprime el detalle por token")
    ap.add_argument("--diagnostico", action="store_true", help="solo con --seco: cruza carteras del creador con compradores")
    ap.add_argument("--max-peticiones", type=int, default=INSIDER_MAX_PETICIONES)
    a = ap.parse_args()
    try:
        desde = parse_desde(a.desde)
        hasta_usuario = parse_desde(a.hasta) if a.hasta else desde + 24 * 3600
    except ValueError:
        ap.error('--desde/--hasta deben tener el formato "YYYY-MM-DD HH:MM"')
    if a.diagnostico and not a.seco:
        ap.error("--diagnostico solo se usa junto a --seco")
    hasta = min(hasta_usuario, time.time() - SEGUIMIENTO_S)
    url = leer_env(TENEDORES_ENV_CLAVE)
    if not url:  # nunca se usa el RPC público de respaldo
        raise SystemExit(f"Falta {TENEDORES_ENV_CLAVE} en .env")
    if a.seco:  # solo lectura de verdad: ni crea la tabla
        con = sqlite3.connect(f"file:{db.RUTA_BD}?mode=ro", uri=True)
    else:
        con = db.conectar()
        db.crear_tablas(con)
    cl = Cliente(url)
    try:
        pendientes = db.pendientes_insider(con, desde, hasta, a.reintentar)[:a.limite]
        print(f"Pendientes: {len(pendientes)}")
        if a.diagnostico:
            return diagnostico(cl, pendientes, a.max_peticiones)
        c = procesar(con, cl, pendientes, a.max_peticiones, a.seco)
    finally:
        con.close()
    print(f"RESUMEN: hechos {c['hechos']} de {len(pendientes)} | ok {c['ok']} | con_insider {c['con_insider']} "
          f"| fallos {c['fallos']} | peticiones {cl.total} {dict(cl.por_metodo)}" + (" | PARADO" if c["parado"] else ""))


if __name__ == "__main__":
    main()
