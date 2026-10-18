"""Simulador de salidas: funciones puras (sin BD ni red). v_sol/real_sol en lamports."""

LAMPORTS = 1e9


def comprar(v_sol, v_tokens, sol_entrada, comision, desliz_extra):
    """Devuelve (tokens_recibidos, sol_neto_en_curva). La comisión se resta antes de la curva."""
    sol_neto = sol_entrada * (1 - comision)
    tokens = v_tokens - (v_sol * v_tokens) / (v_sol + sol_neto)
    return tokens * (1 - desliz_extra), sol_neto


def vender(v_sol, v_tokens, real_sol, tokens, comision, desliz_extra):
    """Devuelve el SOL recibido. No se puede sacar más SOL del que hay (real_sol)."""
    bruto = v_sol - (v_sol * v_tokens) / (v_tokens + tokens)
    bruto = min(bruto, real_sol)
    return bruto * (1 - comision) * (1 - desliz_extra)


def simular_token(fotos, tam_sol, objetivo, stop, retraso, desliz_extra, comision, fee_red):
    """Compra en la primera foto válida y vende según objetivo/stop/tiempo/graduación.

    objetivo es un múltiplo (2.0 = +100 %); stop una fracción (0.3 = -30 %); None = sin límite.
    Las fotos con v_sol nulo se saltan. Devuelve None si no hay entrada posible.
    """
    validas = [f for f in fotos if f["v_sol"] is not None]
    if not validas or validas[0]["complete"]:
        return None
    ie = retraso  # la decisión de entrar se toma en la 1.ª foto; se compra en la efectiva
    if ie >= len(validas) or validas[ie]["complete"]:
        return None
    entrada = validas[ie]
    entrada_lam = tam_sol * LAMPORTS
    tokens, sol_neto = comprar(entrada["v_sol"], entrada["v_tokens"], entrada_lam, comision, desliz_extra)

    def retorno(f):
        # las fotos no incluyen nuestra compra: la sumamos a la curva observada
        recibido = vender(f["v_sol"] + sol_neto, f["v_tokens"] - tokens, f["real_sol"] + sol_neto,
                          tokens, comision, desliz_extra)
        return (recibido - entrada_lam) / entrada_lam - 2 * fee_red / tam_sol

    def salida(i, motivo):
        if validas[i]["complete"]:  # graduada: se vende a la última foto sin graduar
            i, motivo = i - 1, "graduacion"
        return {"retorno": retorno(validas[i]), "motivo_salida": motivo,
                "segundos_entrada": entrada["segundos"], "segundos_salida": validas[i]["segundos"]}

    for j in range(ie + 1, len(validas)):
        f = validas[j]
        if f["complete"]:
            return salida(j, "graduacion")
        r = retorno(f)
        if stop is not None and r <= -stop:
            motivo = "stop"
        elif objetivo is not None and r >= objetivo - 1:
            motivo = "objetivo"
        else:
            continue
        k = j + retraso
        return salida(k, motivo) if k < len(validas) else salida(j, "tiempo")
    return salida(len(validas) - 1, "tiempo")


def simular_escalera(fotos, tam_sol, comision, fee_red, desliz_extra=0):
    """Compra en fotos[0] (el retraso lo decide quien llama) y vende en escalera.

    Escala de precio m = 1 + retorno(f) de simular_token (valor neto de vender todo). Antes de x2: stop m <= 0.7.
    En la 1.ª foto con m >= 2 vende la mitad; el resto sale con stop m <= 0.7 * máximo de m, o al final/graduación.
    Fee de red por cada transacción. La curva de la 2.ª venta = foto + nuestra compra - la 1.ª venta
    (devolvimos los tokens y sacamos los SOL brutos).
    """
    validas = [f for f in fotos if f["v_sol"] is not None]
    if not validas or validas[0]["complete"]:
        return None
    entrada = validas[0]
    entrada_lam = tam_sol * LAMPORTS
    tokens, sol_neto = comprar(entrada["v_sol"], entrada["v_tokens"], entrada_lam, comision, desliz_extra)

    vendido = [0.0, 0.0]  # (tokens devueltos, SOL brutos sacados) en la 1.ª venta

    def valor(f, q):
        return vender(f["v_sol"] + sol_neto - vendido[1], f["v_tokens"] - tokens + vendido[0],
                      f["real_sol"] + sol_neto - vendido[1], q, comision, desliz_extra)

    def m(f):
        return 1 + (valor(f, tokens) - entrada_lam) / entrada_lam - 2 * fee_red / tam_sol

    def fin(j, motivo, cobrado, n_ventas, quedan, mitad):
        if validas[j]["complete"]:  # graduada: se vende a la última foto sin graduar
            j, motivo = j - 1, "graduacion"
        cobrado += valor(validas[j], quedan)
        return {"retorno": (cobrado - entrada_lam) / entrada_lam - (1 + n_ventas) * fee_red / tam_sol,
                "motivo_salida": motivo, "vendio_mitad": mitad,
                "segundos_entrada": entrada["segundos"], "segundos_salida": validas[j]["segundos"]}

    pico, cobrado, quedan = m(entrada), 0.0, tokens
    for j in range(1, len(validas)):
        f = validas[j]
        if f["complete"]:
            return fin(j, "graduacion", cobrado, 1 + (quedan < tokens), quedan, quedan < tokens)
        mj = m(f)
        if quedan == tokens:  # aún no vendimos la mitad
            if mj <= 0.7:
                return fin(j, "stop", 0.0, 1, tokens, False)
            if mj >= 2:
                cobrado, quedan = valor(f, tokens / 2), tokens / 2
                bruto = vender(f["v_sol"] + sol_neto, f["v_tokens"] - tokens, f["real_sol"] + sol_neto,
                               quedan, 0, 0)
                vendido[:] = [quedan, bruto]
            pico = max(pico, mj)
        else:
            pico = max(pico, mj)
            if mj <= 0.7 * pico:
                return fin(j, "stop_resto", cobrado, 2, quedan, True)
    return fin(len(validas) - 1, "tiempo", cobrado, 1 + (quedan < tokens), quedan, quedan < tokens)
