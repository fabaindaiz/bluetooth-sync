# 14 · Graves y volumen con 3 Go 4

**Pregunta:** ¿qué hace de verdad el volumen Bluetooth (AVRCP) de cada Go 4, en dB? ¿El pasa-altos
de `bass=protect` evita que el Go 4 baje los graves por su cuenta al subir el volumen? ¿Se oye el
bajo psicoacústico, y se prefiere? Roadmap i-7c8794-765571; decisiones d-7c8794-0086cf (volumen
por AVRCP, `digital` por defecto hasta medir la curva) y d-7c8794-d1118c (lo nuevo entra apagado
hasta que una prueba repetida lo justifique); spec
`superpowers/specs/2026-10-02-microcuts-chain-and-quality-design.md` §5 y §8.

**Estado: protocolo y scripts listos, sin medir.** Se corre en `PC-Ryzen5` con los 3 Go 4 y el
micrófono fifine (`probes/15-graves-y-volumen/`).

**Entorno:** se anota en cada `.json` de `datos/14/` (equipo, kernel, PipeWire, WirePlumber, BlueZ,
versión de aurasync, hora, batería, posiciones, colocación del micrófono, volumen AVRCP de partida,
firmware si se leyó). Pendiente de copiar aquí con el resultado.

## 1. Por qué

- **El volumen AVRCP no es el de PipeWire.** Bajar Red de 80 % a 64 % (−6 dB por la curva cúbica de
  PipeWire) se vio como −4,1 dB en el micrófono, sin mover el retardo (MEDIDO,
  [experimentos/10](10-servicio-de-control-con-3-go-4.md) §5.4). El modo `volume.avrcp` del servicio
  usa la curva de PipeWire hasta que esta medición dé la del Go 4. Llevar el volumen al parlante y
  la cadena digital cerca de 0 dBFS importa porque el codificador SBC recibe 16 bits sin dither
  (VERIFICADO, research/11 §3.2).
- **La protección de graves del Go 4 es REPORTADA, no medida:** el DSP del parlante bajaría los
  graves pasado ~50 % de volumen (research/11 §2.2, §6). Si existe, quitarle al Go 4 lo que no
  puede dar (el pasa-altos de `protect`, LR4 en 90 Hz) le dejaría margen y la protección actuaría
  más tarde o nunca (INFERIDO).
- **El bajo psicoacústico intermodula:** con 50 + 70 Hz, el producto de 120 Hz queda ~8 dB sobre
  los armónicos (MEDIDO en simulación, research/11 §5.1); es inevitable con un rectificador sin
  memoria. Si molesta con música real solo lo dice un A/B ciego, y algunos oyentes prefieren sin él
  (Larsen y Aarts, VERIFICADO): por eso es una perilla apagada.

## 2. Lo que se construyó para medirlo (2026-10-02, en el Mac)

`probes/15-graves-y-volumen/` (paso a paso en su `README.md`), sobre `sistema.py` y `servicio.py`
de `probes/16-calidad/`:

- `curva_avrcp.py`: por Go 4, uno a la vez y directo a su sink, ruido rosa de pico 0,1 a 20, 40, 60,
  80 y 100 % y de vuelta; cada volumen pedido con `pactl` y **leído de vuelta** (como
  `aurasync.bt_volume`), cada `pw-play` y la grabación **verificados en `pw-dump`**. Nivel por
  tercio y total (100 Hz–10 kHz) y |H| por tercio. **Cambia el sistema** (el volumen de cada sink):
  lee el previo, anota el cambio y su reversión en `datos/14/cambios-de-sistema.txt` antes de
  hacerlo, y lo restaura al terminar, también con Ctrl-C o SIGTERM.
- `proteccion.py`: un Go 4 sonando (los otros en silencio por la API), ruido rosa por aurasync con
  `bass=off` y `bass=protect` (90 Hz, orden 4, `harmonics_db` −24 = sin armónicos), volumen AVRCP
  de 40 a 100 %. Mide el tercio de 125 Hz y la banda de 63–100 Hz **relativos** a 500 Hz–2 kHz.
  **Por qué ruido rosa:** estacionario y con energía en cada tercio, así el nivel de 125 Hz se
  compara entre volúmenes y entre sesiones sin depender del compás que sonó; `--wav` repite con
  música. El nivel que llega al parlante es pico 0,2 (el tope de las pruebas), ajustado con el
  volumen del panel y comprobado con el true peak que mide el servicio.
- `ab_graves.py`: el A/B ciego del servicio con `match_loudness: true` entre dos presets que
  difieren solo en `bass` (`off` contra `protect` + armónicos en 0 dB, la energía que repone lo que
  quita el pasa-altos). Los crea por la API si no existen y comprueba, cargándolos, que solo
  difieran en `bass`. Qué preset es A se sortea por sesión; por ensayo registra la respuesta ABX y
  la preferencia.
- `analisis.py` con 11 tests sobre señales sintéticas de respuesta conocida (`test_analisis_graves.py`):
  una curva inventada medida en dos "colocaciones" (otra sala sintética) sale igual a ±0,1 dB; un
  "Go 4" sintético con protección dependiente del nivel muestra la caída, y con el pasa-altos se
  retrasa; la binomial reproduce la tabla de research/11 §1.3 (12/16, 20/30, 26/40). Cada función
  se vio fallar con una mutación: comparar contra el volumen más alto en vez del más bajo, el nivel
  absoluto en vez del relativo, `>` en vez de `≥` en la binomial, aceptar una curva plana, recortar
  sin ubicar, tolerancia de ±1 dB.
- **Probado en el Mac contra `aurasync service --simular`:** `ab_graves.py` entero (respuestas por
  la entrada estándar; crea los presets, iguala la sonoridad —Δ −0,13 LU—, registra, restaura el
  estado y borra el preset temporal); `proteccion.py --ensayo` (la parte del servicio, y la
  restauración comprobada también interrumpiendo con SIGINT y SIGTERM a mitad); `curva_avrcp.py` y
  `proteccion.py` sin `--ensayo` fallan al principio con "faltan pw-play, pw-record, pw-dump, pactl".
  Nada de esto es una medición: la sala simulada no tiene protección de graves.

## 3. Criterios (escritos antes de medir)

| Prueba | Se acepta si |
|---|---|
| A · curva AVRCP | por Go 4, la curva (dB relativos a 100 %) sube en cada paso más de 0,5 dB y se repite a **±0,5 dB entre dos sesiones independientes** (otra colocación del micrófono, `comparar.py` exige que la colocación anotada difiera) |
| B · protección | con `bass=off` hay caída de 125 Hz (≥ 3 dB respecto de 40 %) y con `protect` empieza a un volumen **mayor o no aparece**, en **las dos** sesiones. Sin caída con `off`: **inconcluso**, no "no hay protección" |
| C · A/B ciego | "se oye" con **≥ 20 de 30 en cada una de 2 sesiones** (p ≤ 0,05, binomial exacta de una cola); "mejor" con la preferencia de las dos sesiones juntas (prueba de signos, p ≤ 0,05), y solo si se oye |

Además, para cualquier número: que no cambie al cambiar un parámetro que no debería importar. La
curva se mide de ida y de vuelta: la histéresis (la diferencia entre la ida y la vuelta en un mismo
volumen) queda en el `.json` y tiene que ser menor que la tolerancia de 0,5 dB para que la curva se
lea.

## 4. Protocolo

`probes/15-graves-y-volumen/README.md`, en el orden de `probes/16-calidad/README.md` (primero si el
Go 4 suma L+R, después la curva AVRCP, después el resto). Dos sesiones por prueba, la segunda otro
día o con el micrófono en otro lugar. `comparar.py` sobre todos los `.json` de `datos/14/`.

**Por decidir en PC-Ryzen5:**
- Si con pico 0,2 la protección no aparece con `off` (inconcluso), subir el nivel es relajar el
  tope de las pruebas: se conversa antes.
- El valor de armónicos del A/B (0 dB por defecto); si en la primera escucha la diferencia es
  obvia, el A/B mide poco: probar −6 dB.
- Con la curva medida, si `volume.avrcp` pasa a usarla y si deja de ser `digital` el valor por
  defecto (d-7c8794-0086cf).

## 5. Resultados

Pendiente.

## Veredicto

Pendiente.
