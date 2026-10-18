import unittest

from herramientas.opiniones import (clasificar, consulta_opiniones, informe, informe_tramos, misma_direccion, resumen,
                                    tramo_compra)


class TestOpiniones(unittest.TestCase):
    def test_clasificar_umbrales(self):
        self.assertIn("punt>=7", clasificar("otro", 7))
        self.assertIn("punt<=5", clasificar("meme", 5))
        g = clasificar("meme", 6)
        self.assertFalse(any(x.startswith("punt>=") or x.startswith("punt<=") for x in g))
        self.assertIn("meme", g)
        self.assertIn("punt>=8", clasificar("otro", 8, alto=8))
        self.assertNotIn("punt>=8", clasificar("otro", 7, alto=8))

    def test_clasificar_descriptivas_y_none(self):
        self.assertEqual(clasificar("x", None), ["punt=None", "etq=x"])
        self.assertIn("punt=6", clasificar("x", 6))
        self.assertIn("etq=x", clasificar("x", 6))

    def test_resumen(self):
        self.assertEqual(resumen([]), (0, None, None))
        self.assertEqual(resumen([None]), (0, None, None))
        self.assertEqual(resumen([1, 2, 6, None]), (3, 3.0, 2))

    def test_misma_direccion(self):
        self.assertTrue(misma_direccion(1, 2))
        self.assertTrue(misma_direccion(-1, -0.5))
        self.assertFalse(misma_direccion(1, -2))
        self.assertFalse(misma_direccion(0, 2))
        self.assertFalse(misma_direccion(None, 2))

    def test_informe_grupo_vacio_no_rompe(self):
        filas = [{"mitad": 1, "grupos": clasificar("meme", 4), "mult": 1.5, "ret": None}]
        txt = informe(filas)
        self.assertIn("punt<=5 | 1 | 1 1.50 1.50 | 0 - -", txt)
        self.assertIn("no", txt)

    def test_tramo_compra_limites(self):
        for x, esperado in ((None, None), (0, None), (0.0999, None), (0.1, "0.1-1"),
                            (0.9999, "0.1-1"), (1, ">=1"), (5, ">=1")):
            self.assertEqual(tramo_compra(x), esperado, x)

    def test_consulta_meta_ok_solo_en_v2(self):
        self.assertNotIn("meta_ok", consulta_opiniones("analisis"))
        self.assertIn("FROM analisis a", consulta_opiniones("analisis"))
        self.assertIn("FROM analisis_v2 a", consulta_opiniones("analisis_v2"))
        self.assertIn("a.meta_ok = 1", consulta_opiniones("analisis_v2"))
        with self.assertRaises(ValueError):
            consulta_opiniones("tokens; DROP TABLE tokens")

    def test_informe_tramos_reparte_n(self):
        def f(mitad, punt, tramo, mult):
            return {"mitad": mitad, "grupos": clasificar("meme", punt), "mult": mult, "ret": None, "tramo": tramo}
        filas = [f(1, 8, "0.1-1", 2.0), f(1, 4, "0.1-1", 1.0), f(2, 8, "0.1-1", 1.5), f(2, 3, "0.1-1", 1.0),
                 f(1, 8, None, 9.0)]
        lineas = informe_tramos(filas).splitlines()
        a = next(l for l in lineas if l.startswith("0.1-1 | media mult"))
        self.assertEqual(a, "0.1-1 | media mult | 1/1 | 1/1 | 1.00 | 0.50 | si")
        b = next(l for l in lineas if l.startswith(">=1 | media mult"))
        self.assertIn("| 0/0 | 0/0 | - | - | no", b)


if __name__ == "__main__":
    unittest.main()
