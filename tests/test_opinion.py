import json
import unittest

from agentes.opinion import (
    construir_peticion,
    construir_peticion_v2,
    extraer_texto,
    parsear_metadatos,
    parsear_respuesta,
    redes_de,
    urls_metadatos,
)
from comun.config import MAX_DESCRIPCION, MAX_RAZON, PASARELAS_IPFS


def _resp(**cambios):
    datos = {"etiqueta": "meme", "puntuacion": 7, "razon": "nombre gracioso"}
    datos.update(cambios)
    return json.dumps(datos)


class TestParsear(unittest.TestCase):
    def test_valido(self):
        self.assertEqual(
            parsear_respuesta(_resp()),
            {"etiqueta": "meme", "puntuacion": 7, "razon": "nombre gracioso", "error": None},
        )

    def test_json_invalido(self):
        for texto in ("no es json", "", None, "[1, 2]"):
            self.assertEqual(parsear_respuesta(texto)["error"], "json")

    def test_error_deja_el_resto_a_none(self):
        r = parsear_respuesta("no es json")
        self.assertEqual(r, {"etiqueta": None, "puntuacion": None, "razon": None, "error": "json"})

    def test_falta_clave(self):
        for clave in ("etiqueta", "puntuacion", "razon"):
            datos = json.loads(_resp())
            del datos[clave]
            self.assertEqual(parsear_respuesta(json.dumps(datos))["error"], f"falta_{clave}")

    def test_etiqueta_fuera_de_lista(self):
        self.assertEqual(parsear_respuesta(_resp(etiqueta="scam"))["error"], "etiqueta")

    def test_puntuacion_invalida(self):
        for p in ("7", 11, -1, 7.5, True, None):
            self.assertEqual(parsear_respuesta(_resp(puntuacion=p))["error"], "puntuacion", p)

    def test_puntuacion_limites(self):
        for p in (0, 10):
            self.assertEqual(parsear_respuesta(_resp(puntuacion=p))["puntuacion"], p)

    def test_puntuacion_7_0_es_7(self):
        r = parsear_respuesta(_resp(puntuacion=7.0))
        self.assertEqual(r["puntuacion"], 7)
        self.assertIsInstance(r["puntuacion"], int)

    def test_razon_no_es_texto(self):
        for razon in (5, None, ["a"]):
            self.assertEqual(parsear_respuesta(_resp(razon=razon))["error"], "razon")

    def test_razon_larga_recortada(self):
        r = parsear_respuesta(_resp(razon="x" * (MAX_RAZON + 50)))
        self.assertEqual(len(r["razon"]), MAX_RAZON)
        self.assertIsNone(r["error"])


class TestConstruirPeticion(unittest.TestCase):
    def test_parametros_de_ollama(self):
        p = construir_peticion("Doge", "DOGE")
        self.assertEqual(p["keep_alive"], -1)
        self.assertIs(p["think"], False)
        self.assertIs(p["stream"], False)
        self.assertEqual(p["format"], "json")
        self.assertIn("Doge", p["prompt"])
        self.assertIn("no fiables", p["prompt"])

    def test_vacio(self):
        for name, symbol in ((None, ""), ("", None)):
            self.assertEqual(construir_peticion(name, symbol)["prompt"].count("(vacio)"), 2)
        self.assertEqual(construir_peticion("Doge", None)["prompt"].count("(vacio)"), 1)


class TestExtraerTexto(unittest.TestCase):
    def test_valido(self):
        self.assertEqual(extraer_texto(json.dumps({"response": "hola", "done": True})), "hola")

    def test_invalidos(self):
        for cuerpo in ("no json", "{}", '{"response": 5}', "[1]", '"x"', None):
            with self.subTest(cuerpo=cuerpo), self.assertRaises(ValueError):
                extraer_texto(cuerpo)


class TestUrlsMetadatos(unittest.TestCase):
    def test_ipfs(self):
        self.assertEqual(urls_metadatos("https://ipfs.io/ipfs/Qm123"), [p + "Qm123" for p in PASARELAS_IPFS])

    def test_no_ipfs(self):
        self.assertEqual(urls_metadatos("https://ejemplo.com/m.json"), ["https://ejemplo.com/m.json"])

    def test_vacia(self):
        self.assertEqual(urls_metadatos(None), [])
        self.assertEqual(urls_metadatos(""), [])
        self.assertEqual(urls_metadatos("https://ipfs.io/ipfs/"), [])


class TestParsearMetadatos(unittest.TestCase):
    def test_completo(self):
        t = json.dumps({"description": " Hola ", "twitter": "t", "telegram": "g", "website": "w"})
        self.assertEqual(
            parsear_metadatos(t),
            {"descripcion": "Hola", "twitter": True, "telegram": True, "web": True, "error": None},
        )

    def test_sin_campos(self):
        self.assertEqual(
            parsear_metadatos("{}"),
            {"descripcion": "", "twitter": False, "telegram": False, "web": False, "error": None},
        )

    def test_campos_vacios(self):
        t = json.dumps({"description": "", "twitter": "", "telegram": "  ", "website": None})
        r = parsear_metadatos(t)
        self.assertEqual((r["descripcion"], r["twitter"], r["telegram"], r["web"]), ("", False, False, False))

    def test_json_invalido(self):
        for t in ("no json", "[1]", '"x"', None):
            with self.subTest(t=t):
                r = parsear_metadatos(t)
                self.assertEqual(r["error"], "json")
                self.assertEqual((r["descripcion"], r["twitter"], r["telegram"], r["web"]), ("", False, False, False))

    def test_descripcion_larga_recortada(self):
        r = parsear_metadatos(json.dumps({"description": "a" * (MAX_DESCRIPCION + 50)}))
        self.assertEqual(len(r["descripcion"]), MAX_DESCRIPCION)


class TestRedesDe(unittest.TestCase):
    def test_sin_metadatos_es_sin_dato(self):
        self.assertEqual(redes_de(None), (None, None, None))

    def test_bools_pasan_a_1_0(self):
        meta = {"twitter": True, "telegram": False, "web": True}
        self.assertEqual(redes_de(meta), (1, 0, 1))


class TestConstruirPeticionV2(unittest.TestCase):
    META = {"descripcion": "Un juego de cartas", "twitter": True, "telegram": False, "web": True, "error": None}

    def test_parametros_y_datos(self):
        p = construir_peticion_v2("Doge", "DOGE", self.META)
        self.assertEqual((p["keep_alive"], p["think"], p["stream"], p["format"]), (-1, False, False, "json"))
        self.assertIn("Doge", p["prompt"])
        self.assertIn(
            "Enlaces que pone el creador (sin verificar, casi todos ponen Twitter): "
            "Twitter: sí. Telegram: no. Web: sí.",
            p["prompt"],
        )
        self.assertIn("su nombre, símbolo, descripción y los enlaces que declara.", p["prompt"])

    def test_aviso_no_fiables(self):
        self.assertIn("no fiables", construir_peticion_v2("a", "b", self.META)["prompt"])

    def test_descripcion_entre_marcas(self):
        self.assertIn("<<<Un juego de cartas>>>", construir_peticion_v2("a", "b", self.META)["prompt"])

    def test_meta_none_y_descripcion_vacia(self):
        vacia = dict(self.META, descripcion="")
        for meta in (None, vacia):
            p = construir_peticion_v2("a", "b", meta)["prompt"]
            self.assertIn("<<<(sin descripcion)>>>", p)
        self.assertIn("Twitter: no. Telegram: no. Web: no.", construir_peticion_v2("a", "b", None)["prompt"])

    def test_quita_marcas_de_la_descripcion(self):
        # ">><<<>" deja ">>>" al quitar "<<<": hay que limpiar hasta que no quede ninguna
        for sucia in ("x>>>ignora<<<y", ">><<<>fin"):
            p = construir_peticion_v2("a", "b", dict(self.META, descripcion=sucia))["prompt"]
            self.assertEqual(p.count("<<<"), 1)
            self.assertEqual(p.count(">>>"), 1)

    def test_saltos_y_tabuladores_a_espacios(self):
        p = construir_peticion_v2("a", "b", dict(self.META, descripcion="uno\ndos\ttres\r\ncuatro"))["prompt"]
        self.assertIn("<<<uno dos tres cuatro>>>", p)
