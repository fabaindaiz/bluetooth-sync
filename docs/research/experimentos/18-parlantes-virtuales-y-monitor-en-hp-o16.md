# 18 · Parlantes virtuales y monitor en HP-O16: ¿llegan los bloques en tiempo real?

**Pregunta:** con todos los parlantes virtuales (`sink: null`) y solo el monitor de audífonos como
salida, ¿la sesión lleva el reloj en tiempo real y el monitor suena sin cortes? Roadmap
i-7c8794-757041; decisiones d-7c8794-0e5063 y d-7c8794-05bdd6; spec
`superpowers/specs/2026-10-05-virtual-speakers-and-hot-join-design.md` §4 y §8.

**Estado: protocolo listo, sin medir.** Se corre en `HP-O16` (el portátil, mismo AX210). Fase 1: solo
tests hasta hoy; nada se ha escuchado.

**Precondición:** el usuario permite usar los audífonos **WH-CH520** en **A2DP** (hoy nada toca los
audífonos ni PipeWire en `HP-O16` sin su permiso). Unos audífonos en HFP suenan mono a 16 kHz y
falsean todo lo de abajo.

## 1. Entorno que se anota (antes de medir)

- Equipo: `HP-O16`. Kernel (`uname -r`), PipeWire 1.6.9, WirePlumber 0.5.17, BlueZ 5.87 (las versiones
  de 2026-10-05; se vuelven a leer).
- Audífonos: modelo WH-CH520, firmware si se puede leer, perfil activo (debe decir A2DP).
- **Estado de energía y carga:** batería o red (`cat /sys/class/power_supply/ADP1/online`; el
  estado del puerto USB-C no es el de la batería), el gobernador de la CPU
  (`cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor`) y la carga (`uptime`) durante la
  medición. El 2026-10-05 un test de costo falló de forma intermitente por la carga de otros
  procesos, no por la batería (i-7c8794-be46cb). Se corre sin otros procesos pesados.
- Fecha, versión de `aurasync` y la instalación (cuántos parlantes virtuales).

## 2. Qué se mide

1. **Bloques en tiempo real con todo virtual.** El spec §4 dice, **INFERIDO**, que sin un dispositivo
   que lo maneje PipeWire asigna el sink `aurasync` a su Dummy-Driver, que corre en tiempo real. Este
   experimento lo **confirma o corrige**: con 3 parlantes virtuales, una aplicación sonando y la
   sesión en marcha, contar los bloques entregados por segundo durante 5 minutos (esperado:
   `rate / block`) y la desviación del reloj. Si no llegan en tiempo real, se anota qué hace en su
   lugar.
2. **Cortes del monitor por minuto.** Con el modo `mix` y luego `binaural`, leer `state.monitor.drops`
   al inicio y al final de 5 minutos y dividir. El monitor no está sincronizado y tira un bloque si
   va atrasado; el reloj de la entrada y el de los audífonos derivan, así que se espera uno
   ocasional (INFERIDO). Repetir en dos corridas independientes, como pide `CLAUDE.md`.
3. **Ningún `pw-play` hacia un parlante.** Con la sesión en marcha, `pw-dump` no debe mostrar ningún
   stream de reproducción dirigido a un parlante virtual ni a un parlante Bluetooth. Se comprueba
   en `pw-dump`, no se supone (`CLAUDE.md`: lo que se le pide a PipeWire se verifica).

## 3. Qué se ejecuta

(Los comandos exactos se anotan aquí al correr.)

```bash
cd host && hatch run aurasync service            # sin --simular
# panel: agregar 3 parlantes virtuales, arrancar, elegir los WH-CH520 en Monitor, modo mix
pw-dump > /tmp/e18-pw-dump.json                  # comprobación 3
# comprobación 2: leer state.monitor.drops del snapshot al inicio y a los 5 min
```

## Hallazgo previo (2026-10-05): cortes del monitor

Antes de correr el protocolo, el monitor ya se oyó cortado. **Causa MEDIDA** en `HP-O16` con
`pw-top` (columna ERR del nodo `pw-play`), audífonos WH-CH520 en A2DP con AAC, bloque del motor de
4096 cuadros (85,3 ms a 48 kHz), quantum del driver Bluetooth de 2048 cuadros (42,7 ms), música de
Spotify, ventanas de 30 s:

- El monitor escribía un bloque cada dos ciclos del driver y **nada por adelantado**: cada bloque
  llegaba justo después del ciclo que lo necesitaba. **352 xruns en 30 s** (11,7/s de 23,4 ciclos/s:
  la mitad de los ciclos en silencio).
- Con 100 ms de silencio escritos una vez al abrir (experimento aparte): **0 xruns en 30 s** con música.
- Con la música en pausa (entrada `None`, el motor marcado por `outputs.Pacer` con `time.monotonic`)
  los xruns volvieron (~320 en ~1 min): un colchón fijo se vacía. **INFERIDO:** que sea por la diferencia entre el reloj
  del motor y el del driver; la causa todavía no se sabe (con ~23 % de ciclos sin datos no encaja con
  una deriva de ppm). Hay que **controlarlo**, no solo cebarlo.

**Arreglo:** el colchón objetivo es un bloque más un quantum del driver (tope 400 ms), se escribe como
silencio al abrir y se vigila en cada bloque con el nivel de la tubería: por debajo de un quantum se
rellena hasta el objetivo (un hueco en vez de una racha de cortes) y por encima de objetivo + 2 bloques
se descarta el bloque. El estado del monitor lo muestra (`cushion_ms`, `level_ms`, `refills`, `trims`).

Cada medición es **una sola corrida**, todavía sin repetir: CLAUDE.md pide repetición entre mediciones
independientes antes de darla por buena. Las comprobaciones formales de este experimento siguen
pendientes; falta medir con el arreglo (xruns de `pw-play` y `state.monitor.refills`/`trims` con música
y en pausa).

## Volumen entre modos del monitor (2026-10-05)

**Síntoma (lo oyó el usuario):** pasar el monitor entre `stereo`, `mix` y `binaural` cambiaba mucho el
volumen.

**Medición sin audio (MEDIDO offline en `HP-O16`, el `Motor` real con la instalación del usuario: 4
parlantes virtuales en cuadrafonía, ruido estéreo a −20 dBFS):**

| volumen general | `stereo` | `mix` | diferencia |
|---|---|---|---|
| −20 dB (el del usuario) | −20,0 dB | −40,9 dB | `mix` 20,9 dB más bajo |
| 0 dB | −20,0 dB | −20,9 dB | −0,9 dB |

Repetida con ruido rosa (`ruido_rosa`, −20 dBFS RMS por canal, cadena por defecto): `mix` − entrada =
−19,7 dB a −20 dB de volumen y +0,3 dB a 0 dB. La diferencia a 0 dB cambia con el espectro del ruido
(−0,9 contra +0,3); la de −20 dB es el volumen entero.

**Causa:** `stereo` mandaba el par de entrada **antes** de la cadena y no veía el volumen digital del
motor; `mix` pliega lo que recibieron los parlantes, **después** del volumen. `binaural` suma además la
ganancia propia del HRTF y la de N espacializadores en el filter-chain de PipeWire, que Python no ve.

**Arreglo (diseño aprobado por el usuario):**

- `stereo` aplica el volumen elegido. La referencia de todos los modos es "la entrada al volumen
  elegido". El valor es `volume_db` del servicio (la perilla del panel) en los dos modos de volumen:
  con `digital` es el mismo volumen digital del motor; con `volume.avrcp` el motor queda a 0 dB y los
  parlantes llevan la perilla, así que el monitor aplica la diferencia (perilla − digital) también a
  `mix` y `binaural` (`monitor.Levels`). El volumen digital que se descuenta es **el que tenía el
  bloque al hacerse** (`Motor.volumen_del_bloque_db`), no el objetivo del motor: al salir de `avrcp`
  el volumen digital salta de 0 a −20 dB en el fondo del corte, después de hacer el último bloque, y
  leer el objetivo dejaba ese bloque ~14 dB sobre el nivel estable en los audífonos (MEDIDO offline en
  la revisión: ventana máxima −20,3 dB contra −33,7 dB estable; tras el arreglo −32,9 contra −33,6).
- **Igualación automática** (`host/src/aurasync/loudness_match.py`): una ganancia de compensación por
  modo que sigue `referencia − candidato` (sonoridad K de corto plazo, 3 s, con el mismo
  `LoudnessMeter` de la franja de calidad), al 10 % del error por segundo y en rampa dentro del bloque,
  con tope ±12 dB. Se congela con la entrada **antes del volumen** bajo −50 LUFS (la decide la
  sonoridad momentánea, así una pausa la congela en 0,4 s; con el umbral sobre la entrada ya al volumen
  elegido, una perilla bajo unos −30 dB la dejaba congelada con la música sonando), con el candidato en
  silencio (todos los parlantes silenciados) y durante un corte o una calibración. Los cambios de la
  perilla también van en rampa dentro del bloque. Cada modo recuerda su compensación: al volver a un modo arranca
  donde quedó y no salta. Si cambian los parlantes o sus ángulos, se olvida.
- `mix` se mide directo (sus cuadros están en Python). Un `mix` nunca visto arranca de una estimación
  por número de parlantes, **MEDIDA offline** (mismo método, `auto` de cada N, 0 dB, ruido rosa):
  `mix` − entrada = −6,2 / −1,9 / −0,5 / +0,3 / +1,2 / +2,2 / +2,9 / +3,9 dB para N = 1…8 (crece más o
  menos como `10·log10(N/4)`). Se descartó la estimación `−10·log10(N/2)` del brief: erraba unos 3 dB
  en todos los N.
- `binaural`: el candidato es la sonoridad de los N canales que van al filtro más la ganancia medida del
  HRTF (abajo). Si el SHA-256 del `.sofa` no está en la tabla no se compensa (0 dB) y el estado lo dice
  (`match: "unmeasured"`; el panel: «Compensación binaural sin medir para este HRTF»).
- El estado del monitor muestra `makeup_db`, `loudness_reference`, `loudness_monitor`, `match`
  (`measuring`, `locked`, `frozen`, `unmeasured`) y `match_reason`; la tarjeta «Monitor (audífonos)»
  dice «Nivel igualado: −1,2 dB» o «Igualando…».

**Lo que comprueban los tests** (`host/tests/test_monitor_loudness.py`, `test_loudness_match.py`; offline,
el `Motor` real con la instalación del usuario y ruido rosa): `stereo` a −20 dB queda 20 dB bajo la
entrada; ya igualados, `mix` y `stereo` quedan a ≤ 1 LU a −20 y a 0 dB; los cambios stereo→mix→stereo→mix
no saltan más de 1 LU (tampoco la primera vuelta); con un `mix` 9 dB más bajo, cada vuelta arranca en
la compensación recordada; 10 s de pausa no la mueven más de 0,05 dB; un candidato 30 dB más bajo se
detiene en +12 dB; un HRTF sin medir da 0 dB y el motivo, y uno medido aplica su constante. Nada de
esto se ha escuchado todavía con los audífonos.

### Ganancia del HRTF del monitor binaural (sonda `probes/21-ganancia-hrtf/`)

**MEDIDO** en `HP-O16` el 2026-10-05, kernel 7.2.8-1-cachyos, PipeWire 1.6.9, WirePlumber 0.5.18,
`/usr/share/libmysofa/MIT_KEMAR_normal_pinna.sofa` (SHA-256
`2768ac841213a7ae11d1ea7fd0f25a69b39216102dc5dd913ea6ba0f0dc57e28`). Ruido rosa a 0,1 de pico por
canal, 6 s; la ganancia es la sonoridad K (G = 1) de la salida L+R menos la de los N canales de entrada,
sobre la parte estable de la grabación. Datos crudos en `probes/21-ganancia-hrtf/resultado.json`.

**Qué se le hizo a PipeWire y cómo se deshizo** (CLAUDE.md): todo en procesos hijos de la sonda, que
los termina al salir.

- Un sink nulo `hrtfprobe_null` (`support.null-audio-sink`, `object.linger = false`,
  `priority.session = 1`) con `pw-cli -m create-node`: desaparece con el proceso.
- Por configuración, el **mismo** filter-chain del monitor (`monitor.binaural_args`) con nombres
  `hrtfprobe_bin` / `hrtfprobe_bin_out` y la salida dirigida al sink nulo (`node.dont-fallback`), con
  `pw-cli -m load-module`; `pw-record` (`hrtfprobe_rec`, `stream.capture.sink`) del monitor del sink
  nulo y `pw-play` (`hrtfprobe_play`) hacia el filtro.
- **Nada audible:** `pw-play` manda primero 1 s de silencio digital y el ruido solo sale después de
  que `pw-dump` muestra exactamente `hrtfprobe_play → hrtfprobe_bin`, `hrtfprobe_bin_out →
  hrtfprobe_null` y `hrtfprobe_null → hrtfprobe_rec`; cualquier otra ruta aborta sin sonar.
- **Comprobado antes y después:** sink por defecto `aurasync` antes y después (`pactl
  get-default-sink`, solo lectura; también se comprueba en cada configuración); nodos `aurasync*` /
  `hrtfprobe*` = `[(150, 'aurasync')]` antes y después (el nodo del servicio del usuario, no tocado);
  ningún `hrtfprobe*` quedó.
- **Lo que sí quedó:** WirePlumber guardó una línea por el sink nulo en
  `~/.local/state/wireplumber/stream-properties` (`Audio/Sink:node.name:hrtfprobe_null`, volumen 1,0)
  y probablemente reescribió con volumen 1,0 las entradas que ya existían para `media.name:aurasync
  monitor` y `application.name:pw-play` (el filtro de la sonda lleva el mismo `media.name` que el del
  monitor; las dos tienen hoy el mapa de 4 canales de la última configuración, y **sus valores
  anteriores no se conocen**: no se leyeron antes de correr la sonda). No cambia nada audible. **Cómo
  revertir:** con WirePlumber detenido (`systemctl --user stop wireplumber`), borrar de ese archivo las
  líneas `Audio/Sink:node.name:hrtfprobe_null`, `Audio/Sink:media.name:aurasync\smonitor`,
  `Output/Audio:media.name:aurasync\smonitor`, `Output/Audio:application.name:pw-play` e
  `Input/Audio:application.name:pw-record` (el grabador de la sonda también pudo reescribirla), y volver a
  arrancarlo; WirePlumber las vuelve a crear con los valores por defecto la próxima vez que aparezcan
  esos streams. Que esas entradas digan 1,0 indica además que la sonda midió con los volúmenes de stream
  en 1,0.

| configuración | ángulos | ganancia (dB), canales independientes | mismo ruido en todos |
|---|---|---|---|
| N = 1 | 0 | +6,10 | — |
| N = 2 | ±90 | +6,40 | +5,91 |
| N = 3 | ±60, 180 | +6,02 | +4,17 |
| N = 4 (la cuadrafonía del usuario) | ±45, ±135 | +5,75 (repetida: +5,73) | +9,09 |
| N = 5 | ±36, ±108, 180 | +5,60 | +5,41 |
| N = 6 | ±30, ±90, ±150 | +5,69 | +5,99 |
| N = 7 | ±25,7, ±77,1, ±128,6, 180 | +5,60 | +4,61 |
| N = 8 | ±22,5, ±67,5, ±112,5, ±157,5 | +5,46 | +5,49 |

Una sola entrada por ángulo: 0° +6,10, 15° +6,27, 30° +6,66, 45° +6,92, 60° +6,82, 75° +6,57, 90°
+6,38, 105° +5,52, 120° +4,77, 135° +4,21, 150° +3,66, 165° +3,59, 180° +3,78 dB; −45, −90 y −135
dieron lo mismo que +45, +90 y +135 (el HRTF es simétrico). La media de potencias de estos valores
reproduce los conjuntos independientes con error ≤ 0,2 dB (N = 4: 5,77 contra 5,75; N = 8: 5,65 contra
5,46), y eso usa el código para un conjunto de ángulos que no está en la tabla (INFERIDO: supone canales
iguales y no correlacionados).

**Lo que no resuelve:** con el **mismo** ruido en todos los canales la ganancia fue de +4,2 a +9,1 dB
según la disposición (los oídos del HRTF suman en coherencia). La constante vale para parlantes no
correlacionados; con música, que está entre los dos extremos, el binaural puede quedar hasta unos 3 dB
arriba o 2 dB abajo de la referencia, y Python no puede corregirlo porque no ve la salida del filtro
(INFERIDO). Cada configuración se midió una vez; la única repetición es la cuadrafonía del usuario
(5,75 y 5,73 dB, dos corridas independientes del filtro con el mismo ruido).

**Hallazgo de paso (MEDIDO, `pw-play` 1.6.9):** `--channel-map AUX0` con un solo canal se lee como un
nombre de disposición y `pw-play` sale con "channels and channel-map incompatible"; con `AUX0,` (coma
final) funciona. El monitor binaural con un solo parlante fallaba al abrir por esto; ya usa la coma
(`monitor.play_channel_map`).

**Volumen del monitor por el audífono (2026-10-05, INFERIDO):** el nivel de la tarjeta del monitor
mueve por defecto el volumen de la salida (`pactl set-sink-volume` sobre el `bluez_output…` del
WH-CH520, con lectura de vuelta). Que ese volumen sea de verdad el AVRCP absoluto, el mismo que mueven
los botones del audífono, solo está REPORTADO para los Go 4 (experimentos/10 §5.4) y para el WH-CH520
queda **sin medir**: pendiente de comprobar con los botones y `pactl get-sink-volume`.

## 4. Resultado

**MEDIDO:** pendiente. Sin número, nada se da por bueno.

## 5. Veredicto

Pendiente. Según el resultado confirma o corrige la línea INFERIDO del Dummy-Driver en el spec §4, y
cambia el estado de i-7c8794-757041 en `docs/roadmap.md`.
