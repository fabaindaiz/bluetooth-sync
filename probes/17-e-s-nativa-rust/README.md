# 17 · E/S nativa en Rust: prueba de concepto desechable

Decisión d-7c8794-36dde5; la variante de prueba de concepto de
[research/12](../../docs/research/12-motor-de-audio-en-rust.md) §4, con la topología de §2.1
y las reglas de tiempo real de §2.2. Es **desechable**: cuando su resultado quede MEDIDO en
`docs/research/experimentos/`, la carpeta se borra (d-7c8794-3208b7).

## 0. Qué responde, y qué no

**Pregunta:** con la E/S nativa —un sink virtual y la reproducción de N canales como dos
`pw_stream` en el mismo `node.link-group`, al estilo de `module-loopback`, con el `process`
en el hilo de tiempo real del grafo—,

1. ¿desaparecen los cortes de tipo **"motor tarde"** y **"tubería vacía"** (no hay hilo de
   Python ni tubería entre medio)?
2. ¿quedan la captura y la salida bajo **un solo driver** (el posible desfase de relojes de
   experimentos/12 §1.1)?
3. ¿**cuánto baja la latencia** de punta a punta (hoy ~300 ms antes de A2DP, research/12 §1)?

**No responde** nada sobre los descartes de radio (paquetes SBC que tira el sink bluez5,
experimentos/12 §1): están después del motor. Por eso el protocolo los mide en A y en B, para
separarlos, no para compararlos como mejora.

**El motor es mínimo a propósito:** por parlante, `pan` (`(1-pan)/2·L + (1+pan)/2·R`, igual
que `motor.py`), retardo **entero** en muestras, ganancia y silencio con rampa. Sin ambiente,
decorrelación, ecualización, retardo fraccionario ni limitador.

## 1. Qué hay

| Ruta | Qué es | Compila en |
|---|---|---|
| `poc-dsp/` | el motor: `Engine::process` (sin reservar memoria, cualquier cantidad de muestras por llamada), las órdenes por un anillo SPSC (`rtrb`), y las métricas (histogramas y contadores atómicos) | cualquier equipo |
| `poc-dsp/tests/` | el motor contra cálculos de referencia, contra sí mismo con llamadas de 64, 1024, 2048, 3000 y tamaños irregulares, y `assert_no_alloc` (con un control que muestra que el contador sí ve una reserva) | cualquier equipo |
| `poc-dsp/examples/bench.rs` | el costo de `process` | cualquier equipo |
| `poc-pw/src/pw_io.rs` | la E/S nativa con `pipewire` 0.10.1 (fijado exacto) | **solo Linux** |
| `poc-pw/src/` (resto) | configuración, control por stdin, registro, `--dry-run` (WAV → WAV) y `combine-config` | cualquier equipo |
| `config.example.json` | el formato de `--config` | — |
| `verificar.py` | el ruteo en `pw-dump` (solo biblioteca estándar) | — |
| `latencia.py` | la latencia con el micrófono (entorno de hatch) | — |

Cómo se usa `poc-pw`:

```text
poc-pw [run] [--config F | --installation F] [--target aurasync_salida] [--node-name aurasync_poc]
             [--log F.jsonl] [--latency 1024/48000]
poc-pw --dry-run --in in.wav --out out.wav [--config F] [--sizes 64,1024,2048,3000]
poc-pw combine-config [--config F] [--name aurasync_salida]
```

- **Configuración:** `--config` (el JSON de este programa), o si no, la instalación del
  servicio (`~/.config/aurasync/instalacion.json`), con el retardo que aplica el servicio
  (`retardo_ms + ambiente × retardo_traseros_ms`, `Motor.retardos_efectivos_ms`) redondeado
  a muestras enteras (≤ 10,4 µs de diferencia).
- **Control en vivo:** una orden JSON por línea en stdin, una respuesta por línea en stdout.
  `{"speaker":0,"pan":0.7,"delay_samples":650,"gain_db":0,"muted":false}`, `{"speaker":"Red","muted":true}`,
  `{"volume_db":-6}`. Un cambio de retardo baja a cero en 20 ms, salta y vuelve en 20 ms.
- **Registro:** una línea JSON por segundo (`kind: interval`) y un resumen al cerrar
  (`kind: summary`): muestras, llamadas, cuántum visto (último, mínimo, máximo),
  histogramas con p50/p99/p99,9/máx de la **duración del callback**, del **período** entre
  callbacks y del **despertar** (inicio del callback menos `spa_io_position.clock.nsec`),
  `callback_p999_of_min_quantum`, **xruns propios** (`no_input`, `no_output`), `bad_buffer`,
  `graph_xrun_flags` (ciclos con `SPA_IO_CLOCK_FLAG_XRUN_RECOVER`), `rt_alloc_violations`
  (tiene que quedar en 0), y por stream el `pw_stream_get_time_n` (`delay`, `delay_ms`,
  `queued`, `buffered`, `now_ns`), el id y nombre del reloj del driver, y `same_driver`.
- **Cierre:** SIGINT o SIGTERM cierran limpio y escriben el resumen.

## 2. Lo que se verificó en el Mac (2026-10-02), y lo que no

Mac (Apple A18 Pro, macOS 27, `cargo` 1.99), con otros agentes corriendo (carga ~11–13).

- `cargo test --workspace`: 26 tests, verdes. Cada test clave se vio fallar con una falla
  plantada (la mezcla con el signo del pan cambiado; una reserva dentro de `process`).
- `cargo clippy --workspace --all-targets -- -D warnings` y `cargo fmt --all --check`: limpios.
- **La parte de PipeWire se chequeó contra los bindings reales, sin enlazar.** En una copia
  fuera del repositorio se generaron los bindings de `pipewire-sys`/`libspa-sys` 0.10.1 con
  bindgen desde los headers de **PipeWire 1.6.9** (con dos shims de `endian.h`/`byteswap.h`
  para macOS), se forzó el módulo Linux y se corrieron `cargo check` (debug y release) y
  `cargo clippy -D warnings`: **limpios**. Encontró un error real de préstamos que ya está
  corregido. Confirmó en los bindings generados: `pw_stream_flags_PW_STREAM_FLAG_ASYNC = 1024`,
  `SPA_IO_Position = 7`, `SPA_AUDIO_CHANNEL_AUX0 = 4096`.
- `--dry-run` de 60 s (3 parlantes, llamadas de 64/1024/2048/3000) contra una referencia en
  numpy: diferencia máxima **7,9·10⁻⁹**.
- **Costo del motor** (`cargo run --release -p poc-dsp --example bench 3`), MEDIDO en el Mac:

  | Parlantes | Cuántum | Mediana | p99 | Máx | Mediana / cuántum |
  |---|---|---|---|---|---|
  | 3 | 1024 | 9,9 µs | 11,0 µs | 25,6 µs | 0,046 % |
  | 3 | 2048 | 15,5 µs | 26,0 µs | 33,0 µs | 0,036 % |
  | 4 | 2048 | 27,2 µs | 43,5 µs | 94,4 µs | 0,064 % |

  Unas 2000 veces el tiempo real. El criterio de §7 (p99,9 < 25 % del cuántum) pide < 5,3 ms
  con 1024: el motor no es lo que se juega aquí, sino el planificador y el grafo.

**No se pudo en el Mac:** enlazar con `libpipewire`, ejecutar el modo en vivo, ni comprobar
nada del comportamiento en el grafo (§9). Todo eso es el primer paso en PC-Ryzen5.

### 2.1 Lo que se verificó en `HP-O16` (2026-10-09), sin parlantes

Contra un sink nulo de prueba (`aurasync_poc_test`, 4 canales `AUX0…AUX3`, `priority.session=0`) en
vez de `aurasync_salida`, con el servicio sonando al lado sin tocarlo. Resultados, entorno y scripts en
`docs/research/experimentos/13-e-s-nativa-en-rust.md` §4 y `docs/research/experimentos/datos/13/`:
- `cargo build --release --locked` enlazó con `libpipewire` 1.6.9 **sin cambiar el código**; tests,
  clippy y fmt limpios. Lo que el Mac no pudo probar (§9) funciona: `data-loop.0` en `FF` 83, los dos
  streams bajo el driver del nodo de destino (`same_driver` y `node.driver-id`), `TRIGGER`/`ASYNC` en el
  mismo ciclo (0 muestras de latencia agregada), callbacks sin aplicación tocando, nada tras `kill -9`.
- **Método de latencia sin micrófono** (`datos/13/latencia.sh`): un sink nulo de N+1 canales; la salida
  de la PoC en `AUX0…`, la entrada copiada con `pw-link` al último canal, y un solo `pw-record` del
  monitor. Sirve también para A (la salida del servicio en otro canal) cuando el servicio no esté en uso.
- Dos trampas de este equipo: `pw-record --channel-map` pide `AUX0`, no `aux0` (con minúsculas no da
  error, pero sus puertos no salen como `AUX…` y el canal de referencia quedó sin grabar); y `grep` es ugrep, que con `-v -q` y entrada vacía devuelve 0.

## 3. Compilar en PC-Ryzen5

**Instalar paquetes es un cambio de sistema:** se anota **antes**, con su reversión, en
`$DATOS/cambios-de-sistema.txt` (CLAUDE.md).

Todos los comandos de aquí en adelante corren **desde la raíz del repositorio**, con:

```bash
POC=probes/17-e-s-nativa-rust
DATOS=docs/research/experimentos/datos/<n>        # el número que le toque al experimento
PY="$(cd host && hatch env find default)/bin/python"
mkdir -p "$DATOS"
```

1. Ver qué hay (no cambia nada):

   ```bash
   git fetch && git status
   pacman -Qi rustup clang pkgconf base-devel 2>&1 | grep -E '^(Name|Version)|error'
   pkg-config --modversion libpipewire-0.3     # tiene que decir 1.6.9
   ls /usr/include/pipewire-0.3/pipewire/stream.h
   ```

   En Arch/CachyOS los headers vienen con la biblioteca (no hay paquete `-dev`). `clang` hace
   falta para bindgen (lee los headers), y un compilador de C (`base-devel`) para los `.c` de
   `libspa-sys`.
2. Anotar en `$DATOS/cambios-de-sistema.txt` lo que **no** estaba, por ejemplo:
   `2026-10-0x PC-Ryzen5: pacman -S --needed rustup clang (revertir: sudo pacman -Rns rustup clang; rm -rf ~/.rustup ~/.cargo)`.
   Si `clang` ya estaba, no se instala ni se desinstala.
3. Instalar y compilar:

   ```bash
   sudo pacman -S --needed rustup clang
   rustup default stable && rustc --version      # ≥ 1.85 (edición 2024)
   (cd $POC && cargo build --release --locked)    # Cargo.lock fija pipewire 0.10.1
   (cd $POC && cargo test --workspace --locked)   # en Linux también compila y enlaza pw_io
   (cd $POC && cargo run --release -p poc-dsp --example bench 3)   # el costo, ahora en el Ryzen
   ```

Si falla al compilar `pipewire`/`pipewire-sys`, ver §10 antes de tocar nada.

## 4. Arrancar (B) y verificar el ruteo

El servicio **apagado**. Cuatro terminales (cada una con las variables de §3):

```bash
# 1. Antes de nada, el estado de referencia
wpctl status > "$DATOS/B-antes-wpctl.txt"
pw-metadata -n default > "$DATOS/B-antes-default.txt"     # default.audio.sink y default.configured.audio.sink

# 2. El sink combinado, igual al del servicio (vive mientras pw-cli corra; Ctrl-C lo quita)
pw-cli -m load-module libpipewire-module-combine-stream "$($POC/target/release/poc-pw combine-config)"
#    su volumen al 100 %, como hace el servicio (WirePlumber le restauraba 46 %, experimentos/10):
wpctl set-volume "$(pw-dump | python3 -c 'import json,sys; print(next(o["id"] for o in json.load(sys.stdin) if (o.get("info") or {}).get("props",{}).get("node.name")=="aurasync_salida"))')" 1.0

# 3. La PoC (lee ~/.config/aurasync/instalacion.json); deja la terminal abierta: su stdin es el control
$POC/target/release/poc-pw --log "$DATOS/B1.jsonl"

# 4. La música: en pavucontrol (pestaña Reproducción) mover la aplicación a "aurasync PoC (Rust)",
#    o:  pactl list short sink-inputs ;  pactl move-sink-input <índice> aurasync_poc
#    **Nunca** "usar como predeterminado": eso queda guardado y es el lazo de experimentos/09.
```

**Verificar (cada vez que se arranca, antes de medir):**

1. `python3 $POC/verificar.py | tee "$DATOS/B1-verificar.txt"` → todo `ok`: el sink existe y es `Audio/Sink`; **no** es
   `default.audio.sink` ni `default.configured.audio.sink`; `aurasync_poc_output` tiene
   `target.object = aurasync_salida` y todos sus enlaces van ahí; cada salida del combinado
   llega a un `bluez_output.*`; volúmenes al 100 %.
2. `diff "$DATOS/B-antes-default.txt" <(pw-metadata -n default)`: sin cambios.
   `wpctl status`: el sink marcado con `*` es el mismo que antes.
3. `pw-link -l | grep -A4 aurasync_poc_output`: N enlaces, de `AUX0…` a los puertos `AUX0…` de `aurasync_salida`.
4. **Un solo driver:** `pw-top` con la música sonando, un minuto. `aurasync_poc`,
   `aurasync_poc_output`, `aurasync_salida`, sus `output.aurasync_salida.*` y los
   `bluez_output.*` tienen que colgar del **mismo driver** (en `pw-top` los seguidores
   aparecen debajo de su driver con ` + ` delante del nombre, o ` = ` si son asíncronos, como
   la captura de la PoC: `pw-top.c` l. 545). Guardar
   `pw-top -b -n 5 > "$DATOS/B1-pwtop.txt"`. En el registro, `same_driver` tiene que ser
   `true` todo el tiempo y `driver.clock_name` decir qué nodo maneja el reloj.
5. **Tiempo real:** `ps -L -o tid,cls,rtprio,comm -p "$(pidof poc-pw)"`: el hilo de datos de
   PipeWire (`data-loop.0`, `context.c` l. 265) en `FF` con prioridad 83 (`rtprio-client`,
   research/12 §2.2); o `chrt -p <tid>`. Si está en `TS`, el `process` no corre en tiempo real y la medición no
   vale: se anota y se para.
6. **Con la aplicación en pausa 30 s:** ¿siguen las `callbacks` por segundo en el registro, y
   los nodos en `R` en `pw-top`? (INFERIDO que sí, por el `link-group`.) Si se detienen y los
   Go 4 se suspenden, al volver traen otro desfase (experimentos/05): es un hallazgo, se anota.

**Al apagar** (Ctrl-C en la PoC, después en `pw-cli`):
`pw-cli ls Node | grep -E 'aurasync_poc|aurasync_salida'` no muestra nada, y
`pw-metadata -n default` igual que antes. **Una vez:** repetir con `kill -9 "$(pidof poc-pw)"`
y comprobar que el nodo desaparece solo (vive en el proceso; P1). Al final del día, devolver
la aplicación a su salida normal (WirePlumber recuerda el destino de cada aplicación).

## 5. Protocolo A/B

**Pregunta:** con la misma música, los mismos parlantes y la misma cadena, ¿B elimina los
cortes que A atribuye al motor y a la tubería, sin cambiar los de radio?

**Siempre igual en A y en B:** mismo día, mismas posiciones y batería anotada, misma
canción por la misma aplicación en bucle, mismo volumen en la aplicación y en los parlantes,
Wi-Fi como en experimentos/10, nadie entre los parlantes y el PC.

**El registro de radio encendido** antes de que los parlantes suenen, como
`probes/14-microcortes/README.md` §5 paso 0.2 (anotado en `$DATOS/cambios-de-sistema.txt`
con su reversión).

| | A: el servicio de hoy, reducido | B: la PoC |
|---|---|---|
| Arranque | `aurasync service` | §4 |
| Cadena | ambiente `off`, decorrelación `off`, ecualización `off`, difusión `off`, graves `off`, lazo de recalibración apagado | la de la PoC |
| Pan y retardos | los de la instalación | los mismos, redondeados a muestras (de la misma instalación) |
| Volumen | el del panel, anotado | `{"volume_db": <el mismo>}` por stdin |
| Lo que queda distinto | limitador `peak` (no actúa bajo 0 dBFS), retardo fraccionario sinc (≤ 10,4 µs) | — |
| La aplicación toca en | `aurasync` | `aurasync_poc` |

**Sesiones: A1, B1, A2, B2, de 10 min cada una, intercaladas.** En cada una:

```bash
INICIO="$(date '+%F %T')"
# A:  $PY probes/14-microcortes/sesion.py --minutos 10 --condicion poc-A --nota A1 --bateria "…" --posiciones "…"
# B:  (la PoC ya corriendo con --log "$DATOS/B1.jsonl"); esperar 10 min
pw-top -b -n 2 > "$DATOS/A1-pwtop-fin.txt"     # el ERR de cada nodo (también al empezar)
FIN="$(date '+%F %T')"
journalctl --user -u wireplumber -u pipewire -o json --since "$INICIO" --until "$FIN" > "$DATOS/A1-journal.jsonl"
```

**Los descartes de radio, contados igual en A y en B** (del journal, con el mismo
`RadioMonitor` del paquete):

```bash
$PY - "$DATOS/B1-journal.jsonl" <<'EOF'
import json, sys
from aurasync.radio import RadioMonitor
m = RadioMonitor()
for line in open(sys.argv[1]):
    m.feed(json.loads(line))
for k, v in m.snapshot()["speakers"].items():
    print(k, "descartes:", v["drops_total"], "bitpool máx:", v["bitpool_max"], "write_mtu:", v["write_mtu"])
EOF
```

**Qué se anota por sesión** (en el experimento, con el entorno: kernel, PipeWire,
WirePlumber, BlueZ, firmware de los Go 4 si se pudo leer, batería, posiciones):

- **A:** cortes por tipo de Diagnóstico → Cortes (o el resumen de `sesion.py`): `late`,
  `underrun`, `low`, `xrun`, `radio`…; el ERR de `pw-top` de los nodos del servicio al
  empezar y al terminar.
- **B:** del resumen de la PoC (`tail -1 "$DATOS/B1.jsonl" | python3 -m json.tool`): xruns
  propios, `graph_xrun_flags`, `bad_buffer`, `rt_alloc_violations`, cuántum,
  p50/p99/p99,9/máx del callback y del período, `callback_p999_of_min_quantum`; `same_driver`
  en todas las líneas; el ERR de `pw-top` de `aurasync_poc` y `aurasync_poc_output` al
  empezar y al terminar. Los segundos con algo raro:

  ```bash
  python3 -c 'import json,sys
  for d in map(json.loads, open(sys.argv[1])):
      m = d.get("metrics", {})
      if d.get("kind") == "interval" and (m["xruns"]["total"] or m["graph_xrun_flags"] or m["bad_buffer"] or d.get("same_driver") is False):
          print(d["t"], m["xruns"], m["graph_xrun_flags"], m["bad_buffer"], d.get("same_driver"))' "$DATOS/B1.jsonl"
  ```
- **En los dos:** los descartes de radio por parlante (arriba) y **cada corte oído con su
  hora** (el oído es la referencia, experimentos/09). Un corte oído en B sin xrun propio, sin
  ERR y sin descarte de radio a esa hora es lo más importante que puede salir: algo que
  ninguna de las dos mediciones ve.

**Repetición (experimentos/08):** un resultado vale si se ve en las dos sesiones de cada
lado. Si A1 y A2 no se parecen (descartes de radio fuera de un factor 2), se hace una
tercera de cada uno antes de concluir.

## 6. Latencia, medida igual en A y en B

La calibración del servicio no sirve para B (no existe en la PoC), así que se mide **lo
mismo en los dos** con `latencia.py`: ráfagas de ruido rosa por `pw-play --target <sink>`,
el micrófono `fifine` con `pw-record`, y `gcc_phat` del paquete para encontrar cada ráfaga.
El número incluye el arranque de `pw-play`/`pw-record` (un sesgo de decenas de ms, el mismo
en todas las corridas), así que **lo que vale es la diferencia** A − B; su error se ve en la
dispersión entre corridas. `latencia.py` comprueba en `pw-dump` que el `pw-play` llegó al sink
pedido (y lleva `node.dont-fallback`), y si no, descarta la medición.

Sin música, con el lazo apagado. El servicio y la PoC **pueden correr juntos** para esto: la
PoC apunta al `aurasync_salida` del servicio (sin el `pw-cli` de §4) y cada medición toca
solo en un sink, así se intercalan sin reiniciar nada:

```bash
MIC=<node.name del fifine: wpctl status / pw-dump>
for i in 1 2 3; do
  for s in aurasync aurasync_poc; do
    $PY $POC/latencia.py --sink $s --mic "$MIC" --etiqueta "$s-$i" --salida "$DATOS/latencia.jsonl"
  done
done
```

Para un número absoluto del motor, agregar la referencia **R**: `--sink` directo al
`bluez_output.…` del parlante con `retardo_ms` ≈ 0 (el que los demás esperan), con el servicio
y la PoC callados. Entonces motor + combinado ≈ A − R y B − R.

**Error esperado:** ± un cuántum (21–43 ms) por el arranque de `pw-play` en grafos con
cuántums distintos. La diferencia esperada es de ~200 ms (research/12 §2.1, INFERIDO), así
que alcanza; si sale menor que la dispersión, se dice así.

## 7. Criterios

- **B, 2 × 10 min:** 0 xruns propios; ERR de `pw-top` de los nodos de la PoC quieto;
  `rt_alloc_violations` = 0; `same_driver` siempre `true`.
- **Tiempo real:** p99,9 del callback < 25 % del cuántum (`callback_p999_of_min_quantum` < 0,25).
- **Radio:** los descartes de B dentro de un factor 2 de los de A (la PoC no los debería
  cambiar; si cambian, algo más cambió entre sesiones).
- **Latencia:** B < A en las tres corridas.
- Si todo se cumple, la E/S nativa elimina por construcción los cortes de motor y de
  tubería: es la luz verde del paso 1 de research/12 §4. Si los cortes oídos siguen en B
  igual que en A y coinciden con descartes de radio, Rust no es lo que los arregla
  (research/12, compuerta).

## 8. Apagar y limpiar

1. Ctrl-C en la PoC (escribe el resumen) y en el `pw-cli` del combinado.
2. `pw-cli ls Node | grep aurasync` vacío (con el servicio apagado); `pw-metadata -n default`
   igual que antes; la aplicación de vuelta en su salida normal.
3. Revertir el registro de radio (`LogLevel.recover()`, probes/14 §5) y anotarlo.
4. `rm -rf $POC/target/` libera ~1 GB. Desinstalar `rustup`/`clang` solo si se instalaron para esto
   y ya no se usan (la reversión anotada en §3).

## 9. Lo que todavía no se sabe (se ve en el primer arranque)

- **El supuesto central:** que dos streams de un cliente (no un módulo dentro del servidor)
  con `node.link-group` queden bajo el driver del combinado (research/12 §2.1, INFERIDO del
  código de `context.c`). Lo dicen `pw-top` y `same_driver`.
- Que `TRIGGER` en la reproducción y `ASYNC` en la captura se comporten igual desde un
  cliente que en `module-loopback`.
- Qué pasa sin música (§4.6) y cuando un parlante se desconecta (`dont-reconnect`: el
  stream queda sin destino; la PoC no lo repara).
- Que WirePlumber no elija `aurasync_poc` como sink por defecto por su cuenta
  (`priority.session=1` lo pone último, INFERIDO; `verificar.py` lo comprueba).

## 10. La API de pipewire-rs 0.10.1 que se usa, y el camino si falla

Leída en el código del crate (crates.io, 0.10.1) y chequeada con `cargo check`/`clippy`
contra bindings generados desde los headers de PipeWire 1.6.9 (§2). **Nada se ejecutó.**

| Uso | Dónde (pipewire-0.10.1 salvo que diga) | Seguridad |
|---|---|---|
| `MainLoopRc::new`, `ContextRc::new`, `connect_rc` | `main_loop/rc.rs` l. 35; `context/rc.rs` l. 48, 80 | alta |
| `StreamRc::new(core, name, props)`, `properties!`, `insert` | `stream/rc.rs` l. 39; `properties/box_.rs` l. 174; `properties/mod.rs` l. 67 | alta |
| `add_local_listener_with_user_data` + `process`, `io_changed`, `param_changed`, `state_changed`, `register` | `stream/mod.rs` l. 164, 860, 743, 773, 686, 895 | alta (el cierre corre en el hilo RT con `RT_PROCESS`: el crate no exige `Send`, l. 405–406) |
| `connect` con `AUTOCONNECT`, `MAP_BUFFERS`, `RT_PROCESS`, `TRIGGER` | `stream/mod.rs` l. 188, 944–959 (`TRIGGER` con la feature `v0_3_41`) | alta |
| `ASYNC`: no está en `StreamFlags` → `from_bits_retain(pw::sys::pw_stream_flags_PW_STREAM_FLAG_ASYNC)` | `stream/mod.rs` l. 944–959; constante en los bindings generados (= 1024) | alta |
| `dequeue_buffer`; el `Drop` de `Buffer` hace `queue_buffer` | `stream/mod.rs` l. 252; `buffer.rs` l. 78–84 | alta |
| `Buffer::datas_mut`, `requested` | `buffer.rs` l. 24, 73 (`v0_3_49`) | alta |
| `Data::data`, `chunk`, `chunk_mut`, `as_raw().maxsize` | libspa-0.10.1 `buffer/mod.rs` l. 66–113 | alta |
| `trigger_process` | `stream/mod.rs` l. 363 (`v0_3_34`); RT-safe según `stream.h` l. 683–686 | alta en tipos; media en comportamiento desde un cliente (§9) |
| `time()` → `pw_stream_get_time_n` | `stream/mod.rs` l. 382 (`v0_3_50`); con seq-lock, se puede llamar desde el hilo principal (`stream.c` l. 2466–2481) | alta |
| `disconnect`, `node_id`, `state` | `stream/mod.rs` l. 271, 353, 335 | alta |
| Formato: `AudioInfoRaw` (F32P), `PodSerializer::serialize`, `Pod::from_bytes` | libspa `param/audio/raw.rs` l. 26–104; `pod/serialize.rs` l. 268; `pod/mod.rs` l. 111 | alta (es el ejemplo `tone.rs` del crate, con F32P) |
| `add_timer` + `update_timer`; `add_signal_local(Signal::INT/TERM)` | `loop_/mod.rs` l. 329, 575, 241 (exige el hilo `main`, `utils.rs` l. 7) | alta |
| `SPA_IO_Position` por `io_changed`, y `spa_io_position.clock` leído con `read_volatile` | `unsafe` acotado y comentado en `pw_io.rs` | media: el mismo supuesto que `module-loopback` |
| Lo que el crate no tiene: `pw_stream_get_nsec` (TODO en l. 402) → `clock_gettime(CLOCK_MONOTONIC)`; `pw_get_library_version` → `pw::sys` | `pw_io.rs` | alta |
| SIGINT/SIGTERM bloqueadas antes de crear hilos: `impl_signalfd_create` solo bloquea en el hilo que llama (`spa/plugins/support/system.c` l. 222–236) | `pw_io.rs` | alta |

**Nada de lo pedido resultó imposible con pipewire-rs 0.10.1.** Si en PC-Ryzen5 el crate no
compila contra los headers del sistema, o se comporta mal en tiempo de ejecución, el camino es
el de research/12 §2.1: un *shim* en C de ~200 líneas que copie `module-loopback.c`
(l. 341–443 y 720–770: los dos `pw_stream`, `capture_process`, `playback_process`) y exponga
una función `poc_start(props, n_canales, callback)` donde el callback recibe
`(const float *in[2], float *out[], uint32_t n)`; se compila con el crate `cc` y el motor
(`poc-dsp`) no cambia. Se decide con el error a la vista, no antes.
