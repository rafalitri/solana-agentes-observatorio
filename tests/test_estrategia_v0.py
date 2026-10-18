import unittest

from herramientas.estrategia_v0 import (calcular_vel, elegir_combinacion, elegir_entrada, grupo_reputacion,
                                        grupo_tenedores, media_sin_mejores, percentil, senal_ok,
                                        veredicto_validacion)
from herramientas.simulador import simular_escalera, simular_token

G = 1e9


class TestMinimoPropio(unittest.TestCase):
    def test_usa_estrategia_min_n(self):
        from comun import config
        from herramientas import estrategia_v0
        self.assertEqual(config.ESTRATEGIA_MIN_N, 100)
        for f in (estrategia_v0.elegir_combinacion, estrategia_v0.veredicto_validacion,
                  estrategia_v0.desarrollo, estrategia_v0.validacion):
            self.assertEqual(f.__defaults__[-1], config.ESTRATEGIA_MIN_N)
        self.assertFalse(hasattr(estrategia_v0, "REBOTES_MIN_N"))


def foto(seg, v_sol=30 * G, complete=0, v_tokens=1.07e15):
    # curva sencilla: el precio sube con v_sol (v_tokens fijo); real_sol grande para que vender no se recorte
    return {"segundos": seg, "v_sol": v_sol, "v_tokens": v_tokens, "real_sol": 1000 * G, "complete": complete}


class TestEntrada(unittest.TestCase):
    def test_elige_primera_valida_desde_60(self):
        fs = [foto(0), foto(30), foto(60, complete=1), foto(70, v_sol=None), foto(80), foto(90)]
        self.assertEqual(elegir_entrada(fs, 0), (4, 4))
        self.assertEqual(elegir_entrada(fs, 1), (4, 5))

    def test_sin_siguiente_o_sin_entrada(self):
        self.assertEqual(elegir_entrada([foto(0), foto(60)], 1), (1, None))
        self.assertEqual(elegir_entrada([foto(0), foto(30), foto(60, complete=1)], 0), (None, None))
        self.assertEqual(elegir_entrada([], 0), (None, None))
        self.assertEqual(elegir_entrada([foto(0), foto(60, v_tokens=0)], 0), (None, None))

    def test_vel_solo_hasta_i(self):
        fs = [foto(5, v_sol=30 * G), foto(65, v_sol=32 * G), foto(100, v_sol=99 * G)]
        self.assertAlmostEqual(calcular_vel(fs, 1), 2.0)


class TestEstadistica(unittest.TestCase):
    def test_percentil(self):
        self.assertEqual(percentil([1, 2, 3, 4, 5], 50), 3)
        self.assertAlmostEqual(percentil([0, 10], 67), 6.7)
        self.assertIsNone(percentil([], 67))

    def test_media_sin_mejores(self):
        self.assertAlmostEqual(media_sin_mejores(list(range(100))), sum(range(99)) / 99)
        self.assertAlmostEqual(media_sin_mejores([1, 2, 3, 100]), 2.0)  # ceil(0,04) = 1
        self.assertIsNone(media_sin_mejores([]))

    def test_ceil_700_es_7(self):
        v = [0.0] * 693 + [1.0] * 7  # n = 700: se quitan exactamente 7
        self.assertEqual(media_sin_mejores(v), 0.0)

    def test_elegir_combinacion(self):
        t = {("A", "S1"): {1: (150, 0.05), 2: (150, 0.02), None: (300, 0.035)},
             ("A", "S2"): {1: (150, 0.20), 2: (99, 0.20), None: (249, 0.20)},   # n corta
             ("B", "S3"): {1: (150, 0.30), 2: (150, -0.01), None: (300, 0.145)},  # una mitad negativa
             ("B", "S1"): {1: (150, 0.04), 2: (150, 0.06), None: (300, 0.05)}}
        self.assertEqual(elegir_combinacion(t), ("B", "S1"))
        self.assertIsNone(elegir_combinacion({("A", "S1"): t[("A", "S2")]}))

    def test_veredicto_validacion(self):
        self.assertTrue(veredicto_validacion({1: (100, 0.1), 2: (120, 0.2)}, 0.01)[0])
        self.assertIn("mitad 2", veredicto_validacion({1: (100, 0.1), 2: (99, 0.2)}, 0.01)[1])
        self.assertFalse(veredicto_validacion({1: (100, 0.1), 2: (120, -0.2)}, 0.01)[0])
        self.assertIn("1 %", veredicto_validacion({1: (100, 0.1), 2: (120, 0.2)}, -0.01)[1])

    def test_senal_y_grupos(self):
        self.assertTrue(senal_ok("A", 2.0, 1.0, None, None, 0))
        self.assertFalse(senal_ok("B", 2.0, 1.0, 1, 1, 0))
        self.assertTrue(senal_ok("B", 2.0, 1.0, 1, 1, 2))
        self.assertFalse(senal_ok("A", 0.5, 1.0, 1, 1, 2))
        self.assertEqual(grupo_reputacion([], 100), "0 previos")
        self.assertEqual(grupo_reputacion([(10, 1, 200)], 100), ">=1 previos sin graduados")  # futuro
        self.assertEqual(grupo_reputacion([(10, 1, 50)], 100), ">=1 graduado")
        self.assertEqual(grupo_reputacion([(10, 0, None)], 100), ">=1 previos sin graduados")
        f = {"error_ten": None, "top10_pct": 60, "top10_circ_pct": 80, "creador_pct": 2, "seg_ten": 30}
        self.assertEqual(grupo_tenedores(f, "top10_pct", 60), "pasa filtro")
        self.assertEqual(grupo_tenedores(f, "top10_circ_pct", 60), "no pasa")
        self.assertEqual(grupo_tenedores(f, "top10_pct", 20), "sin dato a tiempo")
        self.assertEqual(grupo_tenedores({**f, "error_ten": "x"}, "top10_pct", 60), "sin dato a tiempo")


class TestEscalera(unittest.TestCase):
    def sim(self, precios, **kw):
        fs = [foto(60 + 10 * k, v_sol=p * G, **kw) for k, p in enumerate(precios)]
        return simular_escalera(fs, 0.1, 0, 0)

    def test_stop_antes_de_x2(self):
        r = self.sim([30, 29, 20, 100])
        self.assertEqual(r["motivo_salida"], "stop")
        self.assertFalse(r["vendio_mitad"])
        self.assertEqual(r["segundos_salida"], 80)
        self.assertLess(r["retorno"], -0.25)

    def test_mitad_en_x2_y_stop_dinamico(self):
        r = self.sim([30, 45, 60, 90, 120, 70])  # sube; cae >30 % desde el pico
        self.assertEqual(r["motivo_salida"], "stop_resto")
        self.assertTrue(r["vendio_mitad"])
        self.assertGreater(r["retorno"], 0.5)

    def test_segunda_venta_descuenta_la_primera(self):
        import herramientas.simulador as sim
        original, llamadas = sim.vender, []
        sim.vender = lambda *a: llamadas.append(a) or original(*a)
        try:
            r = self.sim([30, 45, 60, 90, 120, 70])
        finally:
            sim.vender = original
        self.assertEqual(r["motivo_salida"], "stop_resto")
        q = min(c[3] for c in llamadas)  # la 1.ª venta vende tokens/2: valor, bruto (comisión 0) y 2.ª venta
        medias = [c for c in llamadas if c[3] == q]
        self.assertEqual(len(medias), 3)
        bruto = original(*medias[1])
        v_sol, v_tokens, real_sol, q2, com, des = medias[2]
        # sin descontar la 1.ª venta la curva tendría v_sol + bruto, v_tokens - q, real_sol + bruto
        sin_descuento = original(v_sol + bruto, v_tokens - q, real_sol + bruto, q2, com, des)
        con_descuento = original(v_sol, v_tokens, real_sol, q2, com, des)
        self.assertLess(con_descuento, sin_descuento)

    def test_fin_sin_tocar_nada(self):
        r = self.sim([30, 31, 30, 32])
        self.assertEqual(r["motivo_salida"], "tiempo")
        self.assertEqual(r["segundos_salida"], 90)
        # igual que aguantar (sin comisión; 1 compra + 1 venta con fee 0)
        fs = [foto(60 + 10 * k, v_sol=p * G) for k, p in enumerate([30, 31, 30, 32])]
        self.assertAlmostEqual(r["retorno"], simular_token(fs, 0.1, None, None, 0, 0, 0, 0)["retorno"])

    def test_graduacion(self):
        fs = [foto(60, 30 * G), foto(70, 31 * G), foto(80, 32 * G, complete=1)]
        r = simular_escalera(fs, 0.1, 0, 0)
        self.assertEqual(r["motivo_salida"], "graduacion")
        self.assertEqual(r["segundos_salida"], 70)

    def test_sin_entrada(self):
        self.assertIsNone(simular_escalera([foto(60, complete=1)], 0.1, 0, 0))

    def test_fees_por_transaccion(self):
        fs = [foto(60, 30 * G), foto(70, 30 * G)]
        sin = simular_escalera(fs, 0.1, 0, 0)["retorno"]
        con = simular_escalera(fs, 0.1, 0, 0.001)["retorno"]
        self.assertAlmostEqual(sin - con, 2 * 0.001 / 0.1)  # 1 compra + 1 venta


if __name__ == "__main__":
    unittest.main()
