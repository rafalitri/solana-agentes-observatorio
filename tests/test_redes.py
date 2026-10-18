import json
import os
import sqlite3
import tempfile
import unittest
import urllib.error

from comun import db
from comun.config import REDES_MAX_FALLOS_SEGUIDOS, REDES_PASARELAS
from herramientas import redes

CID = "https://ipfs.io/ipfs/QmAbc"
URL1 = REDES_PASARELAS[0] + "QmAbc"
URL2 = REDES_PASARELAS[1] + "QmAbc"


def _bajar_de(mapa):
    def bajar(url):
        r = mapa.get(url)
        if r is None:
            raise urllib.error.URLError("caido")
        return r
    return bajar


def _con(test):
    fd, ruta = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    con = sqlite3.connect(ruta)

    def limpiar():
        con.close()
        os.remove(ruta)
    test.addCleanup(limpiar)
    db.crear_tablas(con)
    return con


def _token(con, mint, ts, uri=CID, pasa=1):
    con.execute("INSERT INTO tokens (mint, ts, uri) VALUES (?, ?, ?)", (mint, ts, uri))
    con.execute("INSERT INTO decisiones (mint, ts, pasa) VALUES (?, ?, ?)", (mint, ts, pasa))
    con.commit()


class TestClasificar(unittest.TestCase):
    def test_no_ipfs_no_descarga(self):
        def bajar(url):
            raise AssertionError("no debe descargar")
        for uri in ("https://ejemplo.com/a.json", "", None, "https://x/ipfs/"):
            self.assertEqual(redes.clasificar(uri, bajar), (0, "no_ipfs", None, None, None))

    def test_ok_guarda_los_tres(self):
        meta = json.dumps({"twitter": "https://x.com/a", "telegram": "", "website": "https://a.io"})
        self.assertEqual(redes.clasificar(CID, _bajar_de({URL1: meta})), (1, None, 1, 0, 1))

    def test_segunda_pasarela(self):
        self.assertEqual(redes.clasificar(CID, _bajar_de({URL2: "{}"})), (1, None, 0, 0, 0))

    def test_fallo_de_descarga(self):
        self.assertEqual(redes.clasificar(CID, _bajar_de({})), (0, "descarga", None, None, None))

    def test_solo_pasarelas_redes_nunca_pinata(self):
        pedidas = []

        def bajar(url):
            pedidas.append(url)
            raise urllib.error.URLError("caido")
        redes.clasificar(CID, bajar)
        self.assertEqual(pedidas, [URL1, URL2])
        self.assertFalse(any("pinata" in u for u in pedidas))

    def test_json_invalido(self):
        self.assertEqual(redes.clasificar(CID, _bajar_de({URL1: "<html>"})), (0, "json", None, None, None))


class TestPendientes(unittest.TestCase):
    def test_ventana_pasa_y_ya_hechos(self):
        con = _con(self)
        _token(con, "a", 10.0)
        _token(con, "b", 20.0)
        _token(con, "c", 30.0, pasa=0)
        _token(con, "d", 40.0)
        _token(con, "e", 50.0, uri="")
        _token(con, "f", 5.0)
        db.guardar_redes(con, [("b", 1.0, 1, None, 0, 0, 0)])
        self.assertEqual([m for m, _ in db.pendientes_redes(con, 10.0, 45.0)], ["a", "d"])


class TestProcesar(unittest.TestCase):
    def test_sin_transaccion_abierta_y_contadores(self):
        con = _con(self)
        _token(con, "a", 1.0)
        _token(con, "b", 2.0, uri="https://x.com/j")
        _token(con, "c", 3.0, uri="https://ipfs.io/ipfs/QmZzz")
        estados = []
        meta = json.dumps({"twitter": "t"})

        def bajar(url):
            estados.append(con.in_transaction)  # durante la descarga no puede haber escritura pendiente
            if url.endswith("QmAbc"):
                return meta
            raise urllib.error.URLError("caido")
        c = redes.procesar(con, db.pendientes_redes(con, 0, 99), bajar, pausa=0)
        self.assertEqual(c, {"hechos": 3, "ok": 1, "no_ipfs": 1, "fallos": 1, "parado": False})
        self.assertTrue(estados and not any(estados))
        self.assertFalse(con.in_transaction)  # tras escribir tampoco
        filas = con.execute("SELECT mint, ok, motivo, twitter FROM redes ORDER BY mint").fetchall()
        self.assertEqual(filas, [("a", 1, None, 1), ("b", 0, "no_ipfs", None), ("c", 0, "descarga", None)])

    def test_lotes_sin_transaccion_y_ctrl_c(self):
        con = _con(self)
        for i in range(redes.LOTE + 5):
            _token(con, f"m{i}", float(i))
        pendientes = db.pendientes_redes(con, 0, 999)
        llamadas = []

        def bajar(url):
            llamadas.append(url)
            if len(llamadas) > redes.LOTE + 2:  # 1.er lote ya escrito + 2 en memoria
                raise KeyboardInterrupt
            self.assertFalse(con.in_transaction)
            return "{}"
        redes.procesar(con, pendientes, bajar, pausa=0)
        self.assertFalse(con.in_transaction)
        n = con.execute("SELECT COUNT(*) FROM redes").fetchone()[0]
        self.assertEqual(n, redes.LOTE + 2)  # lo hecho antes de Ctrl+C queda guardado

    def _procesar_con(self, con, orden):
        # orden: lista de True (descarga ok) / False (cae); mints m0, m1, ...
        for i in range(len(orden)):
            _token(con, f"m{i}", float(i), uri=f"https://ipfs.io/ipfs/Qm{i}")
        mapa = {REDES_PASARELAS[0] + f"Qm{i}": "{}" for i, bien in enumerate(orden) if bien}
        return redes.procesar(con, db.pendientes_redes(con, 0, 999), _bajar_de(mapa), pausa=0)

    def test_corte_por_fallos_seguidos(self):
        con = _con(self)
        n = REDES_MAX_FALLOS_SEGUIDOS
        c = self._procesar_con(con, [False] * (n + 5))
        self.assertTrue(c["parado"])
        self.assertEqual(c["hechos"], n)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM redes").fetchone()[0], n)  # lo acumulado se escribe

    def test_un_exito_reinicia_la_cuenta(self):
        con = _con(self)
        n = REDES_MAX_FALLOS_SEGUIDOS
        orden = [False] * (n - 1) + [True] + [False] * (n - 1)
        c = self._procesar_con(con, orden)
        self.assertFalse(c["parado"])
        self.assertEqual(c["hechos"], len(orden))


class TestReintentar(unittest.TestCase):
    def test_incluye_descarga_y_no_las_demas(self):
        con = _con(self)
        for m in "abcde":
            _token(con, m, 1.0)
        db.guardar_redes(con, [
            ("a", 1.0, 1, None, 0, 0, 0), ("b", 1.0, 0, "no_ipfs", None, None, None),
            ("c", 1.0, 0, "json", None, None, None), ("d", 1.0, 0, "descarga", None, None, None)])
        self.assertEqual([m for m, _ in db.pendientes_redes(con, 0, 9)], ["e"])
        self.assertEqual([m for m, _ in db.pendientes_redes(con, 0, 9, reintentar=True)], ["d", "e"])

    def test_reintentar_sobrescribe(self):
        con = _con(self)
        _token(con, "d", 1.0)
        db.guardar_redes(con, [("d", 1.0, 0, "descarga", None, None, None)])
        redes.procesar(con, db.pendientes_redes(con, 0, 9, True), _bajar_de({URL1: "{}"}), pausa=0, reemplazar=True)
        self.assertEqual(con.execute("SELECT ok, motivo FROM redes WHERE mint = 'd'").fetchall(), [(1, None)])


if __name__ == "__main__":
    unittest.main()
