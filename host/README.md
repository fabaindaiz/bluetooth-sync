# aurasync (host)

El paquete Python que corre en el PC (Linux o macOS): la CLI `aurasync`.

**Estado (2026-09-29):** el núcleo está construido, con **A2DP como primer backend
emisor** (d-7c8794-9afee2). Auracast sigue abierto hasta E4.

**Probado con 3 JBL Go 4 el 2026-09-29, y suena de punta a punta**
([experimentos/09](../docs/research/experimentos/09-primera-escucha-con-3-go-4.md)). El efecto
envolvente se percibe pero más débil de lo esperado, aunque la única escucha fue a volumen muy
bajo, que perjudica justamente el mecanismo que lo produce. El lazo de recalibración
(`run --recalibrar`) aplica correcciones y sus filtros evitaron escribir hasta 11 ms de error,
pero **no converge** en el parlante de `ambiente` alto. Sigue apagado por defecto.

**Dos cosas que conviene saber antes de usarlo:**

- **la calibración de `calibrate` muere con su stream.** Tres corridas seguidas dieron 15 ms de
  diferencia entre sí, así que guardarla para la sesión siguiente no sirve; queda como
  diagnóstico. La corrección útil es la que mide el lazo dentro del stream que reproduce;
- **la calibración alinea en el punto del micrófono**, no en toda la pieza: mide el retardo
  total, que incluye el vuelo por el aire (34 cm = 1 ms). Es un hueco conocido
  ([experimentos/09](../docs/research/experimentos/09-primera-escucha-con-3-go-4.md) §7).

## Qué hay en cada módulo

| Módulo | Qué hace | De dónde salen sus parámetros |
|---|---|---|
| `config.py` | la instalación: qué parlantes hay y cómo se corrige cada uno. Descritos por **coordenadas opcionales**, no por etiquetas de canal | [09](../docs/research/09-efecto-ambiental-y-diseno-de-la-experiencia.md) §4 |
| `dsp/decorrelate.py` | filtros todo-paso de fase aleatoria: lo que produce el envolvimiento | Potard y Burnett, [09](../docs/research/09-efecto-ambiental-y-diseno-de-la-experiencia.md) §11.1 |
| `dsp/ambience.py` | extracción de ambiente por coherencia entre canales, y su versión con estado para flujos | Avendaño y Jot, [09](../docs/research/09-efecto-ambiental-y-diseno-de-la-experiencia.md) §11.2 |
| `motor.py` | la cadena completa: estéreo → una señal por parlante, con retardos y ganancias que se pueden cambiar **mientras suena** | figura 9 de Avendaño y Jot |
| `dsp/retardo.py` | retardo fraccionario con rampa de velocidad limitada: cambiar el retardo sin que se oiga un clic | 0,5 ms/s = 0,05 % de cambio de tono |
| `estimulos.py` | las señales de calibración | [experimentos/06](../docs/research/experimentos/06-calibracion-rapida-y-recalibracion.md) |
| `medicion.py` | GCC-PHAT, medición simultánea, niveles y calibración autónoma | [experimentos/06](../docs/research/experimentos/06-calibracion-rapida-y-recalibracion.md) |
| `sincronia.py` | el lazo cerrado: decide si una calibración vale y la escribe sin cortar el sonido | [experimentos/08](../docs/research/experimentos/08-lazo-de-recalibracion-en-simulacion.md) |
| `arrival_loop.py` | el lazo de la sesión sobre llegadas absolutas: acepta por parlante y sigue la deriva de cada uno | [experimentos/11](../docs/research/experimentos/11-sonda-enmascarada-en-simulacion.md) paso 2, [16](../docs/research/experimentos/16-ocho-parlantes-en-simulacion.md) §4 |
| `dsp/probe.py` · `probe_measure.py` | la sonda enmascarada bajo la música (apagada por defecto) y la medición de cada parlante contra ella | [experimentos/11](../docs/research/experimentos/11-sonda-enmascarada-en-simulacion.md) |
| `group_calibration.py` | con más de 6 parlantes, la calibración en dos grupos que comparten tres | [experimentos/11](../docs/research/experimentos/11-sonda-enmascarada-en-simulacion.md) paso 2 §C |
| `sonido.py` | la capa de PipeWire: descubrir parlantes y micrófonos, el **sink virtual** del sistema, reproducir a N, grabar, el **micrófono continuo** en anillo y la **comprobación de ruteo** | P1 del roadmap, [experimentos/09](../docs/research/experimentos/09-primera-escucha-con-3-go-4.md) §2 |
| `dsp/ramps.py` | lo que se mueve mientras suena sin oírse: valores suavizados, rampa en dB y el **corte** (fundido a cero de 80 + 80 ms) | spec del servicio §6 |
| `session.py` | una sesión de audio: streams, silencio, sink virtual, comprobación de ruteo y el lazo, bloque a bloque. La usan `run` y el servicio | [experimentos/09](../docs/research/experimentos/09-primera-escucha-con-3-go-4.md) §2 |
| `control.py` | el **contrato** del servicio: valida un mensaje JSON y lo despacha. Sin E/S, igual para REST y para serie | spec del servicio §5 |
| `presets.py` | presets con nombre, solo campos artísticos, escritos de forma atómica | spec del servicio §5.6 |
| `service.py` | el programa persistente: un solo hilo escribe el motor; `service.json` con token | spec del servicio §4 |
| `rest.py` | el transporte REST, con la biblioteca estándar | spec del servicio §10, [`docs/control-api.md`](docs/control-api.md) |
| `snapshot.py` | el estado que ve el panel, armado desde el servicio | spec del servicio §15 |
| `sources.py` | la fuente del sink virtual: sistema, una aplicación, un archivo o la señal de prueba, **verificada** en `pw-dump` | spec §15, CLAUDE.md |
| `system.py` | lo que se observa del equipo (systemd, `bluetoothctl`, aplicaciones) en un hilo propio, y conectar o desconectar parlantes | spec §15 |
| `logbuffer.py` | el log del proceso en memoria, con cursor, para el panel | rama panel-demo |
| `simulated.py` | `--simular`: el motor y el lazo reales sobre una sala simulada | spec §15.5 |
| `panel/` | el panel web: `index.html`, `app.js`, `tailwind.css` (compilado de `tailwind.input.css` con `hatch run web:css`, sin Node) y `cadena.js` (compilado de `host/web/` con `npm run build`); todo se versiona | [10-panel-de-control](../docs/research/10-panel-de-control.md) |
| `dsp/eq.py` · `dsp/response.py` | ecualización por parlante desde la respuesta medida en la calibración | experimentos/10 §6 |
| `chain.py` | la cadena como datos: cada etapa, sus algoritmos y sus perillas, con sus valores por defecto (el sonido de siempre, bit a bit) y lo que se guarda | spec 2026-10-02 §4 |
| `chain_stages.py` | las etapas nuevas enchufadas al motor: difusión, graves (`protect`, `crossover`) y el limitador de pico real | spec 2026-10-02 §5 |
| `render_branch.py` | la rama del render: lo que cambia por parlante con el render, que un fundido del render corre dos veces y mezcla | spec 2026-10-08 seamless-transitions §4, etapa 3 |
| `dsp/crossover.py` · `dsp/virtual_bass.py` · `dsp/diffuse.py` · `dsp/limiter.py` | Linkwitz-Riley, graves psicoacústicos (NLD), cola difusa por parlante, limitadores de pico y de pico real | [11](../docs/research/11-procesamiento-calidad-canales-y-panel.md) R1-R6 |
| `dsp/loudness.py` · `quality.py` | sonoridad BS.1770 (M/S/I), pico real ×4 y PSR; en vivo, entrada contra salidas, ganancia neta y si la cadena aplana | [11](../docs/research/11-procesamiento-calidad-canales-y-panel.md) §1, spec 2026-10-02 §6 |
| `bt_volume.py` | `volume.avrcp`: el volumen en los parlantes (`pactl`), **leído de vuelta**, sin saltos de nivel al cambiar de modo | experimentos/10 §5.4, spec 2026-10-02 §5 |
| `radio.py` · `cuts.py` | los paquetes que el Bluetooth descarta (`reduce bitpool` en el journal de WirePlumber) y el registro de cortes | spec 2026-10-02 §3, [experimentos/12](../docs/research/experimentos/12-microcortes-con-3-go-4.md) |
| `access.py` · `clients.py` · `pairing.py` | quién puede hablar con el servicio: un token por cliente guardado como hash, alcances (`read`, `control`, `admin`), emparejamiento, intentos fallidos por dirección, tickets del stream y CORS | d-7c8794-37f9bc, [`docs/control-api.md`](docs/control-api.md) |
| `tls.py` · `lan.py` · `remote.py` · `mdns.py` | HTTPS con una raíz propia limitada a la red local, los nombres de la máquina (recalculados si cambia la IP), los puertos HTTP y HTTPS, y el anuncio mDNS opcional | d-7c8794-37f9bc |
| `bumble_fixes.py` | correcciones locales a Bumble 0.0.235, que se aplican con `apply()` antes de abrir un transporte HCI (la primera: `LE Read ISO TX Sync`) | d-7c8794-570a77, [experimentos/21](../docs/research/experimentos/21-f1-iso-en-la-supermini.md) |
| `cli.py` | `doctor`, `sinks`, `init`, `calibrate`, `run`, `play`, `service`, `radio-log`, `clients`, `tls` | — |

## Cómo se usa

```bash
aurasync doctor      # ¿está todo en su lugar?
aurasync init        # crea la instalación con los parlantes conectados
aurasync calibrate   # mide retardo y ganancia con el micrófono
aurasync run         # el modo de uso real (ver abajo)
aurasync play tema.wav --sin-decorrelar   # el A/B que muestra el efecto
aurasync run --recalibrar --volumen-db -12 --registro ~/lazo.jsonl   # sin validar todavía
aurasync service     # programa persistente con API REST (ver docs/control-api.md)
aurasync radio-log on    # registro de radio: ver los paquetes que el Bluetooth descarta
aurasync radio-log off   # devuelve el nivel de registro a como estaba
aurasync clients list    # dispositivos emparejados y solicitudes pendientes
aurasync clients code    # un código de 6 dígitos para emparejar un teléfono
aurasync tls info        # la raíz del HTTPS, su huella y cómo instalarla
```

**El panel como PWA desde GitHub Pages** (d-7c8794-37f9bc, 2026-10-02; el servicio y la PWA
están construidos y probados en Chromium contra el servicio simulado por HTTPS; **sin probar en
teléfonos**; la publicación la activa el usuario, ver *La aplicación web* abajo). El servicio escucha **HTTPS en el
8443** además del HTTP de siempre (8731, que sigue sirviendo el panel local como respaldo).
Un `service.json` nuevo trae `"tls": true`; uno anterior sigue sin HTTPS hasta agregarle esa
clave. Al primer arranque con TLS genera en `~/.config/aurasync/tls/` una **raíz propia** (10
años) y con ella el certificado del servidor (397 días, para `aurasync.local`, el nombre del
equipo, `localhost` y sus IP; se rehace solo si cambia la IP o faltan 30 días). La raíz **solo
puede certificar nombres `.local` y direcciones privadas** (`NameConstraints`), así que
instalarla en un teléfono no le da poder sobre otros sitios.

Conectar un teléfono, la primera vez:

1. **Instalar la raíz** (una vez por teléfono; sin ella, el navegador avisa y la PWA no
   conecta). `aurasync tls info` imprime los links.
   - **iPhone:** abrir en Safari `http://<ip>:8731/v1/tls/root.mobileconfig` → Permitir →
     Ajustes → *Perfil descargado* → Instalar. Después, **Ajustes → General → Información →
     Ajustes de confianza de certificados** → activar *aurasync local root*. Sin ese segundo
     paso iOS instala el perfil pero no confía en la raíz.
   - **Android:** descargar `http://<ip>:8731/v1/tls/root.crt` → Ajustes → Seguridad (o
     *Seguridad y privacidad → Más ajustes*) → *Encriptación y credenciales* → *Instalar un
     certificado* → *Certificado de CA*. Chrome confía en las CA del usuario; Firefox para
     Android necesita además activar las CA de terceros en sus ajustes (REPORTADO, sin probar).
   - Comprobar la huella: la que muestra el teléfono tiene que coincidir con el `sha256` de
     `aurasync tls info`.
2. **Emparejar.** En la PWA (`https://fabaindaiz.github.io/bluetooth-sync/`): *Agregar equipo*
   con la dirección (`aurasync.local:8443` o `IP:8443`), o escaneando el QR de **Conectar
   teléfono** en el panel del equipo, que abre la PWA con la dirección y la huella de la raíz (y
   ningún token). La PWA pregunta `GET /v1/hello`, muestra nombre, versión y huella (y se niega a
   emparejar si la huella no coincide con la del QR); si el `fetch` falla, explica los dos
   caminos: instalar la raíz (paso 1) o abrir `https://<equipo>:8443` y aceptar el aviso. Después
   pide acceso con un nombre, muestra un número de comprobación de 4 dígitos y espera la
   aprobación:
   - si el servicio **no tiene ningún cliente**, la primera solicitud en los 10 minutos
     siguientes a arrancarlo se aprueba sola, como `admin` (`pair_window_s`; 0 la apaga);
   - si ya hay clientes, se aprueba desde un teléfono `admin`, con `aurasync clients approve
     <id>`, o escribiendo en la PWA el código de `aurasync clients code` (o el que imprime la
     terminal al arrancar).
3. Desde ahí la PWA usa su token (`Authorization: Bearer`), que el servicio guarda solo como
   hash en `clients.json` (0600). `aurasync clients revoke <id>` lo corta, también el stream que
   tenga abierto. Rotar el token maestro de `service.json` no desconecta a nadie.

Cómo revertirlo: `"tls": false` en `service.json` apaga el HTTPS; borrar
`~/.config/aurasync/tls/` hace una raíz nueva al próximo arranque (hay que reinstalarla en cada
teléfono); en el iPhone se quita el perfil en Ajustes → General → VPN y gestión de
dispositivos, y en Android en *Credenciales de usuario*. `clients.json` borrado deja sin
clientes (el token maestro sigue valiendo). El anuncio mDNS (`"mdns": true`, apagado por
defecto) usa `avahi-publish-service` como proceso hijo: no deja archivos en `/etc` y termina
con el servicio. Nada de esto está probado todavía en un iPhone ni en Android.

**La cadena tiene todas sus etapas conectadas** (2026-10-02, sin validar con parlantes):
difusión (`diffuse.noise_tail`), graves (`bass.protect` con armónicos opcionales,
`bass.crossover` a un parlante apto), volumen en el parlante (`volume.avrcp`) y limitador de
pico real (`limiter.true_peak`), además de las perillas nuevas de la ecualización y del
decorrelador. **Todo lo nuevo viene apagado**: con los valores por defecto el motor suena
bit a bit como antes (`tests/test_chain_golden.py`). Se eligen desde el panel o con
`chain_set` ([`docs/control-api.md`](docs/control-api.md)). El costo con todo encendido se
mide con `probes/18-costo-de-la-cadena/costo.py`.

**El registro de radio es un cambio de sistema**: `aurasync radio-log on` (o el botón del
panel) sube el nivel de registro de WirePlumber solo para los temas de bluez5, y antes de
hacerlo anota el cambio y cómo revertirlo en `~/.config/aurasync/cambios-de-sistema.txt`.
El servicio lo revierte al cerrarse (también con Ctrl-C y SIGTERM) y si la sesión falla; si
un proceso muerto lo dejó puesto, lo revierte al arrancar.

**`aurasync service` trae un panel web** (2026-10-01, d-7c8794-09d10f, sin validar con
parlantes): el link con el token que imprime al arrancar abre todo lo del panel de
`panel-demo` sobre el motor real —parlantes y roles, servicios con PID, logs, salud, niveles,
configuración, calibración dentro de la sesión, presets y A/B ciego—. `aurasync service
--simular` lo corre sin parlantes. Los tests de navegador: `hatch run browser:install` una
vez y después `hatch run browser:test` (Chromium y Firefox).

**`aurasync service` deja ajustar mientras suena** (2026-10-01, sin validar con parlantes).
Es un programa que queda vivo, con una API REST en la red local protegida por token: se
arranca y se detiene el audio, se mueven `pan`, `ambiente`, ganancias, volumen, retardo
trasero, extracción y decorrelación, y se guardan y cargan presets. La referencia, con
ejemplos de `curl`, está en [`docs/control-api.md`](docs/control-api.md) (en inglés,
d-7c8794-7b3093). Arranca con el audio detenido y el volumen en -20 dB.

**El micrófono ya no está fijo en el código:** se usa `--microfono`, si no el `microphone`
de `~/.config/aurasync/service.json`, y si no la fuente por defecto de PipeWire.

**`--recalibrar` corrige la alineación mientras suena**, midiendo contra el propio contenido:
no interrumpe ni emite ningún estímulo. Cada cambio necesita confirmarse en dos mediciones
seguidas antes de aplicarse, y el retardo se mueve con rampa para que el cambio no se oiga.
**Está sin validar acústicamente** y por eso viene apagado; antes de usarlo, leer el
protocolo en
[experimentos/08](../docs/research/experimentos/08-lazo-de-recalibracion-en-simulacion.md).
Al terminar imprime la **deriva estimada en ms/h**, que es INFERIDA.

**`run` es el modo de uso real.** Crea un dispositivo de salida que el sistema muestra como
cualquier otro; se lo elige como salida —o se le manda una aplicación sola— y todo lo que
suene ahí pasa por el procesamiento. **No deja huella**: el dispositivo vive en el proceso y
desaparece al cerrarlo.

`play` hace lo mismo desde un archivo, para probar sin depender de otra aplicación.

**Nada pide números.** `init` toma lo que hay conectado y `calibrate` mide lo demás. Lo
único que tiene sentido ajustar a mano en `instalacion.json` es `pan` y `ambiente` de cada
parlante: qué reproduce cada uno, que es una decisión artística y no algo medible.

## Parlantes virtuales y sesión sin parlantes reales

(2026-10-05, fase 1, `HP-O16`, **solo con tests: todavía no se escuchó nada**.) Un parlante
**virtual** es uno con `sink: null` (d-7c8794-0e5063): el motor le calcula su señal completa
—retardo, ganancia, EQ, cadena—, pero no sale por ningún dispositivo. Sirve para probar la cadena
y el sonido envolvente sin los parlantes, y para escucharlos por el monitor de audífonos.

- **Agregar uno:** el botón «Agregar parlante virtual» de la tarjeta de dispositivos del panel, o la
  op `speaker_add_virtual` (con la sesión parada; [`docs/control-api.md`](docs/control-api.md)).
- **Sesión sin parlantes reales** (d-7c8794-05bdd6): una sesión arranca con los parlantes reales
  ausentes y sigue viva si se pierden todos. El snapshot dice qué hace cada salida (`output`:
  `virtual`, `absent`, `playing`, `lost`; `output_kind`: `virtual`, `bluetooth`, `wired`). Con todo
  virtual, `outputs.py` (`Pacer`) lleva el reloj de los bloques.
- **Cómo escucharlos:** agregar los parlantes virtuales, arrancar la sesión y elegir los audífonos
  en la tarjeta «Monitor (audífonos)» con el modo `mix` o `binaural` (el monitor recibe todos los
  canales). `stereo` sale **antes** de la cadena, así que no lleva ningún efecto (sí el volumen). Unos audífonos
  inalámbricos en **HFP** (una llamada) suenan mono a 16 kHz: hay que pasarlos antes a **A2DP**.
- **Colchón del monitor:** escribe por adelantado un bloque más un quantum del driver (tope 400 ms)
  y lo vigila con el nivel de la tubería de `pw-play` (`cushion_ms`, `level_ms`, `refills`, `trims`
  en el estado), porque sin él los audífonos cortaban la mitad de los ciclos (experimentos/18).
- **Mismo volumen en todos los modos del monitor:** `stereo` sigue el volumen elegido y `mix`/`binaural`
  se igualan a él con una compensación por modo que se recuerda (`loudness_match.py`; `makeup_db` y
  `match` en el estado; el binaural usa la ganancia del HRTF medida en experimentos/18).
- **Volumen del monitor por el audífono (por defecto):** el nivel de la tarjeta es el volumen de la
  salida del monitor (para Bluetooth, el de `bluez_output…`, que WirePlumber manda como AVRCP; el mismo
  que mueven los botones del audífono) y la ganancia por software queda en 0 dB; la igualación de
  volumen sigue aplicándose. El estado trae el % real leído de vuelta (`device_volume_pct`, con
  `device_volume_reason` si no se pudo pedir o leer), también cuando se cambió con los botones. Al
  abrir el monitor, si la salida está por encima del último valor puesto desde el panel (30 % la primera
  vez) se baja a él; nunca se sube sola. Solo mover el nivel lo pide (cambiar el modo, la salida o
  quién controla el volumen nunca lleva un nivel), y pasar de software al audífono deja el tope en 30 %
  como mucho. **Falla cerrado:** si no se pudo leer la salida a ese valor o por debajo (sin `pactl`,
  una salida que no lo acepta, miente o no se lee, más de 10 s) o PipeWire mandó el monitor a otra
  salida, suena con la ganancia por software (`gain_db`, como mucho -12 dB) y `device_volume_reason` lo dice.
  `volume_control: software` es lo de antes (`gain_db`, la salida intacta). Que ese volumen sea de
  verdad el AVRCP absoluto del WH-CH520 está sin medir (INFERIDO).
- **Recalibración continua apagada por defecto** (`recalibrate`): se enciende en el panel o con
  `start`/`set`. Sin el lazo, la verificación del micrófono en Calibrar lo abre solo unos segundos
  (`mic_check`, 8 s desde que se abre, sin alargarse aunque se pida otra vez) al entrar con la pestaña visible o con «Medir el micrófono», nunca de continuo.
- **Entrar y salir en caliente (fase 2, d-7c8794-618666; sin validar con parlantes, solo tests y
  `--simular`):** con la sesión sonando, la op `speaker_join` / `speaker_leave` (REST
  `POST /v1/speakers/{name}/join|leave`) hace entrar o salir a un parlante real sin parar la sesión; en
  el panel son los botones **Hacer entrar**, **Sacar** y **Reintentar** de cada fila. Responden cuando
  el cambio está pedido: si la preparación falla, el error queda en `state.errors.output` y en el log.
  Un parlante `lost` vuelve solo cuando su sink reaparece (un intento cada 10 s; con 3 caídas en
  5 minutos deja de intentar y el panel ofrece **Reintentar**; nunca reconecta el Bluetooth) y el log
  dice «volvió <nombre>». El cambio usa el corte de 80 + 80 ms, y en `separado` también se reconstruye
  toda la parte real (enmienda al spec §5). Con el lazo de recalibración apagado, el panel avisa que la
  alineación puede haber cambiado; con el lazo encendido, se reinicia solo sobre el conjunto nuevo.
  Qué falta medir: [experimentos/19](../docs/research/experimentos/19-entrada-en-caliente-con-3-go-4.md)
  (y [experimentos/18](../docs/research/experimentos/18-parlantes-virtuales-y-monitor-en-hp-o16.md) para la fase 1).

## La aplicación web (`host/web/`)

El panel se escribe en dos partes que conviven (d-7c8794-6da524): `src/aurasync/panel/app.js`, a
mano y sin build, y las pantallas en **Vite + TypeScript + Preact** en `host/web/src/`, que se
compilan a un solo `cadena.js`. **npm va directo, fuera de hatch** (Node del sistema; probado con
Node 26.10 y npm 11.19):

```bash
cd host/web
npm ci                 # las dependencias exactas de package-lock.json
npm run check          # tipos (tsc)
npm test               # vitest: transporte, enlace del QR, build de la PWA, privacidad
npm run build          # el panel local: escribe ../src/aurasync/panel/cadena.js y su sello (se versionan)
npm run build:pwa      # la PWA: arma host/web/dist-pwa/ (no se versiona)
```

- **Un solo transporte** (`src/transport.ts`): `app.js` y Preact lo usan por `window.aurasync.api`.
  En el panel que sirve el equipo usa la cookie; en la PWA, el token del cliente (`Authorization:
  Bearer`) y el stream por ticket. Avisa una vez de cada 401 (revocado: volver a emparejar), 403,
  429 (con `Retry-After`) y de un equipo que no contesta.
- **La pantalla de conexión** (`src/connect/`): equipos recordados (nombre, dirección, última vez
  visto, huella de la raíz), agregar por dirección o por el QR, emparejar, elegir, olvidar; con un
  token `admin`, las solicitudes pendientes, los clientes (renombrar, revocar) y un código de
  emparejamiento (lo mismo aparece en el diálogo **Conectar teléfono** del panel local). Los
  equipos se guardan en IndexedDB, o en localStorage si no hay.
- **La PWA** (`npm run build:pwa`, `pwa/build.ts`): el mismo `index.html` (rutas relativas, para
  funcionar bajo `/bluetooth-sync/`), `app.js`, `cadena.js` y `tailwind.css`, más
  `manifest.webmanifest`, íconos dibujados en el build (`pwa/icons.ts`), `build.json` (versión y
  commit) y un **service worker propio** (`pwa/sw.js`) con las reglas de `thom-music-player`: cache
  first con la página incluida, cada archivo con su SHA-256, actualización atómica que baja solo
  lo que cambió, y la versión nueva se aplica sola **salvo durante una calibración o un A/B**, que
  esperan. Diagnóstico → Servicios muestra su línea de estado (`sw active 10/10  upd idle  0.0.0
  <commit>`). Nunca guarda respuestas de la API del equipo. El build **falla si encuentra algo
  privado** (MAC, IP privada, token `asc_`, nombre de un sink o de un equipo; `pwa/privacy.ts`).
- `scripts/check.sh` no necesita Node: comprueba con `scripts/web_stamp.py` que `cadena.js`
  corresponde a `host/web/`, y que `dist-pwa/` no está versionado.
- **Tests de navegador de la PWA:** `npm run build:pwa` y después `hatch run browser:test`
  (`tests_browser/test_pwa.py`, solo Chromium: sirve `dist-pwa/` en `http://localhost:5173` bajo
  `/bluetooth-sync/`, que tiene que estar libre, contra el servicio simulado por HTTPS).

**Publicarla.** Vive en la rama **`gh-pages`** (solo el sitio compilado y `.nojekyll`, como el de
thom-music-player), servida con **Settings → Pages → Deploy from a branch → `gh-pages` / (root)**;
publicada desde el 2026-10-04. `.github/workflows/pages.yml` la arma, la revisa y suma un commit a
`gh-pages` en cada push a `main` que toque `host/web/` o el panel, y a mano (*Actions → pages → Run
workflow*); si nada cambió, no empuja. El sitio es público aunque el repositorio sea privado (GitHub
Pro); no lleva ningún dato de los equipos. Para apagarla: *Settings → Pages → Unpublish site* y
borrar el workflow.

## Requisitos

- **hatch** (probado con 1.18.1). Maneja el Python 3.12 y los entornos por su
  cuenta, fuera del repositorio, y usa uv internamente para instalar. No hay que
  instalar uv aparte.
  - macOS: `brew install hatch`.
  - Linux: con el binario oficial de hatch o `pipx install hatch`. Queda en el home
    del usuario y no requiere root.
- **macOS arm64 o Linux x86_64.** `lc3py` solo publica ruedas para esas dos
  plataformas; en cualquier otra, la instalación falla (a propósito).

## Comandos

```bash
cd host
hatch test                  # tests (pytest)
hatch fmt --check           # lint y formato (ruff, con la versión que fija hatch)
hatch run aurasync --version
```

Desde la raíz, `scripts/check.sh` corre todo esto junto con el chequeo del bundle.

## En contenedor (Linux, Podman)

Sin instalar hatch, Node ni los navegadores en el equipo: solo Podman. PipeWire, BlueZ y
avahi siguen en el host; la imagen trae sus clientes y aurasync (d-7c8794-6b1a15; qué se
monta y qué se midió, en `docs/research/08-integracion-y-plan.md` §6.2).

```bash
container/aurasync-container build              # la imagen runtime (o: build dev, build ml)
container/aurasync-container run                # aurasync service, con la config de ~/.config/aurasync
container/aurasync-container run doctor         # cualquier subcomando
container/aurasync-container build dev
container/aurasync-container dev hatch test     # los tests con este repositorio montado
container/aurasync-container dev hatch run browser:test
```

Las pruebas con parlantes siguen en el host hasta medir que el contenedor no agrega cortes.

## Versiones

- `bumble` y `lc3py` van con versión exacta en `pyproject.toml`, porque Bumble
  todavía no llega a 1.0 y cambia su API.
- Las dependencias transitivas no se fijan: los lockfiles de hatch 1.18.1 borran el
  propio proyecto del entorno. La explicación está en el comentario de
  `pyproject.toml` y en d-7c8794-c23c20.
