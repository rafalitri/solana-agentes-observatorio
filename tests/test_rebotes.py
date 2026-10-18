import unittest

from herramientas.rebotes import (COLUMNAS, columna, construir_filas, estadisticas, hora_corte, informe, veredicto)


def reg(i, ret50=None, ret70=None):
    # ts creciente: los tokens 0..n/2 caen en la mitad 1
    r = dict.fromkeys(COLUMNAS)
    r.update(mint=f"m{i}", ts=float(i))
    r[columna("50", 0, "obj")] = ret50
    r[columna("50", 0, "fin")] = ret50
    r[columna("70", 0, "obj")] = ret70
    r[columna("70", 0, "fin")] = ret70
    if ret50 is not None:
        r["seg_susto50"] = 10
    if ret70 is not None:
        r["seg_susto70"] = 10
    return r


def filas(r1, r2):
    # r1/r2: retornos de susto50 de cada mitad (susto70 sin datos)
    regs = [reg(i, x) for i, x in enumerate(r1 + r2)]
    return construir_filas(regs)


class TestRebotes(unittest.TestCase):
    def test_aporta(self):
        txt, motivos = veredicto(filas([0.1] * 100, [0.2] * 100))
        self.assertEqual(txt, "APORTA")
        self.assertIn("susto50 objetivo x1,2/stop -30%: cumple", motivos[0])

    def test_media_negativa_en_una_mitad(self):
        txt, motivos = veredicto(filas([0.1] * 100, [-0.2] * 100))
        self.assertEqual(txt, "NO APORTA")
        self.assertIn("no positiva en mitad 2", motivos[0])
        self.assertNotIn("mitad 1", motivos[0])

    def test_n_menor_que_minimo_en_una_mitad(self):
        txt, motivos = veredicto(filas([0.1] * 100, [0.2] * 99 + [None]))
        self.assertEqual(txt, "NO APORTA")
        self.assertIn("n=99 < 100 en mitad 2", motivos[0])

    def test_lista_vacia(self):
        txt, motivos = veredicto([])
        self.assertEqual(txt, "NO APORTA")
        self.assertIn("n=0 < 100 en mitad 1", motivos[0])
        self.assertEqual(estadisticas([]), (0, None, None, None))
        self.assertIsNone(hora_corte([]))
        self.assertEqual(construir_filas([]), [])
        self.assertIn("NO APORTA", informe([]))

    def test_none_ignorados(self):
        n, media, mediana, pos = estadisticas([None, 0.2, -0.1, None, 0.5])
        self.assertEqual(n, 3)
        self.assertAlmostEqual(media, 0.2)
        self.assertEqual(mediana, 0.2)
        self.assertAlmostEqual(pos, 2 / 3)

    def test_una_combinacion_basta(self):
        # susto50 flojo pero susto70 cumple en las dos mitades
        regs = [reg(i, -0.1, 0.3) for i in range(200)]
        txt, _ = veredicto(construir_filas(regs))
        self.assertEqual(txt, "APORTA")

    def test_corte_unico_sobre_todos_los_tokens(self):
        # solo la mitad de los tokens tiene susto, pero el corte usa todos
        regs = [reg(i, 0.1 if i % 2 else None) for i in range(10)]
        fs = construir_filas(regs)
        self.assertEqual(hora_corte(regs), 4.5)
        self.assertEqual(sorted({f["mitad"] for f in fs if f["ts"] <= 4.5}), [1])
        self.assertEqual(sorted({f["mitad"] for f in fs if f["ts"] > 4.5}), [2])

    def test_informe_cuenta_sustos(self):
        txt = informe(filas([0.1] * 3, [0.2] * 3))
        self.assertIn("total: 6 tokens | susto50: 6 (100.0%) | susto70: 0 (0.0%)", txt)


if __name__ == "__main__":
    unittest.main()
