import unittest

from herramientas.simulador import comprar, simular_token, vender

K = 30e9 * 1.073e15
COM, FEE, TAM = 0.0125, 0.0002, 0.1


def foto(s, v_sol=30e9, real=0.0, complete=0):
    """Foto sintética sobre la misma curva (k constante); v_sol None = lectura fallida."""
    v_tokens = None if v_sol is None else K / v_sol
    return {"segundos": s, "v_sol": v_sol, "v_tokens": v_tokens, "real_sol": real, "complete": complete}


def sim(fotos, objetivo=None, stop=None, retraso=0):
    return simular_token(fotos, TAM, objetivo, stop, retraso, 0.0, COM, FEE)


class TestSimulador(unittest.TestCase):
    def test_misma_foto_solo_comisiones(self):
        r = sim([foto(0), foto(10)])
        self.assertAlmostEqual(r["retorno"], -0.02884, places=4)

    def test_vender_limitado_a_real_sol(self):
        tokens, _ = comprar(30e9, 1.073e15, 1e9, 0, 0)
        self.assertLessEqual(vender(30e9, 1.073e15 - tokens, 0.5e9, tokens, 0, 0), 0.5e9)

    def test_objetivo(self):
        r = sim([foto(0), foto(10), foto(20, 60e9, 30e9)], objetivo=1.5)
        self.assertEqual((r["motivo_salida"], r["segundos_salida"]), ("objetivo", 20))

    def test_stop(self):
        r = sim([foto(0), foto(10, 20e9, 10e9)], stop=0.3)
        self.assertEqual((r["motivo_salida"], r["segundos_salida"]), ("stop", 10))
        self.assertLessEqual(r["retorno"], -0.3)

    def test_tiempo(self):
        r = sim([foto(0), foto(10), foto(20)], objetivo=2, stop=0.5)
        self.assertEqual((r["motivo_salida"], r["segundos_salida"]), ("tiempo", 20))

    def test_graduacion_vende_en_ultima_foto_sin_graduar(self):
        r = sim([foto(0), foto(10), foto(20, complete=1)], objetivo=2, stop=0.5)
        self.assertEqual((r["motivo_salida"], r["segundos_salida"]), ("graduacion", 10))

    def test_retraso_compra_y_vende_en_foto_siguiente(self):
        fotos = [foto(0), foto(10), foto(20, 60e9, 30e9), foto(30, 60e9, 30e9)]
        r = sim(fotos, objetivo=1.5, retraso=1)
        self.assertEqual((r["segundos_entrada"], r["motivo_salida"], r["segundos_salida"]), (10, "objetivo", 30))

    def test_retraso_sin_foto_siguiente_vende_en_la_misma(self):
        r = sim([foto(0), foto(10), foto(20, 60e9, 30e9)], objetivo=1.5, retraso=1)
        self.assertEqual((r["motivo_salida"], r["segundos_salida"]), ("tiempo", 20))

    def test_retraso_con_foto_de_compra_graduada_es_none(self):
        self.assertIsNone(sim([foto(0), foto(10, complete=1)], retraso=1))

    def test_foto_nula_se_salta(self):
        nula = foto(10, None)
        nula["complete"] = 1  # aunque diga complete, sin v_sol no cuenta
        r = sim([foto(0), nula, foto(20)], objetivo=2, stop=0.5)
        self.assertEqual((r["motivo_salida"], r["segundos_salida"]), ("tiempo", 20))

    def test_sin_entrada_valida(self):
        self.assertIsNone(sim([foto(0, complete=1), foto(10)]))
        self.assertIsNone(sim([foto(0, None), foto(10, None)]))


if __name__ == "__main__":
    unittest.main()
