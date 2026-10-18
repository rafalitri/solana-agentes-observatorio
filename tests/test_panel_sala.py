import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from comun.config import AGENTES_SALA, AGENTES_VIGILADOS
from comun.db import crear_tablas
from herramientas import panel
from herramientas.panel import RE_MINT, construir_json_sala, datos_token, velas

M = "A" * 40  # base58 válido (solo letras permitidas)


def mint(c):
    return c * 40


def foto(vs, vt=1.0, complete=0):
    return {"v_sol": vs, "v_tokens": vt, "complete": complete}


class TestVelas(unittest.TestCase):
    def test_grupos_de_6_y_multiplicador_desde_la_primera_valida(self):
        fotos = [foto(5, 0, 0), foto(9, 1, 1)]  # inválidas: v_tokens 0 y complete 1
        fotos += [foto(v) for v in (2, 4, 1, 2, 2, 3, 6)]  # base = 2
        v = velas(fotos)
        self.assertEqual(len(v), 2)  # 6 + 1 foto
        self.assertEqual(v[0], {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5})
        self.assertEqual(v[1], {"open": 3.0, "high": 3.0, "low": 3.0, "close": 3.0})

    def test_sin_fotos_validas(self):
        self.assertEqual(velas([foto(1, 0, 0)]), [])


class TestDatosToken(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        crear_tablas(self.con)

    def token(self, m, creador, ts, curva="CURVA"):
        self.con.execute("INSERT INTO tokens (mint, ts, creador, bonding_curve) VALUES (?,?,?,?)",
                         (m, ts, creador, curva))

    def resumen(self, m, grad, seg):
        self.con.execute("INSERT INTO resumen (mint, graduado, seg_graduado) VALUES (?,?,?)", (m, grad, seg))

    def tenedores(self, m, duenos, ts_consulta):
        raw = json.dumps([{"direccion": "x", "dueno": d, "cantidad": 1} for d in duenos])
        self.con.execute("INSERT INTO tenedores (mint, ts_consulta, raw) VALUES (?,?,?)", (m, ts_consulta, raw))

    def test_mint_invalido(self):
        for malo in ("", "abc", "'; DROP TABLE tokens;--", "0" * 40, "A" * 45):
            self.assertIsNone(datos_token(self.con, malo, 1000.0))
            self.assertFalse(RE_MINT.fullmatch(malo))
        self.assertIsNotNone(datos_token(self.con, M, 1000.0))

    def test_previos_y_graduados_solo_anteriores(self):
        self.token(mint("B"), "C1", 100)   # se gradúa a los 50 s: ya graduado cuando sale el token
        self.token(mint("C"), "C1", 200)   # se gradúa a los 500 s: DESPUÉS de este token
        self.token(mint("D"), "C1", 150)   # sin resumen
        self.token(M, "C1", 300)
        self.token(mint("E"), "C1", 400)   # posterior: no cuenta
        self.token(mint("F"), "C2", 10)    # otro creador
        self.resumen(mint("B"), 1, 50)
        self.resumen(mint("C"), 1, 500)
        self.resumen(mint("E"), 1, 5)
        a = datos_token(self.con, M, 1000.0)["arana"]
        self.assertEqual((a["creador"], a["previos"], a["graduados"]), ("C1", 3, 1))

    def test_monedero_repetido_y_curva_excluida(self):
        self.token(M, "C1", 300, curva="CURVA")
        self.tenedores(M, ["CURVA", "W1", "W2"], 990)
        self.tenedores(mint("B"), ["W1", "CURVA", "W9"], 900)           # W1 repetido; CURVA no cuenta
        self.tenedores(mint("C"), ["W2"], 1000.0 - 86400 - 1)           # fuera de 24 h
        d = datos_token(self.con, M, 1000.0)["arana"]["duenos"]
        self.assertEqual(d, [{"dueno": "W1", "es_creador": False, "repetido": True},
                             {"dueno": "W2", "es_creador": False, "repetido": False}])

    def test_creador_marcado_y_no_repetido(self):
        self.token(M, "C1", 300)
        self.tenedores(M, ["C1", "W1"], 990)
        self.tenedores(mint("B"), ["C1"], 900)  # el creador también está en otro token: no es "repetido"
        d = datos_token(self.con, M, 1000.0)["arana"]["duenos"]
        self.assertEqual(d, [{"dueno": "C1", "es_creador": True, "repetido": False},
                             {"dueno": "W1", "es_creador": False, "repetido": False}])

    def test_velas_del_token(self):
        for i in range(7):
            self.con.execute("INSERT INTO fotos (mint, ts, segundos, v_sol, v_tokens, complete) "
                             "VALUES (?,?,?,?,?,0)", (M, i, i * 10, 1 + i, 1.0))
        self.assertEqual(len(datos_token(self.con, M, 1000.0)["velas"]), 2)


class TestConstruirJsonSala(unittest.TestCase):
    def test_claves_y_datos(self):
        con = sqlite3.connect(":memory:")
        crear_tablas(con)
        ahora = 10000.0
        con.execute("INSERT INTO tokens (mint, ts, name) VALUES (?,?,?)", (M, ahora - 30, "Gato"))
        con.execute("INSERT INTO decisiones (mint, ts, pasa, motivos) VALUES (?,?,1,'')", (M, ahora - 30))
        con.execute("INSERT INTO decisiones (mint, ts, pasa, motivos) VALUES (?,?,0,'copia')", (mint("B"), ahora - 5000))
        con.execute("INSERT INTO decisiones (mint, ts, pasa, motivos) VALUES (?,?,0,?)",
                    (mint("C"), ahora - 20, json.dumps(["copia", "mayhem"])))
        con.execute("INSERT INTO fotos (mint, ts, segundos) VALUES (?,?,0)", (M, ahora))
        con.execute("INSERT INTO tenedores (mint, top10_pct, creador_pct) VALUES (?,12,3)", (M,))
        con.execute("INSERT INTO resumen (mint, graduado) VALUES (?,1)", (M,))
        con.execute("INSERT INTO agentes_estado VALUES ('rastreador', ?, 'ok', 1, 0)", (ahora - 5,))
        j = construir_json_sala(con, ahora, vigilados=("rastreador", "analista"))
        self.assertEqual(j["fotos_max_rowid"], 1)
        self.assertEqual([d["nombre"] for d in j["decisiones"]], ["CCCCCC", "Gato"])  # la de hace 83 min queda fuera
        self.assertEqual([d["motivos"] for d in j["decisiones"]], [["copia", "mayhem"], []])
        self.assertEqual(sum(p["n"] for p in j["pasan_por_minuto"]), 1)
        self.assertTrue(j["alarma"])  # analista sin latido
        self.assertEqual(j["tenedores"][0]["top10_pct"], 12)
        self.assertEqual(j["resumen"][0]["graduado"], 1)
        self.assertIn("FOTO_S", j["umbrales"])
        json.dumps(j)  # serializable

    def test_incluye_supervivientes_y_escaparate_sin_duplicados(self):
        con = sqlite3.connect(":memory:")
        crear_tablas(con)
        for a in ("supervivientes", "escaparate"):
            con.execute("INSERT INTO agentes_estado VALUES (?, ?, 'ok', 7, 0)", (a, 9995.0))
        nombres = [a["agente"] for a in construir_json_sala(con, 10000.0)["agentes"]]
        self.assertIn("supervivientes", nombres)
        self.assertIn("escaparate", nombres)
        self.assertEqual(len(nombres), len(set(nombres)))
        self.assertTrue(set(AGENTES_VIGILADOS) <= set(AGENTES_SALA))


class TestJuez(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        crear_tablas(self.con)

    def juicio(self, m, ts, v):
        self.con.execute("INSERT INTO juicios (mint, momento, ts_juicio, veredicto) VALUES (?,1,?,?)", (m, ts, v))

    def test_cuenta_por_veredicto_y_ventana_de_1h(self):
        for i, (ts, v) in enumerate([(9900, "vigilar"), (9800, "vigilar"), (9700, "descartar"),
                                     (9600, "sin_dato"), (10000 - 3601, "vigilar")]):  # el último, fuera
            self.juicio(mint(chr(65 + i)), ts, v)
        j = construir_json_sala(self.con, 10000.0)["juez"]
        self.assertEqual((j["vigilar"], j["descartar"], j["sin_dato"]), (2, 1, 1))
        self.assertEqual(j["pct_vigilar"], 50)

    def test_sin_juicios_ceros(self):
        j = construir_json_sala(self.con, 10000.0)["juez"]
        self.assertEqual(j, {"vigilar": 0, "descartar": 0, "sin_dato": 0, "pct_vigilar": 0})

    def test_sin_tabla_juicios_no_falla(self):
        self.con.execute("DROP TABLE juicios")
        j = construir_json_sala(self.con, 10000.0)
        self.assertEqual(j["juez"]["vigilar"], 0)
        json.dumps(j)

    def test_juez_vigilado_y_en_la_sala(self):
        self.assertIn("juez", AGENTES_VIGILADOS)
        self.assertIn("juez", AGENTES_SALA)
        self.con.execute("INSERT INTO agentes_estado VALUES ('juez', ?, 'ok', 3, 0)", (9995.0,))
        nombres = [a["agente"] for a in construir_json_sala(self.con, 10000.0)["agentes"]]
        self.assertIn("juez", nombres)


class TestMesas(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        crear_tablas(self.con)

    def op(self, mesa, i, ret):
        self.con.execute("INSERT INTO mesas_ops (mesa, mint, ts_entrada, ret, estado, ts_calculo) VALUES (?,?,?,?,'ok',0)",
                         (mesa, f"m{i}", float(i), ret))

    def test_orden_y_media_base(self):
        for i in range(4):
            self.op("BASE", i, 0.1 * i)
            self.op("SUPERV_T1", i, None if i == 0 else 0.5)
        j = construir_json_sala(self.con, 10000.0)
        self.assertEqual([m["mesa"] for m in j["mesas"]], ["BASE", "JUEZ_VIGILAR", "CERCA_GRADUAR", "SUPERV_T1"])
        self.assertEqual(j["mesas"][0]["etiqueta"], "REFERENCIA")
        self.assertAlmostEqual(j["mesas"][0]["media"], 0.15)
        self.assertEqual(j["mesas"][1]["n"], 0)
        self.assertEqual((j["mesas"][3]["n"], j["mesas"][3]["n_sin_dato"]), (3, 1))
        self.assertEqual(j["mesas"][3]["etiqueta"], "RUIDO")  # n < MESAS_N_MIN
        json.dumps(j)

    def test_sin_tabla_mesas_ops_lista_vacia(self):
        self.con.execute("DROP TABLE mesas_ops")
        j = construir_json_sala(self.con, 10000.0)
        self.assertEqual(j["mesas"], [])
        json.dumps(j)

    def test_contable_vigilado_y_en_la_sala(self):
        self.assertIn("contable", AGENTES_VIGILADOS)
        self.assertIn("contable", AGENTES_SALA)


class TestUltimoJuicio(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        crear_tablas(self.con)

    def test_devuelve_el_mas_reciente_del_momento_1(self):
        for m, v in (("A", "vigilar"), ("B", "descartar")):
            self.con.execute("INSERT INTO juicios (mint, momento, ts_juicio, b1, n_banderas, sin_dato, veredicto) "
                             "VALUES (?,1,9000,1,2,0,?)", (mint(m), v))
        j = construir_json_sala(self.con, 10000.0)["ultimo_juicio"]
        self.assertEqual((j["mint"], j["veredicto"], j["b1"], j["n_banderas"]), (mint("B"), "descartar", 1, 2))
        self.assertEqual(j["nombre"], mint("B")[:6])
        self.assertEqual(set(j), {"mint", "nombre", "ts_juicio", "b1", "b2", "b3", "b4", "b5", "b6",
                                  "n_banderas", "sin_dato", "veredicto"})

    def test_sin_juicios_o_sin_tabla_es_none(self):
        self.assertIsNone(construir_json_sala(self.con, 10000.0)["ultimo_juicio"])
        self.con.execute("DROP TABLE juicios")
        self.assertIsNone(construir_json_sala(self.con, 10000.0)["ultimo_juicio"])


class TestEstaticoYOficina(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        base = Path(self.dir.name)
        (base / "estatico" / "jsm" / "loaders").mkdir(parents=True)
        (base / "estatico" / "a.js").write_text("export const x = 1;")
        (base / "estatico" / "jsm" / "loaders" / "L.js").write_text("// loader")
        (base / "estatico" / "m.glb").write_bytes(b"glTF\x00\xff")
        (base / "estatico" / "x.py").write_text("print(1)")
        (base / "secreto.js").write_text("fuera")
        (base / "oficina.html").write_text("<p>__REFRESCO_MS__</p>")
        self.base = base
        parches = [patch.object(panel, "DIR_ESTATICO", base / "estatico"),
                   patch.object(panel, "RUTA_OFICINA", base / "oficina.html")]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), panel.Manejador)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.dir.cleanup()

    def get(self, ruta):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_port)
        c.request("GET", ruta)  # http.client no normaliza ../
        r = c.getresponse()
        cuerpo = r.read()
        c.close()
        return r.status, r.getheader("Content-Type"), r.getheader("Cache-Control"), cuerpo

    def test_js_con_tipo_exacto_y_cache(self):
        st, tipo, cache, cuerpo = self.get("/estatico/a.js")
        self.assertEqual((st, tipo, cache), (200, "text/javascript; charset=utf-8", "max-age=3600"))
        self.assertEqual(cuerpo, b"export const x = 1;")

    def test_glb_en_binario(self):
        st, tipo, _, cuerpo = self.get("/estatico/m.glb")
        self.assertEqual((st, tipo, cuerpo), (200, "model/gltf-binary", b"glTF\x00\xff"))

    def test_subcarpetas_de_2_niveles(self):
        st, _, _, cuerpo = self.get("/estatico/jsm/loaders/L.js")
        self.assertEqual((st, cuerpo), (200, b"// loader"))

    def test_rechazos_404(self):
        for ruta in ("/estatico/../secreto.js", "/estatico/%2e%2e/secreto.js", "/estatico/jsm/../../secreto.js",
                     "/estatico/x.py", "/estatico/jsm/loaders", "/estatico/jsm", "/estatico/..%5csecreto.js",
                     "/estatico//secreto.js", "/estatico/no_existe.js", "/estatico/"):
            self.assertEqual(self.get(ruta)[0], 404, ruta)

    def test_oficina(self):
        st, tipo, _, cuerpo = self.get("/oficina")
        self.assertEqual((st, tipo), (200, "text/html; charset=utf-8"))
        self.assertNotIn(b"__REFRESCO_MS__", cuerpo)

    def test_oficina_ausente_500(self):
        (self.base / "oficina.html").unlink()
        self.assertEqual(self.get("/oficina")[0], 500)


if __name__ == "__main__":
    unittest.main()
