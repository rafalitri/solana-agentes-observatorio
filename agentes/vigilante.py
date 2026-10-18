import sqlite3
import time

from comun.config import AGENTES_VIGILADOS, LATIDO_MAX_S, RUTA_BD, VIGILANCIA_S
from comun.vigilancia import cambios, revisar

try:
    import winsound
except ImportError:
    winsound = None


def _hora():
    return time.strftime("%H:%M:%S")


def _leer():
    con = sqlite3.connect(f"file:{RUTA_BD}?mode=ro", uri=True)
    try:
        return con.execute("SELECT agente, ts, estado FROM agentes_estado").fetchall()
    finally:
        con.close()


def _aviso(texto):
    print(f"{_hora()} AVISO {texto}", flush=True)
    if winsound:
        winsound.MessageBeep()


def main():
    print(f"Vigilante arrancado (agentes: {', '.join(AGENTES_VIGILADOS)})", flush=True)
    anterior = {}
    while True:
        try:
            filas = _leer()
        except sqlite3.OperationalError as e:
            _aviso(f"no se pudo leer la BD: {e}")
        else:
            ahora = time.time()
            actual = revisar(filas, ahora, AGENTES_VIGILADOS, LATIDO_MAX_S)
            ts = {f[0]: f[1] for f in filas}
            for agente, antes, problema in cambios(anterior, actual):
                if problema is None:
                    print(f"{_hora()} OK {agente}: recuperado", flush=True)
                elif problema == "latido_viejo":
                    _aviso(f"{agente}: latido_viejo (hace {ahora - ts[agente]:.0f} s)")
                else:
                    _aviso(f"{agente}: {problema}")
            anterior = actual
        time.sleep(VIGILANCIA_S)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nVigilante detenido.")
