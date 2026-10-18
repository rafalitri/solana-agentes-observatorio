import unittest

from herramientas.insider import compradores_slot0, es_determinista, elegir_madre, es_transferencia_del_creador, marcar_hermanos, procesar_token
from herramientas.insider_informe import madres_resumen, veredicto_dia

CREADOR, COMPRADOR, OTRO, CURVA, MINT = "creador", "comprador", "otro", "curva", "mint1"


MIN = 10_000_000  # INSIDER_MIN_SOL = 0,01 SOL en lamports


def ins_transfer(origen, destino, tipo="transfer", lamports=MIN):
    return {"program": "system", "parsed": {"type": tipo, "info": {"source": origen, "destination": destino,
                                                                    "lamports": lamports}}}


def tx(arriba=(), internas=(), err=None):
    return {"meta": {"err": err, "innerInstructions": [{"index": 0, "instructions": list(internas)}]},
            "transaction": {"message": {"instructions": list(arriba)}}}


def bal(owner, cantidad, mint=MINT):
    return {"mint": mint, "owner": owner, "uiTokenAmount": {"amount": str(cantidad)}}


def tx_bloque(firma, pre, post, err=None):
    return {"transaction": {"signatures": [firma]}, "meta": {"err": err, "preTokenBalances": pre, "postTokenBalances": post}}


class TransferenciaCreador(unittest.TestCase):
    def test_arriba(self):
        self.assertTrue(es_transferencia_del_creador(tx([ins_transfer(CREADOR, COMPRADOR)]), CREADOR, COMPRADOR))

    def test_en_inner_instructions(self):
        t = tx(internas=[ins_transfer(CREADOR, COMPRADOR, "transferWithSeed")])
        self.assertTrue(es_transferencia_del_creador(t, CREADOR, COMPRADOR))

    def test_creador_transfiere_a_otra_cartera(self):
        t = tx([ins_transfer(CREADOR, OTRO)], [ins_transfer(CREADOR, OTRO)])
        self.assertFalse(es_transferencia_del_creador(t, CREADOR, COMPRADOR))

    def test_otro_origen_no_cuenta(self):
        self.assertFalse(es_transferencia_del_creador(tx([ins_transfer(OTRO, COMPRADOR)]), CREADOR, COMPRADOR))

    def test_otro_programa_o_tx_fallida_o_nula(self):
        otro = {"program": "spl-token", "parsed": {"type": "transfer", "info": {"source": CREADOR, "destination": COMPRADOR}}}
        self.assertFalse(es_transferencia_del_creador(tx([otro]), CREADOR, COMPRADOR))
        self.assertFalse(es_transferencia_del_creador(tx([ins_transfer(CREADOR, COMPRADOR)], err={"x": 1}), CREADOR, COMPRADOR))
        self.assertFalse(es_transferencia_del_creador(None, CREADOR, COMPRADOR))

    def test_instruccion_sin_parsear(self):
        self.assertFalse(es_transferencia_del_creador(tx([{"programId": "x", "data": "abc"}]), CREADOR, COMPRADOR))


class Compradores(unittest.TestCase):
    def test_filtra(self):
        bloque = {"transactions": [
            tx_bloque("s1", [bal(COMPRADOR, 0)], [bal(COMPRADOR, 100), bal(CURVA, 900)]),  # compra
            tx_bloque("s2", [bal(OTRO, 50)], [bal(OTRO, 20)]),  # venta: no
            tx_bloque("s3", [], [bal(CREADOR, 70)]),  # creador: excluido
            tx_bloque("s4", [], [bal("fallida", 5)], err={"x": 1}),  # tx fallida: no
            tx_bloque("s5", [], [bal("otro_mint", 5, mint="m2")]),  # otro mint: no
            tx_bloque("s6", [bal(COMPRADOR, 100)], [bal(COMPRADOR, 130)]),  # repite: se queda la 1.ª firma
        ]}
        self.assertEqual(compradores_slot0(bloque, MINT, {CREADOR, CURVA}), [(COMPRADOR, "s1")])

    def test_bloque_vacio(self):
        self.assertEqual(compradores_slot0(None, MINT, set()), [])
        self.assertEqual(compradores_slot0({"transactions": [{"transaction": {"signatures": ["a"]}, "meta": None}]}, MINT, set()), [])


class Falso:
    """Cliente de Helius simulado: historial del creador = {firma: tx}."""
    def __init__(self, historial):
        self.historial, self.llamadas = historial, []

    def llamar(self, metodo, params):
        self.llamadas.append(metodo)
        if metodo == "getBlock":
            return {"transactions": [tx_bloque("c1", [], [bal(COMPRADOR, 5)]), tx_bloque("c2", [], [bal(OTRO, 5)])]}
        if metodo == "getSignaturesForAddress":
            return [{"signature": f, "err": None} for f in self.historial]
        t = self.historial.get(params[0], {"slot": 77, "transaction": {"message": {"instructions": []}}})
        return t


class ProcesarToken(unittest.TestCase):
    def test_insider_por_historial_del_creador(self):
        h = {"h1": tx([ins_transfer(CREADOR, "ajeno")]), "h2": tx(internas=[ins_transfer(CREADOR, COMPRADOR)])}
        r = procesar_token(Falso(h), MINT, "creacion", CREADOR, CURVA)
        self.assertEqual(r["slot"], 77)
        self.assertEqual(r["compradores"], [(COMPRADOR, True, False), (OTRO, False, False)])

    def test_sin_compradores_no_mira_al_creador(self):
        f = Falso({})
        f.llamar = lambda m, p: {"slot": 1} if m == "getTransaction" else {"transactions": []}
        self.assertEqual(procesar_token(f, MINT, "c", CREADOR, CURVA)["compradores"], [])


class FalsoPorDireccion(Falso):
    """historial = {direccion: {firma: tx}}; getBlock como en Falso (compradores COMPRADOR y OTRO)."""
    def llamar(self, metodo, params):
        if metodo == "getBlock":
            return super().llamar(metodo, params)
        self.llamadas.append(metodo)
        if metodo == "getSignaturesForAddress":
            return [{"signature": f, "err": None} for f in self.historial.get(params[0], {})]
        for h in self.historial.values():
            if params[0] in h:
                return h[params[0]]
        return {"slot": 77, "transaction": {"message": {"instructions": []}}}


class ImporteMinimo(unittest.TestCase):
    def test_polvo_no_cuenta_para_insider(self):
        self.assertFalse(es_transferencia_del_creador(tx([ins_transfer(CREADOR, COMPRADOR, lamports=1000)]), CREADOR, COMPRADOR))

    def test_minimo_exacto_si_cuenta_para_insider(self):
        self.assertTrue(es_transferencia_del_creador(tx([ins_transfer(CREADOR, COMPRADOR, lamports=MIN)]), CREADOR, COMPRADOR))
        self.assertFalse(es_transferencia_del_creador(tx([ins_transfer(CREADOR, COMPRADOR, lamports=MIN - 1)]), CREADOR, COMPRADOR))

    def test_polvo_no_suma_para_madre(self):
        h = {CREADOR: {"h1": tx([ins_transfer("polvo", CREADOR, lamports=1000)]),
                       "h2": tx([ins_transfer("madre", CREADOR, lamports=MIN)])}}
        r = procesar_token(FalsoPorDireccion(h), MINT, "creacion", CREADOR, CURVA)
        self.assertEqual(r["madre"], "madre")  # "polvo" no es candidata aunque sea más reciente
        h = {CREADOR: {"h1": tx([ins_transfer("polvo", CREADOR, lamports=1000)])}}
        self.assertIsNone(procesar_token(FalsoPorDireccion(h), MINT, "creacion", CREADOR, CURVA)["madre"])

    def test_polvo_no_hace_hermano_y_minimo_exacto_si(self):
        base = {CREADOR: {"h1": tx([ins_transfer("madre", CREADOR)])}}
        polvo = dict(base, madre={"m1": tx([ins_transfer("madre", COMPRADOR, lamports=1000)])})
        self.assertEqual(procesar_token(FalsoPorDireccion(polvo), MINT, "c", CREADOR, CURVA)["compradores"][0],
                         (COMPRADOR, False, False))
        exacto = dict(base, madre={"m1": tx([ins_transfer("madre", COMPRADOR, lamports=MIN)])})
        self.assertEqual(procesar_token(FalsoPorDireccion(exacto), MINT, "c", CREADOR, CURVA)["compradores"][0],
                         (COMPRADOR, False, True))


class Hermanos(unittest.TestCase):
    def test_elegir_madre_mayor_importe(self):
        self.assertEqual(elegir_madre([("a", 5), ("b", 9), ("a", 5)], CREADOR), "a")  # suma por cartera
        self.assertEqual(elegir_madre([("a", 5), ("b", 9)], CREADOR), "b")

    def test_elegir_madre_empate_la_mas_reciente(self):
        self.assertEqual(elegir_madre([("reciente", 5), ("antigua", 5)], CREADOR), "reciente")

    def test_elegir_madre_sin_candidata(self):
        self.assertIsNone(elegir_madre([], CREADOR))
        self.assertIsNone(elegir_madre([(CREADOR, 9)], CREADOR))

    def test_hermano_por_owner_y_por_firmante(self):
        comp = [("o1", "s1", "p1"), ("o2", "s2", "p2"), ("o3", "s3", "p3")]
        self.assertEqual(marcar_hermanos(comp, {"o1", "p2"}, set()), {"o1", "o2"})

    def test_madre_compradora_cuenta_como_hermana(self):
        h = {CREADOR: {"h1": tx([ins_transfer("madre", CREADOR)])}, "madre": {}}
        f = FalsoPorDireccion(h)
        bloque_original = f.llamar
        f.llamar = lambda m, p: ({"transactions": [tx_bloque("c1", [], [bal("madre", 5)])]} if m == "getBlock"
                                 else bloque_original(m, p))
        r = procesar_token(f, MINT, "creacion", CREADOR, CURVA)
        self.assertEqual(r["compradores"], [("madre", False, True)])
        self.assertEqual(r["pruebas"][0]["firma_compra"], "c1")
        self.assertEqual(f.llamadas.count("getSignaturesForAddress"), 1)  # ya son todos: no pide la historia de la madre

    def test_hermano_excluye_creador_y_curva(self):
        comp = [("o1", "s1", CREADOR), ("o2", "s2", CURVA)]
        self.assertEqual(marcar_hermanos(comp, {CREADOR, CURVA}, {CREADOR, CURVA}), set())

    def test_procesar_token_hermano_via_madre(self):
        h = {CREADOR: {"h1": tx([ins_transfer("madre", CREADOR)])},
             "madre": {"m1": tx([ins_transfer("madre", COMPRADOR)])}}
        r = procesar_token(FalsoPorDireccion(h), MINT, "creacion", CREADOR, CURVA)
        self.assertEqual(r["madre"], "madre")
        self.assertEqual(r["compradores"], [(COMPRADOR, False, True), (OTRO, False, False)])
        self.assertIsNone(r["error_hermanos"])
        self.assertEqual((r["pruebas"][0]["via"], r["pruebas"][0]["firma_envio"], r["pruebas"][0]["lamports"]), (COMPRADOR, "m1", MIN))

    def test_sin_madre_no_pide_mas(self):
        f = FalsoPorDireccion({CREADOR: {"h1": tx([ins_transfer(CREADOR, "ajeno")])}})
        r = procesar_token(f, MINT, "creacion", CREADOR, CURVA)
        self.assertIsNone(r["madre"])
        self.assertEqual(f.llamadas.count("getSignaturesForAddress"), 1)


class Madres(unittest.TestCase):
    def test_resumen(self):
        filas = [{"madre": "AAAAAAxx", "creador": "c1"}, {"madre": "AAAAAAxx", "creador": "c2"},
                 {"madre": "AAAAAAxx", "creador": "c2"}, {"madre": "BBBBBBxx", "creador": "c3"}, {"madre": None, "creador": "c4"}]
        self.assertEqual(madres_resumen(filas), (2, 1, [("AAAAAA", 2), ("BBBBBB", 1)]))

    def test_etiqueta_hermanos(self):
        self.assertEqual(veredicto_dia(stats(60, -0.1, 60, 0.1), stats(60, 0.0, 60, 0.0), 50, "VEREDICTO_DIA_HERMANOS", "hermanos"),
                         "VEREDICTO_DIA_HERMANOS: NO")


class Deterministas(unittest.TestCase):
    def test_codigos(self):
        self.assertTrue(es_determinista("RPC: {'code': -32015, 'message': 'Transaction version (1)'}"))
        self.assertTrue(es_determinista("RPC: {'code': -32602, 'message': 'x'}"))
        self.assertFalse(es_determinista("RPC: {'code': -32000}"))
        self.assertFalse(es_determinista("URLError: timed out"))


def stats(n1, m1, n2, m2):
    return {1: (n1, m1, m1, 1.0), 2: (n2, m2, m2, 1.0)}


class Veredicto(unittest.TestCase):
    def test_peor(self):
        self.assertEqual(veredicto_dia(stats(60, -0.1, 60, -0.1), stats(60, 0.0, 60, 0.0)),
                         "VEREDICTO_DIA: insider PEOR en las dos mitades")

    def test_no(self):
        self.assertEqual(veredicto_dia(stats(60, -0.1, 60, 0.1), stats(60, 0.0, 60, 0.0)), "VEREDICTO_DIA: NO")

    def test_no_concluyente(self):
        self.assertIn("NO CONCLUYENTE", veredicto_dia(stats(49, -0.1, 60, -0.1), stats(60, 0.0, 60, 0.0)))
        self.assertIn("NO CONCLUYENTE", veredicto_dia(stats(60, -0.1, 60, -0.1), stats(60, 0.0, 10, 0.0)))


if __name__ == "__main__":
    unittest.main()
