import unittest

from agentes.riesgo import evaluar

BUENO = {"mint": "Abc123pump", "name": "Gato", "symbol": "GATO", "solAmount": 0.5}
LIMPIO = {"tokens_creador_24h": 0, "repes_nombre_1h": 0, "repes_uri_1h": 0}


class TestRiesgo(unittest.TestCase):
    def test_pasa(self):
        self.assertEqual(evaluar(BUENO, LIMPIO), {"pasa": True, "motivos": [], "marcas": []})

    def test_r1_creador_en_serie(self):
        r = evaluar(BUENO, {**LIMPIO, "tokens_creador_24h": 3})
        self.assertFalse(r["pasa"])
        self.assertEqual(r["motivos"], ["creador_en_serie"])

    def test_r2_copia(self):
        for clave in ("repes_nombre_1h", "repes_uri_1h"):
            r = evaluar(BUENO, {**LIMPIO, clave: 1})
            self.assertEqual(r["motivos"], ["copia"])

    def test_r3_compra_creador_baja(self):
        sin_sol = {k: v for k, v in BUENO.items() if k != "solAmount"}
        for datos in ({**BUENO, "solAmount": 0.001}, sin_sol):
            self.assertEqual(evaluar(datos, LIMPIO)["motivos"], ["compra_creador_baja"])

    def test_r3_limite(self):
        self.assertEqual(evaluar({**BUENO, "solAmount": 0.09}, LIMPIO)["motivos"], ["compra_creador_baja"])
        self.assertTrue(evaluar({**BUENO, "solAmount": 0.1}, LIMPIO)["pasa"])

    def test_mayhem(self):
        r = evaluar({**BUENO, "is_mayhem_mode": True}, LIMPIO)
        self.assertFalse(r["pasa"])
        self.assertEqual(r["motivos"], ["mayhem"])
        self.assertTrue(evaluar({**BUENO, "is_mayhem_mode": False}, LIMPIO)["pasa"])
        self.assertTrue(evaluar(BUENO, LIMPIO)["pasa"])

    def test_r4_sin_nombre(self):
        for datos in ({**BUENO, "name": "  "}, {**BUENO, "symbol": ""}):
            self.assertEqual(evaluar(datos, LIMPIO)["motivos"], ["sin_nombre"])

    def test_r5_mint_sin_pump_solo_marca(self):
        r = evaluar({**BUENO, "mint": "Abc123"}, LIMPIO)
        self.assertTrue(r["pasa"])
        self.assertEqual(r["marcas"], ["mint_sin_pump"])


if __name__ == "__main__":
    unittest.main()
