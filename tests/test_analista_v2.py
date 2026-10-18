import unittest
import urllib.error
from unittest import mock

from agentes import analista_v2

IPFS = "https://x.io/ipfs/CID"


class TestDescargarMetadatos(unittest.TestCase):
    def test_primera_url_responde(self):
        with mock.patch.object(analista_v2, "_bajar", return_value='{"description": "hola"}') as b:
            meta, ok, seg = analista_v2.descargar_metadatos(IPFS)
        self.assertEqual((meta["descripcion"], ok), ("hola", 1))
        self.assertEqual(b.call_count, 1)
        self.assertGreaterEqual(seg, 0)

    def test_si_la_primera_falla_prueba_la_segunda(self):
        with mock.patch.object(analista_v2, "_bajar",
                               side_effect=[urllib.error.URLError("x"), '{"description": "b"}']) as b:
            meta, ok, _ = analista_v2.descargar_metadatos(IPFS)
        self.assertEqual((meta["descripcion"], ok, b.call_count), ("b", 1, 2))

    def test_ninguna_responde(self):
        with mock.patch.object(analista_v2, "_bajar", side_effect=TimeoutError):
            self.assertEqual(analista_v2.descargar_metadatos(IPFS)[:2], (None, 0))

    def test_json_roto_da_none_sin_seguir_probando(self):
        with mock.patch.object(analista_v2, "_bajar", return_value="<html>") as b:
            self.assertEqual(analista_v2.descargar_metadatos(IPFS)[:2], (None, 0))
        self.assertEqual(b.call_count, 1)

    def test_uri_vacia_o_no_http_no_descarga(self):
        with mock.patch.object(analista_v2, "_bajar") as b:
            self.assertEqual(analista_v2.descargar_metadatos("")[:2], (None, 0))
            self.assertEqual(analista_v2.descargar_metadatos("file:///etc/passwd")[:2], (None, 0))
        b.assert_not_called()


if __name__ == "__main__":
    unittest.main()
