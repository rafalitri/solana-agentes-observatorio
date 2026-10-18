import json
import sqlite3
import time
from pathlib import Path

from comun.config import RUTA_BD, VENTANA_CREADOR_S, VENTANA_REPETIDOS_S, VERSION_REGLAS


def conectar():
    Path(RUTA_BD).parent.mkdir(exist_ok=True)
    con = sqlite3.connect(RUTA_BD)
    con.execute("PRAGMA journal_mode=WAL")
    return con


def deshacer(con):
    # tras un error de BD la transacción queda abierta y la conexión ve una foto vieja; nunca lanza
    try:
        con.rollback()
    except sqlite3.Error:
        pass


def crear_tablas(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS tokens (
            mint TEXT PRIMARY KEY,
            ts REAL,
            hora TEXT,
            name TEXT,
            symbol TEXT,
            creador TEXT,
            sol_amount REAL,
            initial_buy REAL,
            market_cap_sol REAL,
            v_sol REAL,
            mayhem INTEGER,
            pool TEXT,
            uri TEXT,
            signature TEXT,
            bonding_curve TEXT,
            raw TEXT
        );
        CREATE TABLE IF NOT EXISTS operaciones (
            id INTEGER PRIMARY KEY,
            mint TEXT,
            ts REAL,
            hora TEXT,
            tipo TEXT,
            trader TEXT,
            sol_amount REAL,
            token_amount REAL,
            market_cap_sol REAL,
            raw TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_operaciones_mint_ts ON operaciones (mint, ts);
        CREATE INDEX IF NOT EXISTS idx_tokens_creador_ts ON tokens (creador, ts);
        CREATE INDEX IF NOT EXISTS idx_tokens_name ON tokens (name);
        CREATE INDEX IF NOT EXISTS idx_tokens_uri ON tokens (uri);
        CREATE TABLE IF NOT EXISTS decisiones (
            mint TEXT PRIMARY KEY,
            ts REAL,
            version_reglas INTEGER,
            pasa INTEGER,
            motivos TEXT,
            marcas TEXT,
            control INTEGER,
            seguir INTEGER
        );
        CREATE TABLE IF NOT EXISTS fotos (
            mint TEXT,
            ts REAL,
            segundos INTEGER,
            v_sol REAL,
            v_tokens REAL,
            real_sol REAL,
            complete INTEGER,
            market_cap_sol REAL,
            raw TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_fotos_mint_ts ON fotos (mint, ts);
        CREATE TABLE IF NOT EXISTS analisis (
            mint TEXT PRIMARY KEY,
            ts REAL,
            version_prompt INTEGER,
            modelo TEXT,
            etiqueta TEXT,
            puntuacion INTEGER,
            razon TEXT,
            duracion_s REAL,
            error TEXT,
            raw TEXT
        );
        CREATE TABLE IF NOT EXISTS analisis_v2 (
            mint TEXT PRIMARY KEY,
            ts REAL,
            version_prompt INTEGER,
            modelo TEXT,
            etiqueta TEXT,
            puntuacion INTEGER,
            razon TEXT,
            duracion_s REAL,
            error TEXT,
            raw TEXT,
            meta_ok INTEGER,
            meta_s REAL
        );
        CREATE TABLE IF NOT EXISTS agentes_estado (
            agente TEXT PRIMARY KEY,
            ts REAL,
            estado TEXT,
            procesados INTEGER,
            errores INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_decisiones_ts ON decisiones (ts);
        CREATE TABLE IF NOT EXISTS resumen (
            mint TEXT PRIMARY KEY,
            ts_token REAL,
            ts_resumen REAL,
            version_resumen INTEGER,
            n_fotos INTEGER,
            seg_primera INTEGER,
            hueco_max_s INTEGER,
            grad_inst INTEGER,
            curva_atipica INTEGER,
            graduado INTEGER,
            seg_graduado INTEGER,
            mult_max REAL,
            seg_max INTEGER,
            mult_min REAL,
            mult_final REAL,
            caida_max REAL,
            ret_r0_obj REAL,
            ret_r1_obj REAL,
            ret_r0_fin REAL,
            ret_r1_fin REAL,
            seg_susto50 INTEGER,
            ret_s50_r0_obj REAL,
            ret_s50_r1_obj REAL,
            ret_s50_r0_fin REAL,
            ret_s50_r1_fin REAL,
            seg_susto70 INTEGER,
            ret_s70_r0_obj REAL,
            ret_s70_r1_obj REAL,
            ret_s70_r0_fin REAL,
            ret_s70_r1_fin REAL,
            error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_resumen_ts_token ON resumen (ts_token);
        CREATE TABLE IF NOT EXISTS tenedores (
            mint TEXT PRIMARY KEY,
            ts_decision REAL,
            ts_consulta REAL,
            seg_desde_primera_foto REAL,
            curva_pct REAL,
            top10_pct REAL,
            top10_circ_pct REAL,
            creador_pct REAL,
            creador_en_top INTEGER,
            n_cuentas INTEGER,
            raw TEXT,
            error TEXT
        );
        CREATE TABLE IF NOT EXISTS redes (
            mint TEXT PRIMARY KEY,
            ts_descarga REAL,
            ok INTEGER,
            motivo TEXT,
            twitter INTEGER,
            telegram INTEGER,
            web INTEGER
        );
        CREATE TABLE IF NOT EXISTS insider (
            mint TEXT PRIMARY KEY,
            ts_consulta REAL,
            slot_creacion INTEGER,
            n_compradores_slot0 INTEGER,
            n_insiders INTEGER,
            error TEXT,
            raw TEXT
        );
        CREATE TABLE IF NOT EXISTS supervivientes (
            mint TEXT PRIMARY KEY,
            ts_seleccion REAL,
            motivo TEXT,
            ts_graduacion REAL,
            precio_1a_foto REAL,
            precio_30min REAL,
            ts_fin REAL,
            fuente TEXT,
            precio_ultimo_curva REAL,
            ts_ultimo_curva REAL,
            precio_primero_dex REAL,
            ts_primero_dex REAL
        );
        CREATE TABLE IF NOT EXISTS lecturas_superv (
            mint TEXT,
            ts REAL,
            fuente TEXT,
            precio_sol REAL,
            liquidez_usd REAL,
            volumen_5m REAL,
            compras_5m INTEGER,
            ventas_5m INTEGER,
            fdv_usd REAL,
            error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_lecturas_superv ON lecturas_superv (mint, ts);
        CREATE TABLE IF NOT EXISTS escaparate (
            tipo TEXT,
            mint TEXT,
            clave TEXT,
            ts_visto REAL,
            claim_date TEXT,
            amount INTEGER,
            total_amount INTEGER,
            n_links INTEGER,
            en_bd INTEGER,
            raw TEXT,
            PRIMARY KEY (tipo, mint, clave)
        );
        CREATE TABLE IF NOT EXISTS juicios (
            mint TEXT,
            momento INTEGER,
            ts_juicio REAL,
            ts_decision REAL,
            version_juez INTEGER,
            b1 INTEGER,
            b2 INTEGER,
            b3 INTEGER,
            b4 INTEGER,
            b5 INTEGER,
            b6 INTEGER,
            top10_pct REAL,
            n_banderas INTEGER,
            sin_dato TEXT,
            veredicto TEXT,
            PRIMARY KEY (mint, momento)
        );
        CREATE TABLE IF NOT EXISTS mesas_ops (
            mesa TEXT,
            mint TEXT,
            ts_entrada REAL,
            ret REAL,
            estado TEXT,
            ts_calculo REAL,
            PRIMARY KEY (mesa, mint)
        );
    """)
    # pieza 24b: columnas añadidas a insider después de crearla
    existentes = {r[1] for r in con.execute("PRAGMA table_info(insider)")}
    for col, tipo in (("madre", "TEXT"), ("n_hermanos", "INTEGER"), ("error_hermanos", "TEXT")):
        if col not in existentes:
            con.execute(f"ALTER TABLE insider ADD COLUMN {col} {tipo}")
    # pieza 25b: par elegido en cada lectura dex (las filas anteriores quedan con NULL)
    existentes = {r[1] for r in con.execute("PRAGMA table_info(lecturas_superv)")}
    for col in ("dex_id", "pair_address"):
        if col not in existentes:
            con.execute(f"ALTER TABLE lecturas_superv ADD COLUMN {col} TEXT")
    # pieza 29: redes de los metadatos (1/0; NULL = sin metadatos o fila anterior)
    existentes = {r[1] for r in con.execute("PRAGMA table_info(analisis_v2)")}
    for col in ("twitter", "telegram", "web"):
        if col not in existentes:
            con.execute(f"ALTER TABLE analisis_v2 ADD COLUMN {col} INTEGER")
    con.commit()


def pendientes_redes(con, desde, hasta, reintentar=False):
    # (mint, uri) con pasa = 1 en la ventana de decisiones.ts, con uri y sin fila en redes, los más antiguos primero.
    # reintentar: también los que tienen fila con motivo = 'descarga' (el resto de filas siguen contando como hechas)
    return con.execute(
        "SELECT d.mint, t.uri FROM decisiones d JOIN tokens t ON t.mint = d.mint "
        "WHERE d.pasa = 1 AND d.ts >= ? AND d.ts <= ? AND t.uri IS NOT NULL AND t.uri != '' "
        "AND NOT EXISTS (SELECT 1 FROM redes r WHERE r.mint = d.mint AND (? = 0 OR r.motivo IS NOT 'descarga')) "
        "ORDER BY d.ts", (desde, hasta, int(reintentar))).fetchall()


def guardar_redes(con, filas, reemplazar=False):
    # filas: tuplas (mint, ts_descarga, ok, motivo, twitter, telegram, web); un solo commit, sin dejar transacción abierta
    modo = "REPLACE" if reemplazar else "IGNORE"
    con.executemany(f"INSERT OR {modo} INTO redes VALUES (?, ?, ?, ?, ?, ?, ?)", filas)
    con.commit()


def pendientes_insider(con, desde, hasta, reintentar=False):
    # (mint, signature, creador, bonding_curve) del universo v0 en la ventana de decisiones.ts, sin fila en insider
    # (reintentar: también las filas con error), los más antiguos primero. Sin la tabla (--seco sobre BD de solo lectura) = sin filas.
    hay = con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'insider'").fetchone()
    hecho = ("AND NOT EXISTS (SELECT 1 FROM insider i WHERE i.mint = d.mint AND (? = 0 OR i.error IS NULL)) "
             if hay else "AND ? = ? ")
    extra = (int(reintentar),) if hay else (0, 0)
    return con.execute(
        "SELECT d.mint, t.signature, t.creador, t.bonding_curve FROM decisiones d JOIN resumen r ON r.mint = d.mint "
        "JOIN tokens t ON t.mint = d.mint WHERE d.pasa = 1 AND d.ts >= ? AND d.ts <= ? AND r.error IS NULL "
        "AND r.grad_inst = 0 AND r.curva_atipica = 0 AND COALESCE(t.mayhem, 0) = 0 " + hecho + "ORDER BY d.ts",
        (desde, hasta) + extra).fetchall()


def guardar_insider(con, filas):
    # filas: tuplas (mint, ts_consulta, slot_creacion, n_compradores_slot0, n_insiders, error, raw, madre, n_hermanos,
    # error_hermanos); un solo commit
    con.executemany("INSERT OR REPLACE INTO insider VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", filas)
    con.commit()


def pendientes_tenedores(con, desde_ts, limite):
    # (mint, ts decisión, bonding_curve, creador) con pasa = 1, sin fila en tenedores y con alguna foto
    # (EXISTS por mint usa idx_fotos_mint_ts), los más antiguos primero
    return con.execute(
        "SELECT d.mint, d.ts, t.bonding_curve, t.creador FROM decisiones d JOIN tokens t ON t.mint = d.mint "
        "WHERE d.pasa = 1 AND d.ts >= ? "
        "AND NOT EXISTS (SELECT 1 FROM tenedores h WHERE h.mint = d.mint) "
        "AND EXISTS (SELECT 1 FROM fotos f WHERE f.mint = d.mint) "
        "ORDER BY d.ts LIMIT ?", (desde_ts, limite)).fetchall()


def pendientes_juez(con, desde_ts):
    # dicts de las decisiones con pasa = 1 sin juicio de momento 1, con lo que haya de tokens, tenedores y analisis_v2
    # (hay_ten / hay_a2 = existe la fila), los más antiguos primero. Sin tabla juicios (modo --prueba en solo lectura)
    # no se filtra por juicios.
    hay = con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'juicios'").fetchone()
    sin_juicio = ("AND NOT EXISTS (SELECT 1 FROM juicios j WHERE j.mint = d.mint AND j.momento = 1) "
                  if hay else "")
    cur = con.execute(
        "SELECT d.mint AS mint, d.ts AS ts_decision, t.creador AS creador, t.initial_buy AS initial_buy, "
        "te.mint IS NOT NULL AS hay_ten, te.error AS ten_error, te.creador_pct AS creador_pct, "
        "te.top10_pct AS top10_pct, a.mint IS NOT NULL AS hay_a2, a.twitter AS twitter, a.telegram AS telegram, "
        "a.web AS web, EXISTS (SELECT 1 FROM tokens p WHERE p.creador = t.creador AND p.ts < t.ts) AS previo "
        "FROM decisiones d JOIN tokens t ON t.mint = d.mint "
        "LEFT JOIN tenedores te ON te.mint = d.mint LEFT JOIN analisis_v2 a ON a.mint = d.mint "
        f"WHERE d.pasa = 1 AND d.ts >= ? {sin_juicio}ORDER BY d.ts", (desde_ts,))
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def tenedores_con_raw(con, desde_ts):
    # (mint, ts_consulta, raw, bonding_curve, creador): mismas filas que CONSULTA_RAW de arana_informe
    return con.execute(
        "SELECT te.mint, te.ts_consulta, te.raw, t.bonding_curve, t.creador FROM tenedores te "
        "JOIN tokens t ON t.mint = te.mint WHERE te.error IS NULL AND te.raw IS NOT NULL AND te.ts_consulta >= ? "
        "ORDER BY te.ts_consulta", (desde_ts,)).fetchall()


def guardar_juicios(con, filas):
    # filas: dicts con las columnas de juicios; un solo commit
    cols = [r[1] for r in con.execute("PRAGMA table_info(juicios)")]
    con.executemany(f"INSERT OR IGNORE INTO juicios ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    [tuple(f.get(c) for c in cols) for f in filas])
    con.commit()


def primera_foto_ts(con, mint):
    return con.execute("SELECT MIN(ts) FROM fotos WHERE mint = ?", (mint,)).fetchone()[0]


def guardar_tenedores(con, fila):
    # fila: dict con mint y las demás columnas de tenedores (las que falten van a NULL)
    cols = [r[1] for r in con.execute("PRAGMA table_info(tenedores)")]
    con.execute(f"INSERT OR IGNORE INTO tenedores ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                tuple(fila.get(c) for c in cols))
    con.commit()


def _contar(con, sql, valor, mint, desde):
    return con.execute(sql, (valor, mint, desde)).fetchone()[0]


def contexto_token(con, datos):
    # cuenta solo OTROS mints dentro de cada ventana, mirando hacia atrás desde datos["ts"]
    ts, mint = datos["ts"], datos["mint"]
    creador = datos.get("traderPublicKey")
    nombre = (datos.get("name") or "").strip()
    uri = datos.get("uri") or ""
    # shortcut: LOWER de SQLite solo pasa a minúsculas ASCII; si hacen falta acentos, normalizar al guardar
    return {
        "tokens_creador_24h": _contar(
            con, "SELECT COUNT(*) FROM tokens WHERE creador = ? AND mint != ? AND ts >= ?",
            creador, mint, ts - VENTANA_CREADOR_S) if creador else 0,
        "repes_nombre_1h": _contar(
            con, "SELECT COUNT(*) FROM tokens WHERE LOWER(TRIM(name)) = LOWER(?) AND mint != ? AND ts >= ?",
            nombre, mint, ts - VENTANA_REPETIDOS_S) if nombre else 0,
        "repes_uri_1h": _contar(
            con, "SELECT COUNT(*) FROM tokens WHERE uri = ? AND mint != ? AND ts >= ?",
            uri, mint, ts - VENTANA_REPETIDOS_S) if uri else 0,
    }


def guardar_decision(con, mint, decision):
    con.execute(
        "INSERT OR IGNORE INTO decisiones VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            mint, time.time(), VERSION_REGLAS, int(decision["pasa"]),
            json.dumps(decision["motivos"]), json.dumps(decision["marcas"]),
            int(decision["control"]), int(decision["seguir"]),
        ),
    )
    con.commit()


def mints_en_seguimiento(con, desde_ts):
    # dict mint -> (ts, bonding_curve) (de más antiguo a más nuevo), para rehacer `seguidos` al reconectar
    return {mint: (ts, curva) for mint, ts, curva in con.execute(
        "SELECT mint, ts, bonding_curve FROM tokens "
        "WHERE ts >= ? AND bonding_curve IS NOT NULL ORDER BY ts", (desde_ts,))}


def pendientes_analisis(con, desde_ts, limite):
    # tokens que pasaron las reglas y aún no tienen análisis, los más antiguos primero
    return con.execute(
        "SELECT t.mint, t.name, t.symbol FROM decisiones d JOIN tokens t ON t.mint = d.mint "
        "WHERE d.pasa = 1 AND d.ts >= ? "
        "AND NOT EXISTS (SELECT 1 FROM analisis a WHERE a.mint = d.mint) "
        "ORDER BY d.ts LIMIT ?", (desde_ts, limite)).fetchall()


def guardar_analisis(con, mint, ts, version_prompt, modelo, etiqueta, puntuacion,
                     razon, duracion_s, error, raw):
    con.execute(
        "INSERT OR IGNORE INTO analisis VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (mint, ts, version_prompt, modelo, etiqueta, puntuacion, razon, duracion_s, error, raw),
    )
    con.commit()


def pendientes_analisis_v2(con, desde_ts, limite):
    # como pendientes_analisis pero sobre analisis_v2 y con la uri (para bajar los metadatos)
    return con.execute(
        "SELECT t.mint, t.name, t.symbol, t.uri FROM decisiones d JOIN tokens t ON t.mint = d.mint "
        "WHERE d.pasa = 1 AND d.ts >= ? "
        "AND NOT EXISTS (SELECT 1 FROM analisis_v2 a WHERE a.mint = d.mint) "
        "ORDER BY d.ts LIMIT ?", (desde_ts, limite)).fetchall()


def guardar_analisis_v2(con, mint, ts, version_prompt, modelo, etiqueta, puntuacion,
                        razon, duracion_s, error, raw, meta_ok, meta_s,
                        twitter=None, telegram=None, web=None):
    con.execute(
        "INSERT OR IGNORE INTO analisis_v2 (mint, ts, version_prompt, modelo, etiqueta, puntuacion, razon, "
        "duracion_s, error, raw, meta_ok, meta_s, twitter, telegram, web) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (mint, ts, version_prompt, modelo, etiqueta, puntuacion, razon, duracion_s, error, raw,
         meta_ok, meta_s, twitter, telegram, web),
    )
    con.commit()


def guardar_estado_agente(con, agente, ts, estado, procesados, errores):
    con.execute(
        "INSERT OR REPLACE INTO agentes_estado VALUES (?, ?, ?, ?, ?)",
        (agente, ts, estado, procesados, errores),
    )
    con.commit()


def pendientes_resumen(con, desde_ts, hasta_ts, limite):
    # (mint, ts, mayhem) de tokens decididos entre desde_ts y hasta_ts sin resumen, los más antiguos primero
    return con.execute(
        "SELECT d.mint, d.ts, t.mayhem FROM decisiones d JOIN tokens t ON t.mint = d.mint "
        "WHERE d.ts >= ? AND d.ts <= ? "
        "AND NOT EXISTS (SELECT 1 FROM resumen r WHERE r.mint = d.mint) "
        "ORDER BY d.ts LIMIT ?", (desde_ts, hasta_ts, limite)).fetchall()


def fotos_token(con, mint):
    # usa idx_fotos_mint_ts
    return [{"segundos": s, "v_sol": vs, "v_tokens": vt, "real_sol": rs, "complete": c}
            for s, vs, vt, rs, c in con.execute(
                "SELECT segundos, v_sol, v_tokens, real_sol, complete FROM fotos "
                "WHERE mint = ? ORDER BY ts", (mint,))]


def guardar_resumenes(con, filas):
    # filas: dicts con mint, ts_token, ts_resumen, error y las claves de resumen (las que falten van a NULL)
    cols = [r[1] for r in con.execute("PRAGMA table_info(resumen)")]
    con.executemany(
        f"INSERT OR IGNORE INTO resumen ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        [tuple(f.get(c) for c in cols) for f in filas])
    con.commit()


def fotos_token_ts(con, mint):
    # como fotos_token pero con la ts real de cada foto (la usa el agente contable para ts_entrada)
    return [{"ts": t, "segundos": s, "v_sol": vs, "v_tokens": vt, "real_sol": rs, "complete": c}
            for t, s, vs, vt, rs, c in con.execute(
                "SELECT ts, segundos, v_sol, v_tokens, real_sol, complete FROM fotos "
                "WHERE mint = ? ORDER BY ts", (mint,))]


def mesas_hechas(con, mesa):
    # mints con fila en mesas_ops para esa mesa; sin la tabla (modo --prueba en solo lectura) = vacío
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'mesas_ops'").fetchone():
        return set()
    return {r[0] for r in con.execute("SELECT mint FROM mesas_ops WHERE mesa = ?", (mesa,))}


def guardar_mesas_ops(con, filas):
    # filas: dicts con las columnas de mesas_ops; un solo commit
    con.executemany("INSERT OR IGNORE INTO mesas_ops (mesa, mint, ts_entrada, ret, estado, ts_calculo) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [(f["mesa"], f["mint"], f["ts_entrada"], f["ret"], f["estado"], f["ts_calculo"]) for f in filas])
    con.commit()


def guardar_fotos(con, filas):
    # filas: tuplas en el orden de las columnas de fotos
    con.executemany("INSERT INTO fotos VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", filas)
    con.commit()


def guardar_operacion(con, datos):
    con.execute(
        "INSERT INTO operaciones (mint, ts, hora, tipo, trader, sol_amount, token_amount, "
        "market_cap_sol, raw) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            datos["mint"], datos.get("ts"), datos.get("hora"),
            datos.get("txType") or "migracion", datos.get("traderPublicKey"),
            datos.get("solAmount"), datos.get("tokenAmount"), datos.get("marketCapSol"),
            json.dumps(datos, ensure_ascii=False),
        ),
    )
    con.commit()


def guardar_token(con, datos):
    # mismo orden que las columnas de tokens; las claves son los campos de PumpPortal
    con.execute(
        "INSERT OR IGNORE INTO tokens VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            datos["mint"], datos.get("ts"), datos.get("hora"),
            datos.get("name"), datos.get("symbol"), datos.get("traderPublicKey"),
            datos.get("solAmount"), datos.get("initialBuy"), datos.get("marketCapSol"),
            datos.get("vSolInBondingCurve"), datos.get("is_mayhem_mode"),
            datos.get("pool"), datos.get("uri"), datos.get("signature"),
            datos.get("bondingCurveKey"), json.dumps(datos, ensure_ascii=False),
        ),
    )
    con.commit()


def candidatos_superv(con, desde_ts, hasta_ts):
    # (mint, ts decisión) del universo v0 (pasa = 1, resumen válido sin grad_inst ni curva_atipica, sin mayhem)
    # aún no seleccionados, los más antiguos primero
    return con.execute(
        "SELECT d.mint, d.ts FROM decisiones d JOIN tokens t ON t.mint = d.mint JOIN resumen r ON r.mint = d.mint "
        "WHERE d.pasa = 1 AND d.ts >= ? AND d.ts <= ? AND COALESCE(t.mayhem, 0) = 0 "
        "AND r.error IS NULL AND r.grad_inst = 0 AND r.curva_atipica = 0 "
        "AND NOT EXISTS (SELECT 1 FROM supervivientes s WHERE s.mint = d.mint) "
        "ORDER BY d.ts", (desde_ts, hasta_ts)).fetchall()


def fotos_superv(con, mint):
    # (ts, v_sol, v_tokens, complete) por ts; usa idx_fotos_mint_ts
    return con.execute("SELECT ts, v_sol, v_tokens, complete FROM fotos WHERE mint = ? ORDER BY ts", (mint,)).fetchall()


def guardar_escaparate(con, filas):
    # filas: dicts con todas las columnas de escaparate; INSERT OR IGNORE: lo ya visto no se duplica; un solo commit
    con.executemany(
        "INSERT OR IGNORE INTO escaparate VALUES (:tipo, :mint, :clave, :ts_visto, :claim_date, :amount, "
        ":total_amount, :n_links, :en_bd, :raw)", filas)
    con.commit()


def mints_en_tokens(con, mints):
    # subconjunto de mints que ya están en nuestra tabla tokens
    mints = list(mints)
    if not mints:
        return set()
    marcas = ",".join("?" * len(mints))
    return {r[0] for r in con.execute(f"SELECT mint FROM tokens WHERE mint IN ({marcas})", mints)}


def guardar_supervivientes(con, filas):
    # filas: dicts con todas las columnas de supervivientes; un solo commit
    con.executemany(
        "INSERT OR IGNORE INTO supervivientes VALUES (:mint, :ts_seleccion, :motivo, :ts_graduacion, :precio_1a_foto, "
        ":precio_30min, :ts_fin, :fuente, :precio_ultimo_curva, :ts_ultimo_curva, :precio_primero_dex, "
        ":ts_primero_dex)", filas)
    con.commit()


def activos_superv(con, ahora):
    # (mint, fuente, bonding_curve) de los supervivientes que aún se leen
    return con.execute(
        "SELECT s.mint, s.fuente, t.bonding_curve FROM supervivientes s JOIN tokens t ON t.mint = s.mint "
        "WHERE s.ts_fin > ?", (ahora,)).fetchall()


def guardar_lecturas_superv(con, filas):
    # filas: tuplas (mint, ts, fuente, precio_sol, liquidez_usd, volumen_5m, compras_5m, ventas_5m, fdv_usd, dex_id,
    # pair_address, error).
    # Además: último precio de curva, primer precio dex y paso curva -> dex (error 'graduada'); un solo commit
    con.executemany("INSERT INTO lecturas_superv (mint, ts, fuente, precio_sol, liquidez_usd, volumen_5m, compras_5m, ventas_5m, "
        "fdv_usd, dex_id, pair_address, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", filas)
    for mint, ts, fuente, precio, *_, error in filas:
        if fuente == "curva" and error == "graduada":
            con.execute("UPDATE supervivientes SET fuente = 'dex', ts_graduacion = COALESCE(ts_graduacion, ?) "
                        "WHERE mint = ?", (ts, mint))
        elif fuente == "curva" and precio is not None:
            con.execute("UPDATE supervivientes SET precio_ultimo_curva = ?, ts_ultimo_curva = ? WHERE mint = ?",
                        (precio, ts, mint))
        elif fuente == "dex" and precio is not None:
            con.execute("UPDATE supervivientes SET precio_primero_dex = ?, ts_primero_dex = ? "
                        "WHERE mint = ? AND precio_primero_dex IS NULL", (precio, ts, mint))
    con.commit()
