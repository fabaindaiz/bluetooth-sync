# Software de audio en el PC: captura, canales y surround simulado

Investigación del 2026-09-26. Es la séptima línea de la fase de investigación (ver
[README](README.md)). Responde la parte del PC, que está **antes del emisor**:
- de dónde sale el audio;
- si se puede interceptar todo lo que suena en el equipo;
- cómo se separan y rutean los canales;
- cómo se simula surround cuando la fuente es estéreo;
- cuánto cuesta cada opción en latencia y en facilidad de uso, en Linux, macOS y
  Windows.

**Qué significa cada marca:**
- **VERIFICADO**: fuente primaria (código fuente, documentación oficial,
  especificación, norma o paper).
- **REPORTADO**: foro, blog, issue, reseña o página de un fabricante sobre su
  producto.
- **INFERIDO**: deducción que nadie comprobó.

Nada de esto se ha medido todavía. Tres agentes de investigación reunieron las
fuentes. Para PipeWire leyeron el código del tag 1.6.9, porque gitlab.freedesktop.org
bloqueó el acceso y usaron el espejo `github.com/PipeWire/pipewire`. Esta sesión no
volvió a abrir cada fuente.

## Resumen

- **Sí, se puede interceptar casi todo el audio del PC** en los tres sistemas.
  - **Linux** es el caso más limpio: un sink virtual de PipeWire declarado como
    salida por defecto recibe lo que mandan las apps Pulse, ALSA (por
    pipewire-alsa), JACK, Flatpak, navegadores y Steam/Proton.
  - **macOS** lo hace con BlackHole (gratis) o Loopback (US$99).
  - **Windows** lo hace con VB-CABLE o Voicemeeter (donationware).
  - **Lo que queda fuera en todos:**
    - el modo exclusivo;
    - las apps que abren el hardware directo;
    - el **bitstream** Dolby/DTS (passthrough), que no es PCM y no se puede mezclar
      ni repartir;
    - quizás parte del contenido con DRM. Los reportes se contradicen y hay que
      probarlo por servicio.
- **La mayoría de las fuentes son estéreo.** Spotify, YouTube, Prime Video,
  Disney+, y Netflix en Linux y Mac entregan 2 canales. Por eso el **upmix** es
  la pieza que más se usará.
  - Multicanal real en el PC solo hay en cuatro casos:
    - archivos locales decodificados por mpv, VLC o Kodi;
    - **juegos**, que mezclan a 4.0 nativo si el dispositivo se declara quad;
    - Apple Music con Atmos en Mac;
    - Netflix en Edge o la app de Windows, con reportes que se contradicen.
- **Upmix gratis que funciona en todo el sistema:**
  - **Linux:** `channelmix` de PipeWire, filter-chain o el filtro `surround` de
    ffmpeg dentro de PipeWire (≥1.6.0).
  - **Windows:** Voicemeeter (modo UP MIX 4.1) o Equalizer APO.
  - **macOS:** nada con interfaz gráfica. Solo ffmpeg `surround` en una tubería, o
    plugins AU pagados (US$75–799).
- **Tres hallazgos cambian el plan de upmix del roadmap** (§4.2):
  1. el upmix de PipeWire **viene apagado en la práctica** desde 0.3.68: el flag
     `channelmix.upmix` arranca en true, pero el método arranca en `none`, así
     que no genera ningún canal;
  2. el modo `psd` manda a los dos traseros **la misma señal (L−R) en
     contrafase**, así que no hay traseros estéreo y lo centrado queda en
     silencio atrás;
  3. tanto en PipeWire como en FFmpeg, **"4.0" no es cuadrafonía**: significa
     FL FR FC + trasero central. La cuadrafonía se llama `Quad` en PipeWire y
     `quad` en FFmpeg.
- **Bumble acepta 1 o 2 canales por fuente, y su entrada `device:` es solo
  estéreo** (ya estaba en [05](05-opcion-a-bumble.md) §5). Para 4 canales sin
  parche se puede partir el audio en dos FIFO estéreo, pero la BASE sigue
  necesitando el parche de índices y ubicaciones (§8).
- **La latencia no importa en música, importa en video y es un problema en
  juegos.** Al interceptar el audio del sistema, el reproductor no sabe cuánto
  tarda la cadena hasta los parlantes.
  - Auracast suma unos 70–120 ms más los búferes del PC (INFERIDO de los presets).
  - La tolerancia de lip-sync es de +45/−125 ms para que se note (ITU-R BT.1359).
  - Una hipótesis que vale la pena probar (§7): declarar la latencia en el sink
    virtual de PipeWire para que Chrome, Firefox y mpv la compensen solos.
- **Entre los parlantes del par frontal hace falta menos de 1 ms de desfase**; con
  más, la imagen estéreo se corre hacia el que suena primero. Entre frontales y
  traseros, un atraso de 10–20 ms del trasero no hace daño y hasta ayuda (§7.2).
  Esto refuerza lo de un solo BIG (d-7c8794-203de2).
- **Por sistema operativo:**
  - **Linux es el más capaz** y todo es gratis, pero se configura con archivos.
  - **macOS sirve hoy como estación de prueba**: BlackHole + ffmpeg + Bumble por
    `serial:` con las SuperMini.
  - **Windows es el más fácil con interfaz gráfica** (Voicemeeter), pero
    python-sounddevice no captura por WASAPI loopback, así que Bumble necesita un
    cable virtual en medio.

## 1. La cadena en el PC

```
apps ──► sink virtual (2 o 4 canales, salida por defecto)
             │  si la fuente es estéreo: upmix a quad
             ▼
         captura (monitor del sink / BlackHole / VB-CABLE)
             ▼
         separación por canal (si el emisor pide 1–2 canales por fuente)
             ▼
         emisor: Bumble (stdin / file: / FIFO), PipeWire bap_bcast_source,
                 o combine-stream hacia varios sinks A2DP
```

**En Linux hay un atajo** (INFERIDO): si el emisor es el nodo `bap_bcast_source`
de PipeWire, o un combine-stream, las apps tocan directo un sink de PipeWire. En
ese caso no hay captura ni tubería, y PipeWire conoce la latencia del camino
completo.

## 2. ¿Se puede interceptar cualquier fuente de audio?

### 2.1 Linux (PipeWire 1.6.x)

**Sink virtual quad.** Se declara con un archivo en
`~/.config/pipewire/pipewire.conf.d/`. VERIFICADO: sintaxis en
`src/daemon/pipewire.conf.in` de PipeWire 1.6.9 y en la ArchWiki.

```
context.objects = [
  { factory = adapter
    args = {
      factory.name     = support.null-audio-sink
      node.name        = "jbl_quad"
      node.description = "JBL 4.0"
      media.class      = Audio/Sink
      audio.position   = [ FL FR RL RR ]   # o audio.layout = "Quad" (≥1.6.0); nunca "4.0"
      monitor.channel-volumes = true       # sin esto, el volumen del sink no llega al monitor
    } } ]
```

**Qué hay que saber de este archivo:**
- `audio.layout` existe desde 1.5.82, es decir, desde la 1.6.0 estable.
  VERIFICADO: `NEWS`.
- **`"4.0"` en PipeWire es `FL FR FC RC`, no cuadrafonía.** VERIFICADO:
  `spa/include/spa/param/audio/layout.h`.
- Por defecto el monitor entrega la señal **sin el volumen del sink**
  (`monitor.channel-volumes=false`). Si no se cambia, las teclas de volumen no
  afectan lo que se captura. VERIFICADO:
  https://docs.pipewire.org/page_man_pipewire-props_7.html
- El null-sink **solo acepta PCM** (U8…F64, sin IEC958). Además es *driver*, con
  su propio reloj `clock.system.monotonic`. VERIFICADO:
  `spa/plugins/support/null-audio-sink.c`.
  - **Consecuencia (INFERIDO):** si Bumble lee del monitor, el reloj del PC y el del
    controlador Bluetooth derivan entre sí, y alguien tiene que absorber esa
    diferencia. Es el mismo problema de drift de [05](05-opcion-a-bumble.md) §8.
- **Para dejarlo como salida por defecto:** `wpctl set-default <id>`, que guarda la
  preferencia, o `priority.session` en el nodo. VERIFICADO:
  https://pipewire.pages.freedesktop.org/wireplumber/daemon/configuration/settings.html
  (WirePlumber 0.5.17).
- **Variante con procesamiento:** en lugar del null-sink, un
  `libpipewire-module-loopback` o un `filter-chain` con
  `capture.props.media.class = Audio/Sink`. La documentación trae ejemplos de
  "upmix sink". VERIFICADO: https://docs.pipewire.org/page_module_loopback.html
  - Los `.conf` de `/usr/share/pipewire/filter-chain/` dicen "copiar a
    `filter-chain.conf.d/`". Eso solo funciona si corre el servicio de usuario
    `filter-chain.service`; si no, van en `pipewire.conf.d/`. VERIFICADO:
    `src/daemon/systemd/user/filter-chain.service.in`.

**Qué apps quedan capturadas:**
- las de PulseAudio (navegadores, Flatpak, Steam);
- las de ALSA, por el PCM `pipewire`/`default` de pipewire-alsa;
- las de JACK, por pipewire-jack;
- las nativas de PipeWire.

La conversión de canales la hace el adaptador de cada stream y se ajusta en
`client.conf`, `pipewire-pulse.conf` y `jack.conf`. VERIFICADO: sección
"CUSTOMIZING PROPERTIES" de pipewire-props(7).

**Qué no se captura así:**
- Las apps que abren `hw:X` directo, porque se saltan PipeWire. INFERIDO.
- Los streams con `node.exclusive`, que PipeWire hace cumplir desde 1.6.0.
  VERIFICADO: `NEWS` 1.5.81.
- El **passthrough IEC958/IEC61937** (AC-3, DTS, TrueHD). Solo existe hacia sinks
  ALSA con `iec958.codecs`, y el null-sink no acepta esos formatos. VERIFICADO:
  pipewire-props(7) y el código del null-sink.
  - VLC sí pide `PA_ENCODING_AC3/EAC3/DTS_IEC61937`. VERIFICADO:
    https://github.com/videolan/vlc/blob/3.0.x/modules/audio_output/pulse.c
  - En el sink virtual, VLC y Kodi tienen que decodificar a PCM (INFERIDO).
- Las apps con un dispositivo fijado (`target.object`, `node.dont-reconnect`).
  INFERIDO.

**Por aplicación:**
- Los streams se mueven con pavucontrol, qpwgraph o `pw-link`. WirePlumber respeta
  `target.object` y los movimientos vía metadata (`linking.allow-moving-streams`,
  `node.stream.restore-target`). VERIFICADO: settings de WirePlumber.
- **Reglas persistentes:**
  - `pulse.rules` en `pipewire-pulse.conf.d` y `stream.rules` en `client.conf.d`,
    con `matches` por `application.name` o `application.process.binary` y
    `update-props`. VERIFICADO: `pipewire-pulse.conf.in`, `client.conf.in`.
  - `stream.rules` de WirePlumber. VERIFICADO:
    https://pipewire.pages.freedesktop.org/wireplumber/daemon/configuration/stream.html
  - Que poner `target.object` dentro de `update-props` rutee de forma fiable una
    app concreta es INFERIDO: no está documentado de forma explícita.

**Sacar el audio hacia un proceso externo (Bumble):**
```
pw-record --target jbl_quad -P '{ stream.capture.sink = true }' \
  --raw --rate 48000 --channels 4 --channel-map FL,FR,RL,RR \
  --format s16 --latency 10ms - | <emisor>
```
- En 1.6.9 existen `--raw`, `-` a stdout, `--latency`, `-P` y `--format`.
  VERIFICADO: `src/tools/pw-cat.c`.
- `stream.capture.sink` es la propiedad que captura la salida de un sink.
  VERIFICADO: `src/pipewire/keys.h`.
- La opción `-C/--monitor` **solo está en master**, no en 1.6.9. VERIFICADO: diff
  entre master y 1.6.9.
- Alternativa con la interfaz de Pulse: `parec -d jbl_quad.monitor --raw
  --format=s16le --rate=48000 --channels=4
  --channel-map=front-left,front-right,rear-left,rear-right`. INFERIDO.
- CamillaDSP también lee de PipeWire y escribe a `Stdout`. VERIFICADO:
  https://github.com/HEnquist/camilladsp

### 2.2 macOS

**BlackHole:**
- Hay versiones de 2, 16, 64, 128 y 256 canales (`brew install blackhole-16ch`).
- "Zero additional driver latency", de 8 a 768 kHz.
- Licencia GPL-3.0, con licencia comercial para proyectos que no son GPL.
- Se deja como salida por defecto desde Configuración de Audio MIDI. Para escuchar
  a la vez, se arma un Multi-Output con corrección de deriva en todos los
  dispositivos menos el que da el reloj.
- VERIFICADO: https://github.com/ExistentialAudio/BlackHole

**Configurar altavoces** (Configuración de Audio MIDI):
- Ofrece Estéreo, 5.1, 7.1, 7.1 Rear y 7.1.4, con un canal asignado a cada
  parlante. VERIFICADO:
  https://support.apple.com/guide/audio-midi-setup/set-external-speakers-stereo-surround-sound-ams1005/mac
- **No hay "cuadrafonía" en la lista.** Para 4.0 hay que usar 5.1 y dejar C y
  LFE sin uso, o mapear los canales a mano. INFERIDO.

**Rogue Amoeba** (precios VERIFICADOS en sus páginas de compra):
- **Loopback, US$99.** Dispositivos virtuales de hasta 64 canales y captura por
  app; macOS 14.5–27. Pero, en palabras del fabricante: *"Rogue Amoeba's
  applications use stereo audio pipelines, rather than multi-channel audio formats
  such as 5.1"*. El multicanal solo pasa en modo "Pass-Thru".
  - https://rogueamoeba.com/loopback/buy.php
  - https://rogueamoeba.com/support/knowledgebase/?showArticle=Misc-Audio-Channel-Handling&product=Loopback
- **Audio Hijack, US$69.** Captura por app o de todo el sistema.
- **SoundSource, US$49.** Salida y efectos por app, con Audio Units.
- **Consecuencia (INFERIDO):** como la cadena de Rogue Amoeba es estéreo, un AU
  de upmix dentro de SoundSource o Audio Hijack no puede producir 4 canales reales.

**Core Audio Process Taps:**
- `AudioHardwareCreateProcessTap` existe desde **macOS 14.2**.
- Requiere `NSAudioCaptureUsageDescription`, y el sistema pide permiso de
  grabación de audio del sistema la primera vez.
- `muteBehavior` puede silenciar el Mac mientras se captura.
- Capturan tres cosas:
  - la mezcla global, excluyendo procesos;
  - una lista de procesos;
  - desde macOS 26, filtrando por `bundleIDs`.
- VERIFICADO:
  - https://developer.apple.com/documentation/coreaudio/capturing-system-audio-with-core-audio-taps
  - el header `CATapDescription.h` del SDK de macOS 27.
- **Multicanal:** con `mixdown=false` sobre un dispositivo de 4 u 8 canales
  deberían llegar los canales separados. INFERIDO del header, sin probar.
- Hay que programarlos. El ejemplo es AudioCap (REPORTADO):
  https://github.com/insidegui/AudioCap

**Otras vías de macOS:**
- **ScreenCaptureKit** (macOS 13+) **no sirve para multicanal**: `channelCount`
  solo admite 1 o 2. VERIFICADO:
  https://developer.apple.com/documentation/screencapturekit/scstreamconfiguration/channelcount
- **Dispositivo agregado / Multi-Output:** la corrección de deriva remuestrea
  cada dispositivo que no esté sincronizado por hardware. VERIFICADO:
  https://support.apple.com/guide/audio-midi-setup/set-aggregate-device-settings-ams094c7edb4/mac
  - **No compensa la latencia fija de cada parlante Bluetooth.** REPORTADO:
    https://discussions.apple.com/thread/7925534
  - Para la ruta A2DP: un agregado de 4 parlantes daría 8 canales con uno
    asignado a cada parlante, pero sin retardo por dispositivo. INFERIDO.

### 2.3 Windows 10/11

**WASAPI loopback:**
- Captura la mezcla que el motor manda a un dispositivo de salida.
- *"Exclusive-mode streams cannot operate in loopback mode"*.
- Un driver de confianza no deja capturar contenido protegido.
- VERIFICADO: https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording

**Process loopback** (por proceso, desde Windows 10 build 20348):
- Incluye o excluye un árbol de procesos. VERIFICADO:
  https://learn.microsoft.com/en-us/windows/win32/api/audioclientactivationparams/ns-audioclientactivationparams-audioclient_process_loopback_params
- `GetMixFormat` devuelve E_NOTIMPL, y el ejemplo usa estéreo fijo; el multicanal
  no está documentado. REPORTADO:
  https://learn.microsoft.com/en-us/answers/questions/1125409/

**VB-CABLE** (donationware):
- La entrada "Speaker" se configura hasta 7.1, y hay hasta 16 canales en MME,
  WASAPI y KS.
- La tasa interna es fija (48 kHz por defecto).
- Latencia máxima = 3 × búfer (7168 muestras por defecto).
- VERIFICADO: https://vb-audio.com/Cable/VBCABLE_ReferenceManual.pdf

**Voicemeeter Banana / Potato** (donationware):
- Banana tiene 3 salidas físicas (A1–A3) y 2 virtuales; Potato, 5 y 3. Cada bus
  lleva 8 canales.
- **Modos de bus:**
  - **UP MIX 4.1**: FL=L, FR=R, RL=L, RR=R, SW=50%(L+R);
  - **UP MIX TV**: 7.1 desde estéreo;
  - LEFT ONLY, RIGHT ONLY, CENTER ONLY, LFE ONLY y REAR ONLY.
- Con esto se puede mandar un canal a cada parlante Bluetooth conectado a A1, A2
  y A3, que es la ruta A2DP.
- El propio manual advierte: *"Output A1, A2, A3… are not exactly synchronized"*.
  Se compensa con un retardo por salida.
- VERIFICADO: https://vb-audio.com/Voicemeeter/VoicemeeterBanana_UserManual.pdf

**Equalizer APO** (GPL, gratis):
- `Copy:` reemplaza un canal por una suma de canales de origen. VERIFICADO:
  https://sourceforge.net/p/equalizerapo/wiki/Configuration%20reference/
- **Para upmix:**
  - hay que instalarlo en *pre-mix*, sobre un dispositivo configurado en 5.1.
    REPORTADO: https://sourceforge.net/p/equalizerapo/discussion/general/thread/a574b2869d/
  - también funciona sobre VB-CABLE en 7.1, que es como lo usa HeSuVi.
    REPORTADO: https://sourceforge.net/p/hesuvi/wiki/Help/
- Con dispositivos Bluetooth hay fallas conocidas. REPORTADO:
  https://sourceforge.net/p/equalizerapo/tickets/189/

**Ruteo por app:**
- El mezclador de Windows 11 elige la salida de cada app. VERIFICADO.
- EarTrumpet (código abierto) hace lo mismo con otra interfaz. REPORTADO.

**"Shared audio" de Windows 11 (LE Audio):**
- Manda **el mismo** audio a 2 accesorios, solo en PCs Copilot+. No reparte
  canales, así que no sirve. VERIFICADO:
  https://blogs.windows.com/windows-insider/2025/10/31/extending-bluetooth-le-audio-on-windows-11-with-shared-audio-preview/
- Coincide con d-7c8794-1b2706 (Windows descartado como emisor para los JBL).

### 2.4 Qué no se puede interceptar en ningún sistema

| Caso | Por qué | Marca |
|---|---|---|
| Modo exclusivo (WASAPI exclusive, `node.exclusive`, `hw:` directo) | El stream se salta el mezclador del sistema | VERIFICADO (Windows, PipeWire) / INFERIDO (`hw:`) |
| Bitstream Dolby/DTS (IEC 61937, "passthrough") | Va comprimido para que otro equipo lo decodifique. No se puede mezclar, repartir, retardar ni subir de canales sin decodificarlo antes; la solución es que el reproductor decodifique a PCM. El demuxer `spdif` de FFmpeg desempaqueta AC-3, E-AC-3 y DTS core, pero no TrueHD/MAT ni DTS-HD, y los dispositivos virtuales no anuncian formatos comprimidos | VERIFICADO (https://kodi.wiki/view/Settings/System/Audio, `libavformat/spdifdec.c`) / INFERIDO (conclusión) |
| DRM en Windows (PUMA) | El contenido puede exigir "trusted audio drivers" o desactivar salidas (`MFPROTECTION_TRUSTEDAUDIODRIVERS`) | VERIFICADO que el mecanismo existe (https://learn.microsoft.com/en-us/windows/win32/coreaudio/protected-user-mode-audio--puma-) |
| DRM en la práctica | Contradictorio. OBS con Netflix dio sonido y pantalla negra (2017), y otro hilo dice que desde 2021 se bloquean ambos. En el foro de VB-Audio dicen que VB-CABLE funciona con Netflix y Equalizer APO no. En Mac, Apple Music se captura por BlackHole. Lo más probable es que el DRM proteja sobre todo el video (INFERIDO) | REPORTADO: [OBS 2017](https://obsproject.com/forum/threads/recording-netflix-get-sound-but-only-black-screen-video.74657/), [OBS 2021](https://obsproject.com/forum/threads/netflix-blocking-obs-studio-from-recording-video-and-sound-in-firefox-edge-and-chrome.150847/), [VB-Audio](https://forum.vb-audio.com/viewtopic.php?t=1207), [mac2roon](https://github.com/diro/mac2roon) |

## 3. Qué fuentes entregan multicanal

| Fuente | Plataforma | Canales que entrega | ¿Se captura? | Marca |
|---|---|---|---|---|
| Netflix | Windows (Edge o app) | 5.1 DD+, con Dolby Access según la ayuda oficial. Un blog dice que la app actual solo da estéreo | Contradictorio; hay que probar | VERIFICADO el 5.1 (https://help.netflix.com/en/node/14163) / REPORTADO la captura |
| Netflix | Linux (Chrome, Firefox), Mac (Safari) | Estéreo. Chromium solo decodifica AC-3/E-AC-3 con el decodificador del sistema, y en Linux no hay | Probablemente sí | INFERIDO por omisión; REPORTADO (https://github.com/cjw1115/enable-chromium-ac3-ec3-system-decoding) |
| Prime Video | Navegadores y apps de Windows y Mac | Estéreo | Probablemente sí | VERIFICADO (https://www.primevideo.com/help?nodeId=GUX9FYHU5D8LC9EJ) |
| YouTube | Navegador de PC | Estéreo; el 5.1 es solo para TV, consolas y streamers | Sí | VERIFICADO por omisión (https://support.google.com/youtube/answer/11904456) |
| Disney+ | Navegador | Estéreo | Probablemente sí | REPORTADO |
| Max | Navegador | No determinado | No determinado | — |
| Spotify | Escritorio | Estéreo; con Windows en 5.1 suenan solo los frontales | Sí | REPORTADO (https://community.spotify.com/t5/Desktop-Windows/Spotify-desktop-only-uses-the-2-front-speakers-on-a-5-1-setup/td-p/5011541) |
| Tidal, Amazon Music | Escritorio | Estéreo (sin Atmos en PC) | Sí | REPORTADO |
| Apple Music | Mac | Atmos hasta 7.1.4 PCM, si el dispositivo tiene 12 canales configurados. Hay reportes de que a veces da solo 5.1 | Reportes contradictorios con BlackHole: una guía dice que sí, [una discusión](https://github.com/ExistentialAudio/BlackHole/discussions/663) dice "no sound in any channels" | REPORTADO (https://developer.apple.com/forums/thread/702864, https://apl-hud.com/virtuoso-standalone-setup/) |
| Apple TV app | Mac | 5.1 PCM, o bitstream por HDMI | No determinado | REPORTADO |
| Archivos AC-3, E-AC-3, DTS, DTS-HD MA, TrueHD | mpv, VLC, Kodi (FFmpeg) en los tres sistemas | Hasta 7.1 PCM | Sí | VERIFICADO (https://kodi.wiki/view/PulseAudio, https://github.com/foo86/dcadec) |
| Atmos y DTS:X (cama) | FFmpeg | Solo la cama 5.1/7.1; los objetos se ignoran | Sí | REPORTADO (https://forum.kodi.tv/showthread.php?tid=373441) |
| Atmos (objetos) | Cavern | Renderiza a cualquier distribución, **incluida 4.0**, en tiempo real. No hace DTS:X; su licencia desaconseja el uso comercial | Sí | VERIFICADO (https://github.com/VoidXH/Cavern) |
| Juegos | XAudio2, FMOD, Wwise (Windows); Wine/Proton, OpenAL Soft (Linux) | Lo que declare el dispositivo: quad, 5.1 o 7.1 | Sí | VERIFICADO (XAudio2, OpenAL Soft, Wine) / REPORTADO (FMOD, Wwise) |

**Juegos en Linux** (VERIFICADO, código):
- winepulse lleva el mapa de canales del sink a la configuración de Windows más
  cercana. Un sink quad pasa a `KSAUDIO_SPEAKER_QUAD`, y el juego mezcla él mismo
  para 4 parlantes. Fuente: `dlls/winepulse.drv/pulse.c` (`convert_channel_map`)
  en https://github.com/wine-mirror/wine
- OpenAL Soft acepta `channels = quad` y hace paneo 3D real a 4 parlantes:
  https://github.com/kcat/openal-soft/blob/master/alsoftrc.sample

**Consecuencia (INFERIDO):** si el sink se declara quad, los juegos entregan 4.0
nativo sin upmix. Es la fuente donde el surround simulado sale mejor.

## 4. Surround simulado: el upmix

### 4.1 Algoritmos

| Nombre | Tipo | Calidad | ¿Tiempo real? | ¿Libre? | Dónde | Marca |
|---|---|---|---|---|---|---|
| Matriz pasiva (Dolby Surround) | S = L−R, paso-bajo de 7 kHz y retardo para los traseros; centro fantasma | Básica; hay fuga de los frontales hacia atrás | Sí | Sí | Cualquier DSP | VERIFICADO ([Dressler, Dolby](https://educypedia.org/library/208_Dolby_Surround_Pro_Logic_Decoder.pdf)) |
| PipeWire `psd` ("Passive Surround Decoding") | Matriz pasiva: traseros con L−R en contrafase, retardo y Hilbert opcional (§4.2) | Básica | Sí | Sí | `channelmix` | VERIFICADO (código) |
| Dolby Pro Logic II | Matriz activa con steering; traseros estéreo de banda completa; modo Music con Center Width y Panorama; 15 ms de retardo | Buena, estándar de la industria | Sí | **No** (licencia Dolby) | Receptores AV | VERIFICADO ([manual de mezcla](http://decoy.iki.fi/dsound/ambisonic/motherlode/source/PL_II_Mixing%20manual.pdf)) |
| FreeSurround (Christian Kothe) | En frecuencia: FFT por bloques, y por cada bin estima la posición con la diferencia de amplitud y fase entre L y R. Bloque de 4096 | Buena, según la comunidad | Sí (~85 ms) | Sí (GPL-2) | foobar2000 (`foo_dsp_fsurround`), Dolphin | VERIFICADO ([código en Dolphin](https://github.com/dolphin-emu/dolphin/tree/master/Externals/FreeSurround)) |
| FFmpeg `surround` | En frecuencia (STFT), el mismo principio que FreeSurround; `win_size` de 4096 por defecto. Salidas `quad`, 5.1, 7.1 y otras; con `lfe_low/high`, `angle`, `focus` y ganancias por canal | Similar a FreeSurround (INFERIDO) | Sí (~85 ms; ~21 ms con ventana de 1024, perdiendo resolución) | Sí (LGPL) | ffmpeg, mpv (`--af=lavfi=[surround=chl_out=quad]`), filter-chain de PipeWire ≥1.6.0 | VERIFICADO ([`af_surround.c`](https://github.com/FFmpeg/FFmpeg/blob/master/libavfilter/af_surround.c), [filtros](https://ffmpeg.org/ffmpeg-filters.html#surround)); latencia INFERIDA |
| Avendano y Jot (JAES 2004) | En frecuencia: extrae el ambiente por coherencia entre canales | Referencia académica | Sí | Es un paper, no una herramienta | — | VERIFICADO ([resumen](https://www.semanticscholar.org/paper/A-Frequency-Domain-Approach-to-Multichannel-Upmix-Avenda%C3%B1o-Jot/aa9d75d26af87b022bac14125697a61db7015ddd)) |
| Nugen Halo Upmix | Propietario, con extracción de diálogo por red neuronal; salidas 4.0, 5.1, 7.1 | Alta, según el fabricante | Sí (plugin) | No (US$499) | DAW / host AU | REPORTADO (https://nugenaudio.com/haloupmix/) |
| Penteo | Propietario | Alta, según el fabricante | Sí | No (US$299–799, iLok) | DAW | REPORTADO (https://www.perfectsurround.com/) |
| Demucs + mapeo de stems (la voz al centro, etc.) | ML, separación de fuentes | Alta (SDR ~9 dB) | **No**: ~1.5× la duración en CPU. El repositorio ya no se mantiene | Sí (MIT) | Scripts | VERIFICADO (https://github.com/facebookresearch/demucs) |
| HS-TasNet y similares | ML en tiempo real | Media (SDR ~4.7–5.5 dB) | Sí (~23 ms) | Solo implementaciones no oficiales | Papers | VERIFICADO (https://arxiv.org/abs/2402.17701, https://arxiv.org/abs/2609.12201) |

**Trampa de nombres en FFmpeg:** `4.0` es FL+FR+FC+**BC** y `quad` es
FL+FR+**BL+BR**. VERIFICADO: `libavutil/channel_layout.h`. Es la misma trampa que
en PipeWire (§2.1).

### 4.2 El upmix de PipeWire por dentro

Leído en el código de 1.6.9 (`spa/plugins/audioconvert/channelmix-ops.c`,
`channelmix-ops-c.c`, `audioconvert.c`). VERIFICADO salvo donde se indica.

- **Viene apagado en la práctica.**
  - En 1.6.9 el flag `channelmix.upmix` arranca en true, pero `upmix-method`
    arranca en `none`, y los cutoffs y `rear-delay` en 0 (`audioconvert.c`
    l.4349–4354).
  - Con el método en `none`, `channelmix-ops.c` (l.249–264) no genera traseros,
    centro ni LFE, así que esos canales quedan en silencio.
  - El `NEWS` de 0.3.68 lo dice: *"Upmixing is disabled again by default"*.
  - Esta sesión lo revisó de nuevo en el código de 1.6.9, porque un agente había
    leído en la documentación que venía activo.
  - pipewire-props(7) muestra "psd / 150 / 12000 / 12" junto a cada propiedad,
    pero esos son los valores del archivo de ejemplo, no los valores por defecto.
  - **Para activarlo:** copiar `20-upmix.conf` desde
    `/usr/share/pipewire/{pipewire,pipewire-pulse,client}.conf.avail/` a los
    `*.conf.d/` que correspondan. Ese archivo pone `upmix=true`, `psd`,
    `lfe-cutoff=150`, `fc-cutoff=12000` y `rear-delay=12`.
  - **Por nodo:** en `playback.props` de un loopback. La documentación trae un
    ejemplo de "upmix sink".
- **Solo actúa** cuando la fuente es mono o estéreo y el destino tiene más canales.
- **Qué hace con cada canal:**
  - **Frentes:** L y R sin cambios, salvo `stereo-widen`, que les resta parte del
    centro.
  - **Centro:** FC = 0,707·(L+R), con paso-bajo en `fc-cutoff`. Solo si
    `fc-cutoff>0` y el destino tiene FC.
  - **LFE:** 0,5·(L+R), con paso-bajo en `lfe-cutoff`. Solo si el destino tiene
    LFE. **En quad no hay LFE, así que los graves siguen en los cuatro canales.**
  - **Traseros en `simple`:** RL = 0,707·L y RR = 0,707·R.
  - **Traseros en `psd`:** RL = +0,707·D(L−R) y RR = −0,707·D(L−R), con D = el
    retardo `rear-delay` y Hilbert opcional (`hilbert-taps` 15–255). Es la función
    `channelmix_f32_2_4_c`.
  - En destinos 7.1, los laterales son `simple` y solo los traseros son psd.
- **Consecuencias del `psd` con 4 parlantes (INFERIDO):**
  - los dos Go 4 traseros reproducen la misma señal, uno invertido respecto del
    otro;
  - todo lo mono o centrado en la mezcla (voz, bajo, bombo) desaparece atrás;
  - lo que queda atrás es solo la diferencia L−R: reverberación y lo panoramizado
    a los lados;
  - no hay traseros estéreo. Para eso hace falta `simple` o un upmix en
    frecuencia (FFmpeg `surround`, FreeSurround).
- **Los niveles están fijos en 1.6.9:** `center-level`, `surround-level` y
  `lfe-level` configurables solo existen en master.
- **Presets en `/usr/share/pipewire/filter-chain/`:**
  - **`sink-upmix-5.1-filter.conf`:** estéreo a 5.1 con builtins. Sus `links`
    cruzan `copyFL→copyOFR` y `copyFR→copyOFL`, así que **parece intercambiar L
    y R** (INFERIDO). Hay que verificarlo con un tono de prueba antes de usarlo.
    Además la salida es 5.1, y para quad hay que editarlo.
  - `sink-make-LFE.conf` y `sink-mix-FL-FR.conf`: 2→2.1 y mezcla a mono.
  - **No sirven para parlantes:**
    - `sink-virtual-surround-*` y `spatializer-7.1` son HRTF para **audífonos**
      (N→2);
    - `sink-dolby-pro-logic-ii.conf` y `sink-dolby-surround.conf` son
      **codificadores** 5.1→2, al revés de lo que se necesita.

### 4.3 Herramientas de upmix por sistema

| SO | Herramienta | Todo el sistema | Salida >2 canales | Precio / licencia | Facilidad (1–5, INFERIDO) | Marca |
|---|---|---|---|---|---|---|
| Linux | `channelmix` (`20-upmix.conf`) | Sí | Sí | MIT | 4 | VERIFICADO |
| Linux | filter-chain builtins (mixer, delay, biquad, convolver Hilbert) | Sí, como sink | Sí | MIT | 2 | VERIFICADO (https://docs.pipewire.org/page_module_filter_chain.html) |
| Linux | filter-chain + FFmpeg `surround` (tipo `ffmpeg`, ≥1.6.0, compilado con libavfilter). La doc trae de ejemplo `"[in_stereo]surround[out_5.1]"` | Sí | Sí | MIT / LGPL | 2 | VERIFICADO (FC, `NEWS` 1.5.81) |
| Linux | CamillaDSP 4.1.3: mixers, FIR/IIR y delays con cualquier número de canales; backend PipeWire nativo desde v4.0.0; GUI web. No trae upmix espectral: la matriz se arma a mano | Sí, con null-sink o reglas | Sí | GPL-3.0 | 2 | VERIFICADO (https://github.com/HEnquist/camilladsp) |
| Linux | FreeSurround.lv2: 2→5.1/7.1 vía filter-chain `lv2`; 2 estrellas, **sin archivo de licencia** | Sí | Sí | ? | 1 | VERIFICADO (https://github.com/And-Band/FreeSurround.lv2) |
| Linux | EasyEffects 8.2.9 | Sí | **No: solo FL/FR** (`pw_node_manager.cpp:731`) | GPL-3.0 | 5 | VERIFICADO (https://github.com/wwmm/easyeffects) |
| Linux | JamesDSP 2.7.0 | Sí | **No: solo FL/FR** | GPL-3.0 | 5 | VERIFICADO (https://github.com/Audio4Linux/JDSP4Linux) |
| Linux | Carla 2.5.10 | Con null-sink | Rack estéreo; Patchbay con N puertos (INFERIDO) | GPL-2.0+ | 2 | REPORTADO (ArchWiki) |
| Win | Voicemeeter, UP MIX 4.1 / UP MIX TV | Sí | Sí (8 por bus) | Donationware | 3 | VERIFICADO (manual) |
| Win | Equalizer APO en pre-mix. Hay una config de la comunidad tipo Pro Logic IIx 7.1: [Dogway](https://github.com/Dogway/emulation-random/blob/master/EqualizerAPO/Surround/Dolby_ProLogic_IIx_(7.1_upmix)_convo.txt) | Sí | Sí | GPL | 2 | REPORTADO |
| Win | foobar2000 + `foo_dsp_fsurround` | No (solo dentro de foobar) | Sí | Gratis | 3 | REPORTADO |
| Win | LAV Audio (MPC-HC) | No (solo dentro del reproductor) | Sí | Gratis | 3 | REPORTADO |
| Win | Realtek Speaker Fill / Creative CMSS-3D | Sí | Sí | Solo con ese hardware; Realtek lo retiró en algunos equipos con Win11 | — | REPORTADO |
| Win | HeSuVi, Windows Sonic, Dolby Atmos for Headphones | Sí | **No**: son virtualización para audífonos (N→2) | — | no sirven | VERIFICADO (https://learn.microsoft.com/en-us/windows/win32/coreaudio/spatial-sound) |
| Mac | Nada nativo | — | — | — | — | INFERIDO |
| Mac | FFmpeg `surround` en tubería (BlackHole → ffmpeg → emisor) o dentro de mpv/IINA | Con BlackHole | Sí | LGPL | 3 | VERIFICADO |
| Mac | Nugen Halo (US$499), Penteo (US$299–799), Dept. of Sound UpMix (US$74.99), Waves UM225/226 (~US$35) | Solo dentro de un host AU multicanal; SoundSource y Audio Hijack son estéreo | Sí | Pagados | 2 | REPORTADO (páginas de cada fabricante) |
| Mac | Boom 3D | Sí | Virtualización, sobre todo para audífonos | ~US$15–50 | 5 | REPORTADO |
| Mac | eqMac | Sí | **No** (issue #391 pide >2 canales) | Freemium | 5 | REPORTADO (https://github.com/bitgapp/eqMac/issues/391) |
| Todos | mpv / IINA con `--af=lavfi=[surround=chl_out=quad]` | No (solo lo que reproduce mpv) | Sí | GPL | 3 | VERIFICADO (https://mpv.io/manual/stable/) |

### 4.4 Qué recomiendan la norma y la literatura

- **ITU-R BS.775-4, Anexo 5 ("Upwards conversion"):**
  - un programa estéreo en un sistema con centro va solo a L y R;
  - si el programa no trae señal de surround, *"los parlantes de surround no
    deberían activarse"*;
  - si una misma señal de surround va a más de un parlante, hay que decorrelarla
    y atenuarla.
  - VERIFICADO: https://www.itu.int/dms_pubrec/itu-r/rec/bs/R-REC-BS.775-4-202212-I!!PDF-E.pdf
  - **En este proyecto (INFERIDO):** la norma es conservadora. Un upmix agresivo
    es una cuestión de gusto, y el `psd` de PipeWire manda la misma señal a los dos
    traseros **sin decorrelar**, justo lo que la norma pide evitar.
- **Retardo de los traseros:**
  - Dolby usa 15–20 ms (15 ms en Pro Logic II) y PipeWire sugiere 12 ms (fuentes
    en §4.1 y §4.2);
  - dentro de 10–30 ms, el eco puede ser hasta 10 dB más fuerte que el directo
    sin oírse como eco (Haas, 1951). REPORTADO:
    https://en.wikipedia.org/wiki/Precedence_effect. Revisión primaria: Litovsky
    et al., JASA 106(4) 1999, que no se pudo descargar.
- **Paso-bajo en los traseros:** 7 kHz en Dolby Surround, porque la fuga de los
  frontales crece con la frecuencia; Pro Logic II no lo usa. VERIFICADO:
  Dressler.
- **Sin centro (4.0):** queda un centro fantasma. BS.775 manda C a L y R a −3 dB.
  VERIFICADO: BS.775-4, Tabla 2.
- **Imágenes laterales:** entre un frontal y un trasero, son inestables. VERIFICADO
  (resumen): Theile y Plenge, JAES 1977, https://www.aes.org/e-lib/browse.cfm?elib=2335

### 4.5 Cómo repartir los canales entre 3 Go 4 y 1 Charge 6

**Los parlantes** (VERIFICADO, fichas técnicas de JBL):
- **Go 4:** un driver de 45 mm, 4,2 W RMS, 90 Hz–20 kHz.
  https://www.jbl.com/on/demandware.static/-/Sites-masterCatalog_Harman/default/dwec5ad861/pdfs/JBL%20Go%204_%20Specsheet_EN.pdf
- **Charge 6:** woofer de 53×93 mm y tweeter de 20 mm, 30 + 15 W RMS,
  56 Hz–20 kHz.
  https://www.jbl.com/on/demandware.static/-/Sites-masterCatalog_Harman/default/dwdd519eb1/pdfs/JBL_Charge_6_Specsheet_EN.pdf

**Posiciones:**
- BS.775-4 pone los frontales a ±30° y los traseros a 100–120°, y contempla las
  jerarquías 3/1 (L, C, R + un surround mono) y 2/2. VERIFICADO.
- Para quad, la costumbre es un cuadrado con los parlantes a ±45° y ±135°, pero
  no encontré una norma. REPORTADO:
  https://www.quadraphonicquad.com/forums/threads/quad-speaker-setup.16332/

**Distribuciones posibles (INFERIDO):**

| Uso | Distribución | Adelante | Atrás | Costo |
|---|---|---|---|---|
| Música y juegos | **2/2 (quad)** | Dos Go 4 como L y R: un par idéntico da una imagen simétrica | El tercer Go 4 y el Charge 6, este con el nivel bajado y paso-bajo | Los traseros no son iguales entre sí |
| Películas | **3/1 (LCRS, el formato de Pro Logic)** | Go 4 como L y R, Charge 6 al centro para anclar el diálogo | El tercer Go 4 como surround mono | El centro no suena igual que L y R |
| — | 2.1 o 3.1 con el Charge 6 como LFE | — | — | **No tiene sentido**: ninguno es subwoofer, y el Charge 6 baja solo hasta 56 Hz |

**Además (INFERIDO):**
- El Charge 6 tiene unas 10 veces la potencia de un Go 4, así que **el volumen
  máximo del sistema lo pone el Go 4**.
- Se le podría mandar al Charge 6 el bajo que los Go 4 no reproducen (menos de
  90–100 Hz), porque el bajo casi no se localiza. Pero solo funciona si el desfase
  entre parlantes es bajo (§7.2), porque si no, el bajo y el resto se separan.
- Esto responde en parte la pregunta de i-7c8794-c7ccb9 (¿4.0 o 3.1?): **4.0 para
  música y juegos, 3/1 (L C R S) para películas**, y no 3.1 con el Charge 6 como
  LFE. La elección final se hace de oído (experimento 3).

## 5. Separar y rutear canales

| Herramienta | SO | Qué hace | Facilidad (1–5, INFERIDO) | Licencia | Marca |
|---|---|---|---|---|---|
| **qpwgraph 1.0.4** | Linux | Grafo con un puerto por canal y **patchbay persistente**, que vuelve a imponer las conexiones | 3 | GPL-2.0+ | VERIFICADO (https://codeberg.org/rncbc/qpwgraph) |
| Helvum 0.6.2 | Linux | Grafo GTK sin persistencia | 3 | GPL-3.0 | VERIFICADO (Flathub) |
| Crosspipe 0.1.1 | Linux | Grafo GTK4 con drag-and-drop, inmaduro (2026-02) | 3 | GPL-3.0 | VERIFICADO (https://github.com/dp0sk/Crosspipe) |
| Coppwr 1.7.1 | Linux | Inspector avanzado de propiedades y metadata | 2 | GPL-3.0 | VERIFICADO (https://github.com/dimtpap/coppwr) |
| pavucontrol 6.1 | Linux | Mueve apps entre sinks y ajusta el volumen por canal. **No asigna el canal X a la salida Y** | 5 | GPL-2.0+ | VERIFICADO / INFERIDO |
| `pw-link`, `wpctl`, `pw-top`, `pw-record` | Linux | CLI: enlazar puertos, fijar el default, ver formato y canales por nodo, capturar. `pw-link -t` lista latencias | 2 | MIT | VERIFICADO (`src/tools/`) |
| Configuración de Audio MIDI | Mac | Configurar altavoces, agregados, Multi-Output | 3 | Incluido | VERIFICADO |
| Loopback | Mac | Dispositivos virtuales y ruteo por app con interfaz gráfica | 4 | US$99 | VERIFICADO |
| Voicemeeter | Win | Buses de 8 canales y modos LEFT/RIGHT/REAR ONLY por salida | 3 | Donationware | VERIFICADO |
| Mezclador de Windows 11, EarTrumpet | Win | Salida por app | 5 | Incluido / código abierto | VERIFICADO / REPORTADO |

**Cómo se ven los emisores en el grafo de PipeWire:**
- **combine-stream:** un nodo `Audio/Sink`, más un stream de salida por cada sink
  que coincide con `stream.rules`, cada uno con su `combine.audio.position`.
  VERIFICADO: https://docs.pipewire.org/page_module_combine_stream.html
- **`bap_bcast_source`:** el código configura cada BIS por separado
  (`configure_bis`). Si aparece como un nodo por BIS o como un nodo multicanal
  **no está determinado**, igual que en [04](04-implementaciones-y-stacks.md)
  "Correcciones". Lo resuelve E5.

## 6. Qué ven las aplicaciones cuando el sink tiene más de 2 canales

| App | Qué hace | Marca |
|---|---|---|
| **Firefox** | Toma `max_channel_count` del sink **por defecto**. Con un sink de más de 2 canales, abre los streams estéreo con N canales y deja los extra en silencio, lo que **anula el upmix de PipeWire**. El workaround reportado es cambiar `media.cubeb.output_voice_routing`; el issue se cerró en 2024-07 sin que quede claro si se corrigió | VERIFICADO (https://github.com/mozilla/cubeb-pulse-rs/blob/master/src/backend/context.rs) / REPORTADO (https://github.com/mozilla/cubeb-pulse-rs/issues/86) |
| **Chromium** | Toma los canales "nativos" de la *server info* de Pulse, y pipewire-pulse la fija en `pulse.default.position = [FL FR]`. Por eso Web Audio ve 2 canales. Un medio 5.1 sale como 5.1 y PipeWire lo remezcla | VERIFICADO (`media/audio/pulse/audio_manager_pulse.cc`, `module-protocol-pulse/pulse-server.c`) / INFERIDO (conclusión) |
| **mpv** | `--audio-channels=auto-safe` por defecto; pulse y pipewire aceptan cualquier distribución | VERIFICADO (https://github.com/mpv-player/mpv/blob/master/DOCS/man/options.rst) |
| **VLC 3** | Arma el mapa de canales con los de la fuente, hasta 9 | VERIFICADO (`pulse.c`) |
| **Kodi** | Sink PipeWire nativo desde la v20 | REPORTADO |
| **Wine/Proton** | Lleva el sink quad a `KSAUDIO_SPEAKER_QUAD` (§3) | VERIFICADO |
| **SDL3** | Lee `audio.channels` o el formato del nodo | VERIFICADO (`src/audio/pipewire/SDL_pipewire.c`) |
| **OpenAL Soft 1.25.2** | Detecta los canales del sistema, o se fijan con `channels = quad` | VERIFICADO |
| **JACK** | Un puerto por canal, sin channelmix | INFERIDO |

**Downmix automático de 5.1 a quad en PipeWire** (VERIFICADO,
`channelmix_f32_5p1_4_c`):
- `FL' = FL + 0,707·FC + 0,354·LFE`, y lo mismo en FR (el LFE se mezcla porque
  `mix-lfe=true` es el valor por defecto);
- `RL' = SL`, porque el 5.1 de PipeWire usa laterales;
- en 7.1, SL y RL se suman a 0,707.
- `channelmix.normalize=false` por defecto, así que puede recortar al pasar a s16.

**Diseño que esquiva estos problemas (INFERIDO):** dos sinks virtuales que
terminan en el mismo nodo quad.
- **"JBL quad (discreto)"**, de 4 canales, para juegos, OpenAL y archivos 5.1.
- **"JBL estéreo→quad"**, de 2 canales, con upmix, para música, navegadores y
  Firefox.

Así, Firefox y las apps que fijan los canales ven un sink estéreo y reciben
upmix, y las fuentes multicanal no pasan por el upmix.

## 7. Latencia

### 7.1 Latencia de la cadena y lip-sync

**Tolerancias** (el signo + significa que el audio va adelantado):

| Norma o estudio | Umbral | Marca |
|---|---|---|
| ITU-R BT.1359-1 | Detectable desde +45 / −125 ms; aceptable hasta +90 / −185 ms | VERIFICADO (https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.1359-1-199811-I!!PDF-E.pdf) |
| EBU R37 | Extremo a extremo +40 / −60 ms | VERIFICADO (https://tech.ebu.ch/docs/r/r037.pdf) |
| ITU-T G.114 (conversación) | <150 ms de una vía es transparente; >400 ms inaceptable | VERIFICADO |
| Juegos, JND de la latencia de audio | 49 ms (37 participantes) | VERIFICADO (https://dl.acm.org/doi/10.1145/3678299.3678331) |

**Latencia por salto en el PC:**

| Salto | Latencia | Marca |
|---|---|---|
| Quantum por defecto de PipeWire | 1024/48000 = 21,3 ms (mínimo 32, máximo 2048) | VERIFICADO (`pipewire.conf.in`) |
| Null-sink más captura del monitor | Mismo ciclo, sin quantum extra | INFERIDO |
| Nodo async (el ffmpeg de filter-chain recomienda `node.async=true`) | +1 quantum | VERIFICADO (`NEWS` 1.5.81) |
| FFmpeg `surround` | Ventana de 4096 ≈ 43–85 ms | INFERIDO del parámetro |
| Tubería a stdout | Hasta 64 KiB ≈ 170 ms si el lector se atrasa | INFERIDO |
| Core Audio + ffmpeg (Mac) | ~10–20 ms | INFERIDO |
| Motor de Windows + VB-CABLE o Voicemeeter | 10 ms de motor + 10–50 ms. Ojo: el búfer por defecto de `ffmpeg -f dshow` suele ser un múltiplo de 500 ms y hay que bajarlo | VERIFICADO (https://learn.microsoft.com/en-us/windows-hardware/drivers/audio/low-latency-audio, https://ffmpeg.org/ffmpeg-devices.html) / INFERIDO |
| `channelmix.rear-delay` | Retardo intencional **solo en los traseros**. Hay que descontarlo al medir el desfase entre parlantes | VERIFICADO |

**Latencia del enlace:**
- **Auracast** (VERIFICADO por partes, total INFERIDO):
  - trama LC3 de 10 ms + 2,5 ms de retardo algorítmico (`lc3_delay_samples` en
    https://github.com/google/liblc3/blob/main/src/lc3.c);
  - transporte ≤20 ms (preset 48_2_1) o ≤65 ms (48_2_2), según
    `bap_lc3_preset.h` de Zephyr;
  - presentation delay de 40 ms, que todo receptor Auracast debe soportar
    (https://www.bluetooth.com/wp-content/uploads/2024/05/2403_Auracast_Earbuds.pdf).
  - **Total: unos 70–120 ms, más el PC.**
- **A2DP:** 100–220 ms es lo habitual (REPORTADO). Un JBL Charge 5 midió 102 ms
  desde PC (REPORTADO: https://www.gadgetreview.com/jbl-charge-5-review).

**Quién compensa la latencia:**
- **Leen la latencia del dispositivo** (VERIFICADO, código):
  - Chrome en Linux (`pulse::GetHardwareLatency`);
  - Firefox (`pa_stream_get_latency` en cubeb);
  - mpv con PipeWire (`pw_stream_get_time_n().delay`).
  - PipeWire propaga los parámetros `Latency` y `ProcessLatency` por el grafo:
    https://docs.pipewire.org/devel/page_latency.html
  - En macOS: `kAudioDevicePropertyLatency` + `kAudioStreamPropertyLatency`.
    BlackHole permite fijar su latencia al compilar (`kLatency_Frame_Size`):
    https://github.com/existentialaudio/BlackHole/wiki/Adjust-Driver-Latency
- **Ajuste manual:** mpv `--audio-delay` (VERIFICADO), VLC y Kodi (REPORTADO).
  Los navegadores no tienen control de usuario (INFERIDO).
- **Windows:** `IAudioClient::GetStreamLatency` solo conoce la latencia del
  stream. Un cable virtual no sabe cuánto tarda el emisor Bluetooth que está más
  abajo. VERIFICADO / INFERIDO.

**Qué significa para el proyecto (INFERIDO):**
- **Música:** la latencia no importa.
- **Video:** hay que compensarla, a mano (mpv, VLC, Kodi) o declarándola.
  - **Hipótesis a medir:** si el sink virtual de PipeWire declara la latencia
    medida de la cadena (`ProcessLatency` o `latency.internal.ns`), Chrome,
    Firefox y mpv la compensarían solos.
  - En el camino A2DP esto ya ocurre en parte gracias a los delay reports
    ([03](03-bluetooth-clasico-y-sync-por-software.md) §1).
- **Juegos y videollamadas:** 70–120 ms más el PC queda por encima del JND de
  49 ms, y no se puede compensar porque el video no se puede adelantar. Auracast
  sale mucho mejor que A2DP, pero no queda transparente.

### 7.2 Desfase tolerable entre parlantes

- **Con menos de 1 ms** de diferencia entre dos parlantes se forma una imagen
  fantasma cuya posición depende del tiempo y del nivel. **Alrededor de 1 ms**, la
  imagen ya está en el parlante que suena primero. REPORTADO (Sengpiel):
  https://sengpielaudio.com/InterchannelLevelDifferencesAndInterchannelTimeDifferences1.pdf
- La fusión se mantiene hasta unos 5 ms con clicks y 40–50 ms con voz y música. El
  eco se separa por encima de unos 50 ms en voz y 100 ms en música. REPORTADO:
  https://en.wikipedia.org/wiki/Precedence_effect
- **En este proyecto (INFERIDO):**
  - **Par frontal (L/R):** hay que alinearlo a **menos de 1 ms**, idealmente a
    menos de 0,1–0,2 ms. El desfase de ~5–20 ms que se espera de A2DP
    ([03](03-bluetooth-clasico-y-sync-por-software.md) §5) destruye la imagen
    estéreo aunque la música se siga oyendo junta. Es un argumento más a favor de
    un solo BIG (d-7c8794-203de2) y de medir con micrófono en E5.
  - **Traseros:** un **atraso** de 10–20 ms es inofensivo y hasta deseable. Un
    **adelanto** de más de 1 ms tira la imagen hacia atrás.

## 8. Cómo entra el audio a Bumble

Revisado en `bumble/audio/io.py` (commit 32bb7cd) y `apps/auracast.py`.
VERIFICADO:
- **Entradas:**
  - `stdin`, una sola fuente;
  - `file:` con PCM crudo (`int16le|float32le,<Hz>,<canales>`);
  - `.wav`;
  - `device:N`.
- **`device:` abre `sounddevice` con los canales pedidos, pero siempre declara
  2**, así que con 4 u 8 no cuadra el tamaño de la trama. Coincide con
  [05](05-opcion-a-bumble.md) §5.
- **Cada fuente acepta 1 o 2 canales** ("Only 1 and 2 channels PCM configurations
  are supported").
- **Con varias fuentes** (`[[broadcasts]]` / `[[sources]]` en TOML), la app suma
  los canales en **un solo BIG** (`num_bis=channel_count`), con un subgrupo por
  fuente. Eso respeta d-7c8794-203de2.
- **En Windows, python-sounddevice no ofrece WASAPI loopback.** Solo tiene
  `exclusive` y `auto_convert`, y el issue #281 sigue abierto. Hay que leer desde
  un cable virtual.
  - https://python-sounddevice.readthedocs.io/en/latest/api/platform-specific-settings.html
  - https://github.com/spatialaudio/python-sounddevice/issues/281

**Dos formas de darle 4 canales:**
1. **Parchear Bumble** para que acepte N canales por fuente. Es el parche de
   [05](05-opcion-a-bumble.md) §6, y la entrada queda en `stdin` con
   `int16le,48000,4`.
2. **Sin parchear la entrada** (INFERIDO): ffmpeg o sox leen el dispositivo o el
   monitor y escriben 2 FIFO estéreo, que se pasan como dos fuentes `file:`.
   - No se sabe si `open()` sobre un FIFO funciona con el lector de Bumble.
   - **Esto no evita el parche de la BASE.** Los índices de BIS de cada subgrupo
     empiezan en 1 y las ubicaciones quedan en FRONT_LEFT/FRONT_RIGHT
     ([04](04-implementaciones-y-stacks.md) opción (a)). Cada JBL vería dos pares
     FL/FR iguales.

**Cadenas completas por sistema (INFERIDO; ninguna probada):**
- **Linux:** apps → `jbl_estereo` (upmix) y `jbl_quad` → `pw-record --raw
  --channels 4 -` → Bumble parcheado por `stdin`. La otra opción es el nodo
  `bap_bcast_source` de PipeWire, sin tubería.
- **macOS** (la estación de trabajo actual, con Bumble por `serial:` hacia una
  SuperMini): apps → BlackHole 2ch → `ffmpeg -f avfoundation -i ":<n>" -af
  surround=chl_out=quad` → Bumble por `stdin`. Para fuentes que ya son
  multicanal, BlackHole 16ch configurado en 5.1.
- **Windows:** apps → VB-CABLE en 7.1, o Voicemeeter con UP MIX 4.1 → `ffmpeg -f
  dshow` con un búfer bajo → Bumble por `stdin`.

## 9. Compatibilidad y facilidad de uso por sistema

| | Linux (PipeWire) | macOS | Windows |
|---|---|---|---|
| Capturar todo el sistema | Sí, gratis; sink virtual por archivo | Sí: BlackHole gratis, o Loopback US$99 | Sí: VB-CABLE o Voicemeeter, donationware |
| Captura por app | Sí (reglas, pavucontrol, qpwgraph) | Sí: Loopback, Audio Hijack o process taps programados | Sí: mezclador de Win11, EarTrumpet, process loopback programado |
| Sink quad nativo | Sí (`Quad`) | No: se usa 5.1 dejando C y LFE sin uso | Sí: VB-CABLE o Voicemeeter con 8 canales |
| Upmix gratis para todo el sistema | Sí (`channelmix`, filter-chain, FFmpeg `surround` dentro de PipeWire) | No; solo ffmpeg en una tubería | Sí (Voicemeeter UP MIX 4.1, Equalizer APO) |
| Upmix de calidad (en frecuencia) | FFmpeg `surround` en filter-chain; FreeSurround.lv2 (inmaduro) | FFmpeg `surround`; plugins pagados | Equalizer APO con config de la comunidad; foobar/LAV solo en su app |
| Juegos a quad nativo | Sí (Wine/Proton, OpenAL Soft, SDL3) | No determinado | Sí (XAudio2, FMOD, Wwise) |
| Interfaz gráfica | Media (qpwgraph, pavucontrol); el upmix se configura por archivo | Buena para rutear (Loopback, pago); ninguna para upmix multicanal | **La mejor gratis** (Voicemeeter), aunque el diseño es denso |
| Latencia declarada al reproductor | Sí, PipeWire la propaga (falta probar que alcance para lip-sync) | Parcial (BlackHole fija, sin probar) | No |
| Bumble como emisor | Sí (`hci-socket` o `serial:`) | Solo con un controlador externo por `serial:` ([00](experimentos/00-inventario-mac.md)) | Solo con un controlador externo; sin loopback en sounddevice |
| Emisor sin tubería | Sí (`bap_bcast_source` o combine-stream) | No | No |

**Veredicto (INFERIDO):**
- **Linux** es el único sistema donde la cadena completa, con upmix y latencia
  declarada, cabe dentro del servidor de audio sin tuberías. Es el destino natural
  del producto, como ya suponía la Fase 2 del roadmap.
- **macOS** sirve para probar la cadena hoy, con BlackHole, ffmpeg y la
  SuperMini. No sirve como producto para un usuario no experto.
- **Windows** es el más fácil de configurar a mano con Voicemeeter, pero queda
  fuera del camino de Auracast (d-7c8794-1b2706), así que solo interesa para el
  camino A2DP.

## Correcciones a documentos anteriores

- **[03](03-bluetooth-clasico-y-sync-por-software.md) §2 "Upmix"** enumera
  `channelmix.upmix`, `rear-delay`, `lfe-cutoff` y `fc-cutoff` como si
  estuvieran disponibles. **Vienen apagados**: hay que activar `20-upmix.conf`.
  Además, en 1.6.9 `psd` genera traseros en contrafase, no estéreo (§4.2).
- **Roadmap, "Upmix de estéreo a 4.0" (i-7c8794-c7ccb9)** proponía `psd` con
  retardo trasero. Con 4 parlantes, `psd` deja los traseros mudos para el
  contenido centrado y con la misma señal invertida. Hay que comparar `simple`,
  `psd` y FFmpeg `surround=chl_out=quad` de oído (experimento 3, al final). También
  hay que usar `Quad`, nunca `4.0`. Y la opción "3.1 con el Charge 6 como LFE"
  no tiene sentido (§4.5).

## Lo que no se pudo determinar

- **Servicios con DRM:** si Netflix (PlayReady por hardware en Edge), Apple TV+,
  Disney+ o Apple Music con Atmos se pueden capturar con VB-CABLE, BlackHole o
  PipeWire. Los reportes se contradicen.
- Si la app actual de Netflix para Windows sigue entregando 5.1, y qué entrega
  Max en un navegador.
- Qué canales entrega de verdad Chromium con un sink multicanal en pipewire-pulse
  (Web Audio, MSE). Se ve con `pw-top` o `pw-dump`.
- Si Firefox corrigió el issue #86 en el código o solo queda el workaround.
- Si un process tap de macOS con `mixdown=false` entrega los canales separados, y
  si el process loopback de Windows acepta más de 2 canales.
- Si la Apple TV app, Safari o los juegos de Mac mandan 5.1 PCM a un dispositivo
  virtual.
- Si Chrome, Firefox, mpv o AVPlayer respetan una latencia declarada en un sink
  virtual lo bastante bien para el lip-sync.
- Si el preset `sink-upmix-5.1-filter.conf` intercambia de verdad L y R.
- La calidad y la licencia de FreeSurround.lv2, y si Carla en modo Patchbay
  maneja N canales.
- Si `open()` sobre un FIFO funciona con la entrada `file:` de Bumble.
- Cualquier latencia real extremo a extremo con estos parlantes. Todas las cifras
  de este documento son de especificación o estimadas.

## Experimentos que esto sugiere (sin ejecutar)

1. **Canales discretos en el Mac, ya mismo.** Configurar BlackHole 16ch como 5.1,
   reproducir en mpv un WAV de prueba con un tono distinto por canal y grabarlo
   con `ffmpeg -f avfoundation` para ver si llegan separados. No requiere
   parlantes ni Bluetooth.
2. **Qué entrega cada fuente.** En Linux, con el sink quad como salida por
   defecto, anotar con `pw-top` y `pw-dump` los canales que abren Firefox,
   Chromium, Spotify, mpv con un archivo 5.1, y un juego con Proton. Repetir con
   el Netflix de Edge en Windows sobre VB-CABLE 7.1.
3. **Comparar upmix de oído**, con los 4 parlantes y la misma pista:
   - `simple`;
   - `psd` con 12 ms;
   - FFmpeg `surround=chl_out=quad`.
   Anotar la preferencia y cuánto se oye la voz en los traseros.
4. **Latencia declarada.** Declarar en el sink virtual la latencia medida de la
   cadena y medir con micrófono y un video de claqueta si Chrome, Firefox y mpv
   corrigen el lip-sync.
5. **DRM.** Reproducir Netflix, Disney+ y Apple Music (Atmos) hacia el dispositivo
   virtual de cada sistema y anotar si llega señal y cuántos canales.


## Anexo (2026-10-05): leer qué está sonando en el PC

- **MPRIS** (`org.mpris.MediaPlayer2`, D-Bus de sesión) expone estado, metadatos (título, artista,
  álbum, carátula, duración, posición) y controles (play/pausa, siguiente, anterior, saltar). Lo
  publican Spotify, Firefox, Chrome y la mayoría de los reproductores de Linux (REPORTADO).
  **VERIFICADO en `HP-O16`:** `busctl --user call org.mpris.MediaPlayer2.spotify /org/mpris/MediaPlayer2
  org.freedesktop.DBus.Properties Get ss org.mpris.MediaPlayer2.Player PlaybackStatus` devolvió
  `"Paused"`, y el método `Play` de la misma interfaz lo puso a sonar.
- PipeWire da la aplicación que manda audio a un sink (`application.name` del stream), sin metadatos
  de la canción; el servicio ya la lee.
- No existe en macOS (ahí está `MediaRemote`, privado) ni sirve en la Raspberry Pi sin escritorio.
- Roadmap: i-7c8794-99f87e.
