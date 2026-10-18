import sqlite3
import unittest

from comun import db


def _decision(con, mint, ts, pasa):
    con.execute("INSERT INTO tokens (mint, name, symbol) VALUES (?, ?, ?)", (mint, "n" + mint, "s" + mint))
    con.execute("INSERT INTO decisiones (mint, ts, pasa) VALUES (?, ?, ?)", (mint, ts, pasa))


def _analisis(con, mint):
    db.guardar_analisis(con, mint, 1.0, 1, "qwen3:14b", "bueno", 7, "razon", 2.5, None, "{}")


class TestDbAnalista(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        db.crear_tablas(self.con)

    def tearDown(self):
        self.con.close()

    def test_pendientes_solo_pasa_1(self):
        _decision(self.con, "a", 10, 1)
        _decision(self.con, "b", 11, 0)
        self.assertEqual(db.pendientes_analisis(self.con, 0, 10), [("a", "na", "sa")])

    def test_pendientes_respeta_desde_ts_orden_y_limite(self):
        _decision(self.con, "viejo", 5, 1)
        _decision(self.con, "c", 30, 1)
        _decision(self.con, "b", 20, 1)
        self.assertEqual([m for m, _, _ in db.pendientes_analisis(self.con, 10, 10)], ["b", "c"])
        self.assertEqual([m for m, _, _ in db.pendientes_analisis(self.con, 10, 1)], ["b"])

    def test_pendientes_excluye_ya_analizados(self):
        _decision(self.con, "a", 10, 1)
        _decision(self.con, "b", 11, 1)
        _analisis(self.con, "a")
        self.assertEqual([m for m, _, _ in db.pendientes_analisis(self.con, 0, 10)], ["b"])

    def test_guardar_analisis_no_duplica(self):
        _analisis(self.con, "a")
        _analisis(self.con, "a")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM analisis").fetchone()[0], 1)

    def test_guardar_estado_agente_reemplaza(self):
        db.guardar_estado_agente(self.con, "analista", 1.0, "ok", 1, 0)
        db.guardar_estado_agente(self.con, "analista", 2.0, "ocupado", 5, 1)
        self.assertEqual(self.con.execute("SELECT * FROM agentes_estado").fetchall(),
                         [("analista", 2.0, "ocupado", 5, 1)])

    def test_indice_decisiones_ts_existe(self):
        nombres = [f[1] for f in self.con.execute("PRAGMA index_list(decisiones)")]
        self.assertIn("idx_decisiones_ts", nombres)


if __name__ == "__main__":
    unittest.main()
