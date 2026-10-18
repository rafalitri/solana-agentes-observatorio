import os
import sqlite3
import tempfile
import unittest

from comun import db


class TestDeshacer(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.ruta = os.path.join(self.dir.name, "t.db")
        self.a = self._abrir()
        self.b = self._abrir()
        self.a.execute("CREATE TABLE t (x INTEGER)")
        self.a.commit()

    def _abrir(self):
        con = sqlite3.connect(self.ruta, timeout=0.1)  # timeout corto: no esperar 5 s
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def tearDown(self):
        self.a.close()
        self.b.close()
        self.dir.cleanup()

    def test_deja_sin_transaccion(self):
        self.a.execute("INSERT INTO t VALUES (1)")
        self.assertTrue(self.a.in_transaction)
        db.deshacer(self.a)
        self.assertFalse(self.a.in_transaction)

    def test_ve_la_fila_de_otra_conexion(self):
        self.a.execute("BEGIN")
        self.a.execute("SELECT count(*) FROM t").fetchall()  # fija la foto vieja
        self.b.execute("INSERT INTO t VALUES (2)")
        self.b.commit()
        with self.assertRaises(sqlite3.OperationalError):
            self.a.execute("INSERT INTO t VALUES (3)")
        self.assertEqual(self.a.execute("SELECT count(*) FROM t").fetchone()[0], 0)
        db.deshacer(self.a)
        self.assertEqual(self.a.execute("SELECT count(*) FROM t").fetchone()[0], 1)

    def test_no_lanza_sin_transaccion(self):
        db.deshacer(self.a)
        self.assertFalse(self.a.in_transaction)


if __name__ == "__main__":
    unittest.main()
