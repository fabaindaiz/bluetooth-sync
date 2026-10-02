# 11 · Procesamiento del sonido, cómo medir su calidad, canales y fuentes, y el panel

Investigación del 2026-10-02, hecha desde el **Mac** (sin parlantes), en cuatro frentes en
paralelo. Responde: **qué procesamiento mejoraría este producto, cómo se mide si mejoró,
cuánta calidad trae la fuente y cuánta se pierde hasta el parlante, y cómo se muestra y se
controla todo eso.** Lo que se decidió con esto está en la spec
[superpowers/specs/2026-10-02-microcuts-chain-and-quality-design.md](../superpowers/specs/2026-10-02-microcuts-chain-and-quality-design.md).

**Marcas:** VERIFICADO (fuente primaria leída: norma, paper, código fuente), REPORTADO
(fuente secundaria o solo el resumen), INFERIDO (deducción), MEDIDO (medido aquí, con el
entorno anotado), SIMULADO (medido sobre una simulación, sin parlantes).

**Entorno de lo medido en esta investigación:** Mac (macOS 27.0, arm64), numpy 2.5.3, el
entorno `hatch` de `host/`, libsbc `b3deb8a`, ffmpeg 9.0.2, Chromium de Playwright con el
servicio `aurasync service --simular`. Las cifras de CPU sirven para comparar entre sí y hay
que repetirlas en `PC-Ryzen5`.

## Resumen: lo que cambia decisiones

1. **Cada `reduce bitpool` del sink Bluetooth de PipeWire es un paquete descartado, o sea un
   corte de ~24–40 ms.** VERIFICADO en `spa/plugins/bluez5/media-sink.c` (tag 1.6.9): cuando
   el socket está lleno (`EAGAIN`) el código descarta el paquete —*"should just skip this
   packet. There will be a sound glitch in any case"*— y baja el bitpool en 2, a lo más una
   vez cada 0,5 s. `increase bitpool` sale **cada segundo cuando el enlace está sano**, aunque
   el bitpool ya esté en el máximo. La lectura de [experimentos/10](experimentos/10-servicio-de-control-con-3-go-4.md)
   §5.1 era la opuesta, y sus 62 "bajadas" en 50 s son **≥1,2 cortes por segundo**. Es el
   sospechoso principal de los microcortes, y la tarjeta Cortes no lo veía.
2. **No hay propiedad para fijar el bitpool de SBC en PipeWire 1.6.9** (VERIFICADO). Lo que
   hay hoy —el canal del parlante duplicado en L y R, joint stereo, bitpool 40— **es lo mejor
   posible para un Go 4**: SBC mono queda en bitpool 29 y es 6,7 dB peor; SBC-XQ cuesta 1,8×
   de aire por +0,9 dB (SIMULADO con libsbc, §3.2).
3. **El codificador SBC recibe S16 sin dither** (VERIFICADO). Bajar 30–40 dB en digital tira
   resolución: el volumen de la persona va por **AVRCP** y la cadena trabaja cerca de 0 dBFS.
4. **Graves:** un pasa-altos en el Go 4 le quita la excursión que dispara su protección
   (REPORTADO: el DSP del Go 4 corta graves sobre ~50 % de volumen). Con un parlante más
   grande, un crossover Linkwitz-Riley de 4.º orden en fase manda lo bajo allí sin latencia.
   Sin él, el **bajo psicoacústico** (NLD, Larsen-Aarts) da la altura de la fundamental con
   armónicos, como perilla y no como defecto (algunos oyentes prefieren no tenerlo).
5. **Medir:** la cadena digital es el lugar confiable (referencia exacta, mismo reloj, sin
   sala). Con el micrófono sin calibrar solo valen las comparaciones A contra B. Los modelos
   perceptuales (PEAQ, ViSQOL) sirven para pares digitales, no para una grabación de la
   pieza. La métrica del síntoma "aplanada" es el **PSR** de la salida contra el de la entrada.
6. **El panel** fuerza ~7 layouts por cuadro en los medidores, sigue con el stream con la
   pestaña oculta, y tiene estados que solo distingue el color. **El A/B no iguala la
   sonoridad**, así que puede estar midiendo volumen y no preferencia.

---

## 1. Cómo medir la calidad del sonido

### 1.1 Las métricas y qué dice cada una

**Respuesta y función de transferencia con música (doble FFT).** Se divide el espectro de lo
medido por el de la referencia; mientras el estímulo cubra la banda, da igual qué señal sea
(REPORTADO, Rational Acoustics). La **coherencia** γ² = |Gxy|²/(Gxx·Gyy) dice cuánto de lo
medido en esa frecuencia lo causó la referencia; Smaart oculta los tramos con coherencia baja
(*coherence blanking*) y recomienda ≥64 promedios; duplicar los promedios suma 3 dB de SNR
(VERIFICADO, *Fundamentals of FFT-Based Audio Measurements in SmaartLive*). Lo que baja la
coherencia: ruido, reverberación más larga que la ventana, no linealidad y **el retardo sin
compensar** — que con 150–500 ms de latencia la derrumba; `dsp/response.py` ya lo compensa
(INFERIDO).

**Trampa propia del proyecto (INFERIDO):** con varios parlantes sonando contenido
correlacionado (ρ ≈ 0,6 después del decorrelador, experimentos/10 §6), la función de
transferencia de uno solo está mal planteada: la referencia de un parlante "explica" parte de
lo que sonó por otro. Es el mismo mecanismo que [experimentos/08](experimentos/08-lazo-de-recalibracion-en-simulacion.md)
midió en el retardo. Salidas: un parlante a la vez, ruidos independientes (lo que hace la
calibración) o la sonda enmascarada.

**Distorsión.** Con música no se mide THD; se ve como coherencia < 1 con SNR alto (INFERIDO).
El barrido exponencial (Farina, AES 108, 2000) separa la respuesta lineal de cada armónico, y
el barrido sincronizado (Novak, Lotton y Simon, JAES 63(10), 2015) los deja en fase (REPORTADO).
En un parlante chico importa la IMD tipo SMPTE: el bajo modulando la voz (INFERIDO).

**Sonoridad (ITU-R BS.1770-5, 11/2023; EBU R 128 V5).** VERIFICADO en el texto de la norma:
ponderación K en dos etapas (shelving y pasa-altos, coeficientes para 48 kHz en las tablas 1 y
2), media cuadrática por canal, compuerta sobre bloques de 400 ms con 75 % de solape
(absoluta −70 LKFS, relativa −10 dB), constante −0,691. Pesos: L, R, C = 1,0; Ls, Rs = 1,41.
True peak (Anexo 2): sobremuestreo ×4 a 48 kHz. R 128: objetivo −23 LUFS, true peak máximo
−1 dBTP, tolerancia ±0,2 LU. LRA (Tech 3342): percentiles 10–95 de la sonoridad de corto plazo
con compuerta relativa −20 LU. **Para aurasync** conviene medir cada alimentación por separado
con G = 1, porque con un oyente que se mueve el azimut no está definido (INFERIDO).

**PSR y PLR.** PSR = true peak − sonoridad de corto plazo (3 s); PLR = true peak − integrada
(REPORTADO, MeterPlugs). **Si el PSR de la salida queda más de ~1 dB bajo el de la entrada, la
cadena está aplastando la dinámica**: es la métrica de "aplanada" (INFERIDO).

**Cortes.** Además del log de PipeWire (resumen, punto 1): en la salida del motor, la
continuidad en los bordes de bloque; con micrófono, la caída de la correlación deslizante
contra la referencia alineada (INFERIDO). **Los modelos perceptuales no detectan cortes:**
ViSQOL "se queda plano" ante glitches; PEAQ y PEMO-Q responden de forma inestable
(VERIFICADO, He, Williams y Fazenda, DAFx25).

**Espacio.** IACC (ISO 3382-1) necesita una cabeza artificial o micrófonos binaurales: **con
un solo micrófono no se mide** (REPORTADO; INFERIDO para el caso). Lo que el motor controla y
sí se mide es la coherencia por banda entre las alimentaciones digitales (INFERIDO).

### 1.2 Modelos perceptuales con referencia

| Modelo | Implementación abierta | ¿Con micrófono? |
|---|---|---|
| PEAQ (ITU-R BS.1387-2) | GstPEAQ, LGPL-2 (no cumple todas las tolerancias de la norma) | No |
| ViSQOL v3, modo audio | google/visqol, Apache-2.0, solo 48 kHz | No |
| PEMO-Q | binario de Oldenburg, no libre | No |
| HAAQI | pyclarity, MIT | No |
| 2f-model | sobre los MOVs de PEAQ | No |

Torcoli, Kastner y Herre (IEEE/ACM TASLP 29, 2021), VERIFICADO: el **2f-model** es el mejor
en códecs (ρ = 0,90); PEAQ ODG queda 8.º y ViSQOLAudio 9.º; los MOVs sueltos de PEAQ superan
a su ODG. Los autores de ViSQOL no lo recomiendan para regresiones automáticas sin reentrenar
(VERIFICADO). **Con una grabación de la pieza, la respuesta lineal del parlante y la sala
dominan el puntaje** y un artefacto de SBC 20 dB más abajo queda invisible (INFERIDO). Uso
válido: el banco del códec, en digital.

### 1.3 Pruebas de escucha con una sola persona

ABX binomial (INFERIDO, cálculo exacto):

| Ensayos | Aciertos para p ≤ 0,05 | Potencia si se acierta de verdad el 70 % | Si el 80 % |
|---|---|---|---|
| 16 | 12 | 0,45 | 0,80 |
| 30 | 20 | 0,73 | 0,97 |
| 40 | 26 | 0,81 | 0,99 |

**16 ensayos no bastan para afirmar "no se oye"** (Leventhal, JAES 34(6), 1986, REPORTADO):
30 en dos sesiones. MUSHRA (BS.1534-3) y BS.1116-3 exigen **igualar la sonoridad** antes de
comparar (VERIFICADO). Para "envolvimiento" o "preferencia", ABX no sirve: comparación pareada
de preferencia (INFERIDO). El cambio entre opciones tiene que ser sin hueco ni clic, porque el
hueco delata cuál es.

### 1.4 Señales de medición

| Señal | Uso aquí |
|---|---|
| Barrido exponencial | distorsión y respuesta **con un parlante a la vez**; barridos cortos (2–5 s) y repetidos, porque un salto de cuantum o la deriva de reloj rompen la deconvolución (Bryan, Kolar y Abel, AES 129, VERIFICADO) |
| MLS | no: sensible a no linealidad y variación temporal (REPORTADO) |
| Ruido rosa independiente por parlante | la respuesta (lo que ya hace la calibración), sumándole la coherencia |
| Música + doble FFT | A/B y seguimiento en vivo, con la sonda o con un parlante |

La deriva de ~22 ppm (experimentos/10 §5.3) son 1,3 ms en una ventana de 60 s: trocear en 5–10 s
y alinear cada trozo; descartar ventanas cuyo retardo difiera de la mediana en más de 0,5 ms
(INFERIDO).

### 1.5 El micrófono sin calibrar

- **Sustitución:** se cambia solo A por B, con el mismo micrófono, posición, ganancia y
  material; el cociente cancela micrófono y sala (INFERIDO, álgebra de transferencias).
- **No se puede concluir:** SPL absoluto, respuesta absoluta, THD bajo la del micrófono, nada
  sobre ~10 kHz ni bajo ~80–100 Hz, IACC, un MOS (INFERIDO).
- La ganancia de captura tiene que estar fija y sin AGC ni echo-cancel en la ruta: **se
  verifica con `pw-dump`** (regla del repo).
- Un UMIK-1 (~79 USD, REPORTADO) daría respuesta absoluta: es un gasto, se consulta antes.

---

## 2. Procesamiento, efectos y filtros

Restricción que manda: el paquete **no depende de scipy** a propósito (la Raspberry Pi de la
Fase 3), y en numpy un IIR solo es barato convertido en FIR y aplicado por FFT: una sección
biquad en Python puro cuesta **0,34 ms por bloque de 4096** (MEDIDO), cuatro veces el motor
entero por cada cuatro secciones.

### 2.1 Ecualización

- *Audio EQ Cookbook* (Bristow-Johnson, W3C Note): el lenguaje de diseño (VERIFICADO).
- Fase lineal vs mínima: el FIR de fase lineal no corre a un parlante respecto de otro, pero
  tiene pre-ringing; con realces suaves el del EQ actual queda entre −44 y −30 dB (MEDIDO).
- **Límite medido del EQ actual:** con 2048 coeficientes, +6 dB pedidos en el tercio de
  100 Hz entregan 2,78 dB (el ideal, 4,55); con curvas suavizadas la diferencia baja a
  0,3–0,9 dB (MEDIDO). No puede corregir un hueco angosto en graves.
- REW limita el realce por filtro y total, y por defecto **no rellena la caída natural** del
  parlante (REPORTADO). Los oyentes de Harman prefirieron en sala **+6,6 dB de graves bajo
  105 Hz y −2,4 dB de agudos sobre 2,5 kHz** (Olive et al., AES 8994, 2013, REPORTADO):
  **valida que el EQ solo realce y deje la joroba del Go 4** (d-7c8794-e8f7e3).
- Recomendación: presupuesto total de realce y techo de +3 dB sobre 8 kHz, porque con bitpool
  bajo SBC deja sin bits los agudos (INFERIDO). Guardar la FFT de los coeficientes en
  `StreamingFIR` baja el EQ un 37 % (MEDIDO).

### 2.2 Graves en parlantes chicos

- **Crossover hacia un parlante grande.** Linkwitz-Riley de 4.º orden: salidas en fase,
  −6 dB en el cruce, suma todo-paso (VERIFICADO, linkwitzlab). Como FIR causal truncado, ~0,05–
  0,1 ms por parlante y **latencia 0** (MEDIDO por piezas). Kelloniemi et al. (AES 6431, 2005,
  VERIFICADO): el subwoofer se volvió detectable recién con cruce en ≈120 Hz → empezar en
  90–100 Hz. A 100 Hz, 1 ms de error de alineación son 36°; con 2 ms el hueco en el cruce es
  de ~2,2 dB (INFERIDO, cálculo). Por Bluetooth el Charge 6 es el 4.º enlace y el techo
  medido son 3 (experimentos/05): el caso realista es el Charge 6 por USB-C (E7).
- **Bajo psicoacústico.** Larsen y Aarts (JAES 50(3), 2002, VERIFICADO): se extrae la banda
  bajo el corte, una no linealidad genera armónicos que dan la altura de la fundamental
  ausente; el rectificador de onda completa es lineal en amplitud; requiere menos headroom que
  un realce lineal; **algunos oyentes prefieren no procesar** → perilla. Moliner, Rämö y
  Välimäki (DAFx-20, VERIFICADO): NLD con pasa-banda de fc a 4fc; el híbrido NLD + vocoder de
  fase fue el mejor en MUSHRA. La parte no lineal cuesta 0,012 ms por bloque (MEDIDO).
- **Compresión de graves por nivel** (como los parlantes con DSP, TI SLAA857, REPORTADO): solo
  si la medición muestra que la protección del firmware sigue actuando con el pasa-altos.

### 2.3 Sonoridad y dinámica

- La compensación por nivel (ISO 226:2023, que difiere de 2003 en 0,6 dB como mucho,
  VERIFICADO) en un Go 4 bajo 70 Hz es excursión pura: va al parlante grande o al bajo
  psicoacústico (INFERIDO).
- **Volumen por AVRCP** (resumen, punto 3; experimentos/10 §5.4 midió que no mueve el retardo).
- **Limitador true-peak con look-ahead:** detección ×4 por FFT, 0,08 ms por parlante (MEDIDO);
  3 ms de look-ahead iguales en todos. Spotify "Loud" usa 5 ms de ataque y 100 ms de liberación
  a −1 dB (REPORTADO). Diseño de compresores: Giannoulis, Massberg y Reiss (JAES 60(6), 2012).
- **Ningún compresor de banda ancha ni AGC**: aplana justo lo que el oyente se quejó
  (experimentos/10 §7). Criterio: el factor de cresta de la salida no baja más de ~1 dB.

### 2.4 Espacialización

- El decorrelador actual (1–4 ms de retardo de grupo) está muy bajo el umbral de 30 ms de
  Kermit-Canfield y Abel (DAFx-16, VERIFICADO). Mejora posible: **no decorrelar transitorios**
  (HPSS), para que los ataques lleguen coherentes y la cola decorrelada.
- **Cola difusa por parlante:** lo que más puede sumar con mezclas secas, de las que
  Avendaño-Jot casi no saca ambiente. IR de ruido con decaimiento exponencial, semilla por
  parlante, convolución particionada: 0,077 ms por parlante por cada 0,5 s de cola, latencia 0
  (MEDIDO). FDN en numpy no: es recursiva por muestra.
- **M/S por parlante no existe**: cada parlante recibe mono.
- Separación con ML: HS-TasNet da SDR 4,65 dB con 23 ms (VERIFICADO, arXiv 2402.17701), con
  implementación no oficial; Open-Unmix publicado "cannot be used in an online/real-time
  manner" (VERIFICADO). **No ahora.**

### 2.5 Calidad numérica y alternativas de motor

- f64 dentro del motor, f32 a la salida: sobra. La pérdida real es el paso a S16 sin dither en
  PipeWire (VERIFICADO); activar dither en el nodo Bluetooth gastaría bits de SBC en ruido (no).
- Denormales: en Zen la penalización es despreciable (REPORTADO) y el motor es todo FIR.
- **filter-chain de PipeWire** (biquads, convolver, LADSPA/LV2; sin Linkwitz-Riley integrado,
  VERIFICADO) y **CamillaDSP** correrían en tiempo real fuera de Python, pero se pierde la
  medición integrada, el lazo y los tests sobre la señal digital. **Solo si Cortes culpa al
  motor.**

---

## 3. Canales, fuentes y la cadena de códecs

### 3.1 Las fuentes

| Fuente | Máximo | Normalización |
|---|---|---|
| Spotify escritorio | Very High ~320k; Lossless FLAC 24/44,1 (Premium, desde 2025-09) | −14 LUFS por defecto; Loud −11 con limitador; Quiet −19 (VERIFICADO) |
| Spotify web | AAC 128/256 | sin normalización (VERIFICADO) |
| YouTube | Opus ~128k a 48 kHz | solo baja (REPORTADO) |
| Apple Music web | AAC 256 | Sound Check (REPORTADO) |

**El techo lo ponen SBC y el Go 4** (−22 dB a 16 kHz en el micrófono), no la fuente (INFERIDO).
Recomendación: normalización **Normal**, nunca **Loud** (agrega un limitador antes del del
motor), la app al 100 %. El indicador "ancho de banda" del panel **no puede delatar a Opus**
(corta en 20 kHz) ni a Vorbis alto: lo que delata a un códec es lo **abrupto** del corte, no su
altura (REPORTADO, tabla de cortes por encoder de simpleaudiospectral; INFERIDO el criterio).

### 3.2 SBC en PipeWire 1.6.9 (VERIFICADO en el código) y la simulación (SIMULADO)

- Bitpool por defecto a 48 kHz: joint 58, mono o dual 29. El efectivo es el mínimo con el del
  parlante: **el Go 4 anuncia 2–40** → joint 40 = 279 kbps; **mono queda en 29** (el tope es
  el de PipeWire para mono, no el del parlante).
- El modo es joint stereo con 2 canales; asignación LOUDNESS, 16 bloques, 8 subbandas; el
  codificador recibe S16 (`dither.method = none` por defecto).
- La línea de un descarte y la del latido sano: resumen, punto 1. El piso del bitpool es 12.

Simulación con libsbc (la de PipeWire), música sintética a −16 dBFS RMS; repetida con otra
semilla y otra ventana de análisis, cambia ≤0,4 dB:

| Configuración | kbps | SNR total (dB) | 6–10 kHz |
|---|---|---|---|
| **Joint 40, L = R (lo de hoy)** | 279 | **32,8** | 23,3 |
| Mono 29 (`bluez5.default.channels = 1`) | 198 | 26,1 | 16,8 |
| Dual 39 (SBC-XQ en el Go 4) | 504 | 33,7 | 25,2 |
| Stereo no joint 40, L = R | 276 | 20,4 | 9,7 |
| Joint 34 (3 bajadas por congestión) | 243 | 28,1 | 19,2 |
| Joint 28 (6 bajadas) | 207 | 25,0 | 14,8 |
| Joint 40, estéreo real | 279 | 24,1 | 11,7 |

Lectura: con L = R el joint stereo manda casi todo a la suma (VERIFICADO en `sbc.c`). **Perder
6 de bitpool por congestión cuesta más que todo lo demás junto.** Que cada parlante lleve un
solo canal **juega a favor**: un mono duplicado se codifica ~8,7 dB mejor que un estéreo real
con el mismo bitrate.

**Tándem (fuente lossy → SBC):** SBC no agrega más ruido sobre una fuente lossy que sobre una
limpia (SIMULADO); el costo perceptual del tándem (EBU, Marston y Mason 2005, VERIFICADO:
*"the cumulative effect of cascaded audio coding can be highly detrimental"*) solo lo mide
PEAQ o ViSQOL, en digital. Queda como experimento.

### 3.3 Un canal por Go 4

Ya está así (VERIFICADO): cada salida del combine-stream es `MONO`, y el `channelmix` de
PipeWire lo lleva a FL y FR con ganancia 1,0. **Qué hace el Go 4 con L y R distintos no está
documentado**: se mide con un tono en L, en R, en L = R y en L = −R (si L = −R da silencio, suma).
Importa para que nada en el motor ponga en un mismo Go 4 dos versiones de fase opuesta.

### 3.4 Layouts, 5.1 y upmix

- Un sink `aurasync (5.1)` que **no** sea el por defecto (para que Firefox no abra streams de
  6 canales con estéreo), con tablas de mapeo a quad o 3/1; ojo con SL/SR contra BL/BR
  (INFERIDO).
- El siguiente paso con sentido para envolvimiento es un **upmix tipo DirAC** (Pulkki, AES 28th
  2006; Faller, JAES 54(11) 2006: el sweet spot se ensancha, REPORTADO): dirección y difusividad
  por celda tiempo-frecuencia, lo directo paneado a 4 esquinas y lo difuso decorrelado.

### 3.5 Lip-sync

0,5 s de cadena está muy fuera de BT.1359 para video (07 §7.1). El sink `aurasync` es un
`pw-record`, que no puede declarar latencia; **`libpipewire-module-loopback` sí suma
`latencyOffsetNsec`** a la que publica (VERIFICADO en `module-loopback.c`). Que Chrome, Firefox
y mpv la compensen es INFERIDO y se mide.

---

## 4. Visualización y usabilidad del panel

### 4.1 Medidores y análisis

- EBU Tech 3341 (VERIFICADO): M 0,4 s, S 3 s (≥10 Hz), I con compuerta (≥1 Hz); escalas +9 y
  +18; **la unidad siempre visible**; "simply numerical" está permitido. El medidor actual
  (RMS de 300 ms + pico tipo PPM I) sirve para ver señal y saturación; **lo que no responde es
  "¿suenan igual de fuerte A y B?"**: para eso, sonoridad S en LUFS (INFERIDO).
- Zonas −18/−6 son de producción: con música masterizada el ámbar se enciende siempre; mejor
  neutro hasta el umbral del limitador y rojo solo cuando el limitador actúa (INFERIDO).
- Respuesta en frecuencia: atenuar los tramos con coherencia baja, como Smaart (VERIFICADO el
  mecanismo). Retardo como un número; la fase, para especialistas.
- **Sincronía para alguien sin formación (INFERIDO, con 09 §3):** un número —la desalineación
  residual— sobre una regla con zonas: <2 ms "un solo sonido", 2–5 ms "corrido hacia el
  primero", 5–10 ms "en golpes secos puede oírse doble", >10 ms "zona de Haas"; con texto y
  forma, no solo color.

### 4.2 Lo que hacen los productos de consumo

Los de todo público esconden el resultado y muestran el proceso (Sonos Trueplay, Bose
ADAPTiQ, VERIFICADO); los de especialista muestran antes/después (Dirac Live, Audyssey).
**De Dirac Live vale copiar la comprobación del nivel del micrófono antes de medir** (una
ventana objetivo de −15 a −30 dB, VERIFICADO en el manual de Denon). B&O e IRCAM Spat ofrecen
controles en términos perceptivos (VERIFICADO para Spat). "Algo anda mal" se muestra **donde
se mira** (Sonos), no en una pantalla de detalle. El rediseño de la app de Sonos de 2024
(REPORTADO) advierte contra simplificar quitando funciones.

### 4.3 Usabilidad y accesibilidad

- Divulgación progresiva: no más de 2 niveles (NN/g, VERIFICADO). El panel cumple.
- "Never use a warning when you mean undo" (Raskin, VERIFICADO): deshacer en vez de `confirm()`.
- Con ~0,5 s de latencia, mover un control sin aviso lleva a pasarse: un estado "en camino"
  durante la latencia medida (INFERIDO, con los límites de Nielsen, VERIFICADO).
- WCAG 2.2 (VERIFICADO): 1.4.1 (el color no puede ser el único medio — hoy fallan la batería
  baja, las zonas del medidor, la saturación y los tipos de corte); 1.4.11 (3:1 para partes de
  gráficos); 2.2.2 (pausar lo que se actualiza solo); 2.5.8 (blancos ≥24 px; Apple pide 44 pt).
  `.meter-clip` mide 12 px y `.help-btn` 24 px. Doble toque para restablecer un deslizador
  táctil probablemente mueve el valor antes (INFERIDO): mejor un ↺ visible.

### 4.4 Rendimiento web. MEDIDO (Mac, Chromium headless, servicio simulado)

| Evento del stream | eventos/s | kB/s |
|---|---|---|
| `meters` | 17,9 | 7,23 |
| `state` | 1,0 | 6,54 |
| `input` | 9,0 | 2,19 |
| total | | **16,0** |

16 kB/s en una LAN no vale optimizarlo. **Dibujo** (390×844, DPR 3): 60 fps, pero **420
layouts/s (~7 por cuadro)** y el hilo principal ocupado 286 ms/s con la CPU frenada ×4. Causa
probable: `paintMeter` lee `clientWidth` después de escribir estilos y anima `width`/`left`
(INFERIDO). Solo `transform` y `opacity` se resuelven sin layout (VERIFICADO, web.dev). SSE sin
HTTP/2 tiene un límite de **6 conexiones por navegador** (VERIFICADO, MDN), y el stream sigue
recibiendo ~28 eventos/s con la pestaña oculta.

### 4.5 Evaluar con un solo usuario

Heurísticas de Nielsen como lista de chequeo por tarjeta; SUS como serie temporal del mismo
usuario (VERIFICADO el método; INFERIDO el uso); el modelo de costo de `medir.py` como guardia
de regresión; Playwright con `emulate_media` (reduced motion, colores forzados) y snapshots
ARIA (VERIFICADO).

---

## 5. Cómo se aplica a este producto

Ordenado por la prioridad que dio el usuario (cortes → graves y volumen → controles y panel).
El detalle y los criterios de aceptación están en la spec del 2026-10-02.

| Prioridad | Qué | De dónde sale |
|---|---|---|
| 1 | Contar los paquetes descartados por parlante (`reduce bitpool`) y mostrarlos en Cortes; protocolo para atribuir la causa (carga del controlador, enlace, posición, escaneo) | §1.1, §3.2 |
| 1 | Corregir experimentos/10 §5.1 y §5.5 | §3.2 |
| 2 | Pasa-altos de protección + bajo psicoacústico en los Go 4; crossover cuando haya un parlante de graves | §2.2 |
| 2 | Volumen por AVRCP, cadena digital cerca de 0 dBFS | §2.3 |
| 2 | Limitador true-peak con look-ahead | §2.3 |
| 2 | Presupuesto de realce y techo sobre 8 kHz en el EQ | §2.1 |
| 3 | La cadena como lista de etapas con todas sus perillas y algoritmos, en una pantalla propia | pedido del usuario; §4 |
| 3 | Sonoridad de entrada y salida, ganancia neta, true peak, PSR, actividad del limitador | §1.1, §4.1 |
| 3 | Arreglos del panel: medidores sin layout forzado, stream que se cierra oculto, avisos donde se mira, accesibilidad, A/B con sonoridad igualada | §4 |
| 4 | Cola difusa por parlante (envolvimiento con mezclas secas) | §2.4 |
| Después | Banco del códec con PEAQ/ViSQOL; sink 5.1; loopback para lip-sync; DirAC; HPSS | §1.2, §3.4, §3.5, §2.4 |
| No | Separación con ML; compresión de banda ancha o AGC; SBC mono; SBC-XQ; dither en el nodo Bluetooth; mover el DSP fuera de numpy (salvo que Cortes culpe al motor) | §2, §3 |

## 5.1 Lo medido al construir el DSP (2026-10-02, Mac, SIMULADO)

Los módulos nuevos (`host/src/aurasync/dsp/{crossover,virtual_bass,diffuse,loudness}.py`,
`TruePeakLimiter` en `limiter.py`) se construyeron contra los criterios de la spec. Cada
criterio tiene un test, y cada test se vio fallar con la implementación rota a propósito.

| Módulo | Criterio | Resultado |
|---|---|---|
| Crossover LR4 | \|HP+LP\| plano ±0,1 dB de 20 Hz a 20 kHz | **9e-8 dB**; salidas en fase, −6,02 dB en el corte; latencia 0 (el todo-paso atrasa los graves 4,5 ms en continua, igual en las dos ramas) |
| Crossover LR4, fc = 100 Hz | lo que deja el pasa-altos bajo 60 Hz < −24 dB | **−28,3 dB** de energía con ruido rosa. Punto a punto, LR4 deja −18,8 dB a 60 Hz; LR8 (−35,6 dB) o LR4 en 118 Hz lo cumplen en cada frecuencia |
| Bajo psicoacústico (NLD) | con 50 + 70 Hz, productos inarmónicos ≥30 dB bajo los armónicos | **No se cumple:** el producto de 120 Hz queda ~8 dB **sobre** los armónicos. Es inevitable con cualquier rectificador sin memoria (\|cos a + cos b\| = 2\|cos((a+b)/2)\|\|cos((a−b)/2)\|). Cumplirlo exige un vocoder de fase por bin (Moliner et al.), con decenas de ms de latencia. Con ruido rosa: 100–400 Hz sube 0,9 / 2,7 / 6,5 dB con la perilla en −6 / 0 / +6, y bajo 80 Hz no sube |
| Limitador true-peak | ningún valor ×4 sobre −1 dBTP | **−1,01 dBTP** con picos entre muestras de hasta +11 dBTP (entrada limitada a 20 kHz) |
| Limitador true-peak | con un seno de 60 Hz 6 dB sobre el techo, armónicos ≥20 dB bajo los del limitador actual | actual −35 dB; nuevo sin hold −43 dB; **con hold de 15 ms, < −150 dB**. El hold es lo que cumple el criterio |
| Cola difusa | baja la coherencia entre salidas ≥0,15 (500 Hz–4 kHz) a −16 dB | **No se cumple a −16 dB** (0,065–0,075) con alimentaciones idénticas a los tres parlantes, el peor caso; hace falta **−11 dB** (0,18–0,21). Espectro por octava dentro de 0,68 dB; RT60 dentro de 10 % |
| Sonoridad BS.1770 | seno de 1 kHz a −23 y −33 dBFS dentro de ±0,1 LU | cumple en M, S e I; casos 3–5 de Tech 3341 dan −23,0; contra el IIR en el tiempo, 0,004 LU (ruido rosa) y −0,06 LU (señal cargada de graves) |

**Costo por bloque de 4096 (Mac, con carga de otros procesos: cifras pesimistas):** EQ
0,098 → 0,068 ms con la FFT guardada; crossover 0,07–0,08 ms; bajo psicoacústico 0,24 ms;
cola difusa 0,17 ms; limitador true-peak 0,008 ms en reposo y 0,25 ms limitando; sonoridad
0,05–0,07 ms por canal.

**Al integrarlos en el motor (MEDIDO, Mac, simulación):**
- **La ganancia neta no es 0 ± 1 LU en general.** Con una mezcla estéreo amplia sí; con una
  mezcla centrada, tres parlantes que llevan lo mismo suman hasta +1,76 LU (medido +1,27), y el
  ambiente la mueve entre −2,4 y −0,7 LU. El criterio de la spec era demasiado estrecho: la
  ganancia neta depende del material, y el aviso del panel ("ganancia perdida") se dispara bajo
  −3 LU.
- **El PSR de la entrada se toma por canal** (el menor de los dos): con el estéreo sumado, una
  mezcla centrada mostraba un falso aplanamiento de 1,7 dB. Con ruido rosa amplio, el parlante
  que es casi todo ambiente sí marca aplanamiento (PSR 13,6 dB, más de 1 dB bajo la entrada),
  por el overlap-add del extractor: es real, y queda para escucharlo.
- **El crossover necesita el todo-paso también en el parlante de graves.** Sin él, el grave
  propio del Charge 6 y el que recibe de los demás quedaban a ~87° y sumaban 3,6 dB en vez de 6.
  Con él, +5,9 dB entre 25 y 50 Hz, en fase.
- **La cola difusa se alimenta con la mezcla del propio parlante**: alimentarla con todo el
  ambiente más todo el directo la dejaba 8 dB más alta de lo que dice la perilla. A −12 dB la
  correlación entre parlantes baja a 0,90.
- **Costo del motor con 3 parlantes:** 32–38× el tiempo real con los valores por defecto;
  19–22× con `protect` y armónicos, 22–24× con `crossover`. Sonoridad de entrada y salidas:
  0,30–0,42 ms por bloque. Hay que medirlo en `PC-Ryzen5`
  (`probes/18-costo-de-la-cadena/costo.py`).

**Lo que esto cambia:** el bajo psicoacústico queda como perilla apagada y su A/B ciego decide
si la intermodulación molesta con música real; la cola difusa, si se adopta, va hacia −11 dB o
se mide con las salidas ya decorrelacionadas (el caso real); el pasa-altos de protección se
puede subir a ~118 Hz o pasar a LR8 si la medición con micrófono muestra que el Go 4 sigue
recibiendo demasiado grave.

## 6. Lo que no se pudo determinar

- Qué líneas de log permiten asociar cada `reduce bitpool` a su parlante, y cómo subir el nivel
  de log solo para esos módulos: se lee en el código y se comprueba en `PC-Ryzen5`.
- Si el Go 4 suma L+R; el bitpool máximo del Charge 6; el MTU real de los enlaces (fija cuántos
  ms se pierden por descarte).
- Si la protección de graves del Go 4 existe como se reporta y a qué volumen actúa.
- Si Chrome y Firefox compensan un `latencyOffsetNsec` de ~0,5 s.
- El costo perceptual del tándem lossy → SBC.

## Fuentes

**Normas.** ITU-R BS.1770-5 — https://www.itu.int/rec/R-REC-BS.1770 · EBU R 128 —
https://tech.ebu.ch/docs/r/r128.pdf · EBU Tech 3341 — https://tech.ebu.ch/docs/tech/tech3341.pdf ·
EBU Tech 3342 — https://tech.ebu.ch/docs/tech/tech3342.pdf · ITU-R BS.1534-3 —
https://www.itu.int/dms_pubrec/itu-r/rec/bs/R-REC-BS.1534-3-201510-I!!PDF-E.pdf · ITU-R BS.1116-3 —
https://www.itu.int/dms_pubrec/itu-r/rec/bs/R-REC-BS.1116-3-201502-I!!PDF-E.pdf · ITU-R BS.1387 —
https://www.itu.int/rec/R-REC-BS.1387 · WCAG 2.2 Understanding (2.5.8, 1.4.1, 1.4.11, 2.2.2) —
https://www.w3.org/WAI/WCAG22/Understanding/

**Código.** PipeWire 1.6.9 (`spa/plugins/bluez5/media-sink.c`, `a2dp-codec-sbc.c`,
`README-SBC-XQ.md`, `audioconvert/channelmix-ops.c`, `src/modules/module-loopback.c`,
`pipewire-props.7.md`) — https://gitlab.freedesktop.org/pipewire/pipewire/-/tree/1.6.9 ·
libsbc — https://git.kernel.org/pub/scm/bluetooth/sbc.git · Open Sound Meter —
https://github.com/psmokotnin/osm · google/visqol — https://github.com/google/visqol · GstPEAQ —
https://github.com/HSU-ANT/gstpeaq · pyloudnorm — https://github.com/csteinmetz1/pyloudnorm ·
libebur128 — https://github.com/jiixyj/libebur128 · CamillaDSP — https://github.com/HEnquist/camilladsp ·
PipeWire filter-chain — https://docs.pipewire.org/page_module_filter_chain.html

**Papers.** Torcoli, Kastner y Herre 2021 — https://arxiv.org/pdf/2110.11438 · ViSQOL v3 —
https://arxiv.org/pdf/2004.09584 · He, Williams y Fazenda, DAFx25 —
https://dafx25.dii.univpm.it/wp-content/uploads/2025/09/DAFx25_paper_5.pdf · Farina 2000 —
https://secure.aes.org/forum/pubs/conventions/?elib=10211 · Novak et al. 2015 —
https://ant-novak.com/pages/sss/ · Bryan, Kolar y Abel 2010 —
https://ccrma.stanford.edu/groups/chavin/publications/AES129_ClockDrift.pdf · Leventhal 1986 —
https://secure.aes.org/forum/pubs/journal/?elib=5265 · Linkwitz, crossovers —
https://www.linkwitzlab.com/filters.htm · Kelloniemi et al. 2005 —
http://legacy.spa.aalto.fi/research/cat/psychoac/papers/kelloniemiaes118.pdf · Aarts, Larsen,
Schobben 2002 — https://www.sps.tue.nl/rmaarts/RMA_papers/aar02n4.pdf · Moliner, Rämö, Välimäki,
DAFx-20 — https://dafx2020.mdw.ac.at/proceedings/papers/DAFx2020_paper_40.pdf · Kermit-Canfield y
Abel, DAFx-16 — https://ccrma.stanford.edu/~kermit/website/papers/decorrelation_DAFx2016.pdf ·
Välimäki et al. 2012 (reverberación artificial) —
https://aaltodoc.aalto.fi/bitstreams/97ed04a8-cb88-461f-b1a3-e72da5129256/download · HS-TasNet —
https://arxiv.org/abs/2402.17701 · Open-Unmix — https://github.com/sigsep/open-unmix-pytorch ·
Suzuki et al. (ISO 226) — https://www.jstage.jst.go.jp/article/ast/45/1/45_e23.66/_article/-char/en ·
Marston y Mason, EBU 2005 — https://tech.ebu.ch/docs/techreview/trev_304-cascading.pdf · Pulkki,
DirAC — https://research.aalto.fi/en/publications/directional-audio-coding-in-spatial-sound-reproduction-and-stereo ·
Faller 2006 — https://www.aes.org/e-lib/browse.cfm?elib=13886

**Documentación y reseñas (REPORTADO salvo que se diga).** Rational Acoustics, coherencia —
https://support.rationalacoustics.com/support/solutions/articles/150000183871-what-is-coherence- ·
REW — https://www.roomeqwizard.com/help/help_en-GB/html/eqwindow.html · Audio EQ Cookbook
(VERIFICADO) — https://www.w3.org/TR/audio-eq-cookbook/ · Spotify, calidad y normalización
(VERIFICADO) — https://support.spotify.com/us/article/audio-quality/ ,
https://support.spotify.com/us/artists/article/loudness-normalization/ · ValdikSS, SBC —
https://habr.com/en/articles/456182/ · JBL Go 4 —
https://stereoguide.com/bluetooth-speakers/mobile-outdoor/jbl-go-4-mini-bluetooth-speaker-review/ ,
https://www.soundguys.com/jbl-go-4-review-117126/ · simpleaudiospectral —
https://github.com/fishingpvalues/simpleaudiospectral · Sonos Trueplay (VERIFICADO) —
https://support.sonos.com/en-us/article/tune-your-sonos-speakers-with-trueplay · Dirac Live,
manual Denon (VERIFICADO) — https://manuals.denon.com/DiracLive/ALL/EN/DRDZSYdgbiqwvc.php · IRCAM
Spat (VERIFICADO) — https://doc.flux.audio/ircam-spat/Perceptual_Factors_.html · NN/g (VERIFICADO)
— https://www.nngroup.com/articles/progressive-disclosure/ ,
https://www.nngroup.com/articles/response-times-3-important-limits/ · Raskin (VERIFICADO) —
https://alistapart.com/article/neveruseawarning/ · MDN SSE y rendimiento de canvas (VERIFICADO) —
https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events ·
web.dev (VERIFICADO) —
https://web.dev/articles/stick-to-compositor-only-properties-and-manage-layer-count
