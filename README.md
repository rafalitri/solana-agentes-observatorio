# Observatorio de memecoins en Solana

Sistema de 11 agentes en Python que vigila 24/7 los tokens nuevos de pump.fun (Solana) para responder a una pregunta:
**¿puede un bot de trading ganar dinero comprando memecoins recién creadas?**

Respuesta, con datos propios: **no**, al menos no para quien entra después de los bots de milisegundos y de los
compradores con información de dentro.

Proyecto personal, sin dinero real: no hay carteras, claves ni órdenes de compra o venta en el código.

## Qué hace

| Agente | Función |
|---|---|
| rastreador | Recibe cada token nuevo (WebSocket de PumpPortal) y fotografía su curva de precios cada 10 s durante 30 min |
| riesgo | Reglas fijas de descarte: creador en serie, copias, compra baja del creador... |
| analista v1 y v2 | Opinión de un modelo de IA local (Qwen3 14B vía Ollama) sobre nombre, descripción y redes |
| tenedores | Concentración de tenedores y porcentaje del creador en el minuto 1 |
| juez | Junta lo que dicen los demás y emite un veredicto por token antes de conocer el resultado |
| supervivientes | Sigue durante 24 h los tokens que siguen vivos a los 30 min |
| escaparate | Registra perfiles, takeovers y boosts de DexScreener |
| archivista | Resume cada token en una tabla para los informes |
| contable | Lleva "mesas en papel": operaciones simuladas con reglas fijadas de antemano |
| vigilante | Avisa si algún agente se cae o deja de dar señales de vida |

Los agentes se comunican a través de tablas SQLite (modo WAL). Un panel web local de solo lectura muestra la "sala de
agentes" y una oficina 3D (three.js).

Herramientas aparte: detección de *insiders* (compradores del mismo bloque de creación financiados por el creador),
"hermanos" (financiados por la misma cartera madre) y monederos que se repiten entre tokens.

## Metodología

- El criterio de éxito de cada hipótesis se fija **antes** de mirar los datos.
- Cada resultado se comprueba en las dos mitades del día y en días que no se usaron para diseñarlo.
- Costes incluidos: 1,25 % de comisión de pump.fun + 0,5 % de ejecución por operación, más la comisión de red.

## Resultados (15–18 de octubre de 2026)

- Más de 35.000 tokens observados, con el precio cada 10 segundos.
- **Ninguna combinación de entrada y salida gana dinero tras comisiones**, ni en el escenario más optimista.
  La mejor pierde un 6,6 % por operación.
- Los filtros con IA (Qwen) no mejoraron el resultado.
- Un modelo de 7 señales que ganaba un 3,3 % en sus datos de entrenamiento perdía un 11,5 % en datos nuevos.
  Es el error típico de los bots que se venden: resultados ajustados al pasado.
- A cualquier nivel de la curva, la probabilidad real de que un token se gradúe está unos 10 puntos por debajo de la que
  haría falta para no perder dinero comprando a ese precio.

## Seguridad

- Sin carteras ni claves privadas: el sistema solo observa.
- La URL del RPC (que lleva una clave de API) se lee de una variable de entorno y nunca se imprime ni se guarda en el
  repositorio.
- `.gitignore` excluye secretos, datos y registros.
- Los textos de los tokens (nombres, descripciones) se tratan como datos no fiables: nunca como instrucciones para la IA
  ni como HTML en el panel.
- Todo el código usa la librería estándar de Python salvo `websockets`.

## Cómo ejecutarlo

Requisitos: Python 3.13, Ollama con `qwen3:14b` (solo para los analistas).

```
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install websockets
python -m agentes.rastreador
python -m herramientas.panel      # http://127.0.0.1:8765/sala
```

Cada agente se lanza con `python -m agentes.<nombre>`. Tests: `python -m unittest`.

## Créditos

- three.js (licencia MIT) y el modelo RobotExpressive de Quaternius (CC0): ver `herramientas/estatico/`.
- Desarrollado con asistencia de IA (Claude Code).

## Aviso

Esto no es asesoramiento financiero. La mayoría de los tokens de pump.fun pierden casi todo su valor. Si alguien te
vende un bot "rentable", desconfía. Si te pide tu clave privada o tu frase semilla, es una estafa.

Autor: [rafalitri](https://github.com/rafalitri). Historial completo de desarrollo en un repositorio privado,
disponible bajo petición.
