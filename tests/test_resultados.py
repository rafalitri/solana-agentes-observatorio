import unittest
from datetime import datetime

from herramientas.resultados import (
    agrupar, calcular_token, curva_atipica, formatear, grupos_de, mult, parse_desde, resumir)


def _t(*cortes):
    # cortes: (segundos, mc, complete) -> calcular_token con mc_inicial = 10
    return calcular_token(10.0, list(cortes))


class TestCalcularToken(unittest.TestCase):
    def test_normal_y_maximo(self):
        r = _t((60, 20.0, 0), (290, 50.0, 0), (900, 30.0, 0), (1800, 10.0, 0))
        self.assertEqual(r["mult_max"], 5.0)
        self.assertEqual(r["mults"], {5: 5.0, 15: 3.0, 30: 1.0})
        self.assertFalse(r["graduado"])

    def test_max_ignora_none(self):
        r = _t((60, None, 0), (120, 30.0, 0))
        self.assertEqual(r["mult_max"], 3.0)

    def test_sin_mc_alguno(self):
        self.assertIsNone(_t((60, None, 0))["mult_max"])

    def test_foto_lejana_es_none(self):
        # la última foto <= 300 s está a 240 s: fuera de tolerancia
        r = _t((60, 20.0, 0), (800, 30.0, 0))
        self.assertIsNone(r["mults"][5])
        self.assertEqual(r["mults"][15], 3.0)

    def test_sin_fotos_previas_es_none(self):
        self.assertIsNone(_t((400, 20.0, 0))["mults"][5])

    def test_graduado_a_los_20_min(self):
        r = _t((290, 20.0, 0), (890, 40.0, 0), (1200, 90.0, 1))
        self.assertEqual(r["mults"], {5: 2.0, 15: 4.0, 30: "graduado"})
        self.assertTrue(r["graduado"])
        self.assertFalse(r["grad_inst"])

    def test_graduado_instantaneo(self):
        r = _t((0, 90.0, 1))
        self.assertTrue(r["grad_inst"])
        self.assertEqual(r["mults"][5], "graduado")

    def test_mc_inicial_cero_o_none(self):
        self.assertIsNone(calcular_token(0, [(60, 5.0, 0)])["mult_max"])
        self.assertIsNone(calcular_token(None, [(60, 5.0, 0)])["mult_max"])
        self.assertIsNone(mult(5.0, 0))


class TestGruposDe(unittest.TestCase):
    def test_pasa(self):
        self.assertEqual(grupos_de(True, [], [], False), ["pasan"])

    def test_descartado_con_dos_motivos(self):
        self.assertEqual(grupos_de(False, ["copia", "sin_nombre"], [], False),
                         ["descartados", "regla:copia", "regla:sin_nombre"])

    def test_marca_y_control(self):
        self.assertEqual(grupos_de(True, [], ["mint_sin_pump"], True),
                         ["pasan", "marca:mint_sin_pump", "control"])


class TestResumir(unittest.TestCase):
    def test_vacio(self):
        r = resumir([])
        self.assertEqual(r["n"], 0)
        self.assertIsNone(r["med_max"])
        self.assertIsNone(r["pct_grad"])
        self.assertEqual(r["med_5"], (None, 0))

    def test_medianas_y_umbrales_inclusivos(self):
        rs = [_t((290, 20.0, 0)), _t((290, 50.0, 0)), _t((290, 10.0, 0)), _t((290, 30.0, 0))]
        r = resumir(rs)
        self.assertEqual(r["med_max"], 2.5)  # mediana de 1, 2, 3, 5 (n par)
        self.assertEqual(r["pct_2"], 75.0)
        self.assertEqual(r["pct_5"], 25.0)
        self.assertEqual(r["med_5"], (2.5, 4))

    def test_mediana_impar(self):
        rs = [_t((290, 10.0, 0)), _t((290, 30.0, 0)), _t((290, 20.0, 0))]
        self.assertEqual(resumir(rs)["med_5"], (2.0, 3))

    def test_excluye_graduado_y_none_del_conteo(self):
        rs = [_t((290, 20.0, 0)), _t((100, 20.0, 1), (290, 20.0, 1)), _t((900, 20.0, 0))]
        r = resumir(rs)
        self.assertEqual(r["med_5"], (2.0, 1))  # el graduado y el None no entran
        self.assertEqual(r["pct_grad"], 100 / 3)


class TestAgrupar(unittest.TestCase):
    def test_instantaneos_y_grupos(self):
        tokens = [
            (10.0, [(60, 20.0, 0)], (True, [], [], False), False, []),
            (10.0, [(60, 20.0, 0)], (False, ["copia"], [], True), False, []),
            (10.0, [(0, 90.0, 1)], (True, [], [], False), False, []),  # instantáneo: fuera de todo
            (10.0, [(60, 20.0, 0)], None, False, []),
        ]
        grupos, inst, sin_dec, _, _ = agrupar(tokens)
        self.assertEqual(inst, 1)
        self.assertEqual(sin_dec, 1)
        self.assertEqual(len(grupos["todos"]), 3)
        self.assertEqual(len(grupos["pasan"]), 1)
        self.assertEqual(len(grupos["descartados"]), 1)
        self.assertEqual(len(grupos["regla:copia"]), 1)
        self.assertEqual(len(grupos["control"]), 1)


def _curva(base_sol, real_sol=None, v_tokens=(1000, 1000)):
    # una foto por v_tokens, con v_sol = base + real_sol (lamports)
    v_sol = int(base_sol * 1e9) + (real_sol or 0)
    return [(v_sol, real_sol, vt) for vt in v_tokens]


class TestCurvaAtipica(unittest.TestCase):
    DEC = (True, [], [], False)

    def test_base_30_entra(self):
        grupos, _, _, atip, _ = agrupar(
            [(10.0, [(60, 20.0, 0)], self.DEC, False, _curva(30.0, real_sol=5 * 10**9))])
        self.assertEqual(atip, 0)
        self.assertEqual(len(grupos["todos"]), 1)

    def test_base_726_queda_fuera(self):
        tokens = [
            (10.0, [(60, 20.0, 0)], self.DEC, False, _curva(726.44)),  # sin operaciones
            (10.0, [(60, 20.0, 0)], self.DEC, False, _curva(726.44, v_tokens=(1000, 900))),
        ]
        grupos, _, _, atip, atip_ops = agrupar(tokens)
        self.assertEqual((atip, atip_ops), (2, 1))
        self.assertEqual(grupos["todos"], [])
        self.assertEqual(grupos["pasan"], [])

    def test_mayhem_no_es_atipico(self):
        grupos, _, _, atip, _ = agrupar(
            [(10.0, [(60, 20.0, 0)], self.DEC, True, _curva(0.43))])
        self.assertEqual(atip, 0)
        self.assertEqual(len(grupos["todos"]), 1)
        self.assertEqual(len(grupos["pasan"]), 1)
        self.assertEqual(len(grupos["mayhem"]), 1)

    def test_real_sol_none_y_v_sol_none(self):
        self.assertFalse(curva_atipica(False, _curva(30.0)))
        # la 1.ª foto sin v_sol se salta; sin ninguna, no se puede calcular -> normal
        self.assertTrue(curva_atipica(False, [(None, None, None)] + _curva(0.91)))
        self.assertFalse(curva_atipica(False, [(None, None, None)]))
        self.assertFalse(curva_atipica(False, []))


class TestParseYFormato(unittest.TestCase):
    def test_parse_valido(self):
        self.assertEqual(parse_desde("2026-10-16 01:32"), datetime(2026, 10, 16, 1, 32).timestamp())

    def test_parse_invalido(self):
        with self.assertRaises(ValueError):
            parse_desde("16/10/2026")

    def test_formatear(self):
        texto = formatear(*agrupar([
            (10.0, [(0, 90.0, 1)], None, False, []),
            (10.0, [(60, 20.0, 0)], (True, [], [], False), False, []),
            (10.0, [(60, 20.0, 0)], None, False, _curva(726.44, v_tokens=(1000, 900))),
        ]))
        lineas = texto.splitlines()
        self.assertTrue(lineas[2].startswith("todos"))
        self.assertTrue(lineas[3].startswith("pasan"))
        self.assertTrue(any(l.startswith("mayhem") for l in lineas))
        self.assertIn("Graduados instantaneos (excluidos de todos los grupos): 1", texto)
        self.assertIn("Curva atipica (excluidos de todos los grupos): 1 (con operaciones: 1)", texto)


if __name__ == "__main__":
    unittest.main()
