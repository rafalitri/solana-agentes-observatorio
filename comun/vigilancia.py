def revisar(filas, ahora, vigilados, max_s):
    por_agente = {f[0]: f for f in filas}
    resultado = {}
    for agente in vigilados:
        fila = por_agente.get(agente)
        if fila is None:
            resultado[agente] = "sin_latido"
        elif fila[2] == "parado":  # parada limpia: no late, un solo aviso
            resultado[agente] = "parado"
        elif ahora - fila[1] > max_s:
            resultado[agente] = "latido_viejo"
        else:
            resultado[agente] = None if fila[2] == "ok" else fila[2]
    return resultado


def cambios(anterior, actual):
    return [(a, anterior.get(a), p) for a, p in actual.items() if p != anterior.get(a)]
