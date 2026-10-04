# 13 · Otros dispositivos (Raspberry Pi Zero 2 W, Pico 2 W), más de 3 parlantes y el panel independiente

Investigación del 2026-10-02, desde el **Mac**, sin ninguna de las dos placas en la mano: todo lo
de la Zero 2 W y la Pico 2 W es **estimado o reportado**, y cada cifra lleva su marca. Responde
lo que preguntó el usuario: **qué es factible correr en cada dispositivo, qué haría falta
cambiar, si esto se vuelve dos proyectos, cómo pasar de 3 parlantes con control directo del
protocolo, y cómo funciona un panel independiente** conectado por la red local.

**El foco no cambia** (d-7c8794-a5b83b): Linux completo es el camino principal; los dispositivos
se suman después, por iteraciones. Este documento existe para que lo que se construye en Linux
no les cierre la puerta.

**Marcas:** VERIFICADO, REPORTADO, INFERIDO, MEDIDO (con el entorno).

## Resumen

1. **El motor completo no cabe en la Pico 2 W** (como pensaba el usuario): ~170–210 MHz de los
   300 de sus dos núcleos y ~490 KB de sus 520 KB, antes de sumar SBC o LC3, BTstack y el USB
   (INFERIDO con cifras de CMSIS-DSP, §2.1). **Sí cabe una cadena por canal** (EQ IIR, retardo,
   ganancia, limitador: ~35 MHz y ~60 KB).
2. **En la Zero 2 W el motor numpy no llega a tiempo real**: 68–150 ms por bloque de 85 ms con los
   valores por defecto (INFERIDO, escalando lo MEDIDO en el Mac por 25–40×). **Con el motor en
   Rust y f32, ~10–15 % de un núcleo** (INFERIDO). La Zero es el lugar donde Rust deja de ser una
   mejora y pasa a ser necesario.
3. **El Bluetooth integrado de la Zero 2 W no sirve para 3 parlantes:** el CYW43436 es BT 4.2 y
   comparte chip y antena con un Wi-Fi que solo usa 2,4 GHz; hay reportes de que sostiene un solo
   stream A2DP (REPORTADO). Con un hub OTG y 1–2 adaptadores USB sí, pero se pierde el modo de
   tarjeta USB.
4. **No son dos proyectos.** La Zero 2 W corre Linux: es el mismo `aurasync` con otro perfil. La
   Pico no corre Linux: es firmware en C que ya vive en `firmware/pico/`, dentro del monorepo, y
   hablaría el mismo contrato por serie. Lo que sí se separa es el **panel**, que pasa a ser un
   cliente reutilizable (§5).
5. **Tres caminos para pasar de 3 parlantes**, con control del protocolo creciente:
   - varios adaptadores de Bluetooth clásico en el mismo Linux (barato, sin firmware);
   - **la Pico 2 W como emisor A2DP con BTstack**, una por parlante (control total);
   - **Auracast con las SuperMini** (sin techo de enlaces; depende de E4).
6. **Las SuperMini nRF52840 solo hacen Bluetooth LE**, no clásico: no pueden hacer A2DP. Son la
   llave de Auracast, que es para lo que se compraron (d-7c8794-b82ee9).
7. **Una variable gratis para los microcortes de hoy:** hay reportes de cortes de A2DP por
   coexistencia en el AX200/AX210 aunque el equipo esté por cable, que mejoran mucho con
   `rfkill block wifi` (REPORTADO). Se agregó como la primera variable de C3 en
   [experimentos/12](experimentos/12-microcortes-con-3-go-4.md).
8. **El panel independiente** es una PWA en GitHub Pages que se conecta por la red local al
   servicio de cada equipo, por HTTPS con un certificado propio y un token por cliente
   (d-7c8794-37f9bc).

## 1. La Raspberry Pi Zero 2 W

### 1.1 El hardware

RP3A0: 4× Cortex-A53 a 1 GHz con NEON; **512 MB de RAM** (~417 MiB libres para el sistema); radio
**CYW43436** (Wi-Fi 802.11n solo de 2,4 GHz y Bluetooth 4.2 en el mismo chip y la misma antena;
el Bluetooth va por UART a 3 Mbaud); **un solo micro-USB de datos (OTG)** y otro solo de
alimentación; sin Ethernet ni salida de audio. VERIFICADO (documentación de Raspberry Pi).

### 1.2 Lo que corre y lo que no

| Componente | PC Linux | Zero 2 W | Pico 2 W |
|---|---|---|---|
| Servicio, REST, SSE, contrato | corre | **corre** (1–5 % de un núcleo por cliente, INFERIDO) | no (sin Linux ni Python); el contrato por serie |
| Panel | corre | lo dibuja el teléfono; la Pi solo responde | no |
| Motor numpy completo | corre (32×, MEDIDO) | **no** en tiempo real (68–220 ms/85 ms); al límite con f32 y vectorizado; **sí en Rust** | **no** (CPU y RAM) |
| Cadena por canal (EQ IIR, retardo, ganancia, limitador) | corre | corre | **corre** (~35 MHz, ~60 KB) |
| Extractor de ambiente (STFT estéreo) | corre | con el motor en Rust | no: necesita L y R juntos |
| Calibración y lazo | corre (0,74 s en el Mac, MEDIDO) | **con cambios**: ~18–30 s de CPU y 117 MB de pico; medir cada pocos minutos, en f32 | no: se queda en el PC o la Zero |
| `pw-dump` cada 2 s | corre | **con cambios**: `pw-dump --monitor` o nativo | — |
| `radio.py` (journal) | corre | corre (las líneas son iguales en PipeWire 1.4.2) | no aplica: BTstack ve sus propios descartes |
| A2DP a 3 Go 4 | corre (techo de 3 en el AX210) | **no con el BT integrado**; con hub y 1–2 adaptadores, con cambios | 1 por placa (¿2?), con firmware propio |
| Auracast (Bumble + SuperMini) | corre (por USB) | **con cambios**: SuperMini por el UART del GPIO (`dtoverlay=disable-bt`), LC3 sin `lc3py` | con BTstack + liblc3 + SuperMini por UART (H2b) |
| Tarjeta USB del PC (gadget UAC2) | — | corre, pero **se lleva el único USB** | **corre** (TinyUSB) |
| Música por red (AirPlay 2, Spotify Connect, Snapcast) | corre | corre (shairport-sync pide "Pi 2 o Zero 2 W, o mejor", REPORTADO) | no |
| Micrófono de calibración | USB | USB por hub, I2S por GPIO (queda en la caja, no donde está el oyente) o el del teléfono desde el panel (exige HTTPS) | — |
| Instalación | hatch | sistema de 64 bits; `bumble` y `lc3py` como extras (`lc3py` 1.1.3 no publica rueda aarch64, VERIFICADO en PyPI) | firmware UF2 |

### 1.3 El sistema

Raspberry Pi OS trixie trae **PipeWire 1.4.2, WirePlumber 0.5.8, BlueZ 5.82 y Python 3.13**
(VERIFICADO en los índices de paquetes el 2026-10-02): más viejo que el 1.6.9 donde se validó
todo. `combine-stream` existe. Falta comprobar que `wpctl` 0.5.8 acepte niveles de log por topic
(lo usa `radio.py`). RAM sumando todo: ~350–440 MiB de 417 (INFERIDO). La tarjeta SD se desgasta
con el journal en debug: journal volátil y estado persistente en un solo directorio con pocas
escrituras.

### 1.4 Rendimiento

FFT de 4K puntos en doble precisión: i7 a 3,9 GHz 0,08 ms; Cortex-A53 a 1,3 GHz 1,06 ms
(VERIFICADO, benchmarks de Longbottom). La Zero 2 W queda ~25–40× más lenta que el Mac en numpy
(INFERIDO). En el Mac, hoy y con la máquina cargada (MEDIDO): el motor por defecto da mediana
3,82 ms y p99 7,95 ms; con todo encendido, 5,43 y 17,95 ms; son 961 y 1510 llamadas Python por
bloque. Calibrar 3 parlantes tarda 737 ms con un pico de 117 MB. El servicio ocupa 47 MB al
importar.

### 1.5 Qué medir primero en una Zero 2 W real

| # | Qué | Se acepta si |
|---|---|---|
| 1 | `probes/18-costo-de-la-cadena/costo.py` en el sistema de 64 bits | mediana ≤ 40 ms y p99 ≤ 60 ms por bloque; si no, numpy queda descartado en la Zero |
| 2 | La calibración | ≤ 10 s de CPU y ≤ 80 MB de pico |
| 3 | BT integrado con 1, 2 y 3 Go 4, Wi-Fi conectado y el panel abierto, 20 min cada uno | el máximo N con 0 descartes (hipótesis: N ≤ 1) |
| 4 | Lo mismo con un adaptador RTL8761B por hub, con y sin cable de extensión | 3 Go 4 con 0 descartes en 20 min, dos veces |
| 5 | `free -m` con todo andando | ≥ 60 MiB libres en el peor momento, sin swap |
| 6 | Temperatura en la caja, 1 h de música | sin throttling (`vcgencmd get_throttled`) |

### 1.6 Alternativas de hardware

Una **Pi 4 o 5 de 1 GB** (US$35–45, VERIFICADO en el anuncio de precios de 2026) resuelve de una
vez la CPU (~4× la Zero), la RAM, los USB y **el Wi-Fi de 5 GHz**, que saca al Wi-Fi de la banda
del Bluetooth. La Zero 2 W tiene más sentido en el camino Auracast, como tarjeta USB con la
SuperMini por UART y el motor en Rust (INFERIDO). Las compras son decisiones del usuario.

## 2. La Raspberry Pi Pico 2 W

### 2.1 ¿Cabe el motor? Con números

RP2350: 2× Cortex-M33 a 150 MHz (300 MHz en total) con FPU de precisión simple y extensiones DSP,
520 KB de SRAM, sin Linux. Referencia: una RFFT f32 de 1024 puntos son 55 538 ciclos en CMSIS-DSP
(VERIFICADO, white paper de ARM); escalado por N·log N para el resto (INFERIDO). Para 4 canales a
48 kHz:

| Etapa | MHz | RAM |
|---|---|---|
| EQ FIR de 2048 directo | ~590: **no** | 32 KB |
| EQ FIR de 2048 por FFT o particionado | ~50 | ~140 KB |
| EQ IIR (10 biquads por canal) | ~19 | < 2 KB |
| Decorrelador FIR de 256 | 0 si se funde con el EQ fuera de línea | — |
| Extractor STFT de 2048 (estéreo) | ~50 | ~100 KB |
| Retardo sinc de 32 coeficientes | ~10 | 192 KB (250 ms × 4 en f32) |
| Limitador con look-ahead | 3–20 | ~12 KB |
| **Motor completo** | **~170–210** | **~490 KB** |
| SBC por enlace A2DP | 10–25 | ~10 KB |
| LC3 × 4 | 72–150 | ~22 KB |
| BTstack + cyw43 + TinyUSB | 10–30 | 60–100 KB |

**Veredicto (INFERIDO, confianza alta):** el motor completo no cabe. **El papel realista de la
Pico es de puente**: recibe por USB los canales ya procesados por el PC y los transmite, con una
cadena por canal liviana si hace falta.

### 2.2 La Pico 2 W como emisor A2DP

- **Su radio (CYW43439) sí hace Bluetooth clásico y A2DP source**: `a2dp_source_demo` está en
  `pico-examples` (VERIFICADO).
- **Cuántos enlaces:** la configuración de `pico-examples` fija `MAX_NR_AVDTP_CONNECTIONS 1` y
  `MAX_NR_CONTROLLER_ACL_BUFFERS 3`, comentado *"to avoid cyw43 shared bus overrun"*
  (VERIFICADO). BTstack está escrita para varias conexiones, pero **no hay reportes de dos streams
  A2DP simultáneos desde un CYW43439** (VERIFICADO: la issue #143 de BTstack sin respuesta). Lo
  realista es **una Pico por parlante** (~US$7 cada una, REPORTADO).
- **Lo que da y PipeWire no deja elegir** (VERIFICADO en el código): el bitpool lo elige la
  aplicación; la cola es de la aplicación (la política ante un enlace lleno se escribe, en vez de
  descartar el paquete y bajar el bitpool como `media-sink.c`); `hci_write_automatic_flush_timeout`
  acota los reintentos en el aire; y SBC mono es posible.
- **El audio desde el PC:** un speaker TinyUSB UAC con los canales ya procesados. USBPods-Pico2W
  hace USB → A2DP en una Pico 2 W con TinyUSB y BTstack, incluso con LDAC (REPORTADO).
- **El reloj:** cada Pico tiene su cristal (20 ppm entre dos son 72 ms/h). El lazo de micrófono lo
  corregiría; mejor, **enganchar cada Pico al SOF del USB** (1 kHz, igual para todos los
  dispositivos del bus), y así todas siguen el reloj del PC (INFERIDO: comportamiento estándar de
  USB, sin probar en el RP2350).

### 2.3 Los caminos para pasar de 3 parlantes

| | Techo | Control del protocolo | Compra | Esfuerzo | Riesgo principal | Qué medir primero |
|---|---|---|---|---|---|---|
| **(a) Varios adaptadores USB clásicos en Linux** | 2–3 por adaptador (REPORTADO: un puente con 2× CSR8510 maneja 6 parlantes) | bajo (el de PipeWire) | 1–2 UB500 (RTL8761B) ≈ US$12–30 | **bajo**: el código no supone `hci0` (VERIFICADO); falta elegir el adaptador al conectar | interferencia entre radios cercanas | 4 Go 4 en dos adaptadores (2+2): descartes en 20 min |
| **(b) Una Pico 2 W por parlante** | 1 por Pico (¿2?) | **total** | ~US$7 cada una + hub multi-TT | **alto**: firmware en C | 3 buffers ACL en el CYW43439, sin reportes de varios enlaces | 1 Pico → 1 Go 4 con bitpool fijo: cortes y deriva en 1 h |
| **(c) Auracast con una SuperMini** | 4 BIS (más si caben) | total (Bumble o BTstack) | ya están | medio-alto | **E4**: que los JBL no elijan su BIS | E4 |

La Pico que ya existe alcanza para el primer paso de (b) con 1 parlante.

### 2.4 Qué adaptador comprar para el camino (a) (2026-10-02)

**La calidad no la pone el adaptador:** los Go 4 solo hablan SBC con bitpool 40
(experimentos/10 §5.5), así que un adaptador con aptX o LDAC no mejora nada. Lo que sí mueve la
calidad es **que el bitpool no baje por congestión** (research/11 §3.2: −6 de bitpool cuestan más
que todo lo demás junto), y eso se logra **repartiendo los enlaces entre controladores** con un
chip estable en Linux.

Recomendaciones de un puente A2DP multiparlante en Linux (REPORTADO, sendspin-bt-bridge,
consultado el 2026-10-02):
- **Todos los recomendados usan el Realtek RTL8761B**, con driver `btusb` en el kernel y firmware
  en `linux-firmware`. **TP-Link UB500 (v1/v2)**, ~US$12–15, *"the most widely tested BT 5.0 nano
  dongle on Linux"* (kernel ≥ 5.8). Alternativas con el mismo chip: ASUS USB-BT500 (~US$15–20),
  Plugable USB-BT5 (~US$19), y los nano de Zexmte/MPOW (~US$8–10, verificando el USB ID al
  recibirlos).
- **2–3 parlantes por adaptador**; para 6 o más, uno por cada 2–3.
- **Evitar:** las variantes "long range" o de alta ganancia de BT 5.3+ (*"consistently
  underperform for A2DP streaming"*), incluido el **TP-Link UB500 Plus**; el **UB500 v3** (BT 5.4,
  sin confirmar); CSR8510 A10 (BT 4.0, silicio viejo, abundan clones) y Broadcom BCM20702.
- Para alcance, mejor **un cable de extensión USB** (alejar las radios entre sí y del PC) que una
  antena de alta ganancia.
- Los adaptadores baratos de "Bluetooth 5.3/5.4" con chip **Barrot** (p. ej. BR8554, en el UGREEN
  CM748/CM749) **no tienen driver oficial en Linux**; existe uno de la comunidad por ingeniería
  inversa (REPORTADO). Evitarlos.

Para la meta de 8 parlantes (d-7c8794-3b7793): 3–4 adaptadores, sin contar el AX210.

## 3. Lo que se decide ya en Linux para no cerrarles la puerta

Todo en línea con "mismo núcleo, otro backend emisor" (d-7c8794-9afee2) y con el foco
(d-7c8794-a5b83b). INFERIDO como diseño.

1. **Separar tres capas con contratos explícitos:**
   - *diseño*: Python diseña filtros, calibra y decide;
   - *render*: el motor recibe coeficientes y retardos como datos y entrega N canales;
   - *transporte*: PipeWire con A2DP, Picos por USB o Auracast.
   El motor no sabe de BlueZ ni de PipeWire. Ya va en esa dirección: la cadena con descriptores
   (d-7c8794-114c9c) y el trait de E/S del motor en Rust ([research/12](12-motor-de-audio-en-rust.md)).
2. **Dividir el motor en una etapa global** (lo que necesita L+R: la extracción de ambiente, el
   upmix) **y una etapa por canal** (EQ, decorrelador, retardo, ganancia, limitador). La global
   necesita el PC o la Zero; la por canal puede ir donde sea, incluso en una Pico por parlante.
3. **Fundir el decorrelador con el EQ** en un solo FIR por canal (ambos son lineales e
   invariantes): lo abarata en todas las plataformas.
4. **Perfiles válidos de la misma cadena:** una configuración "reducida por canal" (IIR en vez de
   FIR, retardo ≤ 100 ms, sin extractor) es un perfil del mismo contrato, que entiende una Pico.
5. **El contrato también por serie** (i-7c8794-a9f161): JSON por línea por un socket o USB CDC.
6. **Fuera del camino crítico, nada que solo exista en un PC de escritorio:** `pw-dump`
   periódico, `journalctl` y los procesos `pw-play`/`pw-record` detrás de interfaces.
7. **Frecuencias por perfil de hardware** (el lazo cada 20 s, el ruteo cada 2 s, el SSE a 20 Hz),
   no como constantes.
8. **`bumble` y `lc3py` como extras** de instalación; hoy ningún módulo los importa.
9. **El estado persistente en un directorio, con escrituras atómicas y pocas**, para que sirva un
   sistema de solo lectura.

Se aplican cuando toque cada pieza, sin un refactor aparte. Los puntos 1 y 2 son los que más
condicionan; entran al portar el motor a Rust.

## 4. ¿Dos proyectos?

**No.** Las ramas de git son ramas de trabajo, no productos:
- **La Zero 2 W** corre el mismo `aurasync` (servicio, contrato, cadena, calibración, PipeWire,
  BlueZ) con otro perfil de hardware.
- **La Pico** es firmware en C (TinyUSB, BTstack, SBC/LC3) que vive en `firmware/pico/` dentro del
  monorepo (d-7c8794-5c014a) y habla el mismo contrato por serie. Su "motor" es la cadena por
  canal del punto 3.4.
- **El panel** es un cliente del contrato, reutilizable con cualquier equipo (§5).

## 5. El panel independiente

Decisiones del usuario (2026-10-02, d-7c8794-37f9bc): la PWA se publica en **GitHub Pages desde
este repo** (GitHub Pro); "independiente" quiere decir que **existe sin el dispositivo, se
actualiza aparte y alivia a la Pi**; teléfonos **Android y iPhone**; **siempre por la red local**
por ahora; offline como PWA, con criterios como los de `thom-music-player`; **la app nativa al
final**.

**Lo que dicen los navegadores** (informe del 2026-10-02):
- **Chrome** (escritorio y Android, desde la 142) reemplazó Private Network Access por **Local
  Network Access**: un permiso que el usuario acepta una vez. Con él, una página HTTPS puede pedir
  a `http://` en una IP privada sin el bloqueo de contenido mixto (VERIFICADO).
- **Safari e iOS** no tienen esa excepción (WebKit agregó la comprobación de red local el
  2026-09-11, detrás de una preferencia, VERIFICADO). En el iPhone, todos los navegadores son
  WebKit: **una PWA HTTPS no llega a `http://IP`**. Por eso el dispositivo sirve su API **por
  HTTPS**.
- **Firefox** tiene permisos de red local desde la 144; la excepción de contenido mixto no se pudo
  verificar.
- La cookie `SameSite=Strict` de hoy no viaja entre orígenes: **`Authorization: Bearer`**.
  **`EventSource` no puede mandar cabeceras** (VERIFICADO en el estándar): el stream usa un ticket
  de un solo uso o `fetch` con `ReadableStream`.
- **HTTPS** también es lo que permite el micrófono del teléfono (`getUserMedia`), los service
  workers y Web Bluetooth.
- **Descubrimiento:** un navegador **no puede buscar servicios mDNS**, solo resolver un nombre
  `.local`. Queda probar `aurasync.local`, recordar la última IP o leer un QR (con los datos en el
  fragmento `#…`, que GitHub Pages nunca recibe). La app Android, cuando llegue, sí puede mDNS.

**El certificado:** el dispositivo genera una vez una **raíz autofirmada** y con ella firma su
certificado de servidor (para `aurasync.local` y su IP, < 398 días, la regla de Apple). Si la raíz
se instala en el teléfono, no hay avisos, y funciona en la PWA instalada del iPhone y sin
internet. Si no se instala, se abre la dirección del equipo y se acepta el aviso: funciona, pero
la excepción caduca y probablemente no pasa de Safari a la PWA instalada (a probar). **El token
autentica y autoriza; el certificado protege el token en tránsito**: sin la raíz instalada,
alguien en la red podría hacerse pasar por el equipo en el primer intercambio.

**Emparejamiento sin pantalla:** el primer cliente, en una ventana de ~10 minutos tras encender si
no hay ninguno (encenderlo prueba presencia); los siguientes, aprobados desde un cliente ya
emparejado o con un código. El código que suena por los parlantes no sirve para la primera vez
(una Pi nueva aún no tiene parlantes emparejados). Tokens por cliente, aleatorios, guardados solo
como hash, con alcance, revocables, y que no dependen del token maestro (tarjeta
*secrets-survive-rotation*).

**Lo que hay que medir antes de darlo por bueno:** una página en Pages que haga `fetch` a
`/v1/hello` del equipo en Chrome Android, Chrome de escritorio, Safari del iPhone (navegador y PWA
instalada) y Firefox, con la raíz instalada y sin ella.

### 5.1 Lo construido del lado del servicio (2026-10-02, Mac, servicio simulado)

- **La raíz es ECDSA P-256 por 10 años, con `NameConstraints`** que solo permiten `.local`,
  `localhost`, el hostname y direcciones privadas, de loopback, link-local y CGNAT: un teléfono
  que confía en ella **no queda expuesto a que alguien intercepte sitios públicos** con un
  certificado firmado por ella (VERIFICADO en el código y en los tests; que iOS y Android respeten
  `NameConstraints` es lo esperado por el estándar, a comprobar en los teléfonos). El certificado
  de servidor dura 397 días y se rehace solo si cambia la IP o le quedan 30 días.
- **Un perfil `.mobileconfig` para iOS** se genera con `plistlib` de la biblioteca estándar y se
  sirve en `/v1/tls/root.mobileconfig`, junto con `root.pem` y `root.crt` para Android.
- **mDNS con `avahi-publish`** como procesos hijos (apagado por defecto, nada en `/etc`), y no con
  `python-zeroconf`, que sería una dependencia nueva (LGPL-2.1-or-later según PyPI) y un segundo
  respondedor de mDNS en el equipo.
- **El preflight responde también la cabecera de Private Network Access** para Chromium más viejo
  que el que usa Local Network Access.
- **MEDIDO con `curl`:** HTTPS con la raíz → 200, sin ella → error de certificado; el primer
  cliente aprobado por la ventana de 10 minutos, con el token entregado una sola vez; un ticket de
  stream que sirve una vez; el preflight desde Pages → 204 y desde otro origen → 403.
- **Falta:** probar en teléfonos reales (Chrome Android, Safari del iPhone, la PWA instalada) con
  la raíz instalada y sin ella.

### 5.2 Lo construido del lado de la PWA (2026-10-02, Mac, Chromium contra el servicio simulado)

- **Un solo transporte** para el panel local (cookie) y la PWA (`Authorization: Bearer`, stream
  por ticket de un solo uso con `EventSource`): `host/web/src/transport.ts`. Se eligió el ticket y
  no `fetch` + `ReadableStream` porque app.js ya maneja un `EventSource` con sus reintentos.
- **Pantalla de conexión** en Preact: equipos recordados (IndexedDB, con localStorage de
  respaldo), agregar por dirección o por el QR (`#d=…&fp=…`; la PWA se niega a emparejar si la
  huella no coincide), los dos pasos cuando el certificado no es de confianza, emparejamiento con
  el número de comprobación, y la administración de clientes para un `admin`. El QR del panel
  local (`/pairing.svg`) **dejó de llevar el token maestro**: lleva ese enlace.
- **Service worker propio** con las reglas de `thom-music-player` (cache first con la página
  incluida, archivos con su SHA-256, actualización atómica que copia lo que no cambió, versión
  nueva aplicada sola salvo durante una calibración o un A/B), más dos propias: cada archivo
  bajado se **verifica contra su hash** antes de entrar a la caché, y **la API del equipo nunca
  pasa por el worker** (es otro origen y no se intercepta).
- **MEDIDO en Chromium** (Playwright 1.60, `tests_browser/test_pwa.py`, 10 tests, sirviendo
  `dist-pwa/` en `http://localhost:5173/bluetooth-sync/` con `Cache-Control: max-age=600` como
  Pages): agregar por dirección y por fragmento; emparejar por la ventana del primer cliente y por
  aprobación de un admin con el mismo número; En vivo con Bearer y ticket (ningún `token=` en una
  URL ni cookie hacia el equipo); revocar → la app pide volver a emparejar; abre **sin red** desde
  el service worker y dice "Sin conexión con <equipo>"; una versión nueva **espera** mientras corre
  un A/B y se aplica al terminarlo; otra, con la página libre, se aplica sola y baja solo
  `app.js` y `build.json` (más `sw.js`).
- Chromium confía en la raíz por `--ignore-certificate-errors-spki-list` con el SPKI de la raíz, y
  para eso el servidor del test manda la raíz detrás de su certificado; el servicio real manda
  solo el suyo (con la raíz instalada, alcanza). Ningún test usa `ignore_https_errors`.
- **Falta:** teléfonos reales (Chrome Android, Safari del iPhone, la PWA instalada en la pantalla
  de inicio), WebKit (Playwright no tiene el flag de SPKI y su service worker no se probó), la raíz
  instalada en un iPhone, y el aviso de Local Network Access de Chrome desde el origen real de
  Pages (desde `localhost` no aparece).

### 5.3 Calibrar con los teléfonos del panel (2026-10-03, propuesta). INFERIDO salvo lo marcado

**Diseño aprobado y en construcción:** [superpowers/specs/2026-10-03-sync-estimator-design.md](../superpowers/specs/2026-10-03-sync-estimator-design.md) (roadmap i-7c8794-737d4e).

**El pedido (usuario, 2026-10-03):** que los teléfonos conectados al panel graben con su micrófono
el patrón invisible mientras suena la música y manden la calibración al servidor, para
recalibrar cuando haga falta; y que el patrón **distinga a cada parlante**, para calibrar de a
partes cuando un micrófono no oye a todos.

- **El patrón no se puede compartir como semilla.** La sonda (`dsp/probe.py`) es un ruido propio
  por parlante, pero su nivel sigue cuadro a cuadro y por tercio a la música de ese parlante, y
  pasa por el limitador: el teléfono no la regenera desde la semilla (VERIFICADO, código).
- **Dos caminos.** (A) el teléfono graba 4–8 s y los sube (mono, 16 bits, 48 kHz: ~96 KB/s mientras
  mide) y el servidor corre el estimador que ya existe (`probe_measure.py`); (B) el teléfono
  calcula, con la referencia de cada parlante (~192 KB/s por parlante en float32) o con semilla y
  ganancias por banda y el mismo PCG64 que numpy, bit a bit. **Recomendado: A**, porque reusa un
  estimador validado; B después, si importa que el audio no salga del teléfono (WASM desde el
  motor en Rust de research/12).
- **El reloj y la latencia del teléfono se cancelan:** lo que se corrige es la diferencia de
  llegada entre parlantes dentro de una misma grabación; un retardo común a todos no cambia nada.
  Para alinear la grabación con la referencia basta estimar el reloj del servidor con unos pocos
  ida y vuelta por HTTP y buscar la correlación en una ventana amplia, como hoy.
- **Distinguir parlantes: ya lo hace la sonda (SIMULADO,** [experimentos/16](experimentos/16-ocho-parlantes-en-simulacion.md)
  §4.1.1): un micrófono que oye solo a algunos mide esos y rechaza los demás, sin falsos
  positivos. Hacía falta corregir el consenso, que se tomaba sobre todos (corregido el
  2026-10-03). Una medición necesita al menos **dos** parlantes oídos para decir algo de
  alineación (`arrival_loop`: una sola llegada no tiene con qué compararse).
- **Lo que falta para combinar teléfonos:** cada posición mide el retardo electrónico más su
  diferencia de camino (~3 ms por metro). Lo que hay que corregir es el electrónico, igual en toda
  la pieza (research/03). Hoy el lazo sigue cada parlante en una sola pista, sin saber de qué
  micrófono vino la medición: mediciones de dos posiciones se pelearían. Hace falta llevar la
  pista por (micrófono, parlante) y combinar las diferencias de llegada de grabaciones que
  comparten parlantes (un sistema de mínimos cuadrados sobre el grafo "quién oyó a quién"; con el
  plano de los parlantes y varias posiciones se separa lo acústico). Teléfono A oye {Red, Black},
  B oye {Black, Blue}: Black los encadena.
- **Riesgos a medir primero:** que el navegador apague su procesado de voz (`echoCancellation`,
  `noiseSuppression`, `autoGainControl` en false; la supresión de ruido se comería una sonda que
  es ruido; Safari en iPhone no siempre lo respeta); que `getUserMedia` exige HTTPS (§5, el
  servicio ya lo trae); que una medición de un teléfono es dato no confiable (permiso de control
  de `access.py`, los mismos filtros, y nunca mover retardos con una sola); y que la sonda está
  apagada por defecto hasta el A/B ciego (i-7c8794-e3e40d, paso 4).

## 6. Dudas abiertas para el usuario

- ~~¿Cuántos parlantes a futuro?~~ **Hasta 8**, como meta para probar los límites (d-7c8794-3b7793). Las demás dudas quedan para más adelante (usuario, 2026-10-02).
- ¿Se prueba primero el camino barato (dos adaptadores en Linux) o la Pico como emisor A2DP?
  Comprar adaptadores o más Picos se decide antes.
- Para la Pi: ¿producto con música por red (AirPlay, Spotify Connect), como tarjeta USB del PC, o
  ambos? ¿Hay Wi-Fi de 5 GHz donde se usará? ¿Zero 2 W o una Pi 4/5 con más puertos?
- ¿El micrófono queda en la caja (calibra donde está la caja) o se calibra con el teléfono?

## Fuentes

Los informes completos, con todas las URLs: `scratchpad/inv/G-pi-zero-hardware.md` y
`scratchpad/inv/H-panel-independiente.md` (2026-10-02). Las principales:
- Raspberry Pi: documentación de la Zero 2 W, `overlays/README`, el whitepaper *Using OTG mode*,
  y el anuncio de precios de 2026.
- Benchmarks de FFT de Roy Longbottom — http://www.roylongbottom.org.uk/FFTBenchmarks.htm
- ARM, *DSP capabilities of Cortex-M4 and Cortex-M7* (tabla de RFFT F32).
- pico-examples, `bluetooth/config/btstack_config_common.h`; BTstack `a2dp.c`, `hci_cmd.c`, issue
  #143.
- USBPods-Pico2W — https://github.com/wasdwasd0105/USBPods-Pico2W
- sendspin-bt-bridge (adaptadores y coexistencia) — https://trudenboy.github.io/sendspin-bt-bridge/bluetooth-adapters/
- raspberrypi/linux#5293 (coexistencia y A2DP en Pi).
- CamillaDSP — https://github.com/HEnquist/camilladsp
- Chrome, Local Network Access (Chrome 142); WebKit, Local Network Access (2026-09-11); WHATWG,
  `EventSource`; MDN, contextos seguros.
- `thom-music-player` (del usuario): `docs/platform-web.md` §Offline y `tools/service_worker.js`.
