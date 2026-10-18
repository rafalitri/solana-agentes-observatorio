import contextlib
import io
import json
import sqlite3
import time
import unittest
import urllib.error
from unittest import mock

from agentes import tenedores
from comun import db, solana_rpc
from comun.config import TENEDORES_INTENTOS, TENEDORES_MAX_S
from comun.solana_rpc import RpcLimite
from herramientas.panel import construir_json, construir_json_poblado

SUPPLY = 1000
CURVA, CREADOR = "curva", "creador"


def _calc(cuentas, duenos):
    return tenedores.calcular_tenedores(cuentas, duenos, CURVA, CREADOR, SUPPLY)


class TestCalcular(unittest.TestCase):
    def test_curva_excluida_del_top(self):
        r = _calc([("a1", 600), ("a2", 100), ("a3", 50)], [CURVA, "x", "y"])
        self.assertEqual(r["curva_pct"], 60)
        self.assertEqual(r["top10_pct"], 15)
        self.assertAlmostEqual(r["top10_circ_pct"], 100 * 150 / 400)
        self.assertEqual(r["n_cuentas"], 3)

    def test_creador_presente(self):
        r = _calc([("a1", 300), ("a2", 100)], ["x", CREADOR])
        self.assertEqual(r["creador_pct"], 10)
        self.assertEqual(r["creador_en_top"], 1)

    def test_creador_ausente(self):
        r = _calc([("a1", 300)], ["x"])
        self.assertEqual(r["creador_pct"], 0)
        self.assertEqual(r["creador_en_top"], 0)

    def test_dueno_con_dos_cuentas_cuenta_una_vez(self):
        r = _calc([("a1", 100), ("a2", 50), ("a3", 20)], ["x", "x", "y"])
        self.assertEqual(r["top10_pct"], 17)
        self.assertEqual(r["n_cuentas"], 3)

    def test_menos_de_10_duenos_y_mas_de_10(self):
        self.assertEqual(_calc([("a1", 10)], ["x"])["top10_pct"], 1)
        cuentas = [(f"a{i}", 10 + i) for i in range(12)]
        r = _calc(cuentas, [f"d{i}" for i in range(12)])
        self.assertEqual(r["top10_pct"], 100 * sum(range(12, 22)) / SUPPLY)  # las 10 mayores: 12..21

    def test_curva_con_todo_el_supply(self):
        self.assertEqual(_calc([("a1", SUPPLY)], [CURVA])["top10_circ_pct"], 0.0)

    def test_raw_guarda_las_cuentas(self):
        raw = json.loads(_calc([("a1", 5)], ["x"])["raw"])
        self.assertEqual(raw, [{"direccion": "a1", "dueno": "x", "cantidad": 5}])


def _con(test):
    con = sqlite3.connect(":memory:")
    test.addCleanup(con.close)
    db.crear_tablas(con)
    return con


def _token(con, mint, ts, pasa=1, foto=True):
    con.execute("INSERT INTO tokens (mint, ts, creador, bonding_curve) VALUES (?, ?, ?, ?)",
                (mint, ts, CREADOR, CURVA))
    con.execute("INSERT INTO decisiones (mint, ts, pasa) VALUES (?, ?, ?)", (mint, ts, pasa))
    if foto:
        con.execute("INSERT INTO fotos (mint, ts) VALUES (?, ?)", (mint, ts + 1))


class TestPendientes(unittest.TestCase):
    def test_filtros_y_orden(self):
        con = _con(self)
        ahora = time.time()
        desde = ahora - TENEDORES_MAX_S
        _token(con, "b_nuevo", ahora - 10)
        _token(con, "a_viejo_ok", ahora - 100)
        _token(con, "fuera_ventana", desde - 1)
        _token(con, "sin_foto", ahora - 50, foto=False)
        _token(con, "no_pasa", ahora - 50, pasa=0)
        _token(con, "ya_hecho", ahora - 50)
        db.guardar_tenedores(con, {"mint": "ya_hecho"})
        pend = db.pendientes_tenedores(con, desde, 10)
        self.assertEqual([p[0] for p in pend], ["a_viejo_ok", "b_nuevo"])
        self.assertEqual(pend[0][2:], (CURVA, CREADOR))


class TestCiclo(unittest.TestCase):
    def _ciclo(self, con, consultar, intentos, c=None, tot=None):
        c = c or {"ok": 0, "rpc": 0, "bd": 0, "pendientes": 0}
        tot = tot or {"procesados": 0, "errores": 0}
        tenedores.procesar_ciclo(con, 0, intentos, c, tot, consultar, dormir=lambda s: None)
        return c, tot

    def test_fila_correcta(self):
        con = _con(self)
        _token(con, "m", time.time() - 5)
        self._ciclo(con, lambda m: ([("a1", 6_000_000), ("a2", 1_000_000)], ["x", CURVA], 6), {})
        fila = con.execute("SELECT curva_pct, top10_pct, error, seg_desde_primera_foto FROM tenedores").fetchone()
        self.assertAlmostEqual(fila[0], 100 * 1_000_000 / (1_000_000_000 * 10 ** 6))
        self.assertIsNone(fila[2])
        self.assertIsNotNone(fila[3])

    def test_error_tras_3_intentos(self):
        con = _con(self)
        _token(con, "m", time.time() - 5)

        def falla(m):
            raise RuntimeError("RPC: {'code': -32000}")
        intentos = {}
        for i in range(TENEDORES_INTENTOS - 1):
            self._ciclo(con, falla, intentos)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM tenedores").fetchone()[0], 0)
        c, tot = self._ciclo(con, falla, intentos)
        fila = con.execute("SELECT error, curva_pct, n_cuentas FROM tenedores").fetchone()
        self.assertTrue(fila[0].startswith("rpc:"))
        self.assertEqual(fila[1:], (None, None))
        self.assertNotIn("m", intentos)

    def test_linea_de_fallo_sin_url(self):
        con = _con(self)
        _token(con, "m", time.time() - 5)
        url = "https://rpc.ejemplo.test/?api-key=SECRETO"

        def falla(m):  # fallo de red real a través de _rpc, cuyo mensaje trae la URL
            solana_rpc._rpc("getTokenLargestAccounts", [m], url)
        salida = io.StringIO()
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError(f"no llego a {url}")) as falso, \
                contextlib.redirect_stdout(salida):
            self._ciclo(con, falla, {})
        self.assertEqual(falso.call_count, 1)  # solo el urlopen simulado: ninguna red real
        linea = salida.getvalue()
        self.assertIn("fallo RPC RuntimeError:", linea)
        self.assertIn(f"(intento 1/{TENEDORES_INTENTOS})", linea)
        self.assertNotIn("SECRETO", linea)
        self.assertNotIn("https://", linea)

    def test_429_no_cuenta_como_intento(self):
        con = _con(self)
        _token(con, "m", time.time() - 5)

        def limite(m):
            raise RpcLimite()
        esperas = []
        intentos = {"m": 2}
        c = {"ok": 0, "rpc": 0, "bd": 0, "pendientes": 0}
        tenedores.procesar_ciclo(con, 0, intentos, c, {"procesados": 0, "errores": 0}, limite, esperas.append)
        self.assertEqual(intentos, {"m": 2})
        self.assertEqual(esperas, [10])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM tenedores").fetchone()[0], 0)


class TestPanelConTenedores(unittest.TestCase):
    def test_panel_acepta_el_agente_nuevo(self):
        estados = [("tenedores", time.time(), "ok", 1, 0)]
        j = construir_json(time.time(), estados, 0, 0, [], [])
        self.assertIn("tenedores", [a["agente"] for a in j["agentes"]])
        j = construir_json_poblado(time.time(), [], [], [], [], [])
        self.assertIn("tenedores", [a["agente"] for a in j["agentes"]])


if __name__ == "__main__":
    unittest.main()
