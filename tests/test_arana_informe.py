import json
import unittest

from herramientas.arana_informe import (agrupar, contar_previos, distribucion, informe, monederos_activos,
                                        veredicto_dia1, veredicto_dia2)

S = 1_000_000  # supply de prueba: 0,1 % = 1000


def raw(*cuentas):
    return json.dumps([{"direccion": f"c{i}", "dueno": d, "cantidad": q} for i, (d, q) in enumerate(cuentas)])


class TestMonederos(unittest.TestCase):
    def test_excluye_curva_y_creador_y_umbral(self):
        r = raw(("curva", 5000), ("crea", 5000), ("a", 1000), ("b", 999))
        self.assertEqual(monederos_activos(r, "curva", "crea", S, 0.1), {"a"})

    def test_suma_cuentas_del_mismo_dueno(self):
        r = raw(("a", 600), ("a", 400))
        self.assertEqual(monederos_activos(r, "x", "y", S, 0.1), {"a"})


class TestPrevios(unittest.TestCase):
    def test_necesita_3_previos(self):
        items = [("t1", 1, {"w"}), ("t2", 2, {"w"}), ("t3", 3, {"w"}), ("t4", 4, {"w"}), ("t5", 5, {"w"})]
        r = contar_previos(items)
        self.assertEqual([r[m] for m in ("t1", "t2", "t3", "t4", "t5")], [0, 0, 0, 1, 1])

    def test_no_cuenta_propio_ni_posteriores(self):
        items = [("t1", 1, {"w"}), ("t2", 2, {"w"}), ("t3", 3, {"w"}), ("t4", 4, set())]
        self.assertEqual(contar_previos(items)["t3"], 0)  # solo 2 previos

    def test_empates_en_bloque(self):
        items = [("a", 1, {"w"}), ("b", 2, {"w"}), ("c", 3, {"w"}), ("d", 4, {"w"}), ("e", 4, {"w"})]
        r = contar_previos(items)
        self.assertEqual((r["d"], r["e"]), (1, 1))  # e no cuenta a d y viceversa
        items = [("a", 1, {"w"}), ("b", 2, {"w"}), ("c", 3, {"w"}), ("d", 3, {"w"})]
        self.assertEqual((contar_previos(items)["c"], contar_previos(items)["d"]), (0, 0))

    def test_orden_por_ts_consulta_no_por_decision(self):
        # lista DESORDENADA (como si se ordenara por decisión): "x" decidió antes pero se consultó después de "t"
        items = [("x", 5, {"w"}), ("t", 4, {"w"}), ("b", 2, {"w"}), ("a", 1, {"w"})]
        self.assertEqual(contar_previos(items)["t"], 0)  # solo a y b son anteriores; x no cuenta
        items = [("t", 4, {"w"}), ("c", 3, {"w"}), ("x", 5, {"w"}), ("b", 2, {"w"}), ("a", 1, {"w"})]
        r = contar_previos(items)
        self.assertEqual((r["t"], r["x"]), (1, 1))  # c, b, a son anteriores a t: ahora sí cuentan 3

    def test_cuenta_monederos_distintos(self):
        items = [(f"p{i}", i, {"w", "z"}) for i in range(3)] + [("t", 9, {"w", "z", "q"})]
        self.assertEqual(contar_previos(items)["t"], 2)


class TestAgrupar(unittest.TestCase):
    def test_agrupar_y_distribucion(self):
        filas = [{"n_rep": n} for n in (0, 0, 1, 2, 3, 5)]
        con, sin = agrupar(filas)
        self.assertEqual((len(con), len(sin)), (4, 2))
        self.assertEqual(distribucion(filas), {"0": 2, "1": 1, "2": 1, "3+": 2})


class TestVeredictos(unittest.TestCase):
    def test_veredictos(self):
        self.assertEqual(veredicto_dia1(True), "VEREDICTO: pasa a día 2")
        self.assertEqual(veredicto_dia1(False), "VEREDICTO: no aporta")
        self.assertEqual(veredicto_dia2(True), "VEREDICTO: APORTA")
        self.assertEqual(veredicto_dia2(False), "VEREDICTO: no aporta")

    def test_informe_vacio_y_dia1(self):
        self.assertEqual(informe([], {"sin fila": 0}, "dia1")[-1], "Universo vacío.")
        filas = [{"mint": str(i), "ts": i, "ret": -1.0 if i % 2 else 1.0, "mult": 1.0,
                  "mitad": 1 + i % 2, "n_rep": i % 2} for i in range(4)]
        txt = "\n".join(informe(filas, {}, "dia1"))
        self.assertIn("APORTA_DIA1: no", txt)
        self.assertIn("VEREDICTO: no aporta", txt)


if __name__ == "__main__":
    unittest.main()
