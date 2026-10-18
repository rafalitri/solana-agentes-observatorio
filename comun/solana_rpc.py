import json
import struct
import urllib.error
import urllib.request

from comun.config import RPC_URL

CURVA = struct.Struct("<8x5Q?")  # discriminador + 5 u64 little-endian + bool complete


def get_multiple_accounts(direcciones):
    # bloqueante: llamar con asyncio.to_thread. Devuelve {direccion: datos base64 o None si no existe}
    cuentas = {}
    for i in range(0, len(direcciones), 100):  # el RPC admite como máximo 100 cuentas por llamada
        lote = direcciones[i:i + 100]
        cuerpo = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "getMultipleAccounts",
            "params": [lote, {"encoding": "base64"}],
        }).encode()
        peticion = urllib.request.Request(RPC_URL, cuerpo, {"Content-Type": "application/json"})
        with urllib.request.urlopen(peticion, timeout=15) as r:
            respuesta = json.load(r)
        if "error" in respuesta:
            raise RuntimeError(f"RPC: {respuesta['error']}")
        for direccion, cuenta in zip(lote, respuesta["result"]["value"]):
            cuentas[direccion] = cuenta["data"][0] if cuenta else None
    return cuentas


class RpcLimite(Exception):
    """HTTP 429: demasiadas peticiones al RPC público."""


def _rpc(metodo, params, url=RPC_URL):
    # bloqueante. Devuelve "result"; 429 -> RpcLimite; cualquier otro fallo, y "error" en el JSON (aunque el HTTP
    # sea 200), -> RuntimeError. La url puede llevar una clave: nunca aparece en los mensajes ("<rpc>").
    cuerpo = json.dumps({"jsonrpc": "2.0", "id": 1, "method": metodo, "params": params}).encode()
    peticion = urllib.request.Request(url, cuerpo, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(peticion, timeout=15) as r:
            respuesta = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RpcLimite() from None
        raise RuntimeError(f"{type(e).__name__}: {e}".replace(url, "<rpc>")) from None
    except (OSError, ValueError) as e:
        raise RuntimeError(f"{type(e).__name__}: {e}".replace(url, "<rpc>")) from None
    if "error" in respuesta:
        raise RuntimeError(f"RPC: {respuesta['error']}".replace(url, "<rpc>"))
    return respuesta["result"]


def tokens_mayores(mint, url=RPC_URL):
    # las 20 mayores cuentas de token: ([(direccion de la cuenta, cantidad cruda int)], decimales)
    valor = _rpc("getTokenLargestAccounts", [mint, {"commitment": "confirmed"}], url)["value"]
    if not valor:
        raise RuntimeError("sin cuentas de token")
    return [(v["address"], int(v["amount"])) for v in valor], valor[0]["decimals"]


def duenos_de_cuentas(direcciones, url=RPC_URL):
    # owner (base58) de cada cuenta de token, en el mismo orden. jsonParsed evita decodificar base58 a mano.
    valor = _rpc("getMultipleAccounts", [direcciones, {"encoding": "jsonParsed", "commitment": "confirmed"}],
                 url)["value"]
    duenos = []
    for direccion, cuenta in zip(direcciones, valor):
        try:
            duenos.append(cuenta["data"]["parsed"]["info"]["owner"])
        except (KeyError, TypeError):
            raise RuntimeError(f"cuenta sin parsear: {direccion}") from None
    return duenos


def leer_curva(datos):
    v_tokens, v_sol, real_tokens, real_sol, supply, complete = CURVA.unpack_from(datos)
    return {
        "v_tokens": v_tokens, "v_sol": v_sol, "real_tokens": real_tokens, "real_sol": real_sol,
        "supply": supply, "complete": complete,
        # tokens con 6 decimales, SOL con 9; sin reservas (curva vaciada) no hay precio
        "market_cap_sol": (v_sol / 1e9) / (v_tokens / 1e6) * (supply / 1e6) if v_tokens else None,
    }
