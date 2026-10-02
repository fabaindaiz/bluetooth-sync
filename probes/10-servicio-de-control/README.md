# 10 · Servicio de control con 3 Go 4

Los scripts del protocolo de
[experimentos/10](../../docs/research/experimentos/10-servicio-de-control-con-3-go-4.md) §3.
Se borran cuando el resultado esté anotado ahí (d-7c8794-3208b7).

**Volumen:** el servicio arranca en -20 dB y la señal tiene pico 0,3, así que cada parlante
recibe ~0,03. `api` se niega a subir `volume_db` por encima de -14 dB (amplitud 0,2).

## Orden

| Script | Qué hace | Qué hace la persona |
|---|---|---|
| `preflight.sh` | entorno, Modalias (firmware) de cada Go 4, `doctor`, restos de pruebas anteriores, ruteo | prender y conectar los tres |
| *(sin parlantes)* `seco.py` | el paso 3 completo por el servicio real, con la salida en memoria: cortes donde se esperan y ningún clic | — |
| `paso1-run.sh [seg] [wav]` | `run` a -20 dB con la señal, comprueba el ruteo, cierra con Ctrl-C | escuchar si suena como el 29 |
| `paso2-servicio.sh [wav]` | arranca el servicio, `start`, la fuente; **deja todo sonando** | escuchar |
| `paso3-ajustes.sh [seg]` | 17 cambios en vivo (`cambios-paso3.json`) grabando el micrófono; después `clics.py` | escuchar cada cambio; ¿clics? ¿el corte molesta? |
| `paso4-presets.sh [vueltas] [seg]` | presets A (cerrado) y B (amplio) alternados, y A cargado dos veces seguidas | ¿se oye la diferencia? ¿el corte molesta? |
| `paso5-falla.sh` | espera `error`, comprueba que el servicio sigue y el sink se fue; espera la reconexión y vuelve a arrancar | apagar los tres, después prenderlos |
| `paso6-dos-instancias.sh` | `run` con el servicio sonando: tiene que negarse | — |
| `paso7-cierre.sh` | `shutdown` por la API, y Ctrl-C con una sesión sonando | — |

Los pasos 3 a 6 necesitan el 2 sonando. Para juzgar el **envolvimiento** con música, se le
pasa un WAV a `paso2-servicio.sh` (o a `fuente.sh`); para buscar **clics**, la señal de prueba
(tonos graves), que es la de por defecto.

## Piezas

- `lib.sh`: `api` (llama y anota en `datos/10/sesion-<fecha>.jsonl`), `estado`, esperas.
- `servicio.sh arrancar|detener|estado`: el servicio en segundo plano, solo en 127.0.0.1.
- `fuente.sh [wav]|detener`: reproduce en el sink `aurasync` y **comprueba que llegó ahí**.
- `ruteo.py`: dónde va cada stream según `pw-dump`, sin pasar por aurasync.
- `grabar.sh <nombre>|detener`: el micrófono a `datos/10/`.
- `clics.py`: busca clics en una grabación y los cruza con el registro; mide su propia
  sensibilidad en cada grabación. `--autoprueba` lo prueba con defectos sintéticos.
- `senal.py`: tonos de 220 y 330 Hz más ruido rosa débil, distinto en cada canal.
- `seco.py [--romper]`: el ensayo en seco; con `--romper` tiene que fallar.
