from comun.config import MAX_TOKENS_CREADOR_24H, MIN_SOL_CREADOR


def evaluar(datos, contexto):
    """Reglas fijas, sin red ni BD. Devuelve pasa, motivos (descartes) y marcas (solo avisos)."""
    motivos, marcas = [], []
    if contexto["tokens_creador_24h"] >= MAX_TOKENS_CREADOR_24H:
        motivos.append("creador_en_serie")
    if contexto["repes_nombre_1h"] > 0 or contexto["repes_uri_1h"] > 0:
        motivos.append("copia")
    sol = datos.get("solAmount")
    if sol is None or sol < MIN_SOL_CREADOR:
        motivos.append("compra_creador_baja")
    if not (datos.get("name") or "").strip() or not (datos.get("symbol") or "").strip():
        motivos.append("sin_nombre")
    if datos.get("is_mayhem_mode"):
        motivos.append("mayhem")
    if not datos.get("mint", "").endswith("pump"):
        marcas.append("mint_sin_pump")
    return {"pasa": not motivos, "motivos": motivos, "marcas": marcas}
