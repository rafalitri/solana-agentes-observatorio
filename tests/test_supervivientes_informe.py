import os
import sqlite3
import tempfile
import unittest

from comun import db

from herramientas import supervivientes_informe as si

DIA = 86400
C = si.COSTE
FEE = 2 * si.SIM_FEE_RED_SOL / si.SIM_TAM_SOL


def L(ts, p, fuente="curva", dex=None, err=None):
    return (ts, fuente, p, dex, err)


def serie(t0, precios, paso=60):
    return [L(t0 + k * paso, p) for k, p in enumerate(precios)]


def esperado(m):
    return m * (1 - C) ** 2 - 1 - FEE


class TestEntrada(unittest.TestCase):
    def test_primera_lectura_tras_marca_y_error_no_cuenta(self):
        val = si.validas([L(1000, 1.0), L(1060, 1.0, err="sin_par"), L(1120, 2.0), L(1180, 3.0)])
        self.assertEqual(si.elegir_entrada(val, 1030, None), 1)  # la de 1060 tiene error: entra la de 1120
        self.assertEqual(si.elegir_entrada(val, 1030, None, 1), 2)
        self.assertIsNone(si.elegir_entrada(val, 1030, None, 5))
        self.assertIsNone(si.elegir_entrada(val, 5000, None))

    def test_graduado_hace_menos_de_10_min_espera(self):
        val = si.validas([L(1000 + 60 * k, 1.0) for k in range(30)])
        self.assertEqual(val[si.elegir_entrada(val, 1000, 700)][0], 1300)  # grad + 600
        self.assertEqual(val[si.elegir_entrada(val, 1000, 100)][0], 1000)  # graduado hace > 10 min: marca
        self.assertEqual(val[si.elegir_entrada(val, 1000, 1500)][0], 1000)  # graduación posterior a la marca: no aplica


class TestSalidas(unittest.TestCase):
    def test_t1_vende_en_la_primera_con_07_del_maximo(self):
        precios = [1.0, 2.0, 1.5, 1.39, 5.0]
        self.assertEqual(si.indice_salida_t1(precios), 3)  # 1,39 <= 0,7 x 2
        self.assertEqual(si.indice_salida_t1(precios, 1), 4)
        self.assertEqual(si.indice_salida_t1([1.0, 1.1, 1.2]), 2)  # nunca salta: última lectura
        self.assertEqual(si.indice_salida_t1([1.0, 0.7]), 1)

    def test_t1_t2_retorno(self):
        val = si.validas(serie(0, [1.0, 2.0, 1.0, 9.0]))
        self.assertAlmostEqual(si.simular(val, 0, "T1"), esperado(1.0))  # máx 2, cae a 1,0 <= 1,4: vende ahí
        self.assertAlmostEqual(si.simular(val, 0, "T2"), esperado(9.0))
        self.assertIsNone(si.simular(val, None, "T1"))

    def test_t1_retraso_y_ventana_24h(self):
        val = si.validas(serie(0, [1.0, 2.0, 1.0, 9.0]))
        self.assertAlmostEqual(si.simular(val, 1, "T1", 1), esperado(4.5))  # entra a 2; 1,0 <= 1,4 dispara; vende una lectura después (9)
        largo = si.validas([L(0, 1.0), L(DIA, 2.0), L(DIA + 60, 99.0)])
        self.assertAlmostEqual(si.simular(largo, 0, "T2"), esperado(2.0))  # la lectura pasadas 24 h no cuenta


class TestDatos(unittest.TestCase):
    def test_hueco(self):
        ok = si.validas([L(0, 1.0), L(1800, 1.0), L(DIA, 1.0)])
        self.assertFalse(si.hay_hueco(ok, 0, 100000))
        val = si.validas(serie(0, [1.0] * 10) + [L(DIA - 100, 1.0)])
        self.assertTrue(si.hay_hueco(val, 0))  # hueco enorme en medio
        seguido = si.validas([L(60 * k, 1.0) for k in range(0, DIA // 60 + 1)])
        self.assertFalse(si.hay_hueco(seguido, 0))
        corto = si.validas([L(60 * k, 1.0) for k in range(0, DIA // 60 - 40)])  # acaba > 30 min antes de las 24 h
        self.assertTrue(si.hay_hueco(corto, 0))

    def test_par_congelado(self):
        f = lambda t, p="dex", d=None, pr=4.1e-7: (t, pr, p, d)
        self.assertTrue(si.par_congelado([f(0), f(1), f(2)]))
        self.assertFalse(si.par_congelado([f(0), f(1), f(2, "curva")]))  # la curva rompe la racha
        self.assertFalse(si.par_congelado([f(0), f(1), f(2, d="pumpswap")]))
        self.assertFalse(si.par_congelado([f(0), f(1), f(2, pr=5e-7), f(3)]))
        self.assertFalse(si.par_congelado([f(0, "curva"), f(1, "curva"), f(2, "curva")]))

    def test_sin_el_mejor_1pct(self):
        self.assertAlmostEqual(si.sin_el_mejor_1pct([10.0] + [0.0] * 99), 0.0)  # quita 1 de 100
        self.assertAlmostEqual(si.sin_el_mejor_1pct([10.0, 0.0] + [0.0] * 100), 0.0)  # ceil(1,02) = 2
        self.assertIsNone(si.sin_el_mejor_1pct([]))

    def test_veredicto(self):
        s = lambda n1, m1, n2, m2: {1: (n1, m1, 0), 2: (n2, m2, 0)}
        self.assertEqual(si.veredicto(s(50, 0.1, 50, 0.1), 0.05), "SI")
        self.assertEqual(si.veredicto(s(50, 0.1, 50, 0.1), -0.01), "NO")
        self.assertEqual(si.veredicto(s(50, 0.1, 50, -0.1), 0.05), "NO")
        self.assertEqual(si.veredicto(s(49, 0.1, 50, 0.1), 0.05), "NO CONCLUYENTE (n < 50)")
        self.assertEqual(si.veredicto(s(49, -0.1, 50, -0.1), -0.05), "NO CONCLUYENTE (n < 50)")


def reg(mint, ts_sel, lecturas, foto=0, grad=None, fin=None, **kw):
    r = {"mint": mint, "ts_seleccion": ts_sel, "ts_graduacion": grad, "primera_foto": foto, "lecturas": lecturas,
         "ts_fin": fin if fin is not None else foto + si.SUPERV_VENTANA_S + DIA + si.SUPERV_MARGEN_FIN_S,
         "top10_pct": 20.0, "n_insiders": 0, "previo": 0}
    r.update(kw)
    return r


def dia(t0, precios_ini):
    # lecturas cada 60 s desde t0 durante 24 h; precio inicial según la lista, luego plano
    n = DIA // 60 + 1
    return [L(t0 + 60 * k, precios_ini[k] if k < len(precios_ini) else precios_ini[-1]) for k in range(n)]


class TestPreparar(unittest.TestCase):
    AHORA = 10 * DIA

    def test_clasificacion(self):
        m = si.SUPERV_VENTANA_S
        regs = [reg("ok", m + 10, dia(m, [1.0, 2.0, 1.0])),
                reg("tarde", m + 500, dia(m, [1.0])),
                reg("vivo", self.AHORA - DIA + m + 10, dia(self.AHORA - DIA + m, [1.0]), foto=self.AHORA - DIA),
                reg("hueco", m + 10, dia(m, [1.0])[:100]),
                reg("fro", m + 10, [L(m + 60 * k, 4.1e-7, "dex") for k in range(2000)])]
        res = si.preparar(regs, self.AHORA)
        c = res["conteos"]
        self.assertEqual((c["universo"], c["sin_terminar"], c["retrasada"], c["congelado"], c["sin_dato"]), (5, 1, 1, 1, 1))
        self.assertEqual([f["mint"] for f in res["analizados"]], ["ok"])
        f = res["analizados"][0]
        self.assertAlmostEqual(f["base"]["T1"], esperado(1.0))  # máx 2, cae a 1,0 <= 1,4

    def test_descuadre_de_marca(self):
        m = si.SUPERV_VENTANA_S
        r = reg("x", m + 10, dia(m, [1.0]), fin=m + DIA + si.SUPERV_MARGEN_FIN_S + 120)
        self.assertEqual(si.preparar([r], self.AHORA)["conteos"]["descuadre_marca"], 1)

    def test_solo_recuentos_no_calcula_retornos(self):
        m = si.SUPERV_VENTANA_S
        res = si.preparar([reg("ok", m + 10, dia(m, [1.0, 2.0, 1.0]))], self.AHORA, con_retornos=False)
        self.assertIsNone(res["analizados"][0]["base"])
        texto = "\n".join(si.informe(res, solo_recuentos=True))
        for prohibido in ("media", "mediana", "VEREDICTO", "retraso 1:", "%"):
            self.assertNotIn(prohibido, texto)
        self.assertIn("analizados: 1", texto)

    def test_informe_completo_tiene_veredictos_y_sin_dato_a_menos_100(self):
        m = si.SUPERV_VENTANA_S
        regs = [reg("a", m + 10, dia(m, [1.0, 2.0, 1.0])), reg("b", m + 20, dia(m, [1.0])[:100])]
        texto = "\n".join(si.informe(si.preparar(regs, self.AHORA)))
        self.assertIn("VEREDICTO_DIA_T1: NO CONCLUYENTE (n < 50)", texto)
        self.assertIn("VEREDICTO_DIA_T2: NO CONCLUYENTE (n < 50)", texto)
        self.assertIn("sin dato = -100 %", texto)


class TestCargar(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.ruta = os.path.join(self.dir.name, "t.db")
        con = sqlite3.connect(self.ruta)
        db.crear_tablas(con)
        con.executemany("INSERT INTO tokens (mint, ts, creador) VALUES (?, ?, ?)",
                        [("A", 100.0, "c1"), ("B", 200.0, "c1"), ("C", 300.0, "c2")])
        # A: 1.ª foto (t=1000) inválida (complete = 1), luego sin reservas, la válida es t=1020
        con.executemany("INSERT INTO fotos (mint, ts, v_sol, v_tokens, complete) VALUES (?, ?, ?, ?, ?)",
                        [("A", 1000.0, 30e9, 1e15, 1), ("A", 1010.0, None, None, 0), ("A", 1020.0, 30e9, 1e15, 0),
                         ("A", 1030.0, 31e9, 1e15, 0), ("B", 2000.0, 30e9, 1e15, 0)])
        con.executemany("INSERT INTO supervivientes (mint, ts_seleccion, motivo, ts_graduacion, ts_fin, fuente) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        [("A", 3000.0, "curva", None, 9e5, "curva"), ("B", 3100.0, "graduado", 2100.0, 9e5, "dex"),
                         ("C", 99999.0, "curva", None, 9e5, "curva")])  # C queda fuera de la ventana
        con.executemany("INSERT INTO lecturas_superv (mint, ts, fuente, precio_sol, dex_id, error) VALUES (?, ?, ?, ?, ?, ?)",
                        [("A", 3000.0, "curva", 3e-8, None, None), ("A", 2940.0, "curva", None, None, "sin_cuenta"),
                         ("B", 3100.0, "dex", 4e-7, "pumpswap", None)])
        con.execute("INSERT INTO tenedores (mint, top10_pct) VALUES ('A', 33.5)")
        con.execute("INSERT INTO insider (mint, n_insiders) VALUES ('B', 2)")
        con.commit()
        con.close()

    def tearDown(self):
        self.dir.cleanup()

    def test_cargar(self):
        con = sqlite3.connect(f"file:{self.ruta}?mode=ro", uri=True)
        try:
            regs = {r["mint"]: r for r in si.cargar(con, 2500.0, 4000.0)}
        finally:
            con.close()
        self.assertEqual(sorted(regs), ["A", "B"])
        a, b = regs["A"], regs["B"]
        self.assertEqual(a["primera_foto"], 1020.0)  # no la 1.ª foto (complete = 1) ni la de reservas nulas
        self.assertEqual(si.marca30(a["primera_foto"]), 1020.0 + si.SUPERV_VENTANA_S)
        self.assertEqual(b["primera_foto"], 2000.0)
        self.assertEqual([l[0] for l in a["lecturas"]], [2940.0, 3000.0])  # ordenadas por ts
        self.assertEqual((a["top10_pct"], a["n_insiders"], a["previo"]), (33.5, None, 0))
        self.assertEqual((b["top10_pct"], b["n_insiders"], b["previo"]), (None, 2, 1))  # B: A es del mismo creador y anterior
        self.assertEqual((b["ts_graduacion"], a["ts_fin"]), (2100.0, 9e5))

    def test_token_sin_foto_valida(self):
        con = sqlite3.connect(self.ruta)
        con.execute("INSERT INTO tokens (mint, ts, creador) VALUES ('D', 1.0, 'c9')")
        con.execute("INSERT INTO fotos (mint, ts, v_sol, v_tokens, complete) VALUES ('D', 5.0, 30e9, 1e15, 1)")
        con.execute("INSERT INTO supervivientes (mint, ts_seleccion, motivo, ts_fin, fuente) VALUES ('D', 3050.0, 'graduado', 9e5, 'dex')")
        con.commit()
        con.close()
        con = sqlite3.connect(f"file:{self.ruta}?mode=ro", uri=True)
        try:
            d = next(r for r in si.cargar(con, 2500.0, 4000.0) if r["mint"] == "D")
        finally:
            con.close()
        self.assertIsNone(d["primera_foto"])


if __name__ == "__main__":
    unittest.main()
