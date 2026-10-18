"""Pieza 34b: marcador de las mesas en papel (puro, sin BD). Solo para mirar: no decide nada."""
import math
import statistics

from comun.config import SIM_TAM_SOL

MESAS_ORDEN = ("BASE", "JUEZ_VIGILAR", "CERCA_GRADUAR", "SUPERV_T1")  # igual que agentes.contable.MESAS (lo comprueba un test)


def marcador(ops, media_base, n_min, es_base=False):
    """ops = [(ts_entrada, ret)] de una mesa (ret None = sin dato). Etiqueta: RUIDO si n < n_min o si media_base cae
    dentro del IC 95 %; si no POR ENCIMA / POR DEBAJO; REFERENCIA para la base (salvo n < 2: RUIDO)."""
    ops = sorted(ops, key=lambda o: o[0])
    rets = [r for _, r in ops if r is not None]
    n = len(rets)
    out = {"n": n, "n_sin_dato": len(ops) - n, "media": None, "mediana": None, "media_sin_1pct": None, "ic95": None,
           "saldo_sol": sum(rets) * SIM_TAM_SOL, "caida_max_sol": 0.0, "etiqueta": "RUIDO"}
    saldo = pico = 0.0  # el máximo inicial es 0 (antes de la 1.ª operación)
    for r in rets:
        saldo += r * SIM_TAM_SOL
        pico = max(pico, saldo)
        out["caida_max_sol"] = max(out["caida_max_sol"], pico - saldo)
    if n < 2:
        return out
    media = sum(rets) / n
    resto = sorted(rets)[:n - math.ceil(0.01 * n)]  # quita las ceil(1 % de n) mejores
    mitad = 1.96 * statistics.stdev(rets) / math.sqrt(n)
    out.update(media=media, mediana=statistics.median(rets), media_sin_1pct=sum(resto) / len(resto),
               ic95=(media - mitad, media + mitad))
    if es_base:
        out["etiqueta"] = "REFERENCIA"
    elif n < n_min or media_base is None or out["ic95"][0] <= media_base <= out["ic95"][1]:
        out["etiqueta"] = "RUIDO"
    else:
        out["etiqueta"] = "POR ENCIMA" if media > media_base else "POR DEBAJO"
    return out
