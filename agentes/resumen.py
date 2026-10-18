"""Resumen por token (funciones puras, sin BD ni red)."""
from comun.config import SIM_COMISION, SIM_FEE_RED_SOL, SIM_TAM_SOL, UMBRALES_SUSTO, VERSION_RESUMEN
from herramientas.resultados import curva_atipica, grad_inst
from herramientas.simulador import simular_token

OBJETIVO, STOP = 1.2, 0.3  # salida "obj"; la salida "fin" no tiene ni objetivo ni stop
_SUFIJOS = ("r0_obj", "r1_obj", "r0_fin", "r1_fin")
_SUSTOS = tuple(f"{round(u * 100)}" for u in UMBRALES_SUSTO)

CLAVES_RESUMEN = (
    "version_resumen", "n_fotos", "seg_primera", "hueco_max_s", "grad_inst", "curva_atipica",
    "graduado", "seg_graduado", "mult_max", "seg_max", "mult_min", "mult_final", "caida_max",
    *(f"ret_{s}" for s in _SUFIJOS),
    *(c for u in _SUSTOS for c in (f"seg_susto{u}", *(f"ret_s{u}_{s}" for s in _SUFIJOS))),
)


def _retornos(fotos):
    """(r0_obj, r1_obj, r0_fin, r1_fin) en el caso optimista (desliz 0); None si no hay retorno."""
    out = []
    for objetivo, stop in ((OBJETIVO, STOP), (None, None)):
        for retraso in (0, 1):
            r = simular_token(fotos, SIM_TAM_SOL, objetivo, stop, retraso, 0.0, SIM_COMISION, SIM_FEE_RED_SOL)
            out.append(None if r is None else r["retorno"])
    return out


def resumir_token(fotos, mayhem):
    res = dict.fromkeys(CLAVES_RESUMEN)
    res["version_resumen"] = VERSION_RESUMEN
    res["n_fotos"] = len(fotos)
    if not fotos:
        return res
    segs = [f["segundos"] for f in fotos]
    res["seg_primera"] = segs[0]
    if len(segs) > 1:
        res["hueco_max_s"] = max(b - a for a, b in zip(segs, segs[1:]))
    # grad_inst solo mira complete (posición 2), por eso el market cap va a None
    res["grad_inst"] = grad_inst([(f["segundos"], None, f["complete"]) for f in fotos])
    res["curva_atipica"] = curva_atipica(mayhem, [(f["v_sol"], f["real_sol"], f["v_tokens"]) for f in fotos])
    grad = [f["segundos"] for f in fotos if f["complete"]]
    res["graduado"] = bool(grad)
    res["seg_graduado"] = grad[0] if grad else None
    if res["grad_inst"]:
        return res

    # (índice en fotos, precio) solo de fotos sin graduar y con tokens
    precios = [(i, f["v_sol"] / f["v_tokens"]) for i, f in enumerate(fotos)
               if not f["complete"] and f["v_sol"] is not None and f["v_tokens"]]
    if precios:
        p0 = precios[0][1]
        im = max(range(len(precios)), key=lambda k: (precios[k][1], -k))  # 1.ª vez que se alcanza
        res["mult_max"] = precios[im][1] / p0
        res["seg_max"] = segs[precios[im][0]]
        res["mult_min"] = min(p for _, p in precios) / p0
        res["mult_final"] = precios[-1][1] / p0
        maximo, caida = precios[0][1], 0.0
        for _, p in precios:
            maximo = max(maximo, p)
            caida = max(caida, 1 - p / maximo)
        res["caida_max"] = caida
    res.update(zip((f"ret_{s}" for s in _SUFIJOS), _retornos(fotos)))

    for umbral, u in zip(UMBRALES_SUSTO, _SUSTOS):
        maximo = None
        for i, p in precios:
            if maximo is not None and p <= (1 - umbral) * maximo:
                res[f"seg_susto{u}"] = segs[i]
                res.update(zip((f"ret_s{u}_{s}" for s in _SUFIJOS), _retornos(fotos[i:])))
                break
            maximo = p if maximo is None else max(maximo, p)
    return res
