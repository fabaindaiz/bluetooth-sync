# 04 · E8: LE Audio unicast en Linux, contra los Tune 770NC

**Pregunta:** ¿funciona el camino ISO completo de Linux en este equipo —bandera
experimental, socket ISO, liblc3, PipeWire y la negociación de QoS— aunque el
controlador no pueda transmitir? (i-7c8794-ef8389)

**Respuesta: sí, de punta a punta. MEDIDO.**

**Entorno:**
- `PC-Ryzen5`, **Intel AX210**, firmware Bluetooth `202-5.26`.
- Kernel **7.2.7-1-cachyos**, BlueZ **5.87**, PipeWire **1.6.9**, WirePlumber 0.5.17,
  liblc3 1.1.3.
- `main.conf` con `Experimental = true` y el socket ISO activado
  (`probes/e1-iso/02-activar-iso.sh`).
- Receptor: **JBL Tune 770NC** (`88:92:CC:68:91:C0`), emparejado desde antes. Su
  versión de firmware **no se leyó** (pendiente).
- Fecha: 2026-09-28.

**Datos crudos:** [datos/04/](datos/04/) — la traza HCI completa
(`cis-tune.btsnoop`, 369 kB), su decodificación inicial y los perfiles y nodos de
PipeWire.

**Qué se ejecutó:**
```bash
probes/e1-iso/02-activar-iso.sh          # activa el socket ISO y reinicia bluetoothd
# los audífonos se reconectaron solos en perfil BAP
sudo btmon -w cis-tune.btsnoop &
pw-play --target <nodo bap> tono.wav     # tono de 440 Hz, 4 s, amplitud 0,08
```

## Resultado

### Los audífonos se conectan en BAP, no en A2DP: MEDIDO
`pactl list cards` ofrece tres perfiles LE Audio, los tres disponibles, y el activo
es el de LE Audio:

```
bap-duplex: High Fidelity Duplex (BAP Source/Sink, codec LC3)   ← activo
bap-sink:   High Fidelity Playback (BAP Sink, codec LC3)
bap-source: High Fidelity Input  (BAP Source, codec LC3)
Active Profile: bap-duplex
```

`api.bluez5.codec = lc3` en los nodos. Con el socket ISO apagado esos perfiles no
existen: el mismo dispositivo aparecía solo con A2DP y HFP.

### Se estableció un CIG con 2 CIS: MEDIDO
| Parámetro | Valor |
|---|---|
| CIG ID | `0x00`, con **2 handles** (2304 y 2305) |
| CIG Synchronization Delay | **4632 µs** |
| CIS Synchronization Delay | 4632 µs (handle 2304) y **3702 µs** (handle 2305) |
| Latencia central→peripheral | 4632 µs |
| PHY (ambos sentidos) | **LE 2M** |
| ISO Interval | **7,50 ms** |
| Number of Subevents | 3 |
| Burst Number / Flush Timeout | 1 / 1 |
| MTU | **60 B** por sentido |

`Status: Success` en los dos `LE CIS Established`.

### Fluyó audio: MEDIDO
La traza tiene **2214 paquetes `LE-CIS` salientes**, alternando los handles 2304 y
2305, todos con `slen 60` (SDU de 60 B), con números de secuencia consecutivos y
`Number of Completed Packets` de vuelta.

**Un estéreo va en 2 CIS, uno por canal**, no en un CIS multicanal.

60 B cada 7,5 ms son **64 kbps por canal**. Eso coincide con el preset **`32_1`** de
BAP (32 kHz, tramas de 7,5 ms, 60 octetos); el `bluez_input` del mismo dispositivo
reporta 32000 Hz. **Que la frecuencia negociada sea 32 kHz es INFERIDO**: la
configuración del ASE se había hecho antes de empezar a capturar, así que no se leyó
directamente. Se confirma capturando desde el cambio de perfil.

**El tono se escuchó**, confirmado por el usuario con los audífonos puestos
(2026-09-28). Entonces el camino completo —captura, LC3 en software, CIS, receptor—
entrega audio audible, no solo paquetes.

### El hallazgo de arquitectura: PipeWire hace un nodo por stream y los combina
`pw-dump` muestra, para un solo par de audífonos:

```
bluez_output.88_92_CC_68_91_C0.1     api.bluez5.internal = True   api.bluez5.set = …/set_4802…
bluez_output.88_92_CC_68_91_C0.3     api.bluez5.internal = True   api.bluez5.set = …/set_4802…
bluez_output.88_92_CC_68_91_C0.257   api.bluez5.set.leader = True        ← el que ven las apps
output.combine-sink-1233-19_bluez_output.88_92_CC_68_91_C0.1
output.combine-sink-1233-19_bluez_output.88_92_CC_68_91_C0.3
```

O sea: **un nodo interno por cada stream isócrono**, los dos agrupados en un *device
set* de BlueZ, y un **combine-sink** por encima que expone un solo sink estéreo a las
aplicaciones. El nodo "líder" del set es el visible.

**Esto mueve una disputa del proyecto.** [02](../02-le-audio-auracast-linux.md)
§PipeWire dice que PipeWire crea un nodo por BIS, y
[04](../04-implementaciones-y-stacks.md) §4 no pudo confirmarlo; quedaba "en disputa,
se resuelve en E5". Acá está medido **para unicast**: PipeWire crea un nodo por
stream y los combina. Es el mismo mecanismo que necesitaría el camino de transmisión,
y muestra que la máquina de repartir un estéreo en varios streams isócronos de un
mismo grupo **ya existe y funciona**. Que haga lo mismo con `bis[]` sigue siendo
INFERIDO hasta E5.

## Veredicto

- **El stack de LE Audio de Linux funciona en este equipo.** Lo que falta para
  Auracast es únicamente un controlador que declare Isochronous Broadcaster: el resto
  del camino (socket ISO, LC3 en software, negociación de QoS, un stream por canal
  agrupado y combinado) está medido funcionando.
- **Baja el riesgo del plan.** Cuando lleguen las SuperMini (d-7c8794-b82ee9), lo
  único nuevo a depurar es el emisor, no el stack.
- **Dato que conviene no olvidar:** con estos audífonos, BlueZ y PipeWire negociaron
  **32 kHz y 7,5 ms**, no los 48 kHz de los presets `48_2_x` que asume
  [02](../02-le-audio-auracast-linux.md) §4. La negociación la manda el receptor, así
  que **lo que acepten los JBL puede no ser lo que el proyecto planea pedir**. Es una
  razón más para medir la BASE de los propios parlantes (E2).
- **No dice nada sobre los parlantes ni sobre la asignación de canal.** Los Tune son
  audífonos unicast: acá no hay BIG ni BIS.

## Qué falta para cerrarlo

1. Leer la configuración del ASE capturando **desde el cambio de perfil**, para tener
   la frecuencia de muestreo y el **presentation delay** medidos y no inferidos.
2. Leer la versión de firmware de los Tune, que el README de experimentos exige
   anotar.
