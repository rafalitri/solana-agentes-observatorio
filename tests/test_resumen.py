import unittest

from agentes.resumen import CLAVES_RESUMEN, OBJETIVO, STOP, resumir_token
from comun.config import SIM_COMISION, SIM_FEE_RED_SOL, SIM_TAM_SOL, VERSION_RESUMEN
from herramientas.simulador import simular_token

K = 30e9 * 1.073e15


def foto(s, v_sol=30e9, real=0.0, complete=0):
    v_tokens = None if v_sol is None else K / v_sol
    return {"segundos": s, "v_sol": v_sol, "v_tokens": v_tokens, "real_sol": real, "complete": complete}


def con_precio(s, p):
    """Foto con precio p (v_sol/v_tokens) y v_tokens fijo 1e15."""
    return {"segundos": s, "v_sol": p * 1e15, "v_tokens": 1e15, "real_sol": 0.0, "complete": 0}


def sim(fotos, objetivo, stop, retraso):
    r = simular_token(fotos, SIM_TAM_SOL, objetivo, stop, retraso, 0.0, SIM_COMISION, SIM_FEE_RED_SOL)
    return None if r is None else r["retorno"]


class TestResumen(unittest.TestCase):
    def test_sin_fotos(self):
        r = resumir_token([], False)
        self.assertEqual(r["n_fotos"], 0)
        self.assertEqual(r["version_resumen"], VERSION_RESUMEN)
        self.assertTrue(all(v is None for k, v in r.items() if k not in ("n_fotos", "version_resumen")))

    def test_claves(self):
        for fotos in ([], [foto(0, complete=1)], [foto(0), foto(10)]):
            self.assertEqual(tuple(resumir_token(fotos, False)), CLAVES_RESUMEN)

    def test_graduado_instantaneo(self):
        r = resumir_token([foto(5, complete=1), foto(15, complete=1)], False)
        self.assertTrue(r["grad_inst"])
        self.assertTrue(r["graduado"])
        self.assertEqual(r["seg_graduado"], 5)
        self.assertEqual(r["seg_primera"], 5)
        self.assertEqual(r["hueco_max_s"], 10)
        for k in ("mult_max", "mult_min", "caida_max", "ret_r0_obj", "seg_susto50", "ret_s70_r1_fin"):
            self.assertIsNone(r[k])

    def test_curva_normal(self):
        # precios 1, 2, 1, 4, 3 -> mult_max 4 (seg 30), mult_min 1, final 3, caída máx 0.5 (2 -> 1)
        r = resumir_token([con_precio(s, p) for s, p in ((0, 1), (10, 2), (20, 1), (30, 4), (40, 3))], False)
        self.assertFalse(r["grad_inst"])
        self.assertFalse(r["graduado"])
        self.assertIsNone(r["seg_graduado"])
        self.assertEqual((r["mult_max"], r["seg_max"], r["mult_min"], r["mult_final"]), (4, 30, 1, 3))
        self.assertAlmostEqual(r["caida_max"], 0.5)

    def test_seg_max_primera_vez(self):
        r = resumir_token([con_precio(0, 1), con_precio(10, 3), con_precio(20, 3)], False)
        self.assertEqual(r["seg_max"], 10)

    def test_hueco_max(self):
        r = resumir_token([foto(0), foto(10), foto(40), foto(50)], False)
        self.assertEqual(r["hueco_max_s"], 30)
        self.assertIsNone(resumir_token([foto(0)], False)["hueco_max_s"])

    def test_complete_excluida_del_precio(self):
        fotos = [con_precio(0, 1), con_precio(10, 2), dict(con_precio(20, 100), complete=1)]
        r = resumir_token(fotos, False)
        self.assertTrue(r["graduado"])
        self.assertEqual(r["seg_graduado"], 20)
        self.assertFalse(r["grad_inst"])
        self.assertEqual((r["mult_max"], r["mult_final"]), (2, 2))

    def test_susto_50_si_70_no(self):
        # sube a 2 y cae a 1 (= 50 % del máximo): susto 50 en seg 20, no el 70
        fotos = [con_precio(0, 1), con_precio(10, 2), con_precio(20, 1), con_precio(30, 1.2)]
        r = resumir_token(fotos, False)
        self.assertEqual(r["seg_susto50"], 20)
        self.assertIsNone(r["seg_susto70"])
        self.assertIsNone(r["ret_s70_r0_obj"])

    def test_retornos_del_susto(self):
        fotos = [con_precio(0, 1), con_precio(10, 2), con_precio(20, 1), con_precio(30, 1.2), con_precio(40, 1.1)]
        r = resumir_token(fotos, False)
        sub = fotos[2:]
        self.assertEqual(r["ret_s50_r0_obj"], sim(sub, OBJETIVO, STOP, 0))
        self.assertEqual(r["ret_s50_r1_obj"], sim(sub, OBJETIVO, STOP, 1))
        self.assertEqual(r["ret_s50_r0_fin"], sim(sub, None, None, 0))
        self.assertEqual(r["ret_s50_r1_fin"], sim(sub, None, None, 1))
        self.assertEqual(r["ret_r0_fin"], sim(fotos, None, None, 0))

    def test_retorno_sin_foto_para_retraso(self):
        r = resumir_token([foto(0)], False)
        self.assertEqual(r["ret_r0_fin"], sim([foto(0)], None, None, 0))
        self.assertIsNone(r["ret_r1_fin"])


if __name__ == "__main__":
    unittest.main()
