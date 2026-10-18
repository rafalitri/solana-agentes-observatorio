import unittest

from herramientas.tenedores_informe import (CONSULTA_T, agrupar, consultada_a_tiempo, informe, mult_max,
                                            parse_medidas, peor_en_ambas_mitades, preparar, veredicto_dia1,
                                            veredicto_dia2)

G = 1e9


def foto(seg, v_sol, v_tokens=1000, complete=0):
    return {"segundos": seg, "v_sol": v_sol, "v_tokens": v_tokens, "complete": complete, "real_sol": 0}


def st(n1, m1, n2, m2):
    return {1: (n1, m1, m1, 1.0), 2: (n2, m2, m2, 1.0)}


class TestTiempo(unittest.TestCase):
    def test_a_tiempo(self):
        self.assertTrue(consultada_a_tiempo(60, 60))
        self.assertTrue(consultada_a_tiempo(10, 60))
        self.assertFalse(consultada_a_tiempo(61, 60))
        self.assertFalse(consultada_a_tiempo(None, 60))

    def test_consulta_trae_mint_ten(self):
        self.assertIn("te.seg_desde_primera_foto, te.mint FROM", CONSULTA_T)


class TestMult(unittest.TestCase):
    def test_mult(self):
        fs = [foto(60, 30 * G), foto(70, 60 * G), foto(80, 45 * G), foto(90, 99 * G, complete=1)]
        self.assertAlmostEqual(mult_max(fs), 2.0)
        self.assertIsNone(mult_max([foto(0, None)]))


class TestPreparar(unittest.TestCase):
    def reg(self, mint, ts, **k):
        r = {"mint": mint, "ts": ts, "top10_pct": 30.0, "creador_pct": 2.0, "error_ten": None,
             "seg_ten": 30, "mint_ten": mint}
        r.update(k)
        return r

    def test_descartes(self):
        fotos = [foto(0, 30 * G), foto(60, 30 * G), foto(70, 31 * G)]
        regs = [self.reg("ok", 1), self.reg("sf", 2, mint_ten=None), self.reg("er", 3, error_ten="x"),
                self.reg("nulo", 4, top10_pct=None), self.reg("tarde", 5, seg_ten=61),
                self.reg("noent", 6)]
        filas, d = preparar(regs, lambda m: [foto(0, 30 * G)] if m == "noent" else fotos)
        self.assertEqual([f["mint"] for f in filas], ["ok"])
        self.assertEqual((d["sin fila"], d["con error"], d["consultada tarde"], d["sin entrada"]), (1, 2, 1, 1))
        self.assertEqual(filas[0]["mitad"], 1)


class TestAgrupar(unittest.TestCase):
    def test_bordes(self):
        f = [{"top10_pct": 40.0, "creador_pct": 5.0}, {"top10_pct": 39.9, "creador_pct": 5.01}]
        r, s = agrupar(f, "A", 40.0)
        self.assertEqual((r, s), ([f[0]], [f[1]]))
        r, s = agrupar(f, "B", 40.0)
        self.assertEqual((r, s), ([f[1]], [f[0]]))


class TestPeor(unittest.TestCase):
    def test_peor_en_ambas(self):
        self.assertTrue(peor_en_ambas_mitades(st(50, -0.1, 50, -0.2), st(60, 0.0, 60, -0.1), 50)[0])

    def test_n_minimo(self):
        ok, motivo = peor_en_ambas_mitades(st(49, -0.1, 50, -0.2), st(60, 0.0, 60, -0.1), 50)
        self.assertFalse(ok)
        self.assertIn("mitad 1", motivo)
        self.assertFalse(peor_en_ambas_mitades(st(50, -0.1, 50, -0.2), st(60, 0.0, 49, -0.1), 50)[0])

    def test_una_mitad_no_basta_ni_empate(self):
        self.assertFalse(peor_en_ambas_mitades(st(50, -0.1, 50, 0.1), st(60, 0.0, 60, 0.0), 50)[0])
        self.assertFalse(peor_en_ambas_mitades(st(50, 0.0, 50, -0.1), st(60, 0.0, 60, 0.0), 50)[0])

    def test_media_none(self):
        self.assertFalse(peor_en_ambas_mitades(st(0, None, 50, -1), st(60, 0.0, 60, 0.0), 0)[0])

    def test_minimo_por_defecto(self):
        from comun import config
        self.assertEqual(config.TENEDORES_MIN_N, 50)
        self.assertEqual(peor_en_ambas_mitades.__defaults__[-1], 50)


class TestVeredictos(unittest.TestCase):
    def test_dia1(self):
        self.assertEqual(veredicto_dia1({"A": True, "B": False}), (["A"], "PASA_A_DIA2: A"))
        self.assertEqual(veredicto_dia1({"A": False, "B": True})[1], "PASA_A_DIA2: B")
        self.assertEqual(veredicto_dia1({"A": True, "B": True})[1], "PASA_A_DIA2: A,B")
        self.assertEqual(veredicto_dia1({"A": False, "B": False})[1], "PASA_A_DIA2: ninguna")

    def test_dia2_solo_medidas_pedidas(self):
        self.assertEqual(veredicto_dia2({"A": True, "B": True}, ["A"]), (["A"], "VEREDICTO: APORTA: A"))
        self.assertEqual(veredicto_dia2({"A": True, "B": True}, ["A", "B"])[1], "VEREDICTO: APORTA: A,B")
        self.assertEqual(veredicto_dia2({"A": False, "B": True}, ["A"])[1], "VEREDICTO: no aporta")

    def test_parse_medidas(self):
        self.assertEqual(parse_medidas("A,B"), ["A", "B"])
        with self.assertRaises(ValueError):
            parse_medidas("C")


class TestInforme(unittest.TestCase):
    def filas(self):
        return [{"mint": str(i), "ts": i, "top10_pct": 10.0 + i * 10, "creador_pct": 1.0 if i < 4 else 9.0,
                 "ret": 0.1 if i < 4 else -0.1, "mult": 1.5, "mitad": 1 if i % 2 == 0 else 2} for i in range(8)]

    def test_dia1_imprime_umbral_y_veredicto(self):
        txt = "\n".join(informe(self.filas(), {"sin fila": 0}, "dia1", minimo=1))
        self.assertIn("UMBRAL_TOP10 congelado: ", txt)
        self.assertIn("APORTA_DIA1 B: sí", txt)
        self.assertIn("PASA_A_DIA2: ", txt)

    def test_dia2_info(self):
        txt = "\n".join(informe(self.filas(), {}, "dia2", 50.0, ["A"], minimo=1))
        self.assertIn("[solo información]", txt.split("SE_REPITE B")[1])
        self.assertIn("VEREDICTO: ", txt)


if __name__ == "__main__":
    unittest.main()
