import math
import statistics
import unittest

from agentes.contable import MESAS
from comun.config import SIM_TAM_SOL
from herramientas.mesas import MESAS_ORDEN, marcador


def ops(*rets):
    return [(i, r) for i, r in enumerate(rets)]


class TestMarcador(unittest.TestCase):
    def test_orden_igual_que_contable(self):
        self.assertEqual(MESAS_ORDEN, MESAS)

    def test_media_mediana_ic_y_saldo(self):
        rets = [0.1, -0.2, 0.3, 0.0]
        m = marcador(ops(*rets), 0.0, 4)
        self.assertAlmostEqual(m["media"], 0.05)
        self.assertAlmostEqual(m["mediana"], 0.05)
        mitad = 1.96 * statistics.stdev(rets) / 2
        self.assertAlmostEqual(m["ic95"][0], 0.05 - mitad)
        self.assertAlmostEqual(m["ic95"][1], 0.05 + mitad)
        self.assertAlmostEqual(m["saldo_sol"], 0.2 * SIM_TAM_SOL)

    def test_sin_dato_no_cuenta_en_n(self):
        m = marcador(ops(0.1, None, 0.3), 0.0, 1)
        self.assertEqual((m["n"], m["n_sin_dato"]), (2, 1))

    def test_media_sin_1pct_quita_las_mejores(self):
        base = [0.0] * 99 + [10.0]  # n = 100 -> quita 1
        self.assertAlmostEqual(marcador(ops(*base), 0, 1)["media_sin_1pct"], 0.0)
        m = marcador(ops(*([0.0] * 99 + [10.0, 5.0])), 0, 1)  # n = 101 -> ceil(1,01) = 2
        self.assertAlmostEqual(m["media_sin_1pct"], 0.0)
        self.assertEqual(math.ceil(0.01 * 101), 2)

    def test_caida_maxima_con_maximo_inicial_0(self):
        # saldo (en retornos): -1, +1 (pico 0 desde el inicio), +3 (pico), +1, +2, -1 -> caida desde 3 hasta -1 = 4... en ret
        m = marcador(ops(-1.0, 2.0, 2.0, -2.0, 1.0, -3.0), 0, 1)
        self.assertAlmostEqual(m["caida_max_sol"], 4.0 * SIM_TAM_SOL)
        # solo pierde: el pico sigue siendo 0
        self.assertAlmostEqual(marcador(ops(-1.0, -1.0), 0, 1)["caida_max_sol"], 2.0 * SIM_TAM_SOL)

    def test_ordena_por_ts_entrada(self):
        m = marcador([(2, -2.0), (0, 1.0), (1, 1.0)], 0, 1)  # en orden: +1, +1, -2 -> caida 2
        self.assertAlmostEqual(m["caida_max_sol"], 2.0 * SIM_TAM_SOL)

    def test_menos_de_2_es_ruido_sin_medias(self):
        for o in ([], ops(0.5), ops(None, 0.5)):
            m = marcador(o, 0.0, 1, es_base=True)
            self.assertEqual((m["media"], m["mediana"], m["media_sin_1pct"], m["ic95"], m["etiqueta"]),
                             (None, None, None, None, "RUIDO"))

    def test_etiquetas(self):
        alto = ops(*([1.0, 1.1] * 5))   # IC estrecho alrededor de 1,05
        self.assertEqual(marcador(alto, 0.0, 10)["etiqueta"], "POR ENCIMA")
        self.assertEqual(marcador(alto, 5.0, 10)["etiqueta"], "POR DEBAJO")
        self.assertEqual(marcador(alto, 1.05, 10)["etiqueta"], "RUIDO")   # la base cae dentro del IC
        self.assertEqual(marcador(alto, 0.0, 11)["etiqueta"], "RUIDO")    # n < n_min
        self.assertEqual(marcador(alto, None, 10)["etiqueta"], "RUIDO")   # sin base
        self.assertEqual(marcador(alto, None, 10, es_base=True)["etiqueta"], "REFERENCIA")


if __name__ == "__main__":
    unittest.main()
