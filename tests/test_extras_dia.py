import contextlib
import io
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from comun import db
from herramientas import extras_dia as e
from herramientas.resultados import parse_desde

L = 1e9
K = 30 * L * 1.073e15


def foto(seg, v_sol_sol, complete=0):
    v = v_sol_sol * L
    return {"segundos": seg, "v_sol": v, "v_tokens": K / v, "real_sol": v - 30 * L, "complete": complete}


class TestPuras(unittest.TestCase):
    def test_huecos_y_percentil(self):
        self.assertEqual(e.huecos([0, 10, 30]), [10, 20])
        self.assertEqual(e.huecos([5]), [])
        self.assertIsNone(e.percentil([], 50))
        self.assertEqual(e.percentil([1, 2, 3, 4, 5], 50), 3)
        self.assertEqual(e.percentil([1, 2, 3, 4, 5], 100), 5)

    def test_hasta_graduar(self):
        fs = [foto(0, 30), foto(10, 31), foto(20, 85, 1), foto(30, 85, 1)]
        util, grad = e.fotos_hasta_graduar(fs)
        self.assertEqual((len(util), grad), (2, True))
        self.assertEqual(e.fotos_hasta_graduar(fs[:2])[1], False)

    def test_impulso(self):
        self.assertEqual(e.fila_impulso([foto(0, 30), foto(10, 31), foto(20, 32.5)]), 2)  # 30->32.5 en 20 s
        self.assertIsNone(e.fila_impulso([foto(0, 30), foto(100, 31), foto(200, 32.5)]))  # sube 1,5 en 100 s
        self.assertIsNone(e.fila_impulso([foto(0, 30), foto(130, 33)]))  # +3 pero en 130 s

    def test_cerca_graduar(self):
        self.assertEqual(e.fila_cerca_graduar([foto(0, 30), foto(10, 80), foto(20, 81)]), 1)  # real 50
        self.assertIsNone(e.fila_cerca_graduar([foto(0, 30), foto(10, 79.9)]))


class TestInforme(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.ruta = os.path.join(self.d, "t.db")
        con = sqlite3.connect(self.ruta)
        db.crear_tablas(con)
        self.t0 = parse_desde("2020-01-01 10:00") + 60
        # (mint, offset, sol_amount, pasa, fotos)
        sube = [(10 * i, 30 + 0.5 * i, 0) for i in range(1, 13)]
        grad = [(10, 30, 0), (20, 31, 0), (30, 85, 1), (40, 85, 1)]
        toks = [("A", 0, 0.5, 1, sube), ("B", 10, 2.0, 1, grad), ("C", 20, 0.5, 0, sube), ("D", 30, 0.5, 1, [])]
        for mint, off, sol, pasa, fs in toks:
            con.execute("INSERT INTO tokens (mint, ts, sol_amount, mayhem) VALUES (?,?,?,0)",
                        (mint, self.t0 + off, sol))
            con.execute("INSERT INTO decisiones (mint, ts, pasa) VALUES (?,?,?)", (mint, self.t0 + off, pasa))
            for seg, v, c in fs:
                vv = v * L
                con.execute("INSERT INTO fotos (mint, ts, segundos, v_sol, v_tokens, real_sol, complete) "
                            "VALUES (?,?,?,?,?,?,?)", (mint, self.t0 + off + seg, seg, vv, K / vv, vv - 30 * L, c))
        con.commit()
        con.close()

    def correr(self, desde, hasta):
        out = io.StringIO()
        with mock.patch("herramientas.salidas.RUTA_BD", self.ruta), \
                mock.patch("sys.argv", ["x", "--desde", desde, "--hasta", hasta]), contextlib.redirect_stdout(out):
            e.main()
        return out.getvalue()

    def test_con_datos(self):
        s = self.correr("2020-01-01 10:00", "2020-01-01 11:00")
        self.assertIn("Universo: 3 tokens", s)  # C no pasa
        for t in ("== a)", "== b)", "== c)", "impulso", "cerca de graduar", "fotos por token no graduado"):
            self.assertIn(t, s)

    def test_ventana_vacia(self):
        self.assertIn("Sin datos en la ventana.", self.correr("2019-01-01 10:00", "2019-01-01 11:00"))


if __name__ == "__main__":
    unittest.main()
