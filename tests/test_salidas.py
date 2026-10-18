import unittest

from herramientas.salidas import dividir_mitades, metricas, tramo_compra


class TestTramoCompra(unittest.TestCase):
    def test_tramos_y_limites(self):
        casos = [(None, "0"), (0, "0"), (0.005, "<0.01"), (0.01, "0.01-0.1"), (0.1, "0.1-1"),
                 (0.99, "0.1-1"), (1, "1-5"), (4.9, "1-5"), (5, ">=5"), (80, ">=5")]
        for valor, esperado in casos:
            self.assertEqual(tramo_compra(valor), esperado, valor)


class TestMitades(unittest.TestCase):
    def test_par_e_impar(self):
        self.assertEqual(dividir_mitades({"a": 1, "b": 2, "c": 3, "d": 4}), {"a": 1, "b": 1, "c": 2, "d": 2})
        # impar: el de la mediana va a la mitad 1
        self.assertEqual(dividir_mitades({"a": 1, "b": 2, "c": 3}), {"a": 1, "b": 1, "c": 2})

    def test_vacio(self):
        self.assertEqual(dividir_mitades({}), {})


class TestMetricas(unittest.TestCase):
    def test_metricas(self):
        items = [(0.5, "objetivo", 1), (-0.3, "stop", 1), (0.1, "tiempo", 2), (-0.1, "graduacion", 2)]
        m = metricas(items, 0.1)
        self.assertEqual((m["n"], m["n1"], m["n2"]), (4, 2, 2))
        self.assertAlmostEqual(m["media1"], 0.1)
        self.assertAlmostEqual(m["media2"], 0.0)
        self.assertAlmostEqual(m["media"], 0.05)
        self.assertAlmostEqual(m["mediana"], 0.0)
        self.assertEqual(m["pct_pos"], 50.0)
        self.assertEqual(m["peor"], -0.3)
        self.assertEqual(m["pct_motivo"], {"objetivo": 25.0, "stop": 25.0, "tiempo": 25.0, "graduacion": 25.0})
        self.assertAlmostEqual(m["sol_neto"], 0.02)

    def test_vacio_y_mitad_vacia(self):
        self.assertIsNone(metricas([], 0.1)["media"])
        m = metricas([(0.2, "tiempo", 2)], 0.1)
        self.assertIsNone(m["media1"])
        self.assertAlmostEqual(m["media2"], 0.2)


if __name__ == "__main__":
    unittest.main()
