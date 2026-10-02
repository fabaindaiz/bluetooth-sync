# 14 · Microcortes: la radio de cada parlante, medida

Prepara el experimento 12 (`docs/research/experimentos/12-microcortes-con-3-go-4.md`) y su
protocolo C2/C3 (spec `docs/superpowers/specs/2026-10-02-microcuts-chain-and-quality-design.md`
§3). Los datos van a `docs/research/experimentos/datos/12/`.

- `sesion.py`: una sesión de N minutos contra el servicio vivo; guarda el estado, los cortes
  y la radio cada segundo, y al final un resumen.
- `comparar.py`: dos o más sesiones con el criterio de §3.3 (repetible dentro de un factor 2;
  un efecto cuenta solo si baja en **ambas** repeticiones).
- El monitor que lee el journal es código del paquete: `host/src/aurasync/radio.py`
  (`RadioMonitor`, `LogLevel`), con sus tests en `host/tests/test_radio.py`.

Lo de abajo (§1 a §4) se leyó en el código fuente de **PipeWire 1.6.9** y **WirePlumber
0.5.17**, las versiones de `PC-Ryzen5` y `HP-O16` (`experimentos/00-inventario-*.md`). Nada de
esto se vio todavía en un journal real: eso es el paso 0 del protocolo (§5).

## 1. Qué líneas escribe el sink Bluetooth al descartar. VERIFICADO (código)

Todas salen de `spa/plugins/bluez5/media-sink.c`, topic **`spa.bluez5.sink.media`** (l. 44),
y `%p` es `this`: el objeto del sink (uno por parlante).

| Línea | Nivel | Texto | Qué significa |
|---|---|---|---|
| [1090](https://gitlab.freedesktop.org/pipewire/pipewire/-/blob/1.6.9/spa/plugins/bluez5/media-sink.c#L1082-1099) | debug | `"%p: reduce bitpool: %i"` | el socket devolvió `EAGAIN`: **el paquete se descarta** ("There will be a sound glitch in any case") y el bitpool baja 2. Se escribe como mucho una vez cada 0,5 s, así que puede haber más descartes que líneas |
| [1164](https://gitlab.freedesktop.org/pipewire/pipewire/-/blob/1.6.9/spa/plugins/bluez5/media-sink.c#L1159-1166) | debug | `"%p: increase bitpool: %i"` | más de 1 s sin error y nada pendiente en el socket: sube 1. **El latido de un enlace sano**, ~1 por segundo, aunque ya esté en el máximo |
| [2462](https://gitlab.freedesktop.org/pipewire/pipewire/-/blob/1.6.9/spa/plugins/bluez5/media-sink.c#L2462) | debug | `"%p: transport %p state %d->%d"` | el sink y **su transporte**, en cada cambio de estado del transporte |
| [1332](https://gitlab.freedesktop.org/pipewire/pipewire/-/blob/1.6.9/spa/plugins/bluez5/media-sink.c#L1332) | warn | `"%p: connection (%s) terminated unexpectedly"` | el parlante cortó el enlace; trae la ruta D-Bus |
| 1610 | debug | `"%p: SO_SNDBUF: %d"` | el buffer del socket: `4 × write_mtu` (`FILL_FRAMES`, l. 57), duplicado por el kernel |

- El `%i` de `reduce`/`increase` es el bitpool nuevo para SBC (`a2dp-codec-sbc.c` l. 330-380:
  baja 2, sube 1, recortado entre `max(min_bitpool, 12)` y el máximo). Un códec sin
  `reduce_bitpool` (AAC del Charge 6) escribe **0**, y el paquete se descartó igual.
- **No hay propiedad para agrandar ese buffer** en 1.6.9: `impl_init()` solo lee
  `clock.quantum-limit`, `api.bluez5.a2dp-duplex`, `api.bluez5.internal`,
  `bluez5.debug.iso-mono` y el transporte (l. 2625-2638). Por eso la variable C3.5 se omite.
  VERIFICADO.
- El mismo texto `transport %p state` lo escribe el **source** A2DP (`media-source.c` l. 2047,
  topic `spa.bluez5.source.media`); `radio.py` lo descarta por el topic.

Espejo: <https://github.com/PipeWire/pipewire/blob/1.6.9/spa/plugins/bluez5/media-sink.c>.

## 2. Del puntero al parlante. VERIFICADO (código), sin ver en el journal

**Ninguna propiedad visible en `pw-dump` trae el puntero.** El nodo nace con
`api.bluez5.transport = "pointer:0x…"` (`bluez5-device.c` l. 631), pero el protocolo nativo
reemplaza todo valor `pointer:` por `""` al enviarlo (`module-protocol-native/protocol-native.c`
l. 113-121). Por eso en `datos/04/pipewire-bap.txt` se ve `api.bluez5.device = ""`. El mapeo
tiene que salir del log, en dos saltos:

1. **sink → transporte:** `media-sink.c` l. 2462, `"0xSINK: transport 0xTRANSPORT state 1->2"`.
2. **transporte → parlante:** `bluez5-dbus.c`, topic **`spa.bluez5`** (l. 47):
   - l. [3309](https://gitlab.freedesktop.org/pipewire/pipewire/-/blob/1.6.9/spa/plugins/bluez5/bluez5-dbus.c#L3309):
     `"transport %p: %s state changed %d -> %d"`, con `transport->path`;
   - l. [4181](https://gitlab.freedesktop.org/pipewire/pipewire/-/blob/1.6.9/spa/plugins/bluez5/bluez5-dbus.c#L4181):
     `"transport %p: Acquired %s, fd %d MTU %d:%d"` (también da el `write_mtu`).

   La ruta es la de BlueZ, `/org/bluez/hci0/dev_90_F2_60_DA_66_6D/sep1/fd3`: la MAC sale de
   `dev_…`, y es la misma que lleva el nodo `bluez_output.90_F2_60_DA_66_6D.1`.

Atajo: la línea `terminated unexpectedly` (l. 1332) trae sink y ruta juntos, pero solo cuando el
enlace muere.

**Consecuencia práctica:** las líneas 1 y 2 se escriben **cuando el transporte pasa a activo**,
o sea cuando el parlante empieza a sonar. `spa_bt_transport_set_state()` escribe la 3309
**antes** de avisarle al sink, así que el orden en el journal es: transporte → sink. **El
registro tiene que estar activo antes de que el parlante empiece a sonar**; si se activa con
la música andando, los descartes se cuentan igual pero por puntero ("enlace sin
identificar") hasta el próximo cambio de estado. `RadioMonitor` lee hacia atrás las últimas
2000 líneas al arrancar para recuperar el mapeo (sin contar esos descartes viejos).

## 3. Dónde aparecen y con qué forma. VERIFICADO (código); el formato real, INFERIDO

- **Viven en el proceso de WirePlumber.** WirePlumber 0.5 carga el enumerador
  (`SpaDevice("api.bluez5.enum.dbus", …)`, `scripts/monitors/bluez/enumerate-device.lua` l. 45)
  y crea cada nodo **en su propio proceso** (`LocalNode("adapter", properties)`,
  `create-node.lua` l. 42). Por eso `pw-metadata -n settings 0 log.level 4` (el servidor
  PipeWire, sujeto 0) no las mostraba y `wpctl set-log-level 4` sí (`datos/10/cambios-de-sistema.txt`).
- **Llegan al journal como entradas estructuradas.** `wireplumber.service` no redirige stderr
  (`src/systemd/user/wireplumber.service.in`), y WirePlumber escribe por
  `g_log_writer_journald` cuando su stderr es el journal (`lib/wp/log.c` l. 576 y
  [787-838](https://gitlab.freedesktop.org/pipewire/wireplumber/-/blob/0.5.17/lib/wp/log.c#L787)).
  Los mensajes de los plugins SPA pasan por el mismo camino (`wp_spa_log_logtv`, l. 996).
  Campos: `MESSAGE`, `PRIORITY` (7 = debug, 4 = warn), `TOPIC`, `CODE_FILE`, `CODE_LINE`,
  `CODE_FUNC`, `SYSLOG_IDENTIFIER`, `SYSLOG_PID`, `TID`. Con el topic en debug, `MESSAGE` es:

  ```
  D spa.bluez5.sink.media[media-sink.c:1090:flush_data]: 0x5581d6f1e2a0: reduce bitpool: 38
  ```

  (letra del nivel, topic, `[archivo:línea:función]`, el texto). `radio.py` usa el campo
  `TOPIC` y, si falta, lo saca de ese prefijo; las expresiones buscan el texto en cualquier
  parte del mensaje, así que un prefijo distinto no las rompe.
- `%p` de glibc se imprime `0x…` en minúsculas, o `(nil)` si es nulo.

## 4. Subir el nivel solo para esos topics. VERIFICADO (código)

- `wpctl set-log-level "<texto>"` sin id escribe `log.level = <texto>` en la metadata
  `settings`, una vez **por cada cliente de WirePlumber** (`src/tools/wpctl.c` l. 1823-1850). No
  valida el texto.
- `module-log-settings` (`modules/module-log-settings.c` l. 33-44) lo aplica con
  `wp_log_set_level()`, que acepta **`[<patrón glob>:]<nivel>,…`** (`lib/wp/log.c` l. 441-500 y
  531). Nivel: una letra (`F E W N I D T`) o un dígito 0-5. Si un valor no se entiende, se ignora
  entero y queda el nivel anterior (sin error visible para `wpctl`).
- Con `WP_INIT_ALL` (`src/main.c` l. 190) WirePlumber reenvía los patrones a PipeWire:
  `pw_log_set_level_string()` → `update_topic_level()` con `fnmatch` sobre **todos los topics
  registrados**, incluidos los del plugin bluez5, que se registran al cargarlo
  (`src/pipewire/pipewire.c` l. 134-138; `src/pipewire/log.c` l. 102-120 y 318).
- **El primer patrón que coincide gana**, en los dos lados. Por eso los nuestros van primero.
- **Borrar la clave** (`wpctl set-log-level -`) hace que WirePlumber vuelva a `"2"`
  (`module-log-settings.c` l. 43), que equivale a su nivel por defecto (`DEFAULT_LOG_LEVEL 4`
  = notice, `log.c` l. 210).

Lo que usa `LogLevel` (`radio.py`):

| Modo | Comando | Volumen |
|---|---|---|
| liviano | `wpctl set-log-level "spa.bluez5.sink.media:D,spa.bluez5:D,<nivel previo o 2>"` | ~1 línea/s por parlante más las de D-Bus de bluez5 |
| pesado (respaldo) | `wpctl set-log-level 4` | todo WirePlumber en debug: el límite de journald (por defecto 10 000 líneas cada 30 s por unidad, `man journald.conf`, REPORTADO) puede comerse líneas |

`WIREPLUMBER_DEBUG` hace lo mismo pero **al arrancar**: exige reiniciar WirePlumber, y eso corta
los parlantes. No se usa.

**Revertir:** `wpctl set-log-level -` (o el valor previo, si `pw-metadata -n settings` mostraba
uno). `LogLevel` lo anota **antes** de cambiar nada en el archivo de cambios del sistema, con
la etiqueta `[aurasync radio_log]`, y `LogLevel.recover()` revierte un cambio que quedó sin su
línea `revertido` (por ejemplo, si el servicio murió con SIGKILL).

## 5. Protocolo en PC-Ryzen5

Cuánto dura: el paso 0 y C2, ~35 min; cada variable de C3, ~25 min. En total, **unas 3 h**,
que se pueden partir en días, pero **cada día que se mida C3 se repite C2** (la batería, la
gente en la casa y el Wi-Fi de los vecinos cambian).

Desde la raíz del repositorio. `PY` es el intérprete del entorno de hatch (o cualquier
Python 3.12+):

```bash
git fetch && git status                                  # memoria: se trabaja desde varios equipos
PY="$(cd host && hatch env find default)/bin/python"
DATOS=docs/research/experimentos/datos/12 && mkdir -p "$DATOS"
```

### Paso 0 · verificar lo que el código promete (una vez)

1. Con los 3 Go 4 **encendidos pero sin sonar**, que el journal de usuario funcione:
   `journalctl --user -u wireplumber -n 3 -o json | head -c 600`. Si no sale nada, probar
   `journalctl --user-unit wireplumber -n 3` y anotar cuál sirvió (el monitor usa el primero;
   si sirve solo el segundo, hay que cambiar `RadioMonitor.command`).
2. **Activar el registro** (anota el cambio en `datos/12/cambios-de-sistema.txt` **antes** de
   hacerlo, guarda el nivel previo y lo verifica leyendo la metadata):

   ```bash
   (cd host && hatch run python -c "from pathlib import Path; from aurasync.radio import LogLevel; \
     print(LogLevel(Path('../docs/research/experimentos/datos/12/cambios-de-sistema.txt')).enable())")
   pw-metadata -n settings | grep log.level      # tiene que mostrar el valor, con el id de WirePlumber
   ```

   `verified: true` en lo que imprime = la metadata quedó como se pidió. Que WirePlumber lo
   aplicó se comprueba en el punto 4.
3. Recién ahora, `aurasync service` y la música (Spotify, la canción elegida, en bucle).
4. Que aparezcan las líneas, y guardar una muestra para fijar los tests con el formato real:

   ```bash
   journalctl --user -u wireplumber -o json --since -2min > "$DATOS/journal-muestra.jsonl"
   grep -c 'increase bitpool' "$DATOS/journal-muestra.jsonl"     # > 0: el registro anda
   grep -o '"MESSAGE":"[^"]*\(bitpool\|Acquired\|state\)[^"]*"' "$DATOS/journal-muestra.jsonl" | head
   ```

   Anotar en el experimento 12: el `MESSAGE` real de cada tipo, si vienen `TOPIC` y
   `__REALTIME_TIMESTAMP`, si están las líneas `transport 0x…: Acquired /org/bluez/…` y
   `0x…: transport 0x… state 1->2`, y si el `write_mtu` es el esperado (~895). Si algo difiere
   de §1-§3, se corrige `radio.py` y sus fixtures con esta muestra antes de seguir.
5. **El reloj de la captura y el de la salida** (experimentos/12 §1.1): con la música sonando y el
   lazo encendido, `pw-top` durante un minuto. Los nodos `aurasync` (la captura) y
   `aurasync_salida` (el sink combinado) tienen que colgar del **mismo driver**. Anotar cuál es,
   y si cambia cuando el lazo empieza a grabar el micrófono. Si son distintos, es una causa de
   cortes sin relación con la radio: se anota y se avisa antes de seguir con C2.
6. Si el paso 4 no da líneas con el modo liviano: `enable("heavy")` (mismo comando, con
   `.enable('heavy')`), repetir el punto 4 y anotarlo: el modo liviano no sirvió.

**Revertir al terminar el día** (y siempre que algo salga mal):

```bash
(cd host && hatch run python -c "from pathlib import Path; from aurasync.radio import LogLevel; \
  print(LogLevel(Path('../docs/research/experimentos/datos/12/cambios-de-sistema.txt')).recover())")
pw-metadata -n settings | grep log.level        # ya no debe aparecer la clave
```

(`recover()` busca en el archivo el último cambio sin `revertido` y corre su comando de
reversión. A mano: `wpctl set-log-level -`, o el valor previo que dice la línea del cambio.)
Cuando el paquete E conecte la orden `radio_log` y `aurasync radio-log on|off|status`, se usan
esas, que escriben en `~/.config/aurasync/cambios-de-sistema.txt`.

### Qué anotar en cada sesión (va en la línea de comando y en el experimento)

- la **batería** que muestra el panel o la app de JBL para cada parlante (`--bateria`);
- las **posiciones** (`--posiciones`, p. ej. `Red=frente-izq,Blue=frente-der,Black=atras`) y
  las distancias al PC, con una foto o un croquis la primera vez;
- la canción, el volumen del panel y el de los parlantes, y si el lazo de recalibración
  estaba encendido (dejarlo igual en todas las sesiones);
- si el Wi-Fi del AX210 estaba apagado (en experimentos/10 lo estaba) y qué otros dispositivos
  Bluetooth había cerca;
- cada corte que **se oye**, con la hora (el oído es la referencia: CLAUDE.md, experimentos/09).

### C2 · línea de base: 2 × 10 min

Mismas posiciones, misma canción, nadie moviéndose entre los parlantes y el PC:

```bash
$PY probes/14-microcortes/sesion.py --minutos 10 --condicion base --nota "base 1" \
    --bateria "Red=…,Blue=…,Black=…" --posiciones "Red=…,Blue=…,Black=…"
$PY probes/14-microcortes/sesion.py --minutos 10 --condicion base --nota "base 2" --bateria "…" --posiciones "…"
$PY probes/14-microcortes/comparar.py "$DATOS"/<fecha>-base-*.jsonl
```

Un descarte "cuenta" si la tasa de ese parlante se repite dentro de un factor 2 entre las dos
sesiones. Si no se repite, hacer una tercera antes de seguir: C3 no tiene sentido sin una base
estable.

### C3 · una variable a la vez: 2 × 10 min cada una, contra la base

| # | `--condicion` | Qué cambia | Qué distingue |
|---|---|---|---|
| 1 | `dos-parlantes` | apagar un Go 4 (el que menos cortes tuvo en C2) | carga del controlador: si los otros dos bajan, el AX210 no da abasto con 3 |
| 2 | `cambio-posiciones` | intercambiar las posiciones de los dos con más cortes | si los cortes siguen al parlante, es su enlace o su firmware; si se quedan en el lugar, es la posición (obstáculos, distancia) |
| 3 | `a-un-metro` | el de más cortes a 1 m del PC, sin obstáculos | distancia |
| 4 | `escaneo` | `bluetoothctl --timeout 600 scan on` durante toda la sesión | la hipótesis del escaneo LE (experimentos/10 §9): es el peor caso |
| 5 | — | un buffer más grande en los sinks bluez | **se omite**: no hay propiedad en 1.6.9 (§1) |
| 6 | `sin-wifi` | `rfkill block wifi` durante la sesión (anotar en `cambios-de-sistema.txt` antes; revertir con `rfkill unblock wifi`). El Wi-Fi del AX210 "no conectado" sigue escaneando: bloquearlo es otra cosa | coexistencia Wi-Fi/BT en el mismo chip: hay reportes de cortes de A2DP en AX200/AX210 aunque el equipo esté por cable, que mejoran mucho con `rfkill` (REPORTADO, research/13). **Es la variable más barata: conviene probarla primero** |

Después de cada par: `comparar.py` con las sesiones de la base del día y las de la condición.
**Se adopta solo lo que baja los descartes en ambas repeticiones** (la mayor de la condición
por debajo de la menor de la base). Para `dos-parlantes`, mirar por parlante: el total baja
solo por tener uno menos.

**Objetivo:** 0 descartes en 20 min con 3 Go 4; si no se llega, el mínimo medido y su causa
escritos. Si todo apunta a la carga del controlador, la salida son dos controladores: es
hardware, y se conversa antes de comprar nada.

### Al terminar

Revertir el registro (arriba), dejar los `.jsonl` y `cambios-de-sistema.txt` en `datos/12/`, y
escribir el resultado MEDIDO con su entorno (kernel, PipeWire, WirePlumber, BlueZ, firmware de
los Go 4 si se pudo leer, batería, posiciones) en el experimento 12.

## Pendiente de verificar en PC-Ryzen5

- el `MESSAGE` real de cada línea y los campos del journal (paso 0.4);
- que `journalctl --user -u wireplumber` vea las líneas (y no solo `--user-unit`);
- que el modo liviano baste (si no, el pesado y su costo);
- que el mapeo aparezca al empezar a sonar y nombre a los tres parlantes;
- cuánto audio pierde cada descarte (~24 ms con `write_mtu` 895 y bitpool 40, hasta ~40 ms
  con bitpool bajo: INFERIDO en `docs/research/11-…`; el `write_mtu` real sale de la línea
  `Acquired`).
