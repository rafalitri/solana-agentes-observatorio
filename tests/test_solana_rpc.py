import struct
import unittest

from comun.solana_rpc import leer_curva


def curva(v_tokens, v_sol, real_tokens, real_sol, supply, complete):
    return b"\x00" * 8 + struct.pack("<5Q?", v_tokens, v_sol, real_tokens, real_sol, supply, complete)


class TestLeerCurva(unittest.TestCase):
    def test_market_cap(self):
        c = leer_curva(curva(1073 * 10**12, 30 * 10**9, 793 * 10**12, 0, 10**15, False))
        self.assertAlmostEqual(c["market_cap_sol"], 27.96, places=2)
        self.assertEqual(c["v_sol"], 30 * 10**9)
        self.assertFalse(c["complete"])

    def test_completa_sin_reservas(self):
        c = leer_curva(curva(0, 0, 0, 0, 10**15, True))
        self.assertTrue(c["complete"])
        self.assertIsNone(c["market_cap_sol"])

    def test_cuenta_mas_larga(self):
        # las cuentas reales pueden traer bytes extra al final
        c = leer_curva(curva(1073 * 10**12, 30 * 10**9, 0, 0, 10**15, False) + b"\x01" * 32)
        self.assertAlmostEqual(c["market_cap_sol"], 27.96, places=2)


if __name__ == "__main__":
    unittest.main()
