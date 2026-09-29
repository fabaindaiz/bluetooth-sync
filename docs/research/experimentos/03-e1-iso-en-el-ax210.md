# 03 · E1: ¿el Intel AX210 puede transmitir por ISO?

**Pregunta:** ¿el controlador de este equipo puede crear un BIG, o sea transmitir
Auracast? (i-7c8794-3f730a)

**Respuesta: no. MEDIDO, y por dos caminos independientes.**

**Entorno:**
- `PC-Ryzen5`, **Intel AX210**, firmware Bluetooth **`202-5.26`** (timestamp 2026.5,
  buildtype 1, build 82122, SHA1 `0x2925677d`), `intel/ibt-0041-0041.sfi`.
- Kernel **7.2.7-1-cachyos**, BlueZ **5.87**, MGMT 1.23.
- Fecha: 2026-09-28. Sin parlantes involucrados: es una lectura del controlador.

**Datos crudos:** [datos/03/](datos/03/) — la salida del probe
(`01-capacidades.txt`), la traza HCI del arranque del controlador
(`hci-init.btsnoop`) y su decodificación (`hci-init-decodificado.txt`).

**Qué se ejecutó:**
```bash
probes/e1-iso/01-capacidades.sh      # lee LE Features, btmgmt info y /proc/net/protocols
probes/e1-iso/02-activar-iso.sh      # activa el socket ISO experimental de BlueZ
# traza independiente del arranque del controlador:
sudo btmon -w hci-init.btsnoop &
sudo btmgmt power off && sudo btmgmt power on
```

## Resultado

### 1. Los bits de LE Features del controlador: MEDIDO
```
LE: ff 59 01 3c ae 00 00 00
```
| Bit | Capacidad | ¿Está? |
|---|---|---|
| 12 | LE Extended Advertising | **sí** |
| **13** | **LE Periodic Advertising** | **NO** |
| 28 | Connected Isochronous Stream - Central | **sí** |
| 29 | Connected Isochronous Stream - Peripheral | **sí** |
| **30** | **Isochronous Broadcaster** | **NO** |
| **31** | **Synchronized Receiver** | **NO** |

### 2. Los comandos HCI que declara: MEDIDO
En la traza del arranque, la lista de Supported Commands tiene **solo comandos
CIS** (`LE Set CIG Parameters`, `LE Create CIS`, `LE Setup ISO Data Path`, los de
test…). Un `grep` de los comandos de transmisión y de sincronización da **cero**
resultados:

```
LE Set Periodic Advertising Parameters   → ausente
LE Create BIG                            → ausente
LE BIG Create Sync                       → ausente
LE Periodic Advertising Create Sync      → ausente
```

### 3. Lo que ve BlueZ: MEDIDO
```
supported settings: … le advertising … cis-central cis-peripheral ll-privacy
```
`iso-broadcaster` y `sync-receiver`: **ausentes**. Coincide con los bits, como tenía
que ser, porque BlueZ los deriva de ahí.

### 4. El socket ISO del kernel sí funciona: MEDIDO
Con `Experimental = true` y `KernelExperimental = 6fbaf188-…`, el protocolo ISO
aparece en `/proc/net/protocols` y se puede abrir el socket:

```python
socket.socket(AF_BLUETOOTH, SOCK_SEQPACKET, BTPROTO_ISO)   # OK
```

Sin la bandera devuelve `EPROTONOSUPPORT`. **Es una limitación del controlador, no
del kernel ni de la distribución.** Este kernel no tiene ningún `CONFIG_BT_*ISO*`:
el código va con `CONFIG_BT_LE=y`, y la bandera experimental es lo que lo registra.

### 5. Códecs sobre LE CIS: MEDIDO
`Read Local Supported Codecs V2` lista 6 códecs, y el único marcado **"Codec
supported over LE CIS"** es `Transparent (0x03)`. O sea que **el controlador no hace
LC3**: la codificación va en software, en el host. Para LE Audio en Linux es lo
normal (PipeWire y BlueZ codifican LC3 con liblc3, que está instalado).

## Veredicto

- **E1 responde NO, y cierra el camino A con el chip interno de este equipo.** El
  AX210 con firmware `202-5.26` no puede crear un BIG. **Lo que estaba REPORTADO en
  [02](../02-le-audio-auracast-linux.md) §2 ("el firmware solo soporta CIS") pasa a
  MEDIDO**, con el firmware y la fecha anotados.
- **Peor de lo que se esperaba en un punto:** tampoco tiene **LE Periodic
  Advertising (bit 13)** ni **Synchronized Receiver (bit 31)**. Entonces este
  adaptador **tampoco puede leer la BASE ni el BIGInfo** de los propios JBL, que es
  la mitad que falta de E2 (i-7c8794-a999d3). E2 también necesita otro controlador.
  Antes de esta medición se suponía que el AX210 podría al menos escuchar.
- **Mejor de lo que se esperaba en otro:** tiene **CIS Central y CIS Peripheral**, y
  el socket ISO del kernel funciona. Con los **JBL Tune 770NC**, que exponen PACS y
  ASCS ([02](02-servicios-de-los-jbl-linux.md)), se puede probar **LE Audio unicast
  de punta a punta en este equipo**. Eso valida el camino ISO completo (bandera,
  socket, LC3, PipeWire, presentation delay) sin depender de poder transmitir. Cuando
  lleguen las SuperMini, lo único nuevo a depurar sería el emisor.
- **Qué cambia en el plan:** E1 queda **Hecho**. E2, E3, E4 y E5 quedan
  **bloqueados por hardware** hasta que lleguen las SuperMini nRF52840
  (d-7c8794-b82ee9). No hace falta decidir ninguna compra nueva: la opción 1 ya está
  comprada y es justo la que resuelve esto.

## Qué salió mal en el camino (y es un dato reutilizable)

El primer intento de activar el socket ISO **falló en silencio**. `main.conf` tenía:

```ini
KernelExperimental = 6fbaf188-05e0-496a-9885-d6ddfdb4e03e   # socket ISO
```

y bluetoothd respondió:

```
src/main.c:btd_parse_kernel_experimental() Invalid KernelExperimental UUID:
6fbaf188-05e0-496a-9885-d6ddfdb4e03e   # socket ISO
```

**BlueZ no recorta el comentario al final de la línea: lo lee como parte del UUID y
descarta el valor.** El servicio arranca igual y no avisa por otro lado. Lo detectó
el paso de verificación del probe, que comprueba `/proc/net/protocols` en vez de
confiar en que el reinicio alcanzó. **Los comentarios van en líneas aparte.**

## Estado del sistema

`/etc/bluetooth/main.conf` quedó **con el socket ISO activado**, porque el próximo
experimento (unicast con los Tune 770NC) lo necesita.

**Se revierte con:**
```bash
probes/e1-iso/03-revertir.sh          # verifica por md5 que quedó como estaba
sudo rm /etc/sudoers.d/bluetooth-sync # la regla de sudo, al terminar
```
