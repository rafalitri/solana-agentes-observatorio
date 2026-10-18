import sqlite3
import unittest

from agentes.resumen import CLAVES_RESUMEN
from comun import db


def _con(test):
    con = sqlite3.connect(":memory:")
    test.addCleanup(con.close)
    db.crear_tablas(con)
    return con


def _token(con, mint, ts, mayhem=0):
    con.execute("INSERT INTO tokens (mint, ts, mayhem) VALUES (?, ?, ?)", (mint, ts, mayhem))
    con.execute("INSERT INTO decisiones (mint, ts) VALUES (?, ?)", (mint, ts))


def _fila(mint, ts=1.0):
    return {"mint": mint, "ts_token": ts, "ts_resumen": 2.0, "version_resumen": 1, "n_fotos": 3, "error": None}


class TestDbArchivista(unittest.TestCase):
    def test_tabla_resumen_columnas(self):
        con = _con(self)
        cols = [r[1] for r in con.execute("PRAGMA table_info(resumen)")]
        propias = {"mint", "ts_token", "ts_resumen", "error"}
        self.assertEqual([c for c in cols if c not in propias], list(CLAVES_RESUMEN))

    def test_pendientes_resumen(self):
        con = _con(self)
        for mint, ts in (("c", 30.0), ("a", 10.0), ("b", 20.0), ("d", 40.0), ("e", 5.0)):
            _token(con, mint, ts, mayhem=1 if mint == "a" else 0)
        db.guardar_resumenes(con, [_fila("b")])
        r = db.pendientes_resumen(con, 10.0, 40.0, 10)
        self.assertEqual(r, [("a", 10.0, 1), ("c", 30.0, 0), ("d", 40.0, 0)])  # sin e (antes) ni b (resumido)
        self.assertEqual(db.pendientes_resumen(con, 10.0, 30.0, 10), [("a", 10.0, 1), ("c", 30.0, 0)])
        self.assertEqual(db.pendientes_resumen(con, 10.0, 40.0, 2), [("a", 10.0, 1), ("c", 30.0, 0)])

    def test_fotos_token(self):
        con = _con(self)
        for mint, ts, seg, vs in (("m", 2.0, 10, 31.0), ("m", 1.0, 0, 30.0), ("x", 1.5, 0, 99.0)):
            con.execute("INSERT INTO fotos (mint, ts, segundos, v_sol, v_tokens, real_sol, complete) "
                        "VALUES (?, ?, ?, ?, 5.0, 1.0, 0)", (mint, ts, seg, vs))
        self.assertEqual(db.fotos_token(con, "m"), [
            {"segundos": 0, "v_sol": 30.0, "v_tokens": 5.0, "real_sol": 1.0, "complete": 0},
            {"segundos": 10, "v_sol": 31.0, "v_tokens": 5.0, "real_sol": 1.0, "complete": 0}])
        self.assertEqual(db.fotos_token(con, "nada"), [])

    def test_guardar_resumenes_ignora_repetidos(self):
        con = _con(self)
        db.guardar_resumenes(con, [_fila("a"), _fila("b")])
        db.guardar_resumenes(con, [_fila("a"), _fila("c")])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM resumen").fetchone()[0], 3)
        row = con.execute("SELECT n_fotos, mult_max, error FROM resumen WHERE mint = 'a'").fetchone()
        self.assertEqual(row, (3, None, None))


if __name__ == "__main__":
    unittest.main()
