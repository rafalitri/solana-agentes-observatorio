import json
import base64
import sqlite3
import unittest
import urllib.error
from unittest import mock

from agentes import supervivientes as sv
from comun import db
from comun.config import SUPERV_DEX_LOTE, SUPERV_DEX_MAX_MIN, SUPERV_DEX_PARES_MAX, SUPERV_WSOL
from comun.solana_rpc import CURVA

T0 = 1000.0
AHORA = T0 + 1900  # decisión hace > 30 min


def _foto(dt, precio, complete=0):
    # v_sol en lamports, v_tokens en unidades crudas: precio = v_sol/1e9 / (v_tokens/1e6)
    return (T0 + dt, precio * 1e9 if precio else None, 1e6 if precio else None, complete)


def _curva_b64(v_sol, v_tokens, complete):
    return base64.b64encode(CURVA.pack(int(v_tokens), int(v_sol), 0, 0, 1000, complete)).decode()


def _con(test):
    con = sqlite3.connect(":memory:")
    test.addCleanup(con.close)
    db.crear_tablas(con)
    return con


def _token(con, mint, fotos, ts=T0, pasa=1, mayhem=0, grad_inst=0, atipica=0, error=None):
    con.execute("INSERT INTO tokens (mint, ts, mayhem, bonding_curve) VALUES (?, ?, ?, ?)", (mint, ts, mayhem, "bc" + mint))
    con.execute("INSERT INTO decisiones (mint, ts, pasa) VALUES (?, ?, ?)", (mint, ts, pasa))
    con.execute("INSERT INTO resumen (mint, error, grad_inst, curva_atipica) VALUES (?, ?, ?, ?)",
                (mint, error, grad_inst, atipica))
    for ts_f, vs, vt, c in fotos:
        con.execute("INSERT INTO fotos (mint, ts, v_sol, v_tokens, complete) VALUES (?, ?, ?, ?, ?)", (mint, ts_f, vs, vt, c))


def _par(mint, precio="0.5", liq=100.0, quote=SUPERV_WSOL, dex="pumpswap", pair="par1"):
    return {"dexId": dex, "pairAddress": pair, "baseToken": {"address": mint}, "quoteToken": {"address": quote}, "priceNative": precio,
            "liquidity": {"usd": liq}, "volume": {"m5": 7.0}, "txns": {"m5": {"buys": 3, "sells": 2}}, "fdv": 9.0}


class TestEvaluar(unittest.TestCase):
    def test_curva_si_precio_30min_no_baja(self):
        f = sv.evaluar_superviviente([_foto(0, 1.0), _foto(900, 0.5), _foto(1800, 1.0)], AHORA)
        self.assertEqual((f["motivo"], f["precio_1a_foto"], f["precio_30min"]), ("curva", 1.0, 1.0))
        self.assertEqual(f["ts_fin"], T0 + 1800 + 86400 + 900)
        self.assertEqual(f["fuente"], "curva")

    def test_descarta_si_baja(self):
        self.assertIsNone(sv.evaluar_superviviente([_foto(0, 1.0), _foto(1800, 0.9)], AHORA))

    def test_no_usa_fotos_posteriores_a_30_min(self):
        fotos = [_foto(0, 1.0), _foto(1800, 0.9), _foto(1810, 5.0)]
        self.assertIsNone(sv.evaluar_superviviente(fotos, AHORA))

    def test_graduado_en_ventana(self):
        f = sv.evaluar_superviviente([_foto(0, 1.0), _foto(600, 0.4), _foto(700, 0.4, complete=1)], AHORA)
        self.assertEqual((f["motivo"], f["ts_graduacion"], f["fuente"]), ("graduado", T0 + 700, "dex"))
        self.assertEqual(f["precio_ultimo_curva"], 0.4)

    def test_graduacion_tras_ventana_no_cuenta(self):
        self.assertIsNone(sv.evaluar_superviviente([_foto(0, 1.0), _foto(1800, 0.5), _foto(1900, 0.5, 1)], AHORA))

    def test_sin_foto_valida(self):
        self.assertIsNone(sv.evaluar_superviviente([_foto(0, None), _foto(10, 1.0, complete=1)], AHORA))


class TestSeleccion(unittest.TestCase):
    def test_universo_v0_y_evaluados(self):
        con = _con(self)
        buenas = [_foto(0, 1.0), _foto(1800, 1.5)]
        _token(con, "ok", buenas)
        _token(con, "baja", [_foto(0, 1.0), _foto(1800, 0.5)])
        _token(con, "nopasa", buenas, pasa=0)
        _token(con, "mayhem", buenas, mayhem=1)
        _token(con, "grad", buenas, grad_inst=1)
        _token(con, "atip", buenas, atipica=1)
        _token(con, "err", buenas, error="excepcion")
        _token(con, "reciente", buenas, ts=AHORA - 100)
        ev = set()
        self.assertEqual(sv.seleccionar(con, AHORA, ev), 1)
        self.assertEqual([r[0] for r in con.execute("SELECT mint FROM supervivientes")], ["ok"])
        self.assertIn("baja", ev)
        self.assertEqual(sv.seleccionar(con, AHORA, ev), 0)

    def test_error_de_bd_no_marca_evaluado(self):
        con = _con(self)
        _token(con, "ok", [_foto(0, 1.0), _foto(1800, 1.0)])
        ev = set()
        with mock.patch.object(db, "guardar_supervivientes", side_effect=sqlite3.OperationalError("locked")):
            with self.assertRaises(sqlite3.OperationalError):
                sv.seleccionar(con, AHORA, ev)
        self.assertEqual(ev, set())
        self.assertEqual(sv.seleccionar(con, AHORA, ev), 1)


class TestDex(unittest.TestCase):
    def test_parsear_elige_par_sol_con_mas_liquidez(self):
        r = sv.parsear_dex([_par("a", "0.1", 10), _par("a", "0.2", 50), _par("a", "9", 999, quote="usdc"),
                            _par("b", quote="usdc")], ["a", "b", "c"])
        self.assertEqual(r["a"][0]["precio_sol"], 0.2)
        self.assertEqual((r["a"][0]["volumen_5m"], r["a"][0]["compras_5m"], r["a"][0]["ventas_5m"]), (7.0, 3, 2))
        self.assertEqual(r["b"], (None, "sin_par_sol"))
        self.assertEqual(r["c"], (None, "sin_par"))

    def test_elegir_par(self):
        peg = _par("a", "4.1e-7", 0, dex="pumpfun", pair="curva")
        real = _par("a", "0.2", 50, dex="pumpswap", pair="real")
        self.assertEqual(sv.elegir_par([peg]), (None, "sin_par"))
        self.assertEqual(sv.elegir_par([peg, real])[0]["pairAddress"], "real")
        self.assertEqual(sv.elegir_par([peg, _par("a", liq=None)]), (None, "sin_par"))
        self.assertEqual(sv.elegir_par([_par("a", quote="usdc")]), (None, "sin_par_sol"))

    def test_parsear_guarda_dex_y_par(self):
        r = sv.parsear_dex([_par("a", "4.1e-7", 5, dex="pumpfun"), _par("a", "0.2", 50, pair="real")], ["a"])
        d = r["a"][0]
        self.assertEqual((d["dex_id"], d["pair_address"], d["precio_sol"]), ("pumpswap", "real", 0.2))

    def test_sin_par_se_avisa_una_vez(self):
        av = set()
        solo_curva = lambda l: [_par("a", dex="pumpfun", liq=0)]
        with mock.patch("builtins.print") as pr:
            sv.leer_dex(["a"], 1.0, solo_curva, av)
            sv.leer_dex(["a"], 2.0, solo_curva, av)
        self.assertEqual(pr.call_count, 1)

    def test_crear_tablas_anade_columnas_a_bd_antigua(self):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE lecturas_superv (mint TEXT, ts REAL, fuente TEXT, precio_sol REAL, liquidez_usd REAL, "
                    "volumen_5m REAL, compras_5m INTEGER, ventas_5m INTEGER, fdv_usd REAL, error TEXT)")
        db.crear_tablas(con)
        db.crear_tablas(con)  # idempotente
        cols = [r[1] for r in con.execute("PRAGMA table_info(lecturas_superv)")]
        self.assertEqual(cols[-2:], ["dex_id", "pair_address"])

    def test_lotes_de_5(self):
        llamadas = []
        with mock.patch.object(sv.time, "sleep"):
            sv.leer_dex([f"m{i}" for i in range(12)], 1.0, lambda l: llamadas.append(len(l)) or [])
        self.assertEqual(llamadas, [5, 5, 2])

    def test_llamar_dex_lee_dict_pairs(self):
        class R:
            def __init__(self, d): self.d = d
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, *a): return json.dumps(self.d).encode()
        with mock.patch.object(sv.urllib.request, "urlopen", return_value=R({"pairs": [_par("a")]})):
            self.assertEqual(len(sv.llamar_dex(["a"])), 1)
        with mock.patch.object(sv.urllib.request, "urlopen", return_value=R({"pairs": None, "schemaVersion": "1"})):
            self.assertEqual(sv.llamar_dex(["a"]), [])

    def test_treinta_pares_repite_mint_a_mint(self):
        llamadas = []

        def llamar(lote):
            llamadas.append(list(lote))
            if len(lote) > 1:
                return [_par(lote[0], pair=f"p{i}") for i in range(SUPERV_DEX_PARES_MAX)]
            return [_par(lote[0], "0.2", 50)]
        with mock.patch.object(sv.time, "sleep"), mock.patch("builtins.print") as pr:
            filas = sv.leer_dex(["a", "b", "c"], 1.0, llamar)
        self.assertEqual(llamadas, [["a", "b", "c"], ["a"], ["b"], ["c"]])
        self.assertEqual([f[3] for f in filas], [0.2, 0.2, 0.2])
        self.assertEqual(pr.call_count, 1)

    def test_errores_de_red(self):
        def mal(lote):
            raise urllib.error.HTTPError("u", 429, "x", {}, None)
        filas = sv.leer_dex(["a", "b"], 1.0, mal)
        self.assertEqual([f[-1] for f in filas], ["dex_429", "dex_429"])
        filas = sv.leer_dex(["a"], 1.0, mock.Mock(side_effect=OSError("caído")))
        self.assertEqual(filas[0][-1], "dex_red")

    def test_tope_de_llamadas_por_minuto(self):
        n = SUPERV_DEX_LOTE * (SUPERV_DEX_MAX_MIN + 1)
        with mock.patch.object(sv.time, "sleep"):
            filas = sv.leer_dex([f"m{i}" for i in range(n)], 1.0, lambda l: [])
        self.assertEqual(sum(1 for f in filas if f[-1] == "dex_tope"), SUPERV_DEX_LOTE)


class TestCurvas(unittest.TestCase):
    def test_lecturas_y_errores(self):
        cuentas = {"b1": _curva_b64(30e9, 1e12, 0), "b2": None, "b3": _curva_b64(1, 1, 1), "b4": "AAAA"}
        filas = sv.leer_curvas([("m1", "b1"), ("m2", "b2"), ("m3", "b3"), ("m4", "b4")], 5.0, lambda l: cuentas, 0)
        self.assertEqual(filas[0][3], 30.0 / 1e6)
        self.assertEqual([f[-1] for f in filas], [None, "sin_cuenta", "graduada", "curva_ilegible"])

    def test_red_y_lotes_de_100(self):
        filas = sv.leer_curvas([("m", "b")], 1.0, mock.Mock(side_effect=RuntimeError("RPC")), 0)
        self.assertEqual(filas[0][-1], "rpc_red")
        tam = []
        with mock.patch.object(sv.time, "sleep"):
            sv.leer_curvas([(f"m{i}", f"b{i}") for i in range(250)], 1.0, lambda l: tam.append(len(l)) or {}, 0)
        self.assertEqual(tam, [100, 100, 50])


class TestRonda(unittest.TestCase):
    def test_curva_a_dex_y_fin_a_las_24h(self):
        con = _con(self)
        _token(con, "m", [_foto(0, 1.0), _foto(1800, 1.0)])
        ev = set()
        ok = {"bcm": _curva_b64(30e9, 1e12, 0)}
        self.assertEqual(sv.ronda(con, AHORA, ev, lambda l: ok, lambda l: []), (1, 0))
        fila = con.execute("SELECT fuente, precio_ultimo_curva, ts_ultimo_curva FROM supervivientes").fetchone()
        self.assertEqual(fila, ("curva", 30.0 / 1e6, AHORA))
        grad = {"bcm": _curva_b64(30e9, 1e12, 1)}
        self.assertEqual(sv.ronda(con, AHORA + 60, ev, lambda l: grad, lambda l: []), (0, 1))
        self.assertEqual(con.execute("SELECT fuente, ts_graduacion FROM supervivientes").fetchone(), ("dex", AHORA + 60))
        # tras graduarse: no vuelve a leer la curva; dex sin par -> error; luego el primer precio dex
        llamado = []
        self.assertEqual(sv.ronda(con, AHORA + 120, ev, lambda l: llamado.append(1) or {}, lambda l: []), (0, 1))
        self.assertEqual(llamado, [])
        self.assertIsNone(con.execute("SELECT precio_primero_dex FROM supervivientes").fetchone()[0])
        self.assertEqual(sv.ronda(con, AHORA + 180, ev, None, lambda l: [_par("m", "0.00003")]), (1, 0))
        self.assertEqual(sv.ronda(con, AHORA + 240, ev, None, lambda l: [_par("m", "0.9")]), (1, 0))
        self.assertEqual(con.execute("SELECT precio_ultimo_curva, ts_ultimo_curva, precio_primero_dex, ts_primero_dex "
                                     "FROM supervivientes").fetchone(), (30.0 / 1e6, AHORA, 0.00003, AHORA + 180))
        # a las 24 h (+30 min +15 min) desde la 1.ª foto deja de leer
        fin = con.execute("SELECT ts_fin FROM supervivientes").fetchone()[0]
        self.assertEqual(fin, T0 + 1800 + 86400 + 900)
        n = con.execute("SELECT COUNT(*) FROM lecturas_superv").fetchone()[0]
        self.assertEqual(sv.ronda(con, fin + 1, ev, None, lambda l: 1 / 0), (0, 0))
        self.assertEqual(con.execute("SELECT COUNT(*) FROM lecturas_superv").fetchone()[0], n)

    def test_graduado_en_seleccion_empieza_en_dex(self):
        con = _con(self)
        _token(con, "g", [_foto(0, 1.0), _foto(300, 0.8), _foto(400, 0.8, 1)])
        ev = set()
        sv.ronda(con, AHORA, ev, None, lambda l: [_par("g", "0.0000008")])
        self.assertEqual(con.execute("SELECT motivo, ts_graduacion, precio_ultimo_curva, precio_primero_dex "
                                     "FROM supervivientes").fetchone(), ("graduado", T0 + 400, 0.8, 0.0000008))

    def test_columnas_de_tablas_nuevas(self):
        con = _con(self)
        cols = [r[1] for r in con.execute("PRAGMA table_info(lecturas_superv)")]
        self.assertEqual(cols, ["mint", "ts", "fuente", "precio_sol", "liquidez_usd", "volumen_5m", "compras_5m",
                                "ventas_5m", "fdv_usd", "error", "dex_id", "pair_address"])


if __name__ == "__main__":
    unittest.main()
