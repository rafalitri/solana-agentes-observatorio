URL = "wss://pumpportal.fun/api/data"
RUTA_BD = "data/agentes.db"
ESPERA_MIN = 5
ESPERA_MAX = 60

VERSION_REGLAS = 2
MAX_TOKENS_CREADOR_24H = 3
VENTANA_CREADOR_S = 86400  # las 24 h de MAX_TOKENS_CREADOR_24H
VENTANA_REPETIDOS_S = 3600
MIN_SOL_CREADOR = 0.1
PORCENTAJE_CONTROL = 0.10

SEGUIMIENTO_S = 1800  # 30 min de fotos por token nuevo
FOTO_S = 10  # cada cuánto se fotografían las bonding curves (y se quitan caducados)
MAX_SEGUIDOS = 2000
RPC_URL = "https://api.mainnet-beta.solana.com"

BASE_SOL_ESTANDAR = 30.0  # reserva virtual base de una curva estándar (v_sol - real_sol, en SOL)
TOLERANCIA_BASE_SOL = 0.01

SIM_COMISION = 0.0125  # por lado (compra y venta)
SIM_FEE_RED_SOL = 0.0002  # por transacción
SIM_TAM_SOL = 0.1  # tamaño de la compra simulada
ESTRATEGIA_EJECUCION = 0.005  # coste extra por lado en la estrategia v0 (ejecución real peor que la simulada)

VERSION_RESUMEN = 1
UMBRALES_SUSTO = (0.5, 0.7)  # caída desde el máximo previo que cuenta como susto
ESTRATEGIA_MIN_N = 100  # mínimo de retornos por mitad en el veredicto de estrategia_v0 (mismo valor que rebotes)
REBOTES_MIN_N = 100  # mínimo de retornos por mitad para que el veredicto de rebotes cuente una combinación

OLLAMA_URL = "http://localhost:11434/api/generate"
MODELO_ANALISTA = "qwen3:14b"
VERSION_PROMPT = 1
ETIQUETAS_ANALISTA = ("meme", "copia", "estafa", "otro")
MAX_RAZON = 200
PASARELAS_IPFS = ("https://pump.mypinata.cloud/ipfs/", "https://gateway.pinata.cloud/ipfs/")
MAX_DESCRIPCION = 300
# herramientas/redes.py: pasarelas propias (Pinata nos limitó con ~2.400 peticiones seguidas); nunca las de PASARELAS_IPFS
REDES_PASARELAS = ("https://ipfs.filebase.io/ipfs/", "https://4everland.io/ipfs/")
REDES_PAUSA_S = 1.0
REDES_MAX_FALLOS_SEGUIDOS = 20
INSIDER_PETICIONES_S = 3  # Helius (10/s compartidos con el agente tenedores)
INSIDER_INTENTOS = 3
INSIDER_ESPERA_S = 5
INSIDER_429_REINTENTOS = 5  # un 429 espera TENEDORES_429_ESPERA_S y no cuenta como intento, pero con tope
INSIDER_MAX_FALLOS_SEGUIDOS = 20
INSIDER_MAX_PETICIONES = 50000  # por ejecución (el plan da 1 M créditos al mes)
INSIDER_MIN_SOL = 0.01  # transferencias menores (polvo) no cuentan para insider, madre ni hermano
HERMANOS_PREVIAS = 20  # transacciones anteriores de la MADRE que se miran
INSIDER_PREVIAS = 10  # transacciones anteriores del CREADOR (a la creación) que se miran
METADATOS_TIMEOUT_S = 8
ANALISTA_ESPERA_S = 2  # pausa si no hay tokens pendientes
ANALISTA_VENTANA_S = 600  # solo tokens decididos en los últimos 10 min
ANALISTA_LOTE = 20
ANALISTA_TIMEOUT_S = 120
ANALISTA_REINTENTO_S = 30  # pausa tras fallo de red con Ollama

ARCHIVISTA_DESDE = "2026-10-16 18:39"  # hora local; no resume tokens anteriores
ARCHIVISTA_MARGEN_S = 120  # espera extra tras SEGUIMIENTO_S para que estén todas las fotos
ARCHIVISTA_LOTE = 100
ARCHIVISTA_PAUSA_S = 1  # entre lotes llenos (poniéndose al día)
ARCHIVISTA_ESPERA_S = 30  # lote incompleto o vacío, o error de BD

TENEDORES_MIN_N = 50  # mínimo de retornos por grupo y mitad en el informe tenedores_informe
TENEDORES_SUPPLY =1_000_000_000  # tokens de pump.fun (sin decimales; los decimales los da el RPC)
ARANA_MIN_PCT = 0.1  # % del supply para ser "monedero activo" en un token (informe arana_informe)
ARANA_MIN_PREVIOS = 3  # tokens anteriores (por ts_consulta) con >= ARANA_MIN_PCT para ser "repetido"
ARANA_DECIMALES = 6  # suposición: tokens de pump.fun (la tabla tenedores no guarda los decimales)
TENEDORES_ESPERA_S = 5
TENEDORES_MAX_S = 600  # solo decisiones de los últimos 10 min; lo que salga de la ventana se pierde
TENEDORES_PAUSA_S = 0.5  # entre tokens: compartimos el RPC público con el rastreador
TENEDORES_INTENTOS = 3
TENEDORES_429_ESPERA_S = 10  # un 429 no cuenta como intento
TENEDORES_LOTE = 50
JUEZ_VERSION = 1
JUEZ_ESPERA_S = 10
JUEZ_MAX_S = 900  # solo decisiones de los últimos 15 min; lo que salga de la ventana sin juicio se pierde
JUEZ_PLAZO_S = 180  # a los 180 s de la decisión se juzga con lo que haya (lo que falte, NULL)
JUEZ_LOTE = 50
JUEZ_UMBRAL_TOP10 = None  # % de top10 desde el cual b3 vale 1; None = b3 sin dato (umbral aún sin fijar)
MESAS_ESPERA_S = 300  # agente contable: cada cuánto calcula las mesas en papel
MESAS_LATIDO_S = 30  # latido del contable durante la espera (el ciclo dura MESAS_ESPERA_S > LATIDO_MAX_S)
MESAS_DESDE = "2026-10-18 18:39"  # hora local; las mesas no miran decisiones/selecciones anteriores
MESAS_LOTE = 100
# ~150 operaciones para ver +5 % con 95 %/80 % y desv 0,25 (día 16→17), x1,43 por Bonferroni con 3 mesas a prueba
MESAS_N_MIN = 220
MESAS_VISTO_S = 3600  # un token descartado por una mesa solo se da por visto si su decision tiene mas de esto
TENEDORES_ENV_CLAVE = "HELIUS_RPC_URL"  # variable de entorno o línea en .env (la lee main(), no este módulo)

SUPERV_VENTANA_S = 1800  # supervivencia: se mide a los 30 min desde nuestra 1.ª foto
SUPERV_DURACION_S = 86400  # lecturas durante 24 h tras esos 30 min
SUPERV_MARGEN_FIN_S = 900  # +15 min: cubre la espera de 10 min tras graduarse
SUPERV_CANDIDATOS_S = 7200  # solo se evalúan decisiones de las últimas 2 h (con >= 30 min)
SUPERV_PERIODO_S = 60  # una ronda de lecturas por minuto
SUPERV_RPC_LOTE = 100  # máximo de getMultipleAccounts
SUPERV_RPC_PAUSA_S = 0.5  # entre lotes: RPC público compartido con el rastreador
SUPERV_DEX_LOTE = 5  # /latest/dex/tokens: ~3 pares por token y parece cortar en 30 pares por respuesta
SUPERV_DEX_PARES_MAX = 30  # una respuesta con >= 30 pares puede estar cortada: se repite mint a mint
SUPERV_DEX_MAX_MIN = 150  # tope de llamadas por minuto (con 150 graduados: 30 llamadas/min)
SUPERV_DEX_URL = "https://api.dexscreener.com/latest/dex/tokens/"  # /tokens/v1/solana/ no trae el par pumpswap
SUPERV_WSOL = "So11111111111111111111111111111111111111112"

VIGILANCIA_S = 30
LATIDO_MAX_S = 180
AGENTES_VIGILADOS = ("rastreador", "analista", "analista_v2", "archivista", "tenedores", "supervivientes", "juez", "contable")
AGENTES_SALA = AGENTES_VIGILADOS + ("escaparate",)  # los que pinta la sala (el vigilante no vigila escaparate)

PANEL_HOST = "127.0.0.1"  # solo local
PANEL_PUERTO = 8765
PANEL_REFRESCO_S = 5

ESCAPARATE_PERIODO_S = 60  # 3 peticiones por ciclo: ~3/min, muy por debajo del límite (~60/min)
ESCAPARATE_RESUMEN_CADA = 60  # ciclos entre líneas de resumen sin novedades
ESCAPARATE_RAW_MAX = 4096
ESCAPARATE_URLS = {
    "cto": "https://api.dexscreener.com/community-takeovers/latest/v1",
    "perfil": "https://api.dexscreener.com/token-profiles/latest/v1",
    "boost": "https://api.dexscreener.com/token-boosts/latest/v1",
}
