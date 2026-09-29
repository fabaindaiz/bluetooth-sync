# 02 · Qué servicios exponen los JBL, leídos desde Linux

**Pregunta:** ¿los JBL Go 4 y el Charge 6 exponen **BASS** (0x184F) y **PACS**
(0x1850)? De eso dependen los dos mecanismos por los que un receptor elige su BIS
([02](../02-le-audio-auracast-linux.md) §4), y con ellos E4 (i-7c8794-eeac13) y el
asistente BASS (i-7c8794-365524).

**Entorno:** `PC-Ryzen5`, Intel AX210 (firmware `202-5.26`), BlueZ 5.87, kernel
7.2.7-1-cachyos. Fecha: 2026-09-28. Los cuatro dispositivos están **emparejados**
con este equipo desde antes.

**Qué se ejecutó** (solo lectura, sin root, con los parlantes **apagados**):
```bash
bluetoothctl devices Paired
bluetoothctl info <dirección>     # para cada JBL
bluetoothctl --timeout 15 scan le
```

**Actualizado el 2026-09-28 con los parlantes encendidos y conectados.** La primera
versión de este experimento se basaba en la caché de BlueZ, con los parlantes
apagados. Ahora hay medición en vivo, y la conclusión no cambió: se reforzó. Los
datos crudos de esta segunda pasada están en
[datos/02/gatt-en-vivo.txt](datos/02/gatt-en-vivo.txt).

## Resultado: MEDIDO (con el límite de arriba)

### Los parlantes no muestran ni un servicio LE Audio
| Dispositivo | Perfiles que expone |
|---|---|
| JBL Go 4 Black (`90:F2:60:DA:66:6D`) | A2DP Sink, AVRCP (target y control), Headset, Handsfree, GATT (0x1801), 2 vendor |
| JBL Go 4 Red (`90:F2:60:75:4A:83`) | lo mismo, **más Serial Port (SPP, 0x1101)** |
| JBL Charge 6 (`78:66:F3:93:1D:B7`) | A2DP Sink, AVRCP, PnP, GATT (0x1801), 2 vendor |

**Ninguno lista PACS (0x1850), ASCS (0x184E), BASS (0x184F), VCS (0x1844), CAS
(0x1853) ni TMAS (0x1855).**

### Los audífonos Tune 770NC sí muestran el stack LE Audio completo
`88:92:CC:68:91:C0` expone **VCS (0x1844), MICS (0x184D), ASCS (0x184E), PACS
(0x1850), CAS (0x1853) y TMAS (0x1855)**, además de los perfiles clásicos.

Esto es importante por dos razones:
1. **Es el control del método.** El mismo equipo, el mismo adaptador y el mismo
   emparejamiento por BR/EDR **sí** dejan ver los servicios LE Audio de un
   dispositivo que los tiene. Debilita bastante la explicación "BlueZ no los vio
   porque el emparejamiento fue clásico", aunque no la descarta: los Tune podrían
   haberse emparejado también por LE.
2. **Los Tune 770NC sirven de receptor de prueba LE Audio** en unicast (CIS), que es
   lo único que los reportes le atribuyen al AX210
   ([02](../02-le-audio-auracast-linux.md) §2). Con ellos se puede validar todo el
   camino ISO de Linux (bandera experimental, socket ISO, LC3, PipeWire) sin
   depender de que el AX210 pueda transmitir.

**Falta BASS (0x184F) también en los Tune.** Tiene sentido: BASS lo expone un
receptor que acepta que un asistente le diga a qué BIS engancharse, y estos son
audífonos unicast.

### Con los parlantes conectados: no aparece ni un objeto GATT: MEDIDO
`busctl tree org.bluez` muestra qué objetos crea BlueZ para cada dispositivo. Con el
**Go 4 Black y el Charge 6 conectados** (`ServicesResolved: yes` en el Charge 6):

```
dev_90_F2_60_DA_66_6D        ← Go 4 Black
├─ avrcp/player0
└─ sep2/fd3                  ← endpoint A2DP, y nada más

dev_78_66_F3_93_1D_B7        ← Charge 6
├─ sep1
└─ sep2/fd4                  ← endpoints A2DP, y nada más

dev_88_92_CC_68_91_C0        ← Tune 770NC
├─ pac_sink0/{fd0,fd1}       ← PACS: BlueZ crea endpoints LE Audio
├─ pac_source0/fd2
├─ service0001/char{0002,0004,0006,0008}
├─ service0011/char{0012,0016,0018,001a}
└─ service0031/char0032
```

**Los dos parlantes no tienen ni un `service00XX` ni un `pac_sink`.** Los dos se
conectaron por **BR/EDR** (`BREDR.Connected: yes`, y no hay sección `LE.*`), y ahí
está el límite que queda: BlueZ no hizo descubrimiento GATT por LE.

### El Tune sí anuncia sus servicios LE Audio, y los parlantes no anuncian nada: MEDIDO
Un `scan le` de 25 s con todo encendido. El Tune 770NC trae **ServiceData de LE
Audio** en su anuncio:

| Servicio | ServiceData |
|---|---|
| `0000184e` ASCS | `00ff0fff0f00` |
| `00001855` TMAS | `2a00` |
| `00001853` CAS | `00` |

y declara `1843` (AICS), `1844` (VCS), `1845` (VOCS), **`1846` (CSIS)**, `184d`
(MICS), `184e` (ASCS), **`1850` (PACS)**, `1853` (CAS), `1855` (TMAS). **Sigue sin
`184F` (BASS)**, lo que es coherente: es un audífono unicast, no un receptor de
transmisiones.

El `1846` (CSIS, Coordinated Set Identification) explica el *device set* que se vio en
[E8](04-e8-unicast-le-audio-tune-770nc.md): los dos auriculares son un conjunto
coordinado.

**Los dos Go 4 y el Charge 6: ni ServiceData ni ManufacturerData en el anuncio**, y
BlueZ los trata como dispositivos BR/EDR.

### Los UUID de fabricante cambian por unidad: MEDIDO
Todos los JBL comparten `df21fe2c-2515-4fdb-8886-f12c4d67927c`, y además tienen uno
con el prefijo ASCII `excelpoint.`:

| Dispositivo | UUID vendor |
|---|---|
| Go 4 Black | `65786365-6c70-6f69-6e74-2e04ffe42001` (termina en **2001**) |
| Go 4 Red | `65786365-6c70-6f69-6e74-2e04ffe42002` (termina en **2002**) |
| Charge 6 | `65786365-6c70-6f69-6e74-2e04ffe32014` (termina en **2014**) |

Los dos Go 4 son el mismo modelo y **terminan distinto** (`2001` y `2002`), así que
los últimos bytes no identifican el modelo. Si son un índice de unidad o el rol en
un par estéreo, no se sabe: INFERIDO, y vale la pena mirarlo cuando se repita E2 con
el par estéreo formado.

El `df21fe2c-…` es candidato a ser el canal de control de JBL. Si lo es, **el
firmware de cada parlante se puede leer desde este equipo**, que es un dato que el
README de experimentos exige anotar en cada medición y que hoy solo se sabía sacar
con la app de JBL o con openjbl ([04](../04-implementaciones-y-stacks.md) §6). Sin
verificar.

## Veredicto

- **La evidencia sobre la pregunta que bloquea E4(b) es fuerte y va en contra:** ni
  el Go 4 ni el Charge 6 exponen BASS (0x184F) ni PACS (0x1850). Medido con los
  parlantes **conectados**, con un control interno que funciona: el Tune 770NC, en el
  mismo equipo y el mismo adaptador, sí muestra sus servicios LE Audio, tanto en el
  anuncio como en objetos de BlueZ.
- **Si esto se confirma por LE, las dos vías del estándar están cerradas.** El
  asistente BASS (i-7c8794-365524) no sería viable, y E4(a) tampoco, porque sin PACS
  el parlante no publica sus Sink Audio Locations. La asignación de canal dependería
  de algo propietario de JBL.
- **Y es coherente con lo que se sabe de estos parlantes:** un receptor de
  transmisiones Auracast **no necesita** BASS ni PACS para funcionar. Encuentra la
  transmisión solo, con su botón. BASS es el mecanismo *opcional* para que un
  asistente le diga a qué BIS engancharse, y estos parlantes parecen no
  implementarlo.
- Confirma y refuerza lo que decía [05](../05-opcion-a-bumble.md) §resumen ("no hay
  reportes de que un JBL exponga BASS o PACS"), que además observaba que los JBL se
  enganchan solos con el botón, sin conexión clásica.

**Lo que todavía falta para cerrarlo, y es un solo caso:** los dos parlantes se
conectaron por **BR/EDR**, así que BlueZ no enumeró GATT por LE. Falta ver un Go 4
**en modo Auracast**, que es el único estado en que se lo vio anunciar por LE
(en el Mac, [01](01-e2-anuncios-jbl-mac.md), con datos de fabricante `87:…dffd`). Si
en ese estado tampoco expone BASS, la pregunta queda cerrada.

## Qué falta para cerrarlo

1. **Un Go 4 en modo Auracast** (botón), escanear por LE y, si aparece, conectarse e
   enumerar GATT. Es el único estado en que se lo vio anunciar por LE.
2. Leer el firmware por `df21fe2c-…`, y anotarlo.

## Notas de método

- `bluetoothctl` → `menu gatt` → `list-attributes <dirección>` **no sirve** así: con
  una dirección como argumento imprime la ayuda. La enumeración confiable es
  `busctl tree org.bluez`, o `GetManagedObjects` por D-Bus.
- Los anuncios se leen con `GetManagedObjects` filtrando `ManufacturerData` y
  `ServiceData` de `org.bluez.Device1`. Con `busctl --json=short` los valores vienen
  envueltos en variants anidados y hay que desempaquetarlos.
