import unittest

from comun.vigilancia import cambios, revisar

V = ("analista",)


def rev(estado, ts=1000.0, ahora=1000.0):
    return revisar([("analista", ts, estado, 0, 0)], ahora, V, 180)


class TestRevisar(unittest.TestCase):
    def test_sin_fila(self):
        self.assertEqual(revisar([], 1000, V, 180), {"analista": "sin_latido"})

    def test_ok(self):
        self.assertEqual(rev("ok"), {"analista": None})

    def test_limite_justo_no_es_viejo(self):
        self.assertEqual(rev("ok", ahora=1180.0), {"analista": None})

    def test_pasado_el_limite_es_viejo(self):
        self.assertEqual(rev("ok", ahora=1181.0), {"analista": "latido_viejo"})

    def test_parado_y_sin_ollama(self):
        self.assertEqual(rev("parado"), {"analista": "parado"})
        self.assertEqual(rev("sin_ollama"), {"analista": "sin_ollama"})

    def test_viejo_manda_sobre_sin_ollama(self):
        self.assertEqual(rev("sin_ollama", ahora=2000.0), {"analista": "latido_viejo"})

    def test_parado_manda_sobre_viejo(self):
        self.assertEqual(rev("parado", ahora=5000.0), {"analista": "parado"})

    def test_ignora_agentes_no_vigilados(self):
        self.assertEqual(revisar([("otro", 0, "parado", 0, 0)], 1000, V, 180),
                         {"analista": "sin_latido"})


class TestCambios(unittest.TestCase):
    def test_primera_revision_solo_problemas(self):
        self.assertEqual(cambios({}, {"a": None, "b": "sin_latido"}), [("b", None, "sin_latido")])

    def test_primera_revision_sin_problemas(self):
        self.assertEqual(cambios({}, {"a": None}), [])

    def test_recuperacion(self):
        self.assertEqual(cambios({"a": "latido_viejo"}, {"a": None}), [("a", "latido_viejo", None)])

    def test_no_repite_problema(self):
        self.assertEqual(cambios({"a": "parado"}, {"a": "parado"}), [])

    def test_cambio_de_problema(self):
        self.assertEqual(cambios({"a": "sin_ollama"}, {"a": "latido_viejo"}),
                         [("a", "sin_ollama", "latido_viejo")])


if __name__ == "__main__":
    unittest.main()
