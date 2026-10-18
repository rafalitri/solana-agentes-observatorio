import sqlite3
import unittest

from comun.db import crear_tablas
from herramientas import panel
from herramientas.panel import construir_json, edad_latido, en_alarma

V = ("rastreador", "analista")


class TestAlarma(unittest.TestCase):
    def test_edad(self):
        self.assertEqual(edad_latido(990.0, 1000.0), 10.0)
        self.assertIsNone(edad_latido(None, 1000.0))

    def test_alarma(self):
        self.assertFalse(en_alarma(10, "ok", 180))
        self.assertTrue(en_alarma(181, "ok", 180))
        self.assertTrue(en_alarma(10, "error", 180))
        self.assertTrue(en_alarma(10, "parado", 180))
        self.assertTrue(en_alarma(None, "ok", 180))


class TestConstruirJson(unittest.TestCase):
    def test_agentes_y_faltante(self):
        d = construir_json(1000.0, [("rastreador", 990.0, "ok", 5, 1)], 3, 2, [], [],
                           max_s=180, vigilados=V)
        r, a = d["agentes"]
        self.assertEqual((r["agente"], r["edad_s"], r["procesados"], r["alarma"]),
                         ("rastreador", 10.0, 5, False))
        self.assertEqual((a["estado"], a["alarma"]), ("sin_latido", True))
        self.assertEqual(d["decisiones"], {"total": 3, "pasan": 2})

    def test_analisis_meta_ok_solo_en_v2(self):
        d = construir_json(1.0, [], 0, 0, [(1.0, "N", "S", "meme", 5, "r")],
                           [(2.0, "N", "S", "meme", 6, "r", 1)], vigilados=())
        self.assertNotIn("meta_ok", d["analisis_v1"][0])
        self.assertEqual(d["analisis_v2"][0]["meta_ok"], 1)
        self.assertEqual(d["analisis_v1"][0]["razon"], "r")


class TestConsultas(unittest.TestCase):
    def test_sql_sobre_memoria(self):
        con = sqlite3.connect(":memory:")
        crear_tablas(con)
        con.execute("INSERT INTO tokens (mint, name, symbol) VALUES ('m1', 'Nom', 'SYM')")
        for i in range(12):
            con.execute("INSERT INTO analisis (mint, ts, etiqueta, puntuacion, razon) "
                        "VALUES (?, ?, 'meme', 5, 'r')", ("m1" if i == 11 else f"x{i}", float(i)))
        con.execute("INSERT INTO analisis_v2 (mint, ts, meta_ok) VALUES ('m1', 1.0, 1)")
        con.execute("INSERT INTO decisiones (mint, ts, pasa) VALUES ('a', 100.0, 1), ('b', 100.0, 0), ('c', 1.0, 1)")
        v1 = con.execute(panel.SQL_V1).fetchall()
        self.assertEqual(len(v1), 10)
        self.assertEqual(v1[0][:3], (11.0, "Nom", "SYM"))  # el último insertado va primero
        self.assertEqual(con.execute(panel.SQL_V2).fetchall()[0][6], 1)
        self.assertEqual(con.execute(panel.SQL_DECISIONES, (50.0,)).fetchone(), (2, 1))


class TestPoblado(unittest.TestCase):
    def test_nombre_de_reserva(self):
        self.assertEqual(panel._nombre(" Nom ", "S", "abcdefgh"), "Nom")
        self.assertEqual(panel._nombre("", " S ", "abcdefgh"), "S")
        self.assertEqual(panel._nombre(None, None, "abcdefgh"), "abcdef")

    def test_json_poblado(self):
        d = panel.construir_json_poblado(
            1000.0, [("rastreador", 990.0, "ok", 5, 1), ("analista", 500.0, "ok", 1, 0)],
            [("m1", "Nom", "SYM", 1, "", 995.0)],
            [("m1", "Nom", "SYM", 6, "meme", "r1")], [("m2", None, None, None, "otro", "r2")],
            [("m1", "Nom", "SYM", 1, None, 1), ("m3", "X", "Y", 0, "excepcion", None)],
            max_s=180, vigilados=("rastreador", "analista"))
        self.assertTrue(d["alarma"])  # el latido del analista tiene 500 s
        self.assertEqual(d["decisiones"][0], {"mint": "m1", "nombre": "Nom", "simbolo": "SYM",
                                              "pasa": 1, "motivos": "", "ts": 995.0})
        self.assertEqual(d["analisis_v1"][0], {"mint": "m1", "nombre": "Nom", "puntuacion": 6,
                                               "etiqueta": "meme", "razon": "r1"})
        self.assertEqual((d["analisis_v2"][0]["nombre"], d["analisis_v2"][0]["puntuacion"]), ("m2", None))
        self.assertEqual([(r["graduado"], r["error"], r["pasa"]) for r in d["resumen"]],
                         [(1, None, 1), (0, "excepcion", None)])
        ok = panel.construir_json_poblado(1000.0, [("rastreador", 990.0, "ok", 5, 1)], [], [], [], [],
                                          max_s=180, vigilados=("rastreador",))
        self.assertFalse(ok["alarma"])

    def test_sql_poblado_sobre_memoria(self):
        con = sqlite3.connect(":memory:")
        crear_tablas(con)
        con.execute("INSERT INTO tokens (mint, name, symbol) VALUES ('m1', 'Nom', 'SYM')")
        con.execute("INSERT INTO decisiones (mint, ts, pasa, motivos) VALUES "
                    "('m1', 100.0, 1, ''), ('m2', 101.0, 0, 'copia'), ('m3', 1.0, 1, '')")
        for i in range(25):
            con.execute("INSERT INTO analisis (mint, ts, puntuacion) VALUES (?, 1.0, 5)", (f"x{i}",))
        con.execute("INSERT INTO analisis_v2 (mint, ts, puntuacion) VALUES ('m1', 1.0, 7)")
        con.execute("INSERT INTO resumen (mint, ts_token, graduado) VALUES ('m1', 1.0, 1), ('z', 1.0, 0)")
        dec = con.execute(panel.SQL_P_DECISIONES, (50.0,)).fetchall()
        self.assertEqual([f[0] for f in dec], ["m2", "m1"])  # más nuevas primero, sin la antigua
        self.assertEqual(dec[1][:5], ("m1", "Nom", "SYM", 1, ""))
        self.assertEqual(len(con.execute(panel.SQL_P_V1).fetchall()), 20)
        self.assertEqual(con.execute(panel.SQL_P_V2).fetchall()[0][:4], ("m1", "Nom", "SYM", 7))
        res = con.execute(panel.SQL_P_RESUMEN).fetchall()
        self.assertEqual([(r[0], r[5]) for r in res], [("z", None), ("m1", 1)])
        plan = con.execute("EXPLAIN QUERY PLAN " + panel.SQL_P_DECISIONES, (50.0,)).fetchall()
        self.assertIn("idx_decisiones_ts", " ".join(str(f[3]) for f in plan))

    def test_poblado_html_junto_al_modulo(self):
        self.assertTrue(panel.RUTA_POBLADO.is_absolute() and panel.RUTA_POBLADO.exists())


if __name__ == "__main__":
    unittest.main()
