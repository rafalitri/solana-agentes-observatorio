import unittest

from agentes.seguimiento import (
    actualizar_con_foto, caducados, estado_latido, graduados, imprimir_ilegible, raw_para,
)


class TestEstadoLatido(unittest.TestCase):
    def test_sin_fotos_aunque_haya_conexion(self):
        self.assertEqual(estado_latido(True, 5, 0), "sin_fotos")

    def test_sin_fotos_manda_sobre_sin_pumpportal(self):
        self.assertEqual(estado_latido(False, 5, 0), "sin_fotos")

    def test_sin_conexion_con_fotos(self):
        self.assertEqual(estado_latido(False, 5, 30), "sin_pumpportal")

    def test_ok(self):
        self.assertEqual(estado_latido(True, 5, 30), "ok")

    def test_sin_seguidos_depende_de_ws(self):
        self.assertEqual(estado_latido(True, 0, 0), "ok")
        self.assertEqual(estado_latido(False, 0, 0), "sin_pumpportal")


class TestImprimirIlegible(unittest.TestCase):
    def test_primer_intento_no_se_imprime(self):
        self.assertFalse(imprimir_ilegible(1))

    def test_segundo_y_siguientes_si(self):
        self.assertTrue(imprimir_ilegible(2))
        self.assertTrue(imprimir_ilegible(3))


class TestCaducados(unittest.TestCase):
    def test_vacio(self):
        self.assertEqual(caducados({}, 1000, 1800), [])

    def test_quita_solo_los_viejos(self):
        seguidos = {"viejo": 0, "justo": 200, "nuevo": 500}
        self.assertEqual(caducados(seguidos, 2000, 1800), ["viejo", "justo"])

    def test_no_modifica_el_dict(self):
        seguidos = {"viejo": 0}
        caducados(seguidos, 5000, 1800)
        self.assertEqual(seguidos, {"viejo": 0})


def _fila(mint, complete):
    return (mint, 0.0, 0, 1, 1, 1, complete, 1, "raw")


class TestGraduados(unittest.TestCase):
    def test_vacio(self):
        self.assertEqual(graduados([]), [])

    def test_solo_los_completos(self):
        filas = [_fila("a", 0), _fila("b", 1), _fila("c", 0), _fila("d", 1)]
        self.assertEqual(graduados(filas), ["b", "d"])


class TestRawPrimeraFoto(unittest.TestCase):
    def test_primera_foto_conserva_raw_y_las_demas_none(self):
        con_foto = set()
        self.assertEqual(raw_para("a", con_foto, "X"), "X")
        actualizar_con_foto(con_foto, [_fila("a", 0)], {"a": 1})
        self.assertIsNone(raw_para("a", con_foto, "X"))

    def test_curva_ilegible_no_entra_y_conserva_raw(self):
        con_foto = set()
        # "b" estaba seguido pero no generó fila (curva ilegible)
        actualizar_con_foto(con_foto, [_fila("a", 0)], {"a": 1, "b": 1})
        self.assertEqual(con_foto, {"a"})
        self.assertEqual(raw_para("b", con_foto, "X"), "X")

    def test_descarta_los_que_ya_no_se_siguen(self):
        con_foto = {"a", "viejo"}
        actualizar_con_foto(con_foto, [], {"a": 1})
        self.assertEqual(con_foto, {"a"})


if __name__ == "__main__":
    unittest.main()
