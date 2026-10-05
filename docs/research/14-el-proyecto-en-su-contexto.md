# 14 · El proyecto en su contexto: qué existe, qué es propio y hacia dónde reenfocar

**Pregunta (usuario, 2026-10-04):** el proyecto creció mucho; "quiero entenderlo en su contexto,
quizás valga la pena reenfocar, adaptarme a soluciones existentes y entender qué busco construir".
¿Estamos haciendo una consola o un mezclador de audio? ¿Qué ya existe, qué conviene adoptar y qué
es realmente nuestro?

**Cómo se hizo:** cuatro investigaciones en paralelo el 2026-10-04 (consolas, ruteo y audífonos;
motores DSP abiertos; multiroom sincronizado y calibración acústica; audio inmersivo y upmix), cada
una siguiendo enlaces dos o tres niveles. Marcas: **VERIFICADO** (leído en la documentación, el
código o el paper), **REPORTADO** (fuente secundaria, foro, resumen), **INFERIDO**. Las fuentes van
al final de cada sección.

## Resumen

- **Sí, en función es una consola espacial** (de la familia de SpatGRIS, SoundScape Renderer,
  Panoramix): fuentes → procesamiento → N salidas con retardo, ganancia y EQ por salida, una
  disposición de parlantes y un panel. Esa parte es **commodity** (INFERIDO de §1–§2).
- **Lo que nadie hace junto** (en nada de lo revisado, INFERIDO): (1) **un canal distinto por
  parlante, sincronizado acústicamente con micrófono y en lazo continuo, sobre parlantes Bluetooth
  baratos**; (2) un **upmix de estéreo pensado para envolver a un oyente que se mueve** (no para un
  punto dulce de cine); (3) la medición, el A/B y las métricas atadas a la señal exacta que sale a
  cada parlante. Eso es lo propio del proyecto.
- **Lo que conviene adoptar en vez de construir:** el monitor binaural de audífonos (el
  espacializador SOFA de la `filter-chain` de PipeWire, ya instalado en `PC-Ryzen5`), `pyfar` para la
  medición (barridos, deconvolución, retardo fraccional), y dos líneas de base gratis para el A/B
  (`surround` de FFmpeg, `channelmix.upmix-method=psd` de PipeWire).
- **Lo que no conviene adoptar como motor:** CamillaDSP y la `filter-chain` de PipeWire **reinician o
  saltan el retardo al cambiarlo** (VERIFICADO en su código): no sirven de actuador para el lazo de
  sincronía, que mueve retardos en vivo y sin clics.
- **Por qué "el estéreo a veces gana" (la queja que originó el modo espacial):** decorrelacionar copias
  de la misma mezcla es sutil por naturaleza, el ambiente de una mezcla de estudio está 10–15 dB bajo
  el directo, y **los retardos de Haas sobre contenido correlacionado solo funcionan en un punto**: para
  quien camina, el parlante más cercano se vuelve el que llega primero y se roba la imagen. Lo que se
  nota de verdad es **contenido distinto en cada parlante** (ambiente fuerte o pistas separadas) y
  **nunca empeorar el par estéreo de adelante** (§4).

## 1. Consolas, ruteo y audífonos

- **Infraestructura de PipeWire (VERIFICADO, fuentes de 1.6.9):** `filter-chain` (biquads, `param_eq`,
  convolución, `delay`, `mixer` de hasta 8 entradas, y complementos `sofa`, `ebur128`, `ffmpeg`, LV2,
  LADSPA, `onnx`) con controles cambiables en vivo; `module-combine-stream` (lo que ya usa el proyecto)
  manda **posiciones de canal distintas a sinks distintos** y compensa latencia, pero con la latencia
  que PipeWire **informa**, no la acústica (la lección del experimento 09); los "smart filters" de
  WirePlumber 0.5 fijan un filtro a un dispositivo y se reenlazan con `pw-metadata`.
- **Consolas y renderizadores abiertos:** **SpatGRIS** (GPL-3, hasta 256×256, disposiciones
  arbitrarias, binaural, OSC; de escritorio, panea objetos, no upmixea estéreo); **SoundScape
  Renderer** (VBAP, WFS, HOA, binaural; retardo y peso por parlante; **protocolo WebSocket y GUI web**);
  **IEM Plug-in Suite** (GPL-3; su *DistanceCompensator* calcula retardo y ganancia por distancia: una
  referencia directa para las disposiciones); **SPARTA/SAF** (núcleo ISC con bindings de Python:
  la mejor biblioteca si el binaural tuviera que vivir dentro del motor). **Panoramix** (IRCAM) no es
  abierto. Ninguno combina upmix de estéreo con papel principal/ambiental, disposiciones arbitrarias
  con retardo por parlante y control web (INFERIDO).
- **Audífonos como monitor:** PipeWire trae un **espacializador SOFA** desde 0.3.66 y el ejemplo
  `spatializer-7.1.conf` es exactamente "N parlantes virtuales → binaural" (VERIFICADO). En
  `PC-Ryzen5` están el complemento (`libspa-filter-graph-plugin-sofa.so`) y la HRTF
  `MIT_KEMAR_normal_pinna.sofa` (VERIFICADO, 2026-10-04). Generar esa configuración desde la
  disposición da el monitor sin DSP nuevo. **Riesgo:** audífonos Bluetooth tienen 150–250 ms o más de
  latencia que PipeWire no informa bien; para sonar en sincronía tendrían que entrar al lazo medido.
  El usuario aceptó un monitor **no sincronizado** por ahora (2026-10-04). Un segundo enlace Bluetooth
  en la misma radio puede empeorar los microcortes (INFERIDO; medir).
- **Arquitectura recomendada para "salidas":** una **matriz de ruteo del motor**, guardada y en el
  panel (al estilo de Pulsemeeter): fuentes `entrada estéreo`, `canal de cada parlante`, `bus
  principal/ambiental`, `render binaural`; destinos `parlante`, `audífonos`, `sink virtual`; cada ruta
  con ganancia, retardo, silencio y si entra al grupo de sincronía. El motor produce los canales;
  PipeWire hace la distribución y el binaural con `filter-chain` generada.

Fuentes: docs.pipewire.org (filter-chain, combine-stream, pipewire-props(7)); NEWS y código de
PipeWire 1.6.9; WirePlumber *smart filters*; github.com/GRIS-UdeM/SpatGRIS; ssr.readthedocs.io;
plugins.iem.at; github.com/leomccormack/SPARTA y Spatial_Audio_Framework; github.com/badaix/snapcast;
theRealCarneiro/pulsemeeter; wwmm/easyeffects; falkTX/Carla; ffmpeg.org/ffmpeg-filters.html.

## 2. Motores DSP abiertos

- **CamillaDSP** (Rust, GPL-3 o MPL-2.0, v4.1.3 de 2026-04, muy activo; VERIFICADO): filtros IIR y FIR
  (con SIMD), mezclador N→M, retardo con fracción por un todo-paso (preciso hasta ~fs/4), sonoridad,
  compensación de reloj, backend nativo de PipeWire, **API websocket** de ~80 órdenes y una GUI web.
  Hace bien todo lo commodity del motor. **Pero:** cambiar un retardo en vivo **reconstruye la línea
  con ceros** (`Delay::update_parameters`, VERIFICADO en el código); el limitador es solo un recortador;
  no tiene STFT ni API de complementos; no mide. Ya lo había descartado como motor research/12 §2.1.
- **`filter-chain` de PipeWire:** el `delay` es de **muestras enteras** y cambia sin rampa
  (VERIFICADO en `plugin_builtin.c`); no tiene limitador con anticipación.
- **Medición:** **pyfar** (MIT, activo): barridos, deconvolución, inversión regularizada, retardo
  fraccional y `find_impulse_response_delay` con precisión de submuestra (VERIFICADO). Es lo más
  cercano al código propio de calibración. REW es cerrado (su API para automatizar barridos es de
  pago). `python-acoustics` está archivado.
- **Commodity del motor** (lo hacen motores maduros): EQ paramétrica, FIR, retardo y ganancia fijos,
  mezclador, sonoridad, medidores, presets, control remoto. **Propio:** upmix adaptativo en
  tiempo-frecuencia a un anillo de N parlantes; retardos que cambian en vivo sin clics para el lazo;
  medición atada a la señal exacta.
- **Cuándo volver a CamillaDSP:** un modo "perfil fijo" para la Raspberry Pi (exportar la calibración
  a su YAML), o si el experimento 12 culpa al motor de Python de los cortes (la regla que ya tiene el
  repo).

Fuentes: github.com/HEnquist/camilladsp (README, websocket.md, backend_pipewire.md, CHANGELOG,
`src/filters/basicfilters.rs`, `src/pipewire_backend/device.rs`); pycamilladsp; camillagui-backend;
`plugin_builtin.c` de PipeWire; pyfar.readthedocs.io; roomeqwizard.com (API); drc-fir.sourceforge.net;
JDSP4Linux; brutefir; Equalizer APO; fundsp.

## 3. Sincronía de varios parlantes y calibración acústica

- **La sincronía por red está resuelta** a menos de 0,2 ms para salidas de latencia fija: **Snapcast**
  (GPL-3; corrige deriva saltando o duplicando muestras; latencia por cliente; VERIFICADO), **Sendspin**
  de Music Assistant (Kalman de desfase y deriva; `static_delay_ms` por cliente; VERIFICADO), **AirPlay 2**
  con `nqptp` (PTP). **Ninguno manda un canal distinto a cada cliente de forma nativa** (Snapcast
  #747 cerrado sin la función; `shairport-sync` solo elige izquierda o derecha).
- **La comunidad Bluetooth** usa "un cliente Snapcast o Sendspin por parlante + un desfase fijo
  ajustado de oído" (`bluetooth-web-snapclient`, `sendspin-bt-bridge`, `pipewire-multi-output`,
  `hyperboom-duo-stereo`; VERIFICADO en sus README). Nadie cierra el lazo: la deriva y las
  reconexiones son el dolor recurrente. BlueBox usa un adaptador USB por parlante (máx. 4).
- **Calibración acústica:** cerrada y de una sola vez en Sonos Trueplay, Audyssey, Dirac (REPORTADO);
  abierta y muy nueva en `speaker-sync-calibrator` (2026, un parlante por vuelta, primera llegada,
  exporta a Music Assistant) y `ensemble` (navegador, roles de surround por dispositivo, mapa de la
  pieza por chirps) — ambos sin validación con parlantes (VERIFICADO sus README).
- **Productos comerciales con canal por parlante:** solo sistemas cerrados de una marca y enlaces
  propietarios (Sonos, Alexa Home Theater, Sony 360). Con parlantes Bluetooth baratos todo es mono o un
  solo par estéreo (JBL PartyBoost, Samsung Dual Audio; REPORTADO).
- **Auracast desde Linux funciona con un Go 4** (Collabora, mayo de 2026: BlueZ 5.86, PipeWire 1.6.0,
  controlador MT7921; VERIFICADO), con varios BIS configurables por canal. **Sigue sin respuesta pública
  si un JBL reproduce solo su BIS:** es el experimento E4 del proyecto, todavía único.

Fuentes: github.com/badaix/snapcast (README, configuration.md, issues #747 y #50); sendspin-audio.com
(spec); github.com/mikebrady/shairport-sync; roc-streaming.org; docs.pipewire.org (combine-stream,
rtp-sink, snapcast-discover); github.com/vshivtsev-dev/speaker-sync-calibrator; Allencrspy/ensemble;
shuricksumy/bluetooth-web-snapclient; trudenboy/sendspin-bt-bridge; Zigazou/hyperboom-duo-stereo;
jeremyoverman/pipewire-multi-output; collabora.com (Auracast en Genio 700); dev.to (Auracast con
varios subgrupos); support.google.com (retardo de grupos de Cast).

## 4. Audio inmersivo: qué hace notoria la espacialidad

- **Upmix clásico** (Avendaño-Jot, PCA de Goodwin-Jot, Faller): separa directo y ambiente por banda.
  He y Gan (2015, VERIFICADO): el ambiente por PCA filtra poco directo pero es **el menos difuso**; los
  métodos por cuadro dan artefactos en lo difuso.
- **El resultado de escucha más útil — Paulus y Torcoli (Fraunhofer, 2022, VERIFICADO):** con la
  sonoridad igualada (BS.1770), la gente eligió el ambiente atrás a una mediana de **−10,2 dB** respecto
  del frente (−5,8 dB en música instrumental, con algunos sobre 0; −14,5 en voz), y la satisfacción
  subió frente al estéreo (p < 0,001). **Rechazaron que el ambiente moviera la voz.**
- **Lo que la gente alaba en upmixers comerciales (REPORTADO):** Logic7 "deja el frente estéreo intacto
  y solo agrega envolvimiento"; Neural:X "mueve las cosas de forma poco natural en música".
- **Envolvimiento:** Riedel y Zotter (2023, VERIFICADO): **4 parlantes horizontales ya envuelven más que
  el estéreo**; llegadas desde distintos lados con **≤ 20 ms** entre sí dan envolvimiento; a 100 ms se
  oyen como eventos separados. Griesinger (VERIFICADO): el envolvimiento viene de las fluctuaciones
  interaurales que producen fuentes decorrelacionadas a ±90°, y bajo ~700 Hz importa más: **justo la
  banda que los parlantes chicos no tienen** (INFERIDO). Fuera del centro, el envolvimiento se rompe hacia
  el parlante más cercano, y las fuentes puntuales (como estos) son el peor caso (REPORTADO).
- **Separar en pistas (stems):** HTDemucs da ~7,5–9,2 dB de SDR con ~8 s de anticipación (bien para
  música en archivo; ~0,35× tiempo real en CPU); los modelos en streaming (HS-TasNet, RT-STT) tienen
  23 ms de latencia y ~1 núcleo, con 2–4 dB menos de separación (más mezcla entre pistas) (VERIFICADO).
  `SurroundUpmix` (abierto, offline) hace pistas → directo/ambiente por pista → decorrelación atrás.
- **Por qué el estéreo puede ganar con 3 parlantes:** (a) poco ambiente que trabajar; (b) copias
  correlacionadas en varios parlantes dan filtrado peine que cambia al moverse y precedencia hacia el
  más cercano: **los retardos de Haas sobre contenido correlacionado solo sirven en un punto fijo**;
  (c) la decorrelación transparente es débil por diseño (Moore y Hill 2018, VERIFICADO: umbrales de
  decaimiento audibles por banda, 3,7 ms sobre 4 kHz a 180 ms bajo 63 Hz); (d) en comparaciones sin
  sonoridad igualada gana el más fuerte o el de frente más claro.

Fuentes: arxiv.org/html/2301.10210v2 (Paulus y Torcoli); arxiv.org/abs/2402.17701 y el PDF de
L-Acoustics (HS-TasNet); arxiv.org/pdf/2511.13146 (RT-STT); He y Gan (sigport); arxiv.org/pdf/2206.02125
(Riedel y Zotter); DAFx 2023 paper 3; Moore y Hill, JAES 2018; Griesinger (akutek.info); mixxx.org
(Demucs a ONNX); github.com/final-wav/SurroundUpmix; foros de audiosciencereview y avsforum
(REPORTADO); github.com/kronihias/dbap.

## 5. Qué busca construir este proyecto (síntesis, INFERIDO)

**Una consola espacial doméstica para parlantes Bluetooth baratos**: toma lo que suena en el PC, lo
convierte en un canal distinto por parlante pensado para **envolver a quien se mueve por la pieza**, y
mantiene esos parlantes **sincronizados midiendo el sonido**, con un panel que deja configurar todo y
explica cada perilla. De eso, lo propio y valioso es:

1. **la sincronía acústica en lazo cerrado de parlantes Bluetooth** (nadie la hace);
2. **el render para un oyente que se mueve** (nadie lo apunta así; los upmixers apuntan a un punto);
3. **la honestidad de medir**: A/B ciego con sonoridad igualada, métricas sobre la señal exacta.

Lo commodity (EQ, retardos fijos, mezcla, binaural, transporte por red, panel genérico) conviene
tomarlo de afuera cuando empiece a costar.

## 6. Opciones de reenfoque, con su costo

| Opción | Qué cambia | Gana | Cuesta |
|---|---|---|---|
| **A. Mantener el rumbo, recortado** | Congelar lo commodity; adoptar `pyfar`, el binaural de PipeWire y las líneas de base; centrar el trabajo en el lazo de sincronía y el render | Menos código propio, el foco en lo único | Poco: migrar la calibración a `pyfar` con sus tests |
| **B. Contenido distinto de verdad** | Probar pistas separadas (HTDemucs con anticipación para música local) y la regla "nunca peor que el estéreo" (el par de adelante intacto, ambiente fuerte en el resto, sin Haas sobre el directo) | Es lo que la literatura dice que **se nota** | Un piloto offline de una tarde; si gana, CPU y latencia en vivo |
| **C. Sacar el Bluetooth del host** | Una Raspberry Pi por parlante con Snapcast o Sendspin (cable o Bluetooth local), el canal elegido en cada cliente | Reloj resuelto a < 0,2 ms por un proyecto maduro; los microcortes de la radio del host desaparecen | Hardware por parlante; el lazo acústico sigue siendo propio |
| **D. Auracast** | Seguir con E4 (ya hay pila Linux probada con un Go 4 en otro controlador) | Sincronía por construcción si el JBL reproduce su BIS | Hardware (un controlador con `iso-broadcaster` o las SuperMini); la pregunta abierta |

**Recomendación (INFERIDO):** **A + B ahora** —recortar y probar lo que más se oye—, con el A/B del
experimento 17 ampliado a: estéreo solo, clásico, espacial, "nunca peor que el estéreo",
`surround` de FFmpeg, `psd` de PipeWire y un piloto de pistas. **C o D** según lo que diga el
experimento 12 sobre los microcortes: si la radio del host es la culpable, sacar el Bluetooth del host
(C) o Auracast (D) atacan la causa.

## 7. Lo que esto cambia en lo construido

- **El modo espacial** (2026-10-04) aplica Haas solo al **ambiente**, no al directo, y conserva el
  directo ubicado: va en la línea de lo que recomienda §4. Pero el ambiente extraído de mezclas de
  estudio es poco (−10 a −15 dB): la perilla de nivel de ambiente debería llegar a ~0 dB para música
  instrumental (hoy llega a +10 dB de balance; INFERIDO que alcanza) y conviene una variante "par de
  adelante intacto".
- **Experimento 17** suma las líneas de base y el piloto de pistas, y mide en **tres posiciones**,
  una al lado de un parlante (la precedencia se juega ahí).
- **Monitor de audífonos**: con la `filter-chain` SOFA de PipeWire, no con DSP propio.
