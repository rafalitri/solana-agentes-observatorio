"""Panel web de SOLO LECTURA. Ejecutar: python -m herramientas.panel (http://127.0.0.1:8765)."""
import json
import re
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from comun.config import (
    AGENTES_SALA, AGENTES_VIGILADOS, ANALISTA_VENTANA_S, ESTRATEGIA_EJECUCION, FOTO_S, LATIDO_MAX_S,
    MAX_TOKENS_CREADOR_24H, MESAS_N_MIN, MIN_SOL_CREADOR, PANEL_HOST, PANEL_PUERTO, PANEL_REFRESCO_S,
    REBOTES_MIN_N, RUTA_BD, SEGUIMIENTO_S, SIM_COMISION, VENTANA_CREADOR_S,
)
from comun.db import fotos_token
from herramientas.mesas import MESAS_ORDEN, marcador

SQL_ESTADOS = "SELECT agente, ts, estado, procesados, errores FROM agentes_estado"
SQL_DECISIONES = "SELECT COUNT(*), COALESCE(SUM(pasa), 0) FROM decisiones WHERE ts >= ?"
# rowid DESC: analisis no tiene índice por ts y crecerá mucho.
SQL_V1 = """SELECT a.ts, t.name, t.symbol, a.etiqueta, a.puntuacion, a.razon
            FROM analisis a LEFT JOIN tokens t ON t.mint = a.mint
            ORDER BY a.rowid DESC LIMIT 10"""
SQL_V2 = """SELECT a.ts, t.name, t.symbol, a.etiqueta, a.puntuacion, a.razon, a.meta_ok
            FROM analisis_v2 a LEFT JOIN tokens t ON t.mint = a.mint
            ORDER BY a.rowid DESC LIMIT 10"""

RUTA_POBLADO = Path(__file__).with_name("poblado.html")  # relativa a este archivo, no al directorio de trabajo
# Consultas del poblado: decisiones usa idx_decisiones_ts; el resto, rowid DESC o PK de tokens/decisiones.
SQL_P_DECISIONES = """SELECT d.mint, t.name, t.symbol, d.pasa, d.motivos, d.ts
                      FROM decisiones d LEFT JOIN tokens t ON t.mint = d.mint
                      WHERE d.ts >= ? ORDER BY d.ts DESC LIMIT 300"""
SQL_P_V1 = """SELECT a.mint, t.name, t.symbol, a.puntuacion, a.etiqueta, a.razon
              FROM analisis a LEFT JOIN tokens t ON t.mint = a.mint
              ORDER BY a.rowid DESC LIMIT 20"""
SQL_P_V2 = SQL_P_V1.replace("FROM analisis a", "FROM analisis_v2 a")
SQL_P_RESUMEN = """SELECT r.mint, t.name, t.symbol, r.graduado, r.error, d.pasa
                   FROM resumen r LEFT JOIN tokens t ON t.mint = r.mint
                   LEFT JOIN decisiones d ON d.mint = r.mint
                   ORDER BY r.rowid DESC LIMIT 30"""
SQL_S_V2 = """SELECT a.mint, t.name, t.symbol, a.puntuacion, a.etiqueta, a.meta_ok
              FROM analisis_v2 a LEFT JOIN tokens t ON t.mint = a.mint
              ORDER BY a.rowid DESC LIMIT 20"""
SQL_S_TENEDORES = "SELECT mint, top10_pct, creador_pct, error FROM tenedores ORDER BY rowid DESC LIMIT 20"
SQL_S_RESUMEN = """SELECT r.mint, t.name, t.symbol, r.graduado, r.error
                   FROM resumen r LEFT JOIN tokens t ON t.mint = r.mint
                   ORDER BY r.rowid DESC LIMIT 30"""


def edad_latido(ts, ahora):
    return None if ts is None else max(0, ahora - ts)


def en_alarma(edad, estado, max_s):
    return edad is None or edad > max_s or estado != "ok"


def _agente(nombre, fila, ahora, max_s):
    if fila is None:
        return {"agente": nombre, "estado": "sin_latido", "edad_s": None,
                "procesados": None, "errores": None, "alarma": True}
    _, ts, estado, procesados, errores = fila
    edad = edad_latido(ts, ahora)
    return {"agente": nombre, "estado": estado, "edad_s": edad,
            "procesados": procesados, "errores": errores,
            "alarma": en_alarma(edad, estado, max_s)}


def _analisis(fila):
    ts, name, symbol, etiqueta, punt, razon = fila[:6]
    d = {"ts": ts, "name": name, "symbol": symbol, "etiqueta": etiqueta,
         "puntuacion": punt, "razon": razon}
    if len(fila) > 6:
        d["meta_ok"] = fila[6]
    return d


def construir_json(ahora, estados, decididos, pasan, v1, v2,
                   max_s=LATIDO_MAX_S, vigilados=AGENTES_VIGILADOS):
    por_agente = {f[0]: f for f in estados}
    return {
        "ahora": ahora,
        "agentes": [_agente(a, por_agente.get(a), ahora, max_s) for a in vigilados],
        "decisiones": {"total": decididos, "pasan": pasan},
        "analisis_v1": [_analisis(f) for f in v1],
        "analisis_v2": [_analisis(f) for f in v2],
    }


def leer_datos():
    ahora = time.time()
    con = sqlite3.connect("file:" + RUTA_BD + "?mode=ro", uri=True)
    try:
        estados = con.execute(SQL_ESTADOS).fetchall()
        total, pasan = con.execute(SQL_DECISIONES, (ahora - ANALISTA_VENTANA_S,)).fetchone()
        v1 = con.execute(SQL_V1).fetchall()
        v2 = con.execute(SQL_V2).fetchall()
    finally:
        con.close()
    return construir_json(ahora, estados, total, pasan, v1, v2)


def _nombre(name, symbol, mint):
    return (name or "").strip() or (symbol or "").strip() or mint[:6]


def construir_json_poblado(ahora, estados, decisiones, v1, v2, resumen,
                           max_s=LATIDO_MAX_S, vigilados=AGENTES_VIGILADOS):
    agentes = construir_json(ahora, estados, 0, 0, [], [], max_s, vigilados)["agentes"]
    analisis = lambda f: {"mint": f[0], "nombre": _nombre(f[1], f[2], f[0]),
                          "puntuacion": f[3], "etiqueta": f[4], "razon": f[5]}
    return {
        "ahora": ahora,
        "agentes": agentes,
        "alarma": any(a["alarma"] for a in agentes),
        "decisiones": [{"mint": f[0], "nombre": _nombre(f[1], f[2], f[0]), "simbolo": f[2],
                        "pasa": f[3], "motivos": f[4], "ts": f[5]} for f in decisiones],
        "analisis_v1": [analisis(f) for f in v1],
        "analisis_v2": [analisis(f) for f in v2],
        "resumen": [{"mint": f[0], "nombre": _nombre(f[1], f[2], f[0]),
                     "graduado": f[3], "error": f[4], "pasa": f[5]} for f in resumen],
    }


def leer_datos_poblado():
    ahora = time.time()
    con = sqlite3.connect("file:" + RUTA_BD + "?mode=ro", uri=True)
    try:
        estados = con.execute(SQL_ESTADOS).fetchall()
        decisiones = con.execute(SQL_P_DECISIONES, (ahora - ANALISTA_VENTANA_S,)).fetchall()
        v1 = con.execute(SQL_P_V1).fetchall()
        v2 = con.execute(SQL_P_V2).fetchall()
        resumen = con.execute(SQL_P_RESUMEN).fetchall()
    finally:
        con.close()
    return construir_json_poblado(ahora, estados, decisiones, v1, v2, resumen)


RUTA_SALA = Path(__file__).with_name("sala.html")
RUTA_OFICINA = Path(__file__).with_name("oficina.html")
DIR_ESTATICO = Path(__file__).with_name("estatico")
# tipos fijos: mimetypes en Windows lee el registro y puede dar text/plain para .js (el navegador rechaza los módulos)
TIPOS_ESTATICO = {".js": "text/javascript; charset=utf-8", ".glb": "model/gltf-binary",
                  ".txt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8"}
RE_MINT = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")  # base58 (sin 0, O, I, l)
VELA_FOTOS = 6  # 6 fotos de 10 s = 1 vela de 1 min


def umbrales():
    return {"FOTO_S": FOTO_S, "SEGUIMIENTO_S": SEGUIMIENTO_S, "MIN_SOL_CREADOR": MIN_SOL_CREADOR,
            "MAX_TOKENS_CREADOR_24H": MAX_TOKENS_CREADOR_24H, "LATIDO_MAX_S": LATIDO_MAX_S,
            "SIM_COMISION": SIM_COMISION, "ESTRATEGIA_EJECUCION": ESTRATEGIA_EJECUCION,
            "REBOTES_MIN_N": REBOTES_MIN_N}


def _lista(texto):
    # motivos se guarda como texto JSON; si no es una lista válida, lista vacía
    try:
        v = json.loads(texto)
    except (ValueError, TypeError):
        return []
    return v if isinstance(v, list) else []


def juez_hora(con, ahora):
    # juicios de la última hora por veredicto; sin tabla juicios (aún no creada) todo a 0
    c = {"vigilar": 0, "descartar": 0, "sin_dato": 0}
    try:
        for v, n in con.execute("SELECT veredicto, COUNT(*) FROM juicios WHERE ts_juicio >= ? GROUP BY 1",
                                (ahora - 3600,)):
            if v in c:
                c[v] = n
    except sqlite3.OperationalError:
        pass
    total = sum(c.values())
    return {**c, "pct_vigilar": c["vigilar"] / total * 100 if total else 0}


def resolver_estatico(ruta, base):
    # (archivo, tipo) si la ruta cae dentro de base y es un archivo de extensión permitida; si no, None
    ruta = unquote(ruta)
    if not ruta or "\\" in ruta or ":" in ruta or "\0" in ruta or ruta.startswith("/"):
        return None
    tipo = TIPOS_ESTATICO.get(Path(ruta).suffix.lower())
    if tipo is None:
        return None
    try:
        raiz = base.resolve()
        archivo = (raiz / ruta).resolve()
    except (OSError, ValueError):
        return None
    if not archivo.is_relative_to(raiz) or not archivo.is_file():
        return None
    return archivo, tipo


def ultimo_juicio(con):
    # juicio (momento 1) más reciente; sin tabla o sin filas → None
    try:
        f = con.execute(
            "SELECT j.mint, t.name, t.symbol, j.ts_juicio, j.b1, j.b2, j.b3, j.b4, j.b5, j.b6, "
            "j.n_banderas, j.sin_dato, j.veredicto FROM juicios j LEFT JOIN tokens t ON t.mint = j.mint "
            "WHERE j.momento = 1 ORDER BY j.rowid DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return None
    if f is None:
        return None
    return {"mint": f[0], "nombre": _nombre(f[1], f[2], f[0]), "ts_juicio": f[3],
            **{f"b{i}": f[3 + i] for i in range(1, 7)},
            "n_banderas": f[10], "sin_dato": f[11], "veredicto": f[12]}


def mesas_marcador(con):
    # una entrada por mesa, en orden fijo; sin tabla mesas_ops (contable aún no arrancado) lista vacía
    try:
        filas = con.execute("SELECT mesa, ts_entrada, ret FROM mesas_ops").fetchall()
    except sqlite3.OperationalError:
        return []
    ops = {m: [(f[1], f[2]) for f in filas if f[0] == m] for m in MESAS_ORDEN}
    base = marcador(ops["BASE"], None, MESAS_N_MIN, es_base=True)
    return [{"mesa": m, **marcador(ops[m], base["media"], MESAS_N_MIN, es_base=(m == "BASE"))} for m in MESAS_ORDEN]


def construir_json_sala(con, ahora, max_s=LATIDO_MAX_S, vigilados=AGENTES_SALA):
    # fotos: solo max(rowid) (O(1)); el resto por índice o rowid DESC con LIMIT
    estados = con.execute(SQL_ESTADOS).fetchall()
    agentes = construir_json(ahora, estados, 0, 0, [], [], max_s, vigilados)["agentes"]
    por_min = con.execute(
        "SELECT CAST(ts / 60 AS INTEGER), COUNT(*) FROM decisiones WHERE ts >= ? AND pasa = 1 "
        "GROUP BY 1 ORDER BY 1", (ahora - 3600,)).fetchall()
    return {
        "ahora": ahora,
        "agentes": agentes,
        "alarma": any(a["alarma"] for a in agentes),
        "fotos_max_rowid": con.execute("SELECT max(rowid) FROM fotos").fetchone()[0] or 0,
        "decisiones": [{"mint": f[0], "nombre": _nombre(f[1], f[2], f[0]), "pasa": f[3],
                        "motivos": _lista(f[4]), "ts": f[5]}
                       for f in con.execute(SQL_P_DECISIONES, (ahora - ANALISTA_VENTANA_S,))],
        "pasan_por_minuto": [{"minuto": m * 60, "n": n} for m, n in por_min],
        "analisis_v1": [{"mint": f[0], "nombre": _nombre(f[1], f[2], f[0]), "puntuacion": f[3],
                         "etiqueta": f[4]} for f in con.execute(SQL_P_V1)],
        "analisis_v2": [{"mint": f[0], "nombre": _nombre(f[1], f[2], f[0]), "puntuacion": f[3],
                         "etiqueta": f[4], "meta_ok": f[5]} for f in con.execute(SQL_S_V2)],
        "tenedores": [{"mint": f[0], "top10_pct": f[1], "creador_pct": f[2], "error": f[3]}
                      for f in con.execute(SQL_S_TENEDORES)],
        "resumen": [{"mint": f[0], "nombre": _nombre(f[1], f[2], f[0]), "graduado": f[3],
                     "error": f[4]} for f in con.execute(SQL_S_RESUMEN)],
        "juez": juez_hora(con, ahora),
        "ultimo_juicio": ultimo_juicio(con),
        "mesas": mesas_marcador(con),
        "umbrales": umbrales(),
    }


def velas(fotos):
    # multiplicador del precio (v_sol / v_tokens) respecto a la 1.ª foto válida (complete = 0, v_tokens > 0)
    mults, base = [], None
    for f in fotos:
        if f["complete"] or not f["v_tokens"] or f["v_tokens"] <= 0:
            continue
        precio = f["v_sol"] / f["v_tokens"]
        base = base or precio
        mults.append(precio / base)
    return [{"open": c[0], "high": max(c), "low": min(c), "close": c[-1]}
            for c in (mults[i:i + VELA_FOTOS] for i in range(0, len(mults), VELA_FOTOS))]


def datos_token(con, mint, ahora):
    """Detalle de un token para la sala. None si el mint no es base58 de 32-44 caracteres."""
    if not RE_MINT.fullmatch(mint):
        return None
    fila = con.execute("SELECT creador, ts, bonding_curve FROM tokens WHERE mint = ?", (mint,)).fetchone()
    creador, ts, curva = fila if fila else (None, None, None)
    previos = graduados = 0
    if creador and ts is not None:
        # graduado ANTES de este token: ts del previo + seg_graduado <= ts de este token
        previos, graduados = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(r.graduado = 1 AND r.seg_graduado IS NOT NULL "
            "AND t.ts + r.seg_graduado <= ?), 0) "
            "FROM tokens t LEFT JOIN resumen r ON r.mint = t.mint "
            "WHERE t.creador = ? AND t.ts < ?", (ts, creador, ts)).fetchone()
    duenos = []
    ten = con.execute("SELECT raw FROM tenedores WHERE mint = ?", (mint,)).fetchone()
    if ten and ten[0]:
        try:
            duenos = [d["dueno"] for d in json.loads(ten[0]) if d["dueno"] and d["dueno"] != curva]
        except (ValueError, TypeError, KeyError):
            pass
    otros = set()  # un solo escaneo: dueños de los raw de otros tokens en 24 h, buscados en Python
    if duenos:
        for (raw,) in con.execute("SELECT raw FROM tenedores WHERE mint != ? AND ts_consulta >= ?",
                                  (mint, ahora - VENTANA_CREADOR_S)):
            try:
                otros.update(d["dueno"] for d in json.loads(raw or "[]"))
            except (ValueError, TypeError, KeyError):
                pass
    return {"mint": mint, "velas": velas(fotos_token(con, mint)),
            "arana": {"creador": creador, "previos": previos, "graduados": graduados,
                      "duenos": [{"dueno": d, "es_creador": d == creador,
                                  "repetido": d in otros and d != creador} for d in duenos]}}


def _abrir_ro():
    return sqlite3.connect("file:" + RUTA_BD + "?mode=ro", uri=True)


def leer_datos_sala():
    con = _abrir_ro()
    try:
        return construir_json_sala(con, time.time())
    finally:
        con.close()


def leer_datos_token(mint):
    con = _abrir_ro()
    try:
        return datos_token(con, mint, time.time())
    finally:
        con.close()


PAGINA = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>Panel agentes</title>
<style>
body{font-family:sans-serif;margin:1em;background:#111;color:#ddd}
table{border-collapse:collapse;margin-bottom:1em;width:100%}
td,th{border:1px solid #444;padding:3px 6px;text-align:left;font-size:14px}
.rojo{background:#6b1b1b}
#aviso{color:#f66}
</style></head><body>
<p><a href="/poblado" style="color:#8db43c">Ver el poblado goblin</a> · <a href="/sala" style="color:#8db43c">Ver la sala de agentes</a></p>
<h2>Agentes</h2><table id="agentes"></table>
<h2>Decisiones (últimos 10 min)</h2><p id="decisiones"></p>
<h2>Análisis v1</h2><table id="v1"></table>
<h2>Análisis v2</h2><table id="v2"></table>
<p id="aviso"></p>
<script>
const REFRESCO_MS = __REFRESCO__;
function tabla(id, cabecera, filas, rojas) {
  const t = document.getElementById(id);
  t.replaceChildren();
  const h = document.createElement("tr");
  for (const c of cabecera) { const th = document.createElement("th"); th.textContent = c; h.appendChild(th); }
  t.appendChild(h);
  filas.forEach((f, i) => {
    const tr = document.createElement("tr");
    if (rojas && rojas[i]) tr.className = "rojo";
    for (const v of f) { const td = document.createElement("td"); td.textContent = v === null || v === undefined ? "" : String(v); tr.appendChild(td); }
    t.appendChild(tr);
  });
}
const hora = ts => new Date(ts * 1000).toLocaleTimeString();
async function refrescar() {
  const aviso = document.getElementById("aviso");
  try {
    const r = await fetch("/datos");
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || r.status);
    tabla("agentes", ["agente", "estado", "último latido (s)", "procesados", "errores"],
      d.agentes.map(a => [a.agente, a.estado, a.edad_s === null ? "" : Math.round(a.edad_s), a.procesados, a.errores]),
      d.agentes.map(a => a.alarma));
    document.getElementById("decisiones").textContent = d.decisiones.total + " decididos, " + d.decisiones.pasan + " pasan";
    const fila = a => [hora(a.ts), a.name, a.symbol, a.etiqueta, a.puntuacion, a.razon];
    tabla("v1", ["hora", "nombre", "símbolo", "etiqueta", "puntuación", "razón"], d.analisis_v1.map(fila));
    tabla("v2", ["hora", "nombre", "símbolo", "etiqueta", "puntuación", "razón", "meta_ok"],
      d.analisis_v2.map(a => fila(a).concat([a.meta_ok])));
    aviso.textContent = "";
  } catch (e) { aviso.textContent = "Error: " + e.message; }
}
refrescar();
setInterval(refrescar, REFRESCO_MS);
</script></body></html>
""".replace("__REFRESCO__", str(PANEL_REFRESCO_S * 1000))


class Manejador(BaseHTTPRequestHandler):
    def _responder(self, codigo, tipo, cuerpo):
        datos = cuerpo.encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(datos)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(datos)

    def do_GET(self):
        if self.path == "/":
            self._responder(200, "text/html; charset=utf-8", PAGINA)
        elif self.path == "/datos":
            try:
                self._responder(200, "application/json", json.dumps(leer_datos()))
            except Exception as e:  # BD ausente o bloqueada: la página muestra el aviso
                self._responder(500, "application/json", json.dumps({"error": str(e)}))
        elif self.path == "/datos_poblado":
            try:
                self._responder(200, "application/json", json.dumps(leer_datos_poblado()))
            except Exception as e:
                self._responder(500, "application/json", json.dumps({"error": str(e)}))
        elif self.path == "/datos_sala":
            try:
                self._responder(200, "application/json", json.dumps(leer_datos_sala()))
            except Exception as e:
                self._responder(500, "application/json", json.dumps({"error": str(e)}))
        elif self.path.startswith("/datos_token?"):
            mint = parse_qs(urlsplit(self.path).query).get("mint", [""])[0]
            try:
                d = leer_datos_token(mint) if RE_MINT.fullmatch(mint) else None
                if d is None:
                    self._responder(400, "application/json", json.dumps({"error": "mint no válido"}))
                else:
                    self._responder(200, "application/json", json.dumps(d))
            except Exception as e:
                self._responder(500, "application/json", json.dumps({"error": str(e)}))
        elif self.path == "/sala":
            try:
                html = RUTA_SALA.read_text(encoding="utf-8").replace("__REFRESCO_MS__", str(PANEL_REFRESCO_S * 1000))
                self._responder(200, "text/html; charset=utf-8", html)
            except OSError as e:
                self._responder(500, "text/plain", str(e))
        elif self.path == "/oficina":
            try:
                html = RUTA_OFICINA.read_text(encoding="utf-8").replace("__REFRESCO_MS__", str(PANEL_REFRESCO_S * 1000))
                self._responder(200, "text/html; charset=utf-8", html)
            except OSError as e:
                self._responder(500, "text/plain", str(e))
        elif self.path.startswith("/estatico/"):
            r = resolver_estatico(urlsplit(self.path).path[len("/estatico/"):], DIR_ESTATICO)
            try:
                datos = r[0].read_bytes() if r else None
            except OSError:
                datos = None
            if datos is None:
                self._responder(404, "text/plain", "no encontrado")
            else:
                self.send_response(200)
                self.send_header("Content-Type", r[1])
                self.send_header("Content-Length", str(len(datos)))
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                self.wfile.write(datos)
        elif self.path == "/poblado":
            try:
                self._responder(200, "text/html; charset=utf-8", RUTA_POBLADO.read_text(encoding="utf-8"))
            except OSError as e:
                self._responder(500, "text/plain", str(e))
        else:
            self._responder(404, "text/plain", "no encontrado")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    servidor = ThreadingHTTPServer((PANEL_HOST, PANEL_PUERTO), Manejador)
    print(f"Panel en http://{PANEL_HOST}:{PANEL_PUERTO} (Ctrl+C para parar)")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()
