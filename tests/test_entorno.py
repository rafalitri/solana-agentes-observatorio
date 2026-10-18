import os
import tempfile
import unittest
import urllib.error
from unittest import mock

from comun import solana_rpc
from comun.entorno import leer_env

URL = "https://rpc.ejemplo.test/?api-key=SECRETA"


class TestLeerEnv(unittest.TestCase):
    def _archivo(self, texto):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        ruta = os.path.join(d.name, ".env")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(texto)
        return ruta

    def setUp(self):
        p = mock.patch.dict(os.environ)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("CLAVE_X", None)

    def test_lee_con_espacios_comentarios_y_vacias(self):
        ruta = self._archivo("# comentario\n\n  OTRA = 1 \n  CLAVE_X =  https://a/?k=b=c  \n")
        self.assertEqual(leer_env("CLAVE_X", ruta), "https://a/?k=b=c")

    def test_clave_ausente_o_comentada(self):
        ruta = self._archivo("# CLAVE_X=no\nOTRA=1\n")
        self.assertIsNone(leer_env("CLAVE_X", ruta))

    def test_archivo_inexistente(self):
        self.assertIsNone(leer_env("CLAVE_X", os.path.join(tempfile.gettempdir(), "no_existe_xyz", ".env")))

    def test_entorno_tiene_prioridad(self):
        ruta = self._archivo("CLAVE_X=del_archivo\n")
        os.environ["CLAVE_X"] = "del_entorno"
        self.assertEqual(leer_env("CLAVE_X", ruta), "del_entorno")


class TestErroresSinUrl(unittest.TestCase):
    def test_error_de_red_con_url_se_oculta(self):
        err = urllib.error.URLError(f"fallo en {URL}")
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(RuntimeError) as cm:
                solana_rpc.tokens_mayores("mint", URL)
        self.assertNotIn("SECRETA", str(cm.exception))
        self.assertIn("<rpc>", str(cm.exception))
        self.assertIsNone(cm.exception.__cause__)  # sin cadena que reimprima el original

    def test_http_500_con_url_se_oculta(self):
        err = urllib.error.HTTPError(URL, 500, f"malo {URL}", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(RuntimeError) as cm:
                solana_rpc.duenos_de_cuentas(["a"], URL)
        self.assertNotIn("SECRETA", str(cm.exception))

    def test_429_sigue_siendo_limite(self):
        err = urllib.error.HTTPError(URL, 429, "x", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(solana_rpc.RpcLimite):
                solana_rpc.tokens_mayores("mint", URL)

    def test_error_json_con_url_se_oculta(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        with mock.patch("urllib.request.urlopen", return_value=resp), \
                mock.patch("json.load", return_value={"error": {"message": f"en {URL}"}}):
            with self.assertRaises(RuntimeError) as cm:
                solana_rpc.tokens_mayores("mint", URL)
        self.assertNotIn("SECRETA", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
