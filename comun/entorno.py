import os


def leer_env(clave, ruta=".env"):
    # la variable de entorno manda; si no, línea "clave=valor" del archivo (ignora vacías y #). None si no hay
    if os.environ.get(clave):
        return os.environ[clave]
    try:
        with open(ruta, encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if not linea or linea.startswith("#") or "=" not in linea:
                    continue
                k, _, v = linea.partition("=")
                if k.strip() == clave:
                    return v.strip() or None
    except OSError:
        pass
    return None
