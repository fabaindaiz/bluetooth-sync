# 01 · E2 parcial: qué anuncian los JBL, visto desde CoreBluetooth

**Pregunta:** ¿qué datos de fabricante y de servicio anuncian el Go 4 y el Charge 6,
en reposo y mientras transmiten Auracast? ¿Cambia el código de fabricante entre
modelos? Corresponde a i-7c8794-a999d3 (E2), en su versión parcial: sin la BASE ni
el BIGInfo.

**Entorno:**
- Mac con Apple Silicon, macOS 27.0 (26A428). Controlador MTK_7932 por PCIe (ver
  [00-inventario-mac.md](00-inventario-mac.md)).
- `bleak` 3.0.2 sobre CoreBluetooth, en un entorno virtual temporal con Python
  3.14.
- Parlantes: 1× **JBL Go 4 negro (black)** y 1× **JBL Charge 6 morado (purple)**.
  Los colores los informó el usuario. El firmware real no se leyó (macOS reporta
  "1.0.0", que no es la versión real).
- Fecha: 2026-09-26.

**Qué se ejecutó:**
```bash
python probes/e2-scan-mac/scan.py <segundos>
```
El probe primero dice cuántos dispositivos vio en total y después los que traen
datos de Harman (0x0057) o un nombre JBL. En el documento, **los bytes propios de
cada parlante están enmascarados (`xx`)**, porque podrían identificar al
dispositivo concreto.

## Resultado 1: parlantes en reposo (encendidos, sin conexión) — MEDIDO

| Escaneo | Duración | Dispositivos vistos | Con datos Harman o nombre JBL |
|---|---|---|---|
| Sin JBL encendidos | 15 s | 40 | 0 |
| Go 4 y Charge 6 encendidos | 20 s | 50 | 1 (solo el Go 4) |
| Go 4 y Charge 6 encendidos | 30 s | 45 | 2 |

En el escaneo de 20 s no apareció el Charge 6. Su anuncio llega con menos
frecuencia o con más variación de alcance: en el escaneo de 30 s su RSSI fue de
-65 a -19. **Hay que escanear 30 s o más para no perder parlantes.**

| | JBL Go 4 | JBL Charge 6 |
|---|---|---|
| Nombre anunciado | `JBL Go 4 Bl` | `JBL Charge6` |
| Datos de fabricante 0x0057 (10 bytes) | `e4 20 01 38 xx xx xx xx 09 00` | `e3 20 14 38 xx xx xx xx 09 00` |
| Service data 0xFDDF | presente, vacío | presente, vacío |
| Service data 0xFE2C (Google Fast Pair) | — | `81 4c 5f` |

### Lectura de los bytes
- **Bytes 0–1 = identificador de modelo, en little-endian.**
  - Charge 6: `e3 20` = **0x20E3**, igual al Product ID que reporta macOS y al que
    documenta openjbl. MEDIDO.
  - Go 4: `e4 20` = 0x20E4. Que sea su PID es INFERIDO por analogía; macOS no
    reporta un PID para el Go 4.
- **Byte 2:** `01` en el Go 4 negro y `14` en el Charge 6 morado. **Hipótesis:
  codifica el color** (INFERIDO). El resultado 4 la refuerza: Go 4 rojo = `02`,
  azul = `03`. Lo apoya que el Go 4 se anuncie en reposo como
  `JBL Go 4 Bl` ("Bl" = black). Se puede comprobar con los otros dos Go 4: si son
  de otro color y el byte 2 cambia, se confirma. Si son negros, deberían dar `01`.
- **Byte 3:** `38` en los dos.
- **Bytes 4–7:** distintos en cada parlante. Probablemente sean un identificador
  propio de cada dispositivo (INFERIDO). No coinciden con su dirección Bluetooth.
- **Bytes 8–9:** `09 00` en los dos. Podría ser un campo de estado (INFERIDO); a
  confirmar comparando con el modo transmisor.
- **El UUID 0xFDDF escrito en little-endian es `df fd`**, justo el sufijo del valor
  que usan Bumble (`87:…dffd`) y Collabora (`0xdf 0xfd`) en la transmisión. Así
  que ese sufijo **no es una firma arbitraria: es el UUID de servicio de JBL
  (0xFDDF) metido en los datos de fabricante** (INFERIDO con confianza alta). Los
  UUID de 16 bits 0xFDxx los asigna el SIG a empresas miembro; que 0xFDDF esté
  asignado a Harman no se verificó.
- **Fast Pair:** el Charge 6 anuncia el model ID `0x814C5F` (MEDIDO). Coincide con
  que openjbl vio Fast Pair (`FE2C`).
- **El anuncio en reposo no es el mismo formato que el de Auracast.** El valor de
  Bumble tiene 18 bytes (16 ceros y `dffd`); el anuncio en reposo tiene 10 bytes
  con el modelo al principio. Falta ver el anuncio en modo transmisor (resultado 2).

## Resultado 2: Go 4 transmitiendo Auracast — MEDIDO

El Go 4 estaba conectado por A2DP a un teléfono, reproduciendo música, y se
presionó el botón Auracast. El Charge 6 estaba apagado. Escaneo de 30 s: 75
dispositivos vistos, 1 con datos Harman o nombre JBL.

| Campo | Valor |
|---|---|
| Nombre anunciado | El nombre que el usuario le puso al parlante (no el nombre de modelo). Omitido aquí por privacidad |
| Datos de fabricante 0x0057 (18 bytes) | **`00000000000000000000000000000000dffd`** |
| Service data **0x1852** (Broadcast Audio Announcement) | `05 81 00` → **Broadcast_ID 0x008105** (3 bytes, little-endian) |
| Anuncio en reposo (0x0057 de 10 bytes + 0xFDDF) | **No apareció** mientras transmitía |

### Lectura
- **CoreBluetooth en macOS sí entrega el anuncio extendido de una transmisión
  Auracast.** Se vio el service data 0x1852, que solo existe en el anuncio
  extendido de un emisor BAP. Esto resuelve la duda que dejó
  [00-inventario-mac.md](00-inventario-mac.md).
- **Los datos de fabricante de la transmisión del Go 4 son idénticos, byte a byte,
  al valor que documenta Bumble** (`87:00000000000000000000000000000000dffd`).
  **MEDIDO.** Confirma que el receptor JBL filtra por este bloque y que Bumble lo
  copió del propio parlante.
- El formato es distinto del anuncio en reposo: 16 bytes en cero seguidos del UUID
  0xFDDF en little-endian, sin identificador de modelo. **Si el Charge 6 transmite
  lo mismo, un emisor propio puede usar un único valor para los dos modelos**
  (resultado 3).
- **No se vio service data 0x1856 (Public Broadcast Announcement).** El JBL no
  anuncia PBP (INFERIDO; CoreBluetooth sí entregó 0x1852, así que probablemente
  entregaría 0x1856 si existiera).
- `bleak` no expone el AD type Broadcast Name (0x30). Si JBL lo usa, no se vio.
- El Broadcast_ID puede ser aleatorio en cada transmisión o fijo por parlante. Se
  sabrá repitiendo la transmisión.

## Resultado 3: Charge 6 transmitiendo Auracast — MEDIDO

El Charge 6 estaba conectado por A2DP a un teléfono, reproduciendo música, y se
presionó el botón Auracast. El Go 4 no estaba transmitiendo. Escaneo de 30 s: 80
dispositivos vistos, 2 anuncios con datos Harman, ambos del Charge 6.

### Anuncio de transmisión
| Campo | Valor |
|---|---|
| Nombre anunciado | `JBL Charge 6`, el nombre de modelo (el Go 4 usaba el nombre que le puso el usuario) |
| Datos de fabricante 0x0057 (18 bytes) | **`00000000000000000000000000000000dffd`**, idénticos a los del Go 4 |
| Service data **0x1852** (Broadcast Audio Announcement) | `33 22 11` → **Broadcast_ID 0x112233** |
| Service data **0x1856** (Public Broadcast Announcement) | **`04 00`** |

### Anuncio de reposo, que sigue presente mientras transmite
| | En reposo (resultado 1) | Transmitiendo |
|---|---|---|
| Datos de fabricante 0x0057 | `e3 20 14 38 xx xx xx xx 09 00` | `e3 20 14 **39** xx xx xx xx **0b** 00` |
| Service data 0xFDDF | vacío | vacío |

### Lectura
- **Un solo valor de datos de fabricante sirve para los dos modelos**: el Go 4 y el
  Charge 6 transmiten los mismos 18 bytes. MEDIDO. Esto resuelve la duda de
  [01-parlantes-jbl.md](../01-parlantes-jbl.md) y de la documentación de Bumble,
  que decía que "dffd" podía cambiar entre modelos. Al menos entre estos dos, no
  cambia.
- **El Charge 6 anuncia PBP y el Go 4 no.** Lectura del byte de funciones `04`
  según la especificación PBP (INFERIDO; no se contrastó con el texto de la
  especificación en esta sesión): bit 0 = 0, **sin cifrar**; bit 2 = 1, hay una
  configuración de **alta calidad** (48 kHz). El `00` siguiente es un largo de
  metadata igual a cero.
  - **Consecuencia:** la transmisión del Charge 6 **no va cifrada**, así que un
    receptor propio (Bumble `receive`) podría leer su BASE sin Broadcast Code.
  - Del Go 4 no se sabe, porque no anunció 0x1856.
- **Broadcast_ID 0x112233:** tiene toda la pinta de un valor fijo o de prueba del
  firmware, no de uno aleatorio (INFERIDO). Si todos los Charge 6 usan el mismo ID,
  dos Charge 6 transmitiendo cerca podrían confundir a los receptores. Se puede
  confirmar repitiendo la transmisión.
- **Bits de estado en el anuncio de reposo:** al transmitir, el byte 3 pasó de
  `38` a `39` (bit 0) y el byte 8 de `09` a `0b` (bit 1). Probablemente indican
  "Auracast activo" o "rol transmisor" (INFERIDO). La app JBL Portable podría usar
  estos bits para mostrar el estado sin conectarse.
- En el resultado 2 no se vio el anuncio de reposo del Go 4 mientras transmitía. No
  se sabe si el Go 4 lo apaga o si el escaneo no lo alcanzó a ver.

## Resultado 4: dos Go 4 en estéreo JBL, reproduciendo — MEDIDO

Los dos Go 4 ya estaban sincronizados en modo estéreo JBL y reproduciendo música;
no estaban en proceso de emparejamiento. Se hicieron dos escaneos: 30 s (93
dispositivos vistos) y 45 s (99 vistos). En ambos aparecieron 2 anuncios con datos
Harman y los mismos valores.

| Anuncio | Byte 0–1 | Byte 2 | Byte 3 | Bytes 4–7 | Bytes 8–9 | 0xFDDF |
|---|---|---|---|---|---|---|
| Go 4 **negro**, solo en reposo (resultado 1, anunciado como `JBL Go 4 Bl`) | `e4 20` | `01` | `38` | `xx xx xx xx` | `09 00` | vacío |
| Go 4 **rojo**, en estéreo (`JBL Go 4 Re`) | `e4 20` | **`02`** | `d4` | `ed 0e xx xx` | `09` **`60`** | vacío |
| Go 4 **azul**, en estéreo (`JBL Go 4 Bl`) | `e4 20` | **`03`** | `f8` | `00 00 xx xx` | `09` **`60`** | vacío |

Los colores los informó el usuario. **El Go 4 azul y el negro se anuncian con el
mismo nombre abreviado (`Bl`)**, así que el nombre no sirve para distinguirlos.

**No apareció ningún service data 0x1852 (Broadcast Audio Announcement) ni 0x1856
en ninguno de los dos escaneos.**

### Lectura
- **La sincronización estéreo de JBL no se ve como una transmisión Auracast
  pública desde CoreBluetooth.** El mismo probe sí detectó el 0x1852 en los
  resultados 2 y 3, así que la ausencia no es una falla del escaneo. Hay tres
  explicaciones posibles (todas INFERIDAS):
  1. **El transmisor apaga el anuncio extendido cuando el secundario ya se
     sincronizó**, y solo mantiene los anuncios periódicos y el BIG. En el modo
     fiesta, en cambio, el anuncio sigue activo para que se unan más parlantes.
     **Es la hipótesis más probable**, y se puede probar escaneando *durante* el
     emparejamiento estéreo.
  2. El estéreo usa otro mecanismo (un enlace LE conectado, CIS, o algo
     propietario) y no un BIG.
  3. CoreBluetooth filtra ese anuncio por algún motivo (por ejemplo, una dirección
     privada). Es poco probable, porque entregó los anuncios del modo fiesta.
- **El byte 2 codifica el color** (INFERIDO con confianza media): Go 4 negro =
  `01`, rojo = `02`, azul = `03`; Charge 6 morado = `14`. Son tres unidades
  distintas con tres valores distintos. Otra lectura posible era el rol en el par
  estéreo; se descarta porque el Go 4 negro dio `01` estando solo, y el par
  estéreo lo formaban el rojo y el azul. Se confirma con el mismo parlante en los
  dos modos: por ejemplo, el rojo solo debería seguir dando `02`.
- **Los bytes 8–9 pasan a `09 60` en estéreo**: los bits 5 y 6 del byte 9 marcarían
  "en grupo estéreo" (INFERIDO). En el Charge 6 transmitiendo en modo fiesta, el
  cambio fue otro: byte 3 bit 0 y byte 8 bit 1.
- **Los bytes 4–7** son distintos en cada unidad, lo que es coherente con un
  identificador por dispositivo. Pero los bytes 4–5 del rojo (`ed 0e`) coinciden
  con los del Charge 6, así que no es seguro. Pueden depender en parte de otra cosa
  (INFERIDO).
- **Qué implica para el proyecto:** para saber cómo transporta L/R el propio JBL,
  CoreBluetooth no alcanza. Hace falta:
  - escanear durante el emparejamiento estéreo, por si el anuncio extendido
    aparece solo en ese momento, o
  - un sniffer de anuncios periódicos y BIS (Bumble `scan` con un controlador
    accesible, o `auracast-hackers-toolkit`, ver
    [04](../04-implementaciones-y-stacks.md) §6).

**Pendiente:** cuál quedó como izquierdo y cuál como derecho. También medir una
misma unidad sola y en estéreo, para separar lo que depende del color de lo que
depende del modo.

## Veredicto

- **CoreBluetooth en macOS sirve para leer tanto los anuncios de reposo como los
  anuncios extendidos de transmisión Auracast de los JBL.** El Mac sirve para esta
  parte de E2 sin hardware adicional.
- **Datos de fabricante para un emisor propio:**
  `87:00000000000000000000000000000000dffd` (0x0057 + 16 ceros + UUID 0xFDDF en
  little-endian). Un único valor para el Go 4 y el Charge 6. MEDIDO.
- **La transmisión del Charge 6 no va cifrada** (según su anuncio PBP; la lectura
  del byte es INFERIDA). La del Go 4 está por confirmar.
- **El estéreo JBL no expone una transmisión Auracast visible** mientras
  reproduce (resultado 4). En el anuncio de reposo, el byte 2 parece indicar el
  color y el `60` del byte 9 marcaría el modo estéreo.
- **Queda pendiente** para E2 completo: la BASE (cuántos BIS, qué ubicaciones y qué
  presentation delay) y el BIGInfo. Están en los anuncios periódicos, que
  CoreBluetooth no entrega. Hacen falta Bumble `scan` con un adaptador que el
  host pueda controlar directamente, o el equipo Linux.
- **Qué cambia en otros documentos:**
  - [01-parlantes-jbl.md](../01-parlantes-jbl.md): el Charge 6 usa los mismos
    datos de fabricante, y su transmisión va sin cifrar.
  - [decisions.md](../../decisions.md) d-7c8794-1b2706: sigue en pie. Los teléfonos
    no ponen este bloque.
