# 10 · El servicio de control con 3 Go 4: validación con parlantes

**Preguntas:** ¿`run` suena igual que antes de mover su lazo a `session.py`? ¿El servicio
arranca, ajusta mientras suena, carga un preset, sobrevive a que falle una sesión y se
detiene en orden? ¿Los cambios en vivo se oyen sin clics? (i-7c8794-bdb678)

**Estado: protocolo y scripts listos, sin correr con parlantes.** Lo medido hasta ahora: el
costo del motor (§1), una prueba de humo sin audio (§2) y el ensayo en seco del paso 3 (§3).

**Entorno:** `PC-Ryzen5` (AMD Ryzen 5 5600G), CachyOS, kernel 7.2.8-1-cachyos, PipeWire
1.6.9, WirePlumber 0.5.17, BlueZ 5.87, Python 3.12.12, numpy 2.5.3. Micrófono fifine
(`alsa_input.usb-3142_fifine_Microphone-00.analog-stereo`), que también es la fuente por
defecto de PipeWire en este equipo. Fecha: 2026-10-01.

---

## 1. Costo del motor frente al tiempo real. MEDIDO

El primer paso que pedía la spec (§12): hasta ahora solo estaba medido el costo de la
calibración (`08-…`), no el del motor.

**Qué se ejecutó:** `Motor` con 3 parlantes (`pan` ±0,7 y 0, `ambiente` 0,15, 0,15 y 0,55),
extractor y decorrelador encendidos, 600 bloques de 4096 muestras de ruido (51,2 s de audio).
Dos corridas: en reposo, y cambiando `pan` y pidiendo un corte cada 10 bloques.

| Caso | CPU por bloque de 85 ms | Veces el tiempo real |
|---|---|---|
| en reposo | 1,35 ms | 63× |
| con rampas y cortes | 1,34 ms | 64× |

**Veredicto:** el motor no es un problema en este equipo: deja más del 98 % del tiempo de
cada bloque libre. Tampoco se nota el costo de las rampas, porque se paga solo en los
bloques en los que algo se mueve. El número vuelve a importar en la Raspberry Pi Zero 2 W de
la Fase 3, que es mucho más lenta: hay que medirlo allá.

## 2. Prueba de humo del servicio, sin audio. MEDIDO

Con `XDG_CONFIG_HOME` temporal y `--bind 127.0.0.1 --port 18731`, sin parlantes conectados:

- `service.json` se creó con permisos `600` y un token de 43 caracteres;
- `GET /v1/state` sin token respondió **401**; con token, el estado con los 3 Go 4 de la
  instalación y el volumen en -20 dB;
- `PATCH /v1/global {"volume_db": -25}` respondió `{"sequence": 1}`;
- `POST /v1/session/start` respondió `unavailable` y nombró a los tres parlantes no
  conectados, sin crear nada;
- `POST /v1/shutdown` respondió y el programa terminó con el mensaje de cierre.

Destapó un error, ya corregido: la ruta por defecto de la instalación en `service.json` no
respetaba `XDG_CONFIG_HOME`.

## 3. Lo preparado antes de los parlantes (2026-10-01)

**Los scripts** están en [`probes/10-servicio-de-control/`](../../../probes/10-servicio-de-control/README.md),
uno por paso. Todo lo que se le pide al servicio queda anotado en
`datos/10/sesion-<fecha>.jsonl` con la hora, y los pasos 3 y 4 graban el micrófono.

**Ensayo en seco del paso 3. MEDIDO.** Se pasó la misma secuencia de 17 cambios por el
servicio real, con la señal de prueba procesada por el motor y la salida guardada en memoria,
sin parlantes:
- los **cortes** ocurren exactamente en los 6 cambios en los que se le dirá al oyente que
  habrá uno: `ambience` 0,55→0,8 y de vuelta, `decorrelate` en los dos sentidos, y
  `rear_delay_ms` 13→20 y de vuelta. En los otros 11 no hay corte;
- **ningún clic** en la salida digital de los tres parlantes;
- **se vio fallar:** con el suavizado de `pan` y `ambiente` vuelto instantáneo, el ensayo
  encuentra un clic de +15 dB en Red al mover su `pan`.

**El detector de clics se vio fallar antes de usarlo.** En la primera versión promediaba la
energía en ventanas de 5 ms, y no encontró ni un salto de 6 dB ni uno de 2 ms en una señal
sintética. Ahora usa el pico de la ventana. Con un tono de 0,03 y ruido 40 dB por debajo
encuentra los dos saltos y no confunde el corte de 80 + 80 ms con un clic. **Su sensibilidad
con micrófono depende del ruido de la pieza**: con ruido 30 dB por debajo de la señal ya no
ve un salto de 6 dB. Por eso, en cada grabación, mide su propia sensibilidad insertando saltos
sintéticos en tramos sin cambios, y si no encuentra ninguno da el resultado por NO
CONCLUYENTE.

**Dos correcciones que salieron de preparar el paso 5:**
- **un parlante apagado no siempre mata a su `pw-play`**: el stream puede quedar huérfano y
  WirePlumber puede moverlo a otra salida, incluido el sink virtual. Ahora la sesión revisa
  el ruteo **cada 2 s** mientras suena, y no solo al abrir. Si el destino de un stream ya no
  existe, cierra ese stream en vez de intentar devolverlo. Cuando no queda ninguno, la
  sesión termina en `error`;
- `state` marca cada parlante con `playing` y avisa en `warnings` de los perdidos y de los
  streams devueltos a su parlante.

**Lo que se decide de oído en el paso 3:** mover `ambience` cambia el retardo de Haas del
parlante (`ambiente × retardo_traseros_ms`), y un cambio de más de 1 ms pasa por el corte.
O sea que casi cualquier movimiento útil de `ambience` corta. Si molesta, las alternativas
son separar el retardo de Haas de `ambience` o dejar que ese retardo se mueva más rápido.

### 3.1 El panel sobre el motor real, y el error de niveles que destapó (2026-10-01)

Se construyó el panel completo sobre el servicio (d-7c8794-09d10f, spec §15), con un modo
`--simular` que corre el motor, el lazo y la calibración reales sobre una sala simulada:
cada parlante llega al micrófono con 3, 7,5 y 12 ms de retardo y ganancias de 1, 0,8 y 0,6.

**La calibración simulada dio bien los retardos y mal las ganancias. MEDIDO (simulación).**
La corrección esperada era -4,4, -2,5 y 0 dB, y `calibrar` devolvió -6,0, 0,0 y -7,9. No era
la simulación: con una mezcla sintética limpia daba lo mismo. `medicion.niveles` tenía dos
errores:
1. **dividía por la energía de la referencia.** La autocorrelación de un ruido rosa cambia
   mucho de una realización a otra, porque casi toda su energía está en los graves. En 12
   realizaciones (6 semillas × 5 y 10 s), el peor error era de **11,4 dB**. Ahora se quitan
   los graves bajo 300 Hz y se normaliza por la autocorrelación de la propia referencia en
   la misma ventana: el peor error baja a **0,22 dB**;
2. **cortaba la correlación circular en el índice 0.** El parlante que llega antes que la
   mediana tiene su pico en un retraso negativo, y quedaba afuera: con llegadas de 3, 7,5 y
   12 ms, `calibrar` le pedía -14,7 dB al del medio. Ahora los índices van módulo `n` y el
   pico se busca a ±15 ms de la pista.

La ventana de nivel bajó de 80 a 20 ms. Con 80 ms y la normalización nueva, un parlante mudo
medía 0,14 y 0,20 del que suena y no se detectaba; con 20 ms mide 0,06 y 0,0, y la exactitud
sigue en 0,24 dB. **Que 20 ms alcance en la pieza real es INFERIDO**: se valida con la prueba
de cierre del paso 9.

**Por qué no se vio antes:** el estimador se validó con "0,3 dB entre dos corridas de la
misma sala". Eso es repetibilidad: las dos corridas usaban la misma semilla, así que daban
el mismo error. Es la trampa que CLAUDE.md describe para la estabilidad. Los tests nuevos
fallaban con el código anterior (12 casos de semillas y 4 de llegadas) y pasan ahora. Con la
corrección, la calibración simulada da -4,7, -2,8 y 0 dB, y los retardos exactos (9,0, 4,5 y
0,0 ms).

**Tests:** 325 de unidad (`scripts/check.sh`) y 38 de navegador (19 casos en Chromium y 19
en Firefox 150, `hatch run browser:test`), dos corridas seguidas sin fallas.

## 4. Protocolo con parlantes (pendiente)

**Volumen:** amplitud 0,1 por defecto y nunca más de 0,2 (el servicio arranca en -20 dB, que
es 0,1). Anotar el firmware de cada Go 4 antes de empezar.

1. **`run` sigue sonando como antes** (`session.py` movió su lazo sin reescribirlo):
   `aurasync run --volumen-db -20`, poner música en la salida "aurasync (envolvente)". Los
   tres suenan, no hay realimentación, y `pactl list short sink-inputs` muestra cada
   `pw-play` en su parlante. Ctrl-C: la salida desaparece.
2. **El servicio arranca y suena:** `aurasync service --bind 127.0.0.1`, `POST
   /v1/session/start`, música en "aurasync (envolvente)". `state` dice `playing`.
3. **Ajustes en vivo, de oído:** mover `pan` de un parlante de -0,7 a 0,7; subir `ambience`
   del trasero de 0,55 a 0,8, que pasa por el corte; `volume_db` de -20 a -30 y de vuelta;
   `extract_ambience` a `false` y a `true`; `decorrelate` a `false` y a `true`. Anotar si
   alguno se oye como clic o salto, y cómo suena el corte (80 + 80 ms).
4. **Presets:** guardar `a`, cambiar `ambience` y `rear_delay_ms`, guardar `b`, y alternar
   `POST /v1/presets/a/load` y `b/load`. ¿Se oye la diferencia? ¿El corte molesta?
5. **Una sesión que falla:** con la sesión en `playing`, apagar los tres parlantes. `state`
   pasa a `error` con el motivo, el programa sigue respondiendo y la salida virtual
   desaparece (`pactl list short sinks`). Prenderlos, reconectarlos, y `start` otra vez.
6. **Dos instancias:** con el servicio sonando, `aurasync run` tiene que negarse (`conflict`:
   ya hay un nodo `aurasync`).
7. **Cierre:** `POST /v1/shutdown` y, en otra prueba, Ctrl-C. En los dos casos la salida
   virtual desaparece y el proceso termina.

8. **El panel desde el teléfono:** el servicio con `--bind 0.0.0.0`, escanear el QR, y
   repetir dos o tres ajustes del paso 3 caminando por la pieza. ¿Responde? ¿Los niveles y
   los servicios dicen lo mismo que el oído?
9. **Calibración dentro de la sesión, y prueba de cierre:** con el lazo apagado, "Calibrar"
   a amplitud 0,1, "Aplicar", y "Calibrar" otra vez. **La segunda tiene que pedir
   correcciones cercanas a 0 ms y 0 dB**; si no, el estimador de nivel (20 ms de ventana) o
   el de retardo no alcanzan en esta pieza. Guardar las dos con "Guardar medición"
   (`measurements` de `service.json` apuntando a `datos/10/`).
10. **A/B ciego:** dos presets que se diferencien en `ambience` del trasero, 10 respuestas.
    Con 8 o más aciertos, la diferencia se oye (p ≈ 0,055 al azar).

**Lo que se anota:** qué pasó en cada paso, lo que se oyó, y lo que el servicio imprimió.

## 5. ¿Se puede calibrar solo con el protocolo? Y lo que el micrófono encontró (2026-10-01)

**Pregunta del usuario:** ¿se pueden aplicar calibraciones solo a nivel de protocolo
Bluetooth, con lo que informan las capas (latencia, señal, códec), y qué cambian los códecs?
Todo se comparó contra el micrófono.

**Entorno:** `PC-Ryzen5`, 3× Go 4 conectados a la vez (SBC), Spotify por `aurasync`, el
servicio corriendo y todas las pruebas por órdenes a su API (`probes/10-…/campana.py`,
`monitores.py`, `protocolo.py`). Firmware de los Go 4: **sin leer** (`bluetoothctl` no muestra
Modalias). Datos crudos en `datos/10/` (`campana-*.jsonl`, `calibracion-*.json` y `.npz`,
`monitores-*.wav`, `cambios-de-sistema.txt`).

### 5.1 Lo que informa el protocolo. VERIFICADO

| Fuente | Qué dice | ¿Sirve para alinear? |
|---|---|---|
| BlueZ, transporte A2DP: `Delay` | **no existe**: los Go 4 no hacen Delay Reporting | no |
| BlueZ, endpoints remotos | **solo SBC**, bitpool 2–40, todos los modos | no hay códecs que elegir |
| PipeWire, `Latency` del nodo | **145,19 ms, idéntico en los tres y constante** todo el día | no: el micrófono ve 5 a 40 ms de diferencia entre ellos |
| PipeWire, `channelVolumes` / AVRCP `Volume` | 0,512 / 102 de 127 | para la ganancia, sí (5.4) |
| `pw-top` ERR de los sinks | 0 siempre | no |
| `pw-top` ERR de cada `pw-play` | 105 a 106 por stream | delata el mecanismo de 5.3 |
| RSSI, potencia | `btmgmt conn-info`: *Permission Denied* sin root | no se pudo leer |
| WirePlumber en debug | "increase bitpool" ~1 vez/s por sink: el enlace se congestiona y PipeWire baja la calidad SBC | indica congestión, no la latencia |

**Respuesta: el retardo entre parlantes no se puede calibrar solo con el protocolo.** Nada de
lo que exponen BlueZ o PipeWire refleja la diferencia real, que nace en el buffer de cada
parlante y en el camino del PC. Hace falta el micrófono para la parte fija. Lo que sí sirve
del protocolo: el **cuantum** (5.3) y el **volumen AVRCP** (5.4).

### 5.2 La calibración necesitaba cuatro correcciones antes de poder comparar. MEDIDO

1. **"Aplicar" no aplicaba con la música en pausa:** un corte pendiente solo avanza si el
   motor procesa, y sin entrada no procesaba. Ahora procesa silencio.
2. **La calibración medía el desfase crudo**, sin pasar por las correcciones: la prueba de
   cierre no podía dar cero. Ahora mide el residuo. Con eso, **cierre: 0,35 ms como máximo**
   después de aplicar.
3. **El audio sonaba ~1,03 s después de escrito:** la tubería de 64 KB hacia cada `pw-play`
   acumulaba 0,68 s. Con dos bloques de tubería, **~0,50 s** (12 mediciones: 476 a 512 ms).
4. **La alineación gruesa exigía 20 ms de consenso** entre parlantes, y la separación real
   llegaba a 40–90 ms (5.3): fallaba de a ratos con "could not align". Ahora 100 ms; las dos
   grabaciones que habían fallado se miden.

Más un hallazgo de ruteo: **WirePlumber movía el stream de Red al sink `aurasync`** en cada
arranque, porque Red era la salida por defecto y `aurasync` es la que tiene guardada como
preferida. Con `node.dont-move` en los streams, 3 de 3 arranques limpios (antes, 3 de 3
rechazados por la salvaguarda de ruteo).

### 5.3 Los saltos de exactamente un cuantum, y su causa. MEDIDO

Con **un `pw-play` por parlante**, en 23 calibraciones las diferencias entre parlantes eran
una constante por sesión más saltos de **exactamente 2048 muestras (42,67 ms)** en **un
solo** parlante: Black−Blue valía −13,66 o +29,00 (1,00 cuantum), y en otra sesión −16,6 o
+26,0. En la sesión con tubería corta, fuera de los saltos, **0,02 ms de variación en 7
minutos y 4 reinicios de streams**; el ajuste de buffer de `pw-play` (100, 200, 400 ms) no
cambió ni el desfase ni la latencia.

**Causa:** cada sink Bluetooth es su propio driver en PipeWire, con su reloj. La diferencia
de ritmo se acumulaba en la tubería de cada `pw-play` hasta que se quedaba sin datos un
cuantum (los ~105 xruns de cada uno). La grabación simultánea de los tres monitores (lo que
recibe cada sink) mostró los streams alineados a 0,00 ms mientras el micrófono veía a Black
correrse un cuantum cada ~25 s; el salto está después del monitor. (Esa captura cruza drivers
y tiene ±1 cuantum de ambigüedad propia: no decide el punto exacto, solo que no es el motor.)

**Corrección: salida combinada** (`ReproductorCombinado`, d-7c8794-a41ec9). Un solo stream
de N canales a un sink de combine-stream, con un reloj y remuestreo adaptativo por salida.
**Con salida combinada, 0 saltos en 8 calibraciones** (antes, 4 de 13 y 5 de 6). Lo que queda
es una **deriva lineal y suave** de Black y Blue respecto de Red, de **~1,35 ms/min (~22 ppm)**,
con Black−Blue fijo en 0,92–0,95 ms: lo que el lazo de recalibración y la rampa de 0,5 ms/s
corrigen sin que se oiga. Es INFERIDO que Red sea el driver del sink combinado y los otros
dos lo sigan remuestreados.

### 5.4 Ganancia por volumen Bluetooth (AVRCP). MEDIDO

Bajar el volumen del sink de Red de 80 % a 64 % (−6 dB según la curva cúbica de PipeWire;
AVRCP 102 → 81) **no movió su retardo (0,02 ms)**, y el micrófono vio **4,1 dB** de bajada,
no 6. La ganancia se puede corregir por el protocolo —mejor que atenuar la señal digital,
que pierde resolución—, pero hace falta medir la curva de volumen del Go 4 antes.

### 5.5 Códecs. VERIFICADO y MEDIDO

- **Los Go 4 solo hablan SBC** (un único endpoint remoto, bitpool máximo 40): no hay códec que
  elegir. El AAC del Charge 6 ya se descartó para mezclar (experimentos/05).
- **SBC mono** (`bluez5.default.channels = 1`, temporal y revertido; registro en
  `cambios-de-sistema.txt`): el enlace quedó en mono a 48 kHz, bitpool 29 (~198 kbps contra
  ~279 en estéreo). **No redujo la congestión**: 122 bajadas de bitpool en 50 s contra 62 en
  estéreo, en condiciones no idénticas. Se revirtió.
- El Wi-Fi del AX210 estaba **apagado** durante todas las mediciones: la congestión es de
  Bluetooth puro, con tres enlaces A2DP en un controlador.

### 5.6 Repetibilidad de la calibración. MEDIDO

Dos calibraciones seguidas sin cambiar nada: retardo dentro de 0,3 ms; ganancia dentro de
0,5 a 1,0 dB. El retraso inyectado a propósito (+4 ms en Black) se midió con 0,9 ms de error y
la ganancia inyectada (−6 dB en Red) con 0,9 dB de error: **el sistema detecta lo que se le
hace a propósito**, con la precisión que da la repetibilidad. El lazo contra la música con
+5 ms inyectados queda pendiente: Spotify estaba en pausa, y la señal de prueba (tonos puros,
periódicos) no sirve como contenido para el lazo.

## 6. El audio "degradado": tres causas, medidas (2026-10-01, tarde)

El usuario oyó el audio "muy degradado" y pidió poder medir la calidad desde el panel.

1. **Cortes por falta de datos, ~10 por segundo. MEDIDO** (`pw-top`, ERR del `pw-play` de la
   salida: 1207 → 1324 en ~12 s). Causa: la tubería de dos bloques (8192 muestras) era más
   chica que lo que `pw-play` pide por ciclo con `--latency 200ms` (9600). Lo introdujo la
   corrección de latencia de §5.2. Ahora la tubería es al menos el ciclo de `pw-play` más un
   bloque, y `pw-play` pide 50 ms: el contador quedó quieto en 20 s. **El panel muestra
   ahora los cortes por minuto** (Salud → "Cortes en la salida").
2. **El decorrelador coloreaba cada parlante. MEDIDO.** El filtro "todo-paso" de fase al azar
   por bin era plano solo en sus 65 bins de diseño; entre ellos, **−46 a +5 dB (±9,5 dB por
   tercio de octava)**: un ecualizador al azar en cada parlante. El test que lo validaba miraba
   solo los bins de diseño. Ahora el retardo de grupo es aleatorio y suave (256 coeficientes,
   media 2,5 ms igual para todos, ±1,5 ms): **plano a ±0,2 dB**, y la correlación entre
   salidas con ruido rosa baja de 0,67 a menos de 0,6 (más envolvimiento, no menos).
3. **El extractor de ambiente no aporta artefactos de bloque. MEDIDO:** procesar la música por
   bloques de 4096 o 1024 da lo mismo que procesarla entera (0,00 %).

**¿Se puede aprovechar más el espectro de cada parlante?** La salida del motor ya no recorta
(espectro por octava de cada parlante contra la entrada: dentro de −6 a +4 dB, sin caída de
graves ni agudos, medido en el monitor del sink combinado). La **respuesta medida en el
micrófono** de cada Go 4, que ahora entrega cada calibración y muestra el panel, es útil de
**~100 Hz a 8–10 kHz** (−10 dB; ±2 dB, orientativa bajo 100 Hz). **No es el códec:** Black
solo, con el enlace libre, mide igual que con los tres transmitiendo (−21 y −19 dB en
12,7 kHz). Es el parlante en la posición del micrófono (INFERIDO: no hay referencia del
micrófono). Lo que queda: una ecualización por parlante desde esta medición, y mandar lo que
está bajo 100 Hz a un parlante más grande.

**Y "Aplicar" ya no usa mediciones dudosas:** un parlante dudoso o que no sonó queda como
estaba, y la respuesta lo dice (`skipped`). La primera calibración después del decorrelador
nuevo aplicó a Blue con 16,8 ms de desacuerdo entre ventanas.

**Al cierre de esta ronda,** dos calibraciones seguidas dieron un parlante que "no suena"
distinto cada vez (Blue, después Black) con los tres conectados, ruteados y el sink activo:
algo cambió del lado de los parlantes (batería, botones de volumen o posición). Queda sin
explicar.

## 7. La música "apagada y aplanada": dónde se perdía el volumen y los bajos (2026-10-01, noche)

El oído del usuario: *"muy apagada y aplanada, sin bajos y muy uniforme"*. MEDIDO en
`PC-Ryzen5`, sobre el servicio vivo, con `/v1/state` y `pactl list sinks`:

| Etapa | Valor | Efecto |
|---|---|---|
| Ecualización por parlante (la primera versión) | entre −6 y −18 dB en toda la curva; −18 dB de 125 a 315 Hz en los tres | quitaba la joroba de graves del Go 4 y todo sonaba más bajo |
| Sink combinado `aurasync_salida` | 46 % (−20,23 dB), restaurado por WirePlumber | 20 dB perdidos además del volumen del panel |
| Volumen del panel | −15 dB | |
| Parlantes (AVRCP) | 80 % (−5,81 dB) | lo pone la persona |
| Formato de salida | s16 | con −40 dB en total quedaban ~10 bits para la música |

**Por qué la ecualización hacía eso:** apuntaba a una respuesta plana y se normalizaba para
que su punto más alto fuera 0 dB. El Go 4 tiene +6 a +9 dB entre 100 y 160 Hz respecto del
medio, así que "aplanarlo" era recortarle los graves. Y la curva se acumulaba entre
calibraciones: cada recorte nuevo se sumaba al anterior, hasta −18 dB.

**Lo que se cambió** (d-7c8794-e8f7e3):
- la ecualización solo realza;
- el sink combinado se fija al 100 % y se comprueba (verificado después del cambio: 100 %,
  0,00 dB en los tres canales);
- la salida va en f32;
- un limitador de pico al final de cada cadena.

Al redesplegar se bajó el volumen del panel a −38 dB **antes** de iniciar, para que los
~20 dB que se recuperaban no llegaran de golpe.

**Pendiente:** la comparación medida contra la música directa a un Go 4 (roadmap
i-7c8794-10ccb4).

## 8. "La salida directa suena mejor que el envolvente": lo que el motor le hacía al sonido (2026-10-01, noche)

El usuario mandó la música directo al sink combinado (`aurasync (salida a los parlantes)`) y
le sonó mejor que pasando por el motor (`aurasync (envolvente)`). Eso no debería pasar.
Se midió cada etapa por separado, fuera de línea, con el motor real y la instalación viva:
ruido rosa estéreo correlacionado, contra la mezcla ideal de cada parlante
(`probes/11-calidad-de-la-cadena/cadena.py`, `PC-Ryzen5`). MEDIDO:

| Etapa | 1 kHz | 6,3 kHz | 12,7 kHz | Coherencia |
|---|---|---|---|---|
| Todo, como corría (Red, retardo 13,56 ms) | −0,9 dB | −1,7 dB | **−4,4 dB** | 1,00 |
| Todo, como corría (Black, 0 ms + Haas) | −0,9 | −1,7 | **−4,2** | 1,00 |
| Todo, como corría (Blue, 11,59 ms) | −0,9 | −1,1 | −1,7 | 1,00 |
| Sin extractor ni decorrelador (Red) | 0,0 | −0,8 | −3,5 | 1,00 |
| **Con la lectura de banda limitada (los tres)** | −0,9 | −0,9 | **−0,9** | 1,00 |

Lo que dice:
- **La interpolación lineal del retardo fraccionario era un filtro de agudos**, distinto en
  cada parlante según la fracción de muestra de su retardo: hasta 3,5 dB a 12,7 kHz. Se
  cambió por un sinc con ventana Kaiser de 32 coeficientes (`dsp/interpolation.py`), que es
  plano a ±0,002 dB hasta 20 kHz y exacto en retardos enteros. El precio son 16 muestras
  (0,33 ms) de latencia fija, iguales en todos los parlantes.
- **El −0,9 dB parejo es la mezcla de ambiente en 0,1:** el 90 % de la señal es la directa.
  No es una pérdida de calidad.
- El decorrelador y el extractor tienen coherencia 1,00: son filtros lineales y no
  ensucian. La diferencia entre "todo" y "sin los dos" es solo ese −0,9 dB.

**Otras dos cosas encontradas en el camino:**
- **La entrada se capturaba en 16 bits** (el sink `aurasync` se veía como `S16LE` en
  `pw-dump`). Ahora es f32, comprobado con `pactl list sinks short`: `float32le 2ch 48000Hz`.
- **La lectura de la entrada tiraba los bytes de un marco cortado.** Si `read1` devolvía
  una cantidad que no era múltiplo del marco, el resto se perdía y la lectura siguiente
  quedaba corrida: L y R cambiados, o un salto. Ahora los bytes sobrantes esperan a la
  lectura siguiente (`test_sonido.py`, un caso cortado a mitad de una muestra).

**Lo que queda de diferencia** (INFERIDO, sin medir con micrófono): la salida directa no pasa
por el volumen del panel (−18 dB en ese momento), así que sonaba más fuerte, y más fuerte
suele oírse "mejor". Para comparar parejo, el volumen del panel va en 0 dB. Queda pendiente
compararlo con micrófono (roadmap i-7c8794-10ccb4, "la referencia medida").

## 9. Los cortes: qué los puede causar, cómo se ven y qué se cambió (2026-10-01, noche)

El usuario: los cortes "afectan mucho a la música". Esa noche los parlantes estaban apagados
(`br-connection-page-timeout`), así que esta sección investiga sin sonido y deja todo listo
para atribuir cada corte cuando vuelvan a sonar. Entorno: `PC-Ryzen5`, kernel
7.2.8-2-cachyos, PipeWire 1.6.9, BlueZ 5.87, red por cable (el WiFi del AX210 no está
conectado, así que no hay convivencia en 2,4 GHz).

**1. El observador del panel abría ~3 clientes de Bluetooth por segundo. MEDIDO** en el
journal: 14 498 líneas `bluetoothd: Path / reserved for Adv Monitor app` en 80 minutos del
arranque anterior (18:37 a 19:58), en ráfagas de 10 cada 3 s, y 12 193 en el arranque
actual. Las producía `system.Observer`: `bluetoothctl devices` y un `bluetoothctl info` por
dispositivo, cada 3 s, con sesión o sin ella. Cada `bluetoothctl` registra un monitor de
anuncios LE en `bluetoothd`. **INFERIDO, sin medir:** un monitor puede hacer que el
controlador escanee, y escanear le quita tiempo de radio a los 3 enlaces A2DP, que es una
causa conocida de cortes. **Se cambió igual**, porque es gratis: el estado se lee ahora con
una sola llamada D-Bus de solo lectura (`GetManagedObjects`, ~3 ms, no registra nada), y
`bluetoothctl` queda solo para lo que pide la persona (conectar, emparejar, buscar, olvidar). **Verificado después del cambio (MEDIDO):** con el servicio viejo había 20 registros
cada 4 s hasta las 00:16:37 del 2026-10-02, cuando se detuvo; con el nuevo, 0 en los
primeros 47 s del nuevo (`journalctl --since 00:16:40 | grep -c "Adv Monitor"`).

**2. El hilo del motor tenía poco margen. MEDIDO en simulación** (`probes/12-cortes/jitter.py`,
servicio real con la sesión simulada, órdenes cada 80 ms y el lazo midiendo cada 5 s): entre
dos bloques pasan como mucho 114 a 125 ms frente a los 85 ms de un bloque. La tubería tenía
de margen el ciclo de `pw-play` más **un** bloque (~135 ms en total).

**Una hipótesis que la medición descartó:** que la medición del lazo le quitara el GIL al
motor. Se la pasó a otro proceso y **fue peor** (MEDIDO,
`probes/12-cortes/lanzar.py`, tres lanzamientos de cada uno): el hilo que lanza se frenaba hasta 15 ms al serializar ~2 MB de referencias, y el
primer lanzamiento tardaba 23 ms. Con el hilo, la pausa máxima es de 1 a 4 ms, porque la FFT
de numpy suelta el GIL. Además, el proceso con `forkserver` re-importa el script principal:
en el probe sin guarda `__main__` el lanzamiento falló y **tiró abajo la sesión**. Se volvió
al hilo. Quedó una protección: si una medición no arranca, se anota y se intenta en la vuelta
siguiente, sin tocar el audio (`test_a_loop_measurement_that_cannot_start…`, que se vio
fallar sin la protección).

Lo que sí se cambió:
- el chequeo de ruteo (`pw-dump`, decenas de ms cada 2 s) corre en un hilo aparte, y el
  motor solo aplica lo que encontró;
- la tubería guarda **dos** bloques de margen (+85 ms de latencia, que la calibración mide).

**3. Cada corte queda registrado con su causa probable** (`host/src/aurasync/cuts.py`):
- la tubería vacía o casi vacía al volver a escribir;
- el motor que llega tarde;
- los xruns de PipeWire, ahora **por parlante** (el stream del sink combinado hacia cada uno
  y el nodo Bluetooth), y no solo el de `pw-play`;
- la entrada que deja de llegar por menos de un segundo;
- los streams perdidos o desviados;
- los cortes intencionales, para no confundirlos.

Cada evento guarda el contexto: si el lazo medía, si Bluetooth buscaba dispositivos, qué
orden acababa de ejecutarse y cuánto tardó. El panel lo muestra en Diagnóstico → **Cortes**:
una línea de tiempo de 10 minutos, una tabla y una lectura de la causa más probable. El
chip de la barra superior cuenta estos cortes y lleva ahí.

**4. Un error encontrado en el camino:** el `<body>` del panel lleva `data-layout` con el
nombre de la organización (`pestanas`), y el selector de los botones de distribución de la
sala (`[data-layout]`) lo atrapaba: cada clic en la página mandaba `layout: "pestanas"`. El
servicio lo rechazaba (decenas de líneas `rejected: out_of_range` en el log), así que no
cambió nada, pero ensuciaba el log. Ahora el atributo de la sala es `data-room-layout`.

**5. Y otro de privacidad:** `servicio.sh` manda la salida del servicio a un log dentro de
`docs/research/experimentos/datos/10/`, y el servicio imprimía ahí el link con el token y su
QR. El archivo nunca entró en git. Se censuró (53 copias del token y 230 líneas de QR), y el
servicio ya no imprime el token cuando su salida no es una terminal.

**Pendiente, con parlantes:**
- escuchar y mirar la tarjeta Cortes durante 20 minutos de música, con el lazo encendido;
- contar los cortes y su causa antes y después de estos cambios. Hay que comparar con el
  mismo material y a la misma distancia, porque el enlace Bluetooth también corta por
  distancia, obstáculos y batería;
- confirmar o descartar con un experimento la hipótesis del escaneo LE: buscar dispositivos
  a propósito mientras suena y contar los cortes.

## Veredicto

Pendiente de los pasos 1 a 7.
