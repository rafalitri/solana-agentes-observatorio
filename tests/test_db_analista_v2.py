import sqlite3
import unittest

from comun import db


def _decision(con, mint, ts, pasa):
    con.execute("INSERT INTO tokens (mint, name, symbol, uri) VALUES (?, ?, ?, ?)",
                (mint, "n" + mint, "s" + mint, "u" + mint))
    con.execute("INSERT INTO decisiones (mint, ts, pasa) VALUES (?, ?, ?)", (mint, ts, pasa))


def _analisis_v2(con, mint):
    db.guardar_analisis_v2(con, mint, 1.0, 2, "qwen3:14b", "otro", 7, "razon", 2.5, None, "{}", 1, 0.4)


class TestDbAnalistaV2(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        db.crear_tablas(self.con)

    def tearDown(self):
        self.con.close()

    def test_pendientes_solo_pasa_1_y_trae_uri(self):
        _decision(self.con, "a", 10, 1)
        _decision(self.con, "b", 11, 0)
        self.assertEqual(db.pendientes_analisis_v2(self.con, 0, 10), [("a", "na", "sa", "ua")])

    def test_pendientes_respeta_desde_ts_orden_y_limite(self):
        _decision(self.con, "viejo", 5, 1)
        _decision(self.con, "c", 30, 1)
        _decision(self.con, "b", 20, 1)
        self.assertEqual([f[0] for f in db.pendientes_analisis_v2(self.con, 10, 10)], ["b", "c"])
        self.assertEqual([f[0] for f in db.pendientes_analisis_v2(self.con, 10, 1)], ["b"])

    def test_pendientes_excluye_analizados_en_v2(self):
        _decision(self.con, "a", 10, 1)
        _decision(self.con, "b", 11, 1)
        _analisis_v2(self.con, "a")
        self.assertEqual([f[0] for f in db.pendientes_analisis_v2(self.con, 0, 10)], ["b"])

    def test_analizado_solo_en_v1_sigue_pendiente_en_v2(self):
        _decision(self.con, "a", 10, 1)
        db.guardar_analisis(self.con, "a", 1.0, 1, "qwen3:14b", "meme", 5, "r", 1.0, None, "{}")
        self.assertEqual([f[0] for f in db.pendientes_analisis_v2(self.con, 0, 10)], ["a"])
        self.assertEqual(db.pendientes_analisis(self.con, 0, 10), [])

    def test_guardar_guarda_meta_y_no_duplica(self):
        _analisis_v2(self.con, "a")
        _analisis_v2(self.con, "a")
        self.assertEqual(self.con.execute("SELECT mint, version_prompt, meta_ok, meta_s FROM analisis_v2").fetchall(),
                         [("a", 2, 1, 0.4)])

    def test_columnas_redes_en_bd_nueva(self):
        cols = {r[1] for r in self.con.execute("PRAGMA table_info(analisis_v2)")}
        self.assertLessEqual({"twitter", "telegram", "web"}, cols)

    def test_columnas_redes_se_anaden_en_bd_vieja(self):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE analisis_v2 (mint TEXT PRIMARY KEY, ts REAL, version_prompt INTEGER, modelo TEXT,"
                    " etiqueta TEXT, puntuacion INTEGER, razon TEXT, duracion_s REAL, error TEXT, raw TEXT,"
                    " meta_ok INTEGER, meta_s REAL)")
        con.execute("INSERT INTO analisis_v2 (mint, meta_ok) VALUES ('viejo', 1)")
        db.crear_tablas(con)
        db.crear_tablas(con)  # idempotente
        self.assertEqual(con.execute("SELECT mint, twitter, telegram, web FROM analisis_v2").fetchall(),
                         [("viejo", None, None, None)])
        con.close()

    def test_guardar_redes_1_0(self):
        db.guardar_analisis_v2(self.con, "a", 1.0, 2, "m", "otro", 7, "r", 2.5, None, "{}", 1, 0.4, 1, 0, 1)
        self.assertEqual(self.con.execute("SELECT twitter, telegram, web FROM analisis_v2").fetchall(), [(1, 0, 1)])

    def test_guardar_redes_null_sin_metadatos(self):
        db.guardar_analisis_v2(self.con, "a", 1.0, 2, "m", "otro", 7, "r", 2.5, None, "{}", 0, 0.4, None, None, None)
        self.assertEqual(self.con.execute("SELECT twitter, telegram, web FROM analisis_v2").fetchall(),
                         [(None, None, None)])


if __name__ == "__main__":
    unittest.main()
