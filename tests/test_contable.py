import contextlib
import io
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from agentes.contable import MESAS, calcular, ciclo, esperar, juicios_de, prueba
from comun import db

T0 = 1000.0
AHORA = T0 + 1800 + 86400 + 900 + 5000  # todo terminado (fotos y 24 h de supervivientes)
UMBRAL = 20.0


class TestContable(unittest.TestCase):
    def setUp(self):
        fd, self.ruta = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.con = sqlite3.connect(self.ruta)
        db.crear_tablas(self.con)

    def tearDown(self):
        self.con.close()
        os.remove(self.ruta)

    def token(self, mint, tenedores=True, juicio=None, real_sol=1e9, top10=10.0, t0=T0):
        """Token con 3 fotos (seg 0, 60, 70): entrada en la 2.ª (ts T0 + 60), la 3.ª sube x1,78 (objetivo x1,2)."""
        c = self.con
        c.execute("INSERT INTO tokens (mint, ts, creador, mayhem) VALUES (?, ?, 'C', 0)", (mint, t0))
        c.execute("INSERT INTO decisiones (mint, ts, pasa) VALUES (?, ?, 1)", (mint, t0))
        c.execute("INSERT INTO resumen (mint, ts_token, grad_inst, curva_atipica, error) VALUES (?, ?, 0, 0, NULL)",
                  (mint, t0))
        for seg, vs, vt, rs in ((0, 30e9, 1e15, 1e9), (60, 30e9, 1e15, 1e9), (70, 40e9, 0.75e15, real_sol)):
            c.execute("INSERT INTO fotos (mint, ts, segundos, v_sol, v_tokens, real_sol, complete) "
                      "VALUES (?, ?, ?, ?, ?, ?, 0)", (mint, t0 + seg, seg, vs, vt, rs))
        if tenedores:
            c.execute("INSERT INTO tenedores (mint, ts_decision, ts_consulta, seg_desde_primera_foto, top10_pct, "
                      "creador_pct, error) VALUES (?, ?, ?, 30, ?, 2.0, NULL)", (mint, t0, t0 + 30, top10))
        if juicio is not None:
            bs = {"b1": 0, "b2": 0, "b4": 0, "b5": 0, "b6": 0, **juicio}
            db.guardar_juicios(c, [{"mint": mint, "momento": 1, "version_juez": 1, **bs}])
        c.commit()

    def superviviente(self, mint, hueco=False, sel=10, inicio=0):
        """Entrada en la 1.ª lectura (marca = 1.ª foto + 30 min); lecturas cada 10 min durante 24 h (o solo 2 si hueco)."""
        c = self.con
        c.execute("INSERT INTO fotos (mint, ts, segundos, v_sol, v_tokens, real_sol, complete) "
                  "VALUES (?, ?, 0, 30e9, 1e15, 1e9, 0)", (mint, T0))
        marca = T0 + 1800
        c.execute("INSERT INTO supervivientes (mint, ts_seleccion, ts_fin) VALUES (?, ?, ?)",
                  (mint, marca + sel, marca + 86400 + 900))
        pasos = (0, 100) if hueco else range(inicio, 86401, 600)
        for k in pasos:
            c.execute("INSERT INTO lecturas_superv (mint, ts, fuente, precio_sol) VALUES (?, ?, 'curva', 3e-8)",
                      (mint, marca + k))
        c.commit()

    def mesas(self):
        return {m: {r[0]: r[1:] for r in self.con.execute("SELECT mint, ts_entrada, ret, estado FROM mesas_ops "
                                                          "WHERE mesa = ?", (m,))} for m in MESAS}

    def nuevas(self, umbral=UMBRAL, vistos=None):
        return ciclo(self.con, AHORA, vistos or {m: set() for m in MESAS}, 0, True, umbral)

    def datos_basicos(self):
        self.token("A", juicio={})  # BASE y JUEZ_VIGILAR
        self.token("B", juicio={"b2": 1})  # BASE, no JUEZ_VIGILAR (bandera)
        self.token("C", tenedores=False, real_sol=51e9)  # solo CERCA_GRADUAR (sin tenedores)

    def test_cada_mesa(self):
        self.datos_basicos()
        self.superviviente("S1")
        self.superviviente("S2", hueco=True)
        self.nuevas()
        m = self.mesas()
        self.assertEqual(set(m["BASE"]), {"A", "B"})
        self.assertEqual(set(m["JUEZ_VIGILAR"]), {"A"})
        self.assertEqual(set(m["CERCA_GRADUAR"]), {"C"})
        self.assertEqual(set(m["SUPERV_T1"]), {"S1", "S2"})
        self.assertEqual(m["BASE"]["A"][0], T0 + 60)  # ts real de la foto de entrada
        self.assertEqual(m["BASE"]["A"][2], "ok")
        self.assertGreater(m["BASE"]["A"][1], 0)  # llega al objetivo x1,2
        self.assertEqual(m["CERCA_GRADUAR"]["C"][0], T0 + 70)  # foto que cumple real_sol >= 50 SOL
        self.assertEqual(m["SUPERV_T1"]["S1"][0], T0 + 1800)
        self.assertEqual(m["SUPERV_T1"]["S1"][2], "ok")
        self.assertIsNotNone(m["SUPERV_T1"]["S1"][1])
        self.assertEqual(m["SUPERV_T1"]["S2"][1:], (None, "sin_dato"))

    def test_juez_sin_umbral_no_calcula_y_luego_si(self):
        self.datos_basicos()
        vistos = {m: set() for m in MESAS}
        out = self.nuevas(umbral=None, vistos=vistos)
        self.assertEqual(out["JUEZ_VIGILAR"], [])
        self.assertEqual({r["mint"] for r in out["BASE"]}, {"A", "B"})
        self.assertEqual(vistos["JUEZ_VIGILAR"], set())  # pendiente, no "visto"
        out = self.nuevas(umbral=UMBRAL, vistos=vistos)
        self.assertEqual([r["mint"] for r in out["JUEZ_VIGILAR"]], ["A"])
        self.assertEqual(out["BASE"], [])  # BASE no se repite

    def test_juez_b3_recalculado_con_umbral(self):
        self.token("A", juicio={}, top10=25.0)  # top10 >= umbral: bandera b3
        self.nuevas()
        self.assertEqual(self.mesas()["JUEZ_VIGILAR"], {})
        self.assertEqual(set(self.mesas()["BASE"]), {"A"})

    def test_idempotencia(self):
        self.datos_basicos()
        self.superviviente("S1")
        vistos = {m: set() for m in MESAS}
        self.nuevas(vistos=vistos)
        antes = self.con.execute("SELECT COUNT(*) FROM mesas_ops").fetchone()[0]
        out = self.nuevas(vistos=vistos)  # 2.º ciclo del mismo proceso
        self.assertTrue(all(f == [] for f in out.values()))
        self.nuevas()  # proceso reiniciado (vistos vacío): tampoco duplica
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM mesas_ops").fetchone()[0], antes)
        self.assertEqual(antes, 2 + 1 + 1 + 1)

    def test_vistos_no_se_marcan_si_no_se_guarda(self):
        self.datos_basicos()
        vistos = {m: set() for m in MESAS}
        ciclo(self.con, AHORA, vistos, 0, escribir=False, umbral_top10=UMBRAL)
        self.assertEqual(vistos["BASE"], set())

    def test_prueba_no_escribe(self):
        self.datos_basicos()
        self.superviviente("S1")
        self.con.execute("DROP TABLE mesas_ops")  # como una BD aún sin la tabla: --prueba no la crea
        self.con.commit()
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            out = prueba(0, self.ruta)
        self.assertEqual({m: len(out[m]) for m in ("BASE", "CERCA_GRADUAR", "SUPERV_T1")},
                         {"BASE": 2, "CERCA_GRADUAR": 1, "SUPERV_T1": 1})  # JUEZ_VIGILAR depende del umbral de config
        self.assertIn("no se ha escrito nada", buf.getvalue())
        self.assertIsNone(self.con.execute("SELECT 1 FROM sqlite_master WHERE name = 'mesas_ops'").fetchone())
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM agentes_estado").fetchone()[0], 0)

    def test_descartado_reciente_se_reintenta_y_antiguo_se_da_por_visto(self):
        self.token("VIEJO", tenedores=False)  # sin tenedores: no entra en BASE; decisión de hace > 3600 s
        reciente = AHORA - 2000  # ya con 30 min de fotos, pero con menos de MESAS_VISTO_S
        self.token("R", tenedores=False, t0=reciente)
        vistos = {m: set() for m in MESAS}
        self.nuevas(vistos=vistos)
        self.assertIn("VIEJO", vistos["BASE"])
        self.assertNotIn("R", vistos["BASE"])
        self.con.execute("INSERT INTO tenedores (mint, ts_decision, ts_consulta, seg_desde_primera_foto, top10_pct, "
                         "creador_pct, error) VALUES ('R', ?, ?, 30, 10.0, 2.0, NULL)", (reciente, reciente + 30))
        self.con.commit()
        self.nuevas(vistos=vistos)  # llegaron los tenedores: ahora sí entra
        self.assertIn("R", self.mesas()["BASE"])

    def test_superv_excluidos_con_fin_pasado_se_dan_por_vistos(self):
        self.superviviente("RET", sel=500)  # (a) selección retrasada (> 120 s tras la marca), terminado
        self.superviviente("TARDE", inicio=8000)  # 1.ª lectura tarde: ts_fin pasó pero la ventana de 24 h aún no
        vistos = {m: set() for m in MESAS}
        self.nuevas(vistos=vistos)
        self.assertEqual(self.mesas()["SUPERV_T1"], {})
        self.assertEqual(vistos["SUPERV_T1"], {"RET"})  # TARDE (sin_terminar) se reintentará

    def test_juicios_de_solo_los_pedidos(self):
        self.token("A", juicio={})
        self.token("B", juicio={})
        self.assertEqual(set(juicios_de(self.con, ["A"])), {"A"})
        self.assertEqual(juicios_de(self.con, []), {})
        self.assertEqual(set(juicios_de(self.con, ["A", "B", "X"], trozo=1)), {"A", "B"})

    def test_calcular_no_modifica_vistos(self):
        self.datos_basicos()
        vistos = {m: set() for m in MESAS}
        calcular(self.con, AHORA, vistos, 0, UMBRAL)
        self.assertTrue(all(v == set() for v in vistos.values()))


class TestEsperar(unittest.TestCase):
    def correr(self, espera_s, latido_s, falla=False):
        reloj, latidos, tot = [0], [], {"procesados": 0, "errores": 0}

        def guardar(*a):
            latidos.append(reloj[0])
            if falla:
                raise sqlite3.OperationalError("database is locked")

        def dormir(s):
            reloj[0] += s

        with mock.patch.object(db, "guardar_estado_agente", guardar), \
                mock.patch.object(db, "deshacer") as deshacer, contextlib.redirect_stdout(io.StringIO()):
            esperar(None, tot, espera_s, latido_s, dormir)
        return reloj[0], latidos, tot, deshacer

    def test_latido_cada_30_s_en_300(self):
        total, latidos, _, _ = self.correr(300, 30)
        self.assertEqual(total, 300)
        huecos = [b - a for a, b in zip([0] + latidos, latidos)]  # el 0 es el latido de tras el ciclo
        self.assertTrue(all(h <= 30 for h in huecos))
        self.assertEqual(latidos[-1], 300)

    def test_bd_que_falla_no_rompe_la_espera(self):
        total, latidos, tot, deshacer = self.correr(300, 30, falla=True)
        self.assertEqual(total, 300)
        self.assertEqual(tot["errores"], 10)
        self.assertEqual(deshacer.call_count, 10)


if __name__ == "__main__":
    unittest.main()
