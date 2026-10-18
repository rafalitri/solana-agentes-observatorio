import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest

from agentes.juez import (Recuento, calcular_banderas, calcular_veredicto, juicio, juzgar, listo, prueba)
from comun import db
from herramientas.arana_informe import contar_previos, monederos_activos

BASE = dict(mint="M", ts_decision=1000.0, creador="C", initial_buy=100_000_000.0, hay_ten=1, ten_error=None,
            creador_pct=10.0, top10_pct=30.0, hay_a2=1, twitter=1, telegram=0, web=0, previo=0)


def d(**kw):
    return {**BASE, **kw}


def bandera(nombre, n_rep=0, umbral=None, **kw):
    return calcular_banderas(d(**kw), n_rep, umbral)[nombre]


class TestBanderas(unittest.TestCase):
    def test_b1_creador_vendio(self):
        # compró 100e6 tokens = 10 % del supply; vendió si ahora tiene < 5 %
        self.assertEqual(bandera("b1", creador_pct=4.0), 1)
        self.assertEqual(bandera("b1", creador_pct=5.0), 0)
        self.assertIsNone(bandera("b1", creador_pct=None))
        self.assertIsNone(bandera("b1", initial_buy=None))

    def test_b2_creador_mas_5(self):
        self.assertEqual(bandera("b2", creador_pct=5.1), 1)
        self.assertEqual(bandera("b2", creador_pct=5.0), 0)
        self.assertIsNone(bandera("b2", creador_pct=None))

    def test_b3_top10(self):
        self.assertEqual(bandera("b3", umbral=30.0, top10_pct=30.0), 1)
        self.assertEqual(bandera("b3", umbral=30.0, top10_pct=29.9), 0)
        self.assertIsNone(bandera("b3", umbral=None))
        self.assertIsNone(bandera("b3", umbral=30.0, top10_pct=None))

    def test_b4_arana(self):
        self.assertEqual(bandera("b4", n_rep=1), 1)
        self.assertEqual(bandera("b4", n_rep=0), 0)
        self.assertIsNone(bandera("b4", n_rep=None))

    def test_b5_sin_redes(self):
        self.assertEqual(bandera("b5", twitter=0, telegram=0, web=0), 1)
        self.assertEqual(bandera("b5", twitter=0, telegram=1, web=0), 0)
        self.assertEqual(bandera("b5", twitter=1, telegram=None, web=None), 0)
        self.assertIsNone(bandera("b5", twitter=None, telegram=None, web=None))
        self.assertIsNone(bandera("b5", twitter=0, telegram=None, web=0))
        self.assertIsNone(bandera("b5", hay_a2=0, twitter=None, telegram=None, web=None))

    def test_b6_historial(self):
        self.assertEqual(bandera("b6", previo=1), 1)
        self.assertEqual(bandera("b6", previo=0), 0)
        self.assertIsNone(bandera("b6", creador=None))

    def test_tenedores_con_error_o_sin_fila(self):
        for kw in (dict(ten_error="rpc: x"), dict(hay_ten=0, creador_pct=None, top10_pct=None)):
            bs = calcular_banderas(d(**kw), 1, 30.0)
            self.assertEqual([bs[b] for b in ("b1", "b2", "b3", "b4")], [None] * 4)
            self.assertEqual(bs["b5"], 0)


class TestVeredicto(unittest.TestCase):
    def test_vigilar_descartar_y_sin_dato(self):
        r = calcular_veredicto(dict(b1=0, b2=0, b3=None, b4=0, b5=None, b6=0))
        self.assertEqual((r["n_banderas"], r["sin_dato"], r["veredicto"]), (0, "b3,b5", "sin_dato"))
        r = calcular_veredicto(dict(b1=1, b2=1, b3=None, b4=0, b5=0, b6=0))
        self.assertEqual((r["n_banderas"], r["sin_dato"], r["veredicto"]), (2, "b3", "descartar"))

    def test_veredicto_segun_datos_que_faltan(self):
        todo = dict.fromkeys(("b1", "b2", "b3", "b4", "b5", "b6"), 0)
        for umbral in (None, 20.0):
            self.assertEqual(calcular_veredicto(todo, umbral)["veredicto"], "vigilar")
            for b in ("b1", "b2", "b4", "b5", "b6"):
                self.assertEqual(calcular_veredicto({**todo, b: None}, umbral)["veredicto"], "sin_dato", b)
            self.assertEqual(calcular_veredicto({**todo, "b1": 1, "b5": None}, umbral)["veredicto"], "descartar")
        self.assertEqual(calcular_veredicto({**todo, "b3": None}, None)["veredicto"], "vigilar")
        self.assertEqual(calcular_veredicto({**todo, "b3": None}, 20.0)["veredicto"], "sin_dato")

    def test_todo_a_cero_no_tiene_sin_dato(self):
        self.assertEqual(calcular_veredicto(dict.fromkeys(("b1", "b2", "b3", "b4", "b5", "b6"), 0))["sin_dato"], "")


class TestPlazo(unittest.TestCase):
    def test_listo(self):
        self.assertTrue(listo(d(), 1001))  # tenedores y analisis_v2
        self.assertFalse(listo(d(hay_a2=0), 1179))
        self.assertTrue(listo(d(hay_a2=0), 1180))
        self.assertFalse(listo(d(hay_ten=0, hay_a2=0), 1100))
        self.assertTrue(listo(d(hay_ten=0, hay_a2=0), 1180))


# ---------- con BD ----------

SUPPLY_BAJO = 2 * 10 ** 12  # >= 0,1 % de 1e15


def raw(*duenos):
    return json.dumps([{"direccion": f"c{i}", "dueno": w, "cantidad": SUPPLY_BAJO} for i, w in enumerate(duenos)])


def poblar(con, mint, ts_tok, ts_consulta=None, duenos=("w",), creador=None, ts_dec=None, a2=True, ten_error=None):
    con.execute("INSERT INTO tokens (mint, ts, creador, initial_buy, bonding_curve) VALUES (?,?,?,?,?)",
                (mint, ts_tok, creador or f"cr_{mint}", 0.0, f"bc_{mint}"))
    con.execute("INSERT INTO decisiones (mint, ts, pasa) VALUES (?,?,1)", (mint, ts_dec or ts_tok))
    if ts_consulta is not None:
        con.execute("INSERT INTO tenedores (mint, ts_consulta, creador_pct, top10_pct, raw, error) VALUES (?,?,?,?,?,?)",
                    (mint, ts_consulta, 0.0, 20.0, None if ten_error else raw(*duenos), ten_error))
    if a2:
        con.execute("INSERT INTO analisis_v2 (mint, twitter, telegram, web) VALUES (?,0,0,0)", (mint,))
    con.commit()


def bd_memoria():
    con = sqlite3.connect(":memory:")
    db.crear_tablas(con)
    return con


class TestRecuento(unittest.TestCase):
    def test_b4_igual_que_el_informe_y_no_cuenta_posteriores(self):
        con = bd_memoria()
        for i in range(1, 7):  # t1..t6 comparten la cartera "w"; t5 y t6 se consultan después de t3
            poblar(con, f"t{i}", 100 + i, ts_consulta=200 + i)
        rec = Recuento()
        filas = {f["mint"]: f for f in juzgar(con, rec, 301, escribir=False)}
        # t1..t3: < 3 previos → 0 (t4, t5, t6 posteriores no cuentan); t4..t6: 3 o más previos → 1
        self.assertEqual([filas[f"t{i}"]["b4"] for i in range(1, 7)], [0, 0, 0, 1, 1, 1])
        items = [(m, ts, monederos_activos(r, bc, cr)) for m, ts, r, bc, cr in db.tenedores_con_raw(con, 0)]
        esperado = contar_previos(items)
        self.assertEqual({m: int(n >= 1) for m, n in esperado.items()}, {m: f["b4"] for m, f in filas.items()})

    def test_mismo_ts_consulta_no_se_pierde_ni_se_duplica(self):
        con = bd_memoria()
        poblar(con, "a", 100, ts_consulta=200.0)
        rec = Recuento()
        rec.actualizar(con)
        poblar(con, "b", 101, ts_consulta=200.0)  # llega después con el mismo ts_consulta
        rec.actualizar(con)
        rec.actualizar(con)
        self.assertEqual(sorted(m for m, _, _ in rec.items), ["a", "b"])

    def test_error_o_sin_fila_en_tenedores_no_entra(self):
        con = bd_memoria()
        poblar(con, "a", 100, ts_consulta=200.0, ten_error="rpc: x")
        poblar(con, "b", 101)
        rec = Recuento()
        rec.actualizar(con)
        self.assertEqual(rec.items, [])


class TestJuzgar(unittest.TestCase):
    def test_espera_a_los_datos_hasta_el_plazo(self):
        con = bd_memoria()
        poblar(con, "x", 1000, ts_consulta=1005.0, a2=False)
        self.assertEqual(juzgar(con, Recuento(), 1100, escribir=False), [])
        filas = juzgar(con, Recuento(), 1180, escribir=False)
        self.assertEqual((len(filas), filas[0]["b5"], filas[0]["sin_dato"]), (1, None, "b3,b5"))

    def test_escribe_una_vez_y_no_repite(self):
        con = bd_memoria()
        poblar(con, "x", 1000, ts_consulta=1005.0, creador="cx")
        poblar(con, "y", 1001, ts_consulta=1006.0, creador="cx")  # y: mismo creador, pero x es anterior
        self.assertEqual(len(juzgar(con, Recuento(), 1010)), 2)
        self.assertEqual(juzgar(con, Recuento(), 1020), [])
        self.assertEqual(dict(con.execute("SELECT mint, b6 FROM juicios")), {"x": 0, "y": 1})

    def test_fuera_de_ventana_se_pierde(self):
        con = bd_memoria()
        poblar(con, "x", 1000, ts_consulta=1005.0)
        self.assertEqual(juzgar(con, Recuento(), 1000 + 901, escribir=False), [])

    def test_juicio_copia_top10(self):
        f = juicio(d(), {"M": 0}, 1010.0, 30.0)
        self.assertEqual((f["top10_pct"], f["b3"], f["momento"], f["veredicto"]), (30.0, 1, 1, "descartar"))


class TestPrueba(unittest.TestCase):
    def preparar(self, quitar_juicios):
        tmp = tempfile.TemporaryDirectory()
        ruta = os.path.join(tmp.name, "t.db")
        con = sqlite3.connect(ruta)
        db.crear_tablas(con)
        poblar(con, "abcdefghij", 1, ts_consulta=2.0)
        poblar(con, "zzzzzzzzzz", 1, ts_consulta=3.0)
        if quitar_juicios:
            con.execute("DROP TABLE juicios")
        con.commit()
        con.close()
        return tmp, ruta

    def comprobar(self, quitar_juicios):
        tmp, ruta = self.preparar(quitar_juicios)
        self.addCleanup(tmp.cleanup)
        # ts de las decisiones = 1: con "ahora" real quedan fuera de la ventana, así que se mueven a ahora
        import time
        con = sqlite3.connect(ruta)
        con.execute("UPDATE decisiones SET ts = ?", (time.time(),))
        con.commit()
        con.close()
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            filas = prueba(1, ruta)
        self.assertEqual(len(filas), 1)
        self.assertIn("abcdefgh", salida.getvalue())
        con = sqlite3.connect(ruta)
        try:
            tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.assertEqual("juicios" in tablas, not quitar_juicios)
            if "juicios" in tablas:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM juicios").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM agentes_estado").fetchone()[0], 0)
        finally:
            con.close()

    def test_prueba_no_escribe(self):
        self.comprobar(quitar_juicios=False)

    def test_prueba_no_crea_la_tabla_juicios(self):
        self.comprobar(quitar_juicios=True)


if __name__ == "__main__":
    unittest.main()
