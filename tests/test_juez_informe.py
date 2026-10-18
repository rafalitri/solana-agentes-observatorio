import unittest

from herramientas.juez_informe import (agrupar_banderas, informe, por_bandera, por_n_banderas, unir_juicios,
                                       veredicto)


def fila(mint, top10=10.0, mitad=1, ret=0.1234, mult=1.5):
    return {"mint": mint, "ts": 1, "top10_pct": top10, "creador_pct": 1.0, "ret": ret, "mult": mult, "mitad": mitad}


def juicio(mint, version=1, **k):
    j = {"mint": mint, "version_juez": version, "b1": 0, "b2": 0, "b4": 0, "b5": 0, "b6": 0}
    j.update(k)
    return j


def st(n1, m1, n2, m2):
    return {1: (n1, m1, m1, 1.0), 2: (n2, m2, m2, 1.0)}


class TestUnir(unittest.TestCase):
    def test_descartes(self):
        filas = [fila("ok"), fila("sj"), fila("ov"), fila("sd")]
        js = {"ok": juicio("ok"), "ov": juicio("ov", version=2), "sd": juicio("sd", b5=None)}
        out, d = unir_juicios(filas, js, 50.0)
        self.assertEqual([f["mint"] for f in out], ["ok"])
        self.assertEqual(d, {"sin juicio": 1, "otra version": 1, "sin dato": 1})

    def test_b3_recalculado_ignora_guardado(self):
        filas = [fila("a", top10=50.0), fila("b", top10=49.9)]
        js = {"a": juicio("a", b3=0), "b": juicio("b", b3=1)}  # b3 guardado contradice: no se usa
        out, d = unir_juicios(filas, js, 50.0)
        self.assertEqual([(f["b3"], f["n_banderas"]) for f in out], [(1, 1), (0, 0)])
        self.assertEqual(sum(d.values()), 0)

    def test_n_banderas_suma_las_seis(self):
        out, _ = unir_juicios([fila("a", top10=60.0)], {"a": juicio("a", b1=1, b6=1)}, 50.0)
        self.assertEqual(out[0]["n_banderas"], 3)


class TestGrupos(unittest.TestCase):
    def filas(self):
        out, _ = unir_juicios([fila("a", top10=60.0), fila("b"), fila("c", mitad=2), fila("d")],
                              {"a": juicio("a", b1=1), "b": juicio("b"), "c": juicio("c", b2=1),
                               "d": juicio("d", b4=1, b5=1)}, 50.0)
        return out

    def test_agrupar(self):
        riesgo, resto = agrupar_banderas(self.filas())
        self.assertEqual(([f["mint"] for f in riesgo], [f["mint"] for f in resto]), (["a", "c", "d"], ["b"]))

    def test_por_n(self):
        g = por_n_banderas(self.filas())
        self.assertEqual({k: [f["mint"] for f in v] for k, v in g.items()},
                         {"0": ["b"], "1": ["c"], "2": ["a", "d"], "3+": []})

    def test_por_bandera(self):
        con, sin = por_bandera(self.filas(), "b3")
        self.assertEqual(([f["mint"] for f in con], [f["mint"] for f in sin]), (["a"], ["b", "c", "d"]))


class TestVeredicto(unittest.TestCase):
    def test_si(self):
        self.assertTrue(veredicto(st(50, -0.1, 50, -0.2), st(60, 0.0, 60, -0.1), 50).startswith("SI"))

    def test_no(self):
        self.assertEqual(veredicto(st(50, -0.1, 50, 0.1), st(60, 0.0, 60, 0.0), 50), "NO")

    def test_no_concluyente(self):
        self.assertEqual(veredicto(st(49, -0.1, 50, -0.2), st(60, 0.0, 60, -0.1), 50), "NO CONCLUYENTE (n < 50)")
        self.assertEqual(veredicto(st(50, -0.1, 50, -0.2), st(60, 0.0, 49, -0.1), 50), "NO CONCLUYENTE (n < 50)")


class TestInforme(unittest.TestCase):
    def filas(self):
        js = {str(i): juicio(str(i), b1=i % 2) for i in range(8)}
        out, _ = unir_juicios([fila(str(i), mitad=1 + (i // 2) % 2) for i in range(8)], js, 50.0)
        return out

    def test_completo(self):
        txt = "\n".join(informe(self.filas(), {"sin juicio": 0}, minimo=1))
        self.assertIn("VEREDICTO_DIA: ", txt)
        self.assertIn("Solo información", txt)
        self.assertIn("b1 con", txt)

    def test_solo_recuentos_sin_cifras_de_retorno(self):
        txt = "\n".join(informe(self.filas(), {"sin juicio": 3}, solo_recuentos=True))
        for prohibido in ("media", "mediana", "mult", "VEREDICTO", "0.1234", "12.34", "1.50"):
            self.assertNotIn(prohibido, txt)
        self.assertIn("fuera por sin juicio: 3", txt)
        self.assertIn("mitad 1: n=", txt)
        self.assertIn("mitad 2: n=", txt)


if __name__ == "__main__":
    unittest.main()
