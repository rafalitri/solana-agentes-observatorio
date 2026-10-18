def caducados(seguidos, ahora, duracion):
    # seguidos: dict mint -> ts de inicio; devuelve los mints que ya cumplieron `duracion` segundos
    return [mint for mint, inicio in seguidos.items() if ahora - inicio >= duracion]


def graduados(filas):
    # filas: tuplas de `fotos` (mint en [0], complete en [6]); devuelve los mints ya graduados
    return [f[0] for f in filas if f[6]]


def imprimir_ilegible(intento):
    # intento: nº de lecturas de la curva de este token (1 = la primera); en la 1.ª el RPC aún no tiene la cuenta
    return intento >= 2


def estado_latido(ws_ok, n_seguidos, fotos_minuto):
    # "sin_fotos" va primero: con tokens seguidos y 0 fotos, la tarea de fotos está muerta o colgada
    if n_seguidos > 0 and fotos_minuto == 0:
        return "sin_fotos"
    return "ok" if ws_ok else "sin_pumpportal"


def raw_para(mint, con_foto, raw):
    # el raw solo se guarda en la 1.ª foto de cada token; después, None
    return None if mint in con_foto else raw


def actualizar_con_foto(con_foto, filas, seguidos):
    # filas: las que se guardaron de verdad (una curva ilegible no tiene fila, así que no entra);
    # se descartan los mints que ya no se siguen para que el set no crezca
    con_foto.update(f[0] for f in filas)
    con_foto.intersection_update(seguidos)
