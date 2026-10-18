import sqlite3
import unittest

from agentes import escaparate as es
from comun import db

SOL = {"chainId": "solana", "tokenAddress": "MINT1", "icon": "i1", "header": "h", "openGraph": "o",
       "links": [{"type": "twitter", "url": "x"}, {"url": "w"}], "description": "d"}


def _con():
    con = sqlite3.connect(":memory:")
    db.crear_tablas(con)
    return con


class TestPuras(unittest.TestCase):
    def test_solo_solana(self):
        lista = [SOL, {"chainId": "ethereum", "tokenAddress": "E"}, {"chainId": "solana"}, "basura"]
        self.assertEqual(es.solo_solana(lista), [SOL])

    def test_clave_cto(self):
        self.assertEqual(es.clave_de("cto", {"claimDate": "2026-10-17T16:02:29Z"}), "2026-10-17T16:02:29Z")

    def test_clave_perfil_ignora_imagenes(self):
        otra = dict(SOL, icon="OTRO", header="OTRO", openGraph="OTRO")
        self.assertEqual(es.clave_de("perfil", SOL), es.clave_de("perfil", otra))
        self.assertNotEqual(es.clave_de("perfil", SOL), es.clave_de("perfil", dict(SOL, description="nueva")))

    def test_clave_boost(self):
        self.assertEqual(es.clave_de("boost", {"totalAmount": 50, "amount": 10}), "50")
        self.assertEqual(es.clave_de("boost", {"amount": 10}), "10")

    def test_n_links(self):
        self.assertEqual(es.contar_links(SOL), 2)
        self.assertEqual(es.contar_links({}), 0)
        self.assertEqual(es.contar_links({"links": None}), 0)

    def test_raw_recortado(self):
        fila = es.fila_de("perfil", dict(SOL, description="x" * 10000), 1.0, False)
        self.assertEqual(len(fila["raw"]), 4096)


class TestGuardar(unittest.TestCase):
    def test_dos_veces_no_duplica_y_boost_nuevo_si(self):
        con = _con()
        b = dict(SOL, totalAmount=50, amount=50)
        db.guardar_escaparate(con, [es.fila_de("boost", b, 1.0, False)])
        db.guardar_escaparate(con, [es.fila_de("boost", b, 2.0, False)])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM escaparate").fetchone()[0], 1)
        db.guardar_escaparate(con, [es.fila_de("boost", dict(b, totalAmount=100), 3.0, False)])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM escaparate").fetchone()[0], 2)

    def test_ciclo_en_bd_y_error_de_red(self):
        con = _con()
        con.execute("INSERT INTO tokens (mint) VALUES ('MINT1')")

        def falso(url):
            if "boosts" in url:
                raise OSError("sin red")
            return [SOL, dict(SOL, tokenAddress="MINT2", claimDate="c")]
        nuevos, n_bd, err = es.ciclo(con, 5.0, pedir=falso)
        self.assertEqual((nuevos["cto"], nuevos["perfil"], nuevos["boost"], n_bd, err), (2, 2, 0, 2, 1))
        self.assertEqual(con.execute("SELECT COUNT(*) FROM escaparate WHERE en_bd = 1").fetchone()[0], 2)
        self.assertEqual(es.ciclo(con, 6.0, pedir=falso)[0], {"cto": 0, "perfil": 0, "boost": 0})


if __name__ == "__main__":
    unittest.main()
