# Panel de control de aurasync

Diseño del 2026-10-01. Es la spec del panel de control. Se armó con el usuario
paso a paso: propósito, desde dónde se usa, qué muestra y la nota de entendimiento
aprobada. Después el usuario pidió implementarlo de forma autónoma.

**Qué significa cada marca:** igual que en [08](08-integracion-y-plan.md).
Todo lo que sigue es **diseño (INFERIDO)**, salvo lo que se marca como
VERIFICADO.

**Qué se permite construir:**
- **d-7c8794-b1eaac** amplía la enmienda d-7c8794-f619c4. Se puede construir el
  panel completo sobre un **motor simulado**.
- La captura, el DSP, el reloj y el emisor **reales** siguen bloqueados hasta la
  decisión de seguir (i-7c8794-0d129c).

## 1. Lo que se acordó

**Lo que dijo el usuario:**
- El panel sirve para **dos cosas, por etapas**, sobre la misma base:
  - **diagnóstico**, que acompaña los experimentos y los hitos M0–M2;
  - **uso diario**, después de M3.
- Se usa **desde el PC que transmite y también desde el teléfono**, en la misma
  red.
- La vista de diagnóstico muestra cuatro áreas:
  - parlantes y canales;
  - salud del enlace;
  - niveles de audio;
  - calibración y mediciones.

**Lo que se supuso y el usuario aprobó:**
- **Uso diario:** elegir la fuente, encender y apagar, cambiar el modo (quad para
  música y juegos, L-C-R-S para películas), y volumen general y por parlante.
- **Un solo proceso:** el panel es la cara de `aurasync`. El proceso que
  transmite es el mismo que sirve el panel.
- **Seguridad, por defecto cerrada:** escucha solo en el propio equipo. La red
  local es una opción explícita, con un código de emparejamiento por QR.
- **Huella mínima:** se usa con el navegador; no se instala nada en el teléfono ni
  en el PC.

**Criterio de éxito:**
- **Diagnóstico:** durante un experimento, de un vistazo se sabe si el problema
  está en la fuente, en el reloj o en el enlace, o en un parlante. Cada medición
  real queda guardada con su entorno.
- **Uso diario:** desde el teléfono, poner música en quad y bajar el volumen
  trasero toma menos de 10 segundos.

### 1.1 Segunda iteración (2026-10-01)

El usuario probó la demo y pidió cuatro cambios:
1. **Un solo panel, el de diagnóstico.** La vista de uso diario "no tenía
   opciones" y se retira del código (d-7c8794-9d9776). La entrada
   i-7c8794-61ae5d vuelve a Planificado.
2. **Servicios como procesos:** ver su estado, iniciarlos y detenerlos cuando sea
   factible, y ver sus logs internos, "para que sea realmente debug".
3. **Más opciones del motor:** todo lo que el motor va a permitir configurar.
4. **Que todo funcione en la demo.** No pudo asignar canales: era un bug real,
   explicado en §7.4.



| Enfoque | A favor | En contra | Veredicto |
|---|---|---|---|
| **A. Web local servida por el proceso: aiohttp + HTML/JS sin build** | Llega al teléfono sin instalar nada. Un solo proceso y un solo bucle asyncio con Bumble. No requiere Node ni compilación. Es una dependencia madura (aiohttp 3.14.3, Apache-2.0/MIT, ruedas cp312 para macOS arm64 y Linux x86_64; VERIFICADO en PyPI) | Hay que escribir la interfaz a mano, sin framework | **Elegido** (d-7c8794-d2fd15) |
| B. Web con un framework de frontend (React/Vite) | Componentes y ecosistema | Exige una toolchain de Node, rompe "huella mínima" y agrega un paso de build al chequeo | Descartado |
| C. TUI en la terminal (Textual) | Liviana y sin navegador | **No llega al teléfono**, que es un requisito | Descartado |
| D. App nativa (barra de menú o bandeja del sistema) | Integrada con el sistema | Una distinta por sistema operativo, y tampoco llega al teléfono | Descartado |

## 3. Arquitectura

```
          navegador (PC o teléfono)
                 │  HTTP: /, /static/*        WebSocket: /ws
                 ▼
 ┌─────────────────────────── proceso aurasync ───────────────────────────┐
 │ panel/server.py   valida token, Host y Origin; sirve la interfaz;      │
 │                   publica snapshots ~10 Hz y recibe órdenes            │
 │        │  Engine.snapshot()            Engine.apply(Command)           │
 │        ▼                                                               │
 │ engine/base.py    contrato: Snapshot (state.py) + Command validado     │
 │ engine/simulated.py   motor de prueba (hoy)                            │
 │ engine/real.py        Bumble + SuperMini (bloqueado hasta E4)          │
 └────────────────────────────────────────────────────────────────────────┘
```

**Unidades y fronteras:**

| Módulo | Qué hace | De qué depende |
|---|---|---|
| `state.py` | El **contrato** entre el motor y el panel: dataclasses del snapshot y su serialización a JSON. Es la única forma del estado que ven el PC y el teléfono (tarjeta *same-answer-or-refuse*: un solo camino de serialización) | Nada |
| `engine/base.py` | La interfaz `Engine`, los errores, y `parse_command()`: valida cada orden **antes** de que llegue a un motor. Una orden mal formada se rechaza con un motivo; nunca se completa con un valor por defecto | `state.py` |
| `engine/simulated.py` | Motor de prueba. El estado se deriva de **un reloj inyectado** (tarjeta *derive-state-from-one-clock*), así que los tests lo llevan a un instante exacto sin dormir | `engine/base.py` |
| `panel/auth.py` | Token, cookie, chequeo de `Host` y `Origin` | stdlib |
| `panel/pairing.py` | URL de emparejamiento y su QR (SVG para el navegador, texto para la terminal) | `segno` |
| `panel/server.py` | La app aiohttp: rutas, WebSocket y el bucle de publicación | `aiohttp`, todo lo anterior |
| `panel/static/` | `index.html`, `styles.css` y `app.js`: la interfaz, sin build | — |
| `cli.py` | `aurasync panel [--demo] [--lan] [--port N]` | `panel/server.py` |

**Premisa de proceso único** (tarjeta *in-process-guarantees*): hay un motor por
proceso, y el lock que serializa las órdenes vale solo dentro de ese proceso. Un
segundo `aurasync` en el mismo puerto no arranca, porque el puerto ya está
ocupado. Si alguna vez hubiera dos procesos manejando la misma SuperMini, esta
garantía se pierde.

## 4. Modelo de estado (el snapshot)

| Bloque | Campos | Qué responde |
|---|---|---|
| `engine` | `kind` (`simulated` o `real`), `running`, `mode` (`quad`, `lcrs`), `source` (`system`, `app`, `file`, `tone` + nombre), `master_db` | ¿Qué está haciendo? |
| `controller` | `present`, `port`, `unit`, `firmware`, `iso_broadcaster` (**true, false o null = no observado**; tarjeta *absence-is-a-third-value*), `big` (`state`, `num_bis`, `presentation_delay_us`, `sdu_interval_us`) | ¿El controlador está y puede transmitir? |
| `clock` | `queue_sdus`, `queue_target`, `ratio_ppm`, `drift_ppm`, `underruns`, `overruns`, `history` (últimos 60 valores de drift) | ¿El reloj está estable? |
| `speakers[]` | `address`, `name`, `model`, `firmware`, `channel` (o null), `bis_index`, `state` (`unseen`, `seen`, `synced`, `lost`), `rssi_dbm`, `volume_db`, `muted`, `delay_ms`, `gain_db` | ¿Cada parlante recibe lo que corresponde? |
| `meters` | por canal del modo: `rms_db`, `peak_db` | ¿El audio llega y el upmix hace lo esperado? |
| `calibration` | `state` (`idle`, `running`, `done`, `error`), `progress`, `results[]` (canal, retardo, ganancia, confianza), `measured_at`, `simulated` | ¿Qué se midió y con qué confianza? |
| `services[]` | `name`, `label`, `kind` (`proceso`, `tarea`, `enlace`, `sistema`), `managed` (si el panel puede iniciarlo o detenerlo), `state` (`stopped`, `starting`, `running`, `stopping`, `failed`, `unavailable`), `pid`, `started_at`, `uptime_s`, `restarts`, `depends_on[]`, `detail`, `last_error` | ¿Qué parte del motor está viva y por qué no lo está otra? |
| `config` | `upmix`, `rear_delay_ms`, `presentation_delay_us`, `bitrate_kbps`, `transport`, `broadcast_name`, `manufacturer_data` (fijo: `harman`) | ¿Con qué parámetros transmite? |
| `latency` | `capture_ms`, `codec_ms`, `transport_ms`, `presentation_ms`, `total_ms` (estimada, no medida) | ¿Cuánto tarda el audio del PC al parlante? |
| `engine.scanning` | true mientras se buscan parlantes | ¿Está buscando? |
| `seq` | número que crece con cada snapshot | ¿Este estado es más nuevo que el anterior? |

**Canales por modo:** `quad` = FL, FR, RL, RR; `lcrs` = FL, FC, FR, RC (el
surround mono de Pro Logic; [07](07-software-de-audio-en-el-pc.md) §4.5).

## 5. Órdenes

Se mandan por el WebSocket como `{"id": n, "cmd": "...", "args": {...}}`, y la
respuesta es `{"id": n, "ok": true}` o `{"id": n, "ok": false, "error": "..."}`.

| Orden | Argumentos | Reglas (fail-closed) |
|---|---|---|
| `start` / `stop` | — | — |
| `set_mode` | `mode` | Solo `quad` o `lcrs`. **Al cambiar de modo, todas las asignaciones se borran**: un parlante en RL no tiene equivalente en LCRS, y adivinarlo podría mandar un canal al lugar equivocado |
| `set_source` | `kind`, `name` | `app` y `file` exigen `name` |
| `set_master` | `db` | Entre −60 y 0 dB |
| `set_speaker_volume` | `address`, `db` | Hay que nombrar el parlante; **no hay "todos" implícito** (tarjeta *a-default-scope-is-the-widest-one*). La dirección tiene que existir |
| `set_mute` | `address`, `muted` | Igual |
| `assign` | `address`, `channel` | El canal tiene que existir en el modo actual. **Un canal tiene un solo parlante**: si estaba ocupado, la orden se rechaza y hay que liberarlo antes |
| `unassign` | `address` | — |
| `tone` | `channel`, `seconds` | El canal es obligatorio; entre 0,5 y 10 s. **Exige que se esté transmitiendo**, porque el tono sale por el BIG (regla agregada al implementar) |
| `calibrate` / `calibrate_cancel` | — | Requiere que todos los canales del modo tengan parlante |
| `set_speaker_trim` | `address`, `delay_ms` y/o `gain_db` | Al menos uno; retardo de 0 a 100 ms, ganancia de −12 a +6 dB |
| `calibration_apply` | — | Exige una calibración terminada; copia el retardo y la ganancia de cada canal al parlante que lo tiene |
| `scan` | — | Busca parlantes durante 3 s; los nuevos aparecen como `seen` |
| `set_config` | `key`, `value` | Cada clave con su dominio cerrado (§7.3); `manufacturer_data` no se puede cambiar |
| `service_start` / `service_stop` / `service_restart` | `name` | Solo servicios `managed`. **Iniciar exige que sus dependencias estén corriendo** (el mensaje dice cuáles faltan). **Detener detiene también a los que dependen de él.** Iniciar uno que ya corre no hace nada (idempotente) |
| `service_fail` | `name` | **Solo en la demo:** simula una falla para ver cómo la muestra el panel. El motor real la rechaza |
| `save_measurement` | — | **El motor simulado lo rechaza siempre**: una medición simulada no puede terminar en `docs/research/experimentos/` como MEDIDO (`CLAUDE.md`, "la sincronización se mide, no se supone") |

## 6. Seguridad

| Amenaza | Defensa | Tarjeta |
|---|---|---|
| Otro equipo de la red controla los parlantes | Por defecto escucha **solo en 127.0.0.1**. `--lan` es explícito y también exige token | *fail-closed-defaults* |
| Una página web maliciosa en el navegador manda órdenes a `127.0.0.1` (CSRF o DNS rebinding) | **Token obligatorio incluso en localhost.** Se valida el encabezado `Host` (solo la IP o el nombre con que se sirve) y, en el WebSocket, el `Origin` (tiene que coincidir con el `Host`) | — |
| Adivinar el token | 128 bits aleatorios por ejecución (`secrets.token_urlsafe(16)`), comparados con `hmac.compare_digest`. No hay contador que deje de limitar intentos, porque la entropía hace inútil el intento por fuerza bruta | *abuser-controlled-exemption* |
| El token queda en el historial o en el log | El token viaja una sola vez en la URL (`/?t=…`). El servidor lo cambia por una cookie `HttpOnly; SameSite=Strict` y redirige a `/` sin el token | — |

**El emparejamiento del teléfono:** con `--lan`, la terminal imprime la URL de la
red local y su QR, y el panel del PC muestra el mismo QR en "Conectar teléfono".
**El token dura lo que dura el proceso** (huella N1): al reiniciar, el teléfono
tiene que volver a escanear. Un token persistente en `~/.config/aurasync/` (N3)
queda para cuando moleste.

## 7. El panel (uno solo, d-7c8794-9d9776)

Es una grilla que se apila en el teléfono:
1. **Barra de transmisión:** transmitir o detener, el modo, la fuente, el volumen
   general y la latencia estimada. La etiqueta **SIMULADO** está visible cuando
   corresponde.
2. **Parlantes y canales:** el plano de la sala y, por parlante, su estado, RSSI,
   canal, volumen, silencio, retardo y ganancia, más un tono de prueba. Tiene un
   botón "Buscar parlantes".
3. **Servicios** (§7.1).
4. **Salud del enlace:** el controlador, el BIG, la cola, el drift con su
   gráfico, los cortes y la latencia por tramo.
5. **Niveles** por canal.
6. **Configuración del motor** (§7.3).
7. **Calibración:** calibrar o cancelar, aplicar los ajustes a los parlantes y
   guardar la medición (desactivado en la demo).
8. **Logs** (§7.2).

### 7.1 Servicios (d-7c8794-372a31)

| Servicio | Tipo | Depende de | En el motor real | En la demo |
|---|---|---|---|---|
| `controller` · Controlador | enlace | — | El puerto `serial:` abierto hacia la SuperMini | Simulado |
| `capture` · Captura | proceso (con PID) | — | `pw-record` en Linux, o el process tap en macOS (08 §2) | PID falso |
| `dsp` · DSP y reloj | tarea | `capture` | Upmix, remuestreo y lazo de drift | Simulado |
| `emitter` · Emisor | tarea | `controller`, `dsp` | El BIG en Bumble | Simulado |
| `bass` · Asistente BASS | tarea | `controller` | Asignar `BIS_Sync` por BASS (M5) | **No disponible**: espera E4 |
| `panel` · Panel web | tarea | — | Este servidor | Siempre corriendo; no se detiene desde sí mismo |
| `bluetoothd`, `pipewire`, `wireplumber` (Linux) o `coreaudiod` (macOS) | sistema | — | **Solo se observan**: el panel nunca los inicia ni los detiene (huella mínima, [08](08-integracion-y-plan.md) §2) | Estado simulado |

**Reglas:**
- "Transmitir" inicia la cadena en orden (controlador, captura, DSP, emisor).
- "Detener" detiene todo, y también corta el tono y la calibración (tarjeta
  *kill-switch-reaches-every-path*).
- Detener un servicio detiene a los que dependen de él.
- Si falla el emisor después de haber corrido, los parlantes asignados pasan a
  `lost`.
- **En el motor real**, el proceso de captura lo supervisa `aurasync` y lo mata
  al salir (tarjeta *cleanup-belongs-to-the-supervisor*).

### 7.2 Logs

**Son el log real del proceso:** el módulo `logging` de Python, con un búfer en
memoria de 2000 líneas (`logbuffer.py`). El servidor anota las conexiones y las
órdenes, ejecutadas o rechazadas; el motor anota lo de cada servicio
(`aurasync.svc.<servicio>`).

**Cómo llegan al navegador:**
- al conectarse, recibe las últimas 500 líneas;
- después, solo las nuevas, por el mismo WebSocket;
- cada cliente tiene su propia cola, así que uno lento no frena a los demás.

**Qué permite la vista:**
- filtrar por servicio y por nivel mínimo;
- buscar texto;
- pausar el desplazamiento;
- limpiar la vista;
- descargar un `.log`;
- hacer clic en un servicio de la tabla, que filtra los logs por él.

### 7.3 Configuración del motor

| Clave | Valores | Dónde está investigado | Se aplica |
|---|---|---|---|
| `upmix` | `simple`, `psd`, `surround` | [07](07-software-de-audio-en-el-pc.md) §4 | En vivo |
| `rear_delay_ms` | 0–30 | 07 §4.4 | En vivo |
| `presentation_delay_us` | 20000, 40000, 80000 | 02; el Clip 5 necesitó 80 ms ([references](../references.md)) | Reinicia el emisor |
| `bitrate_kbps` | 80, 96, 124 | Presets LC3 de 48 kHz | Reinicia el emisor |
| `transport` | `low_latency` (≤20 ms, RTN 2) o `high_reliability` (≤65 ms, RTN 4) | Presets 48_x_1 y 48_x_2 (07 §7.1) | Reinicia el emisor |
| `broadcast_name` | 1 a 32 caracteres imprimibles | — | Reinicia el emisor |
| `manufacturer_data` | `harman`, **fijo** | [01](01-parlantes-jbl.md): sin esto los JBL ignoran la transmisión | No se cambia |

### 7.4 El bug del desplegable de canal (Firefox en macOS)

**Qué pasaba:** el panel volvía a escribir el valor de cada control 10 veces por
segundo, salvo que el control tuviera el foco. En Firefox para macOS, un clic en
un `<select>` **no le da el foco**: la preferencia
`accessibility.mouse_focuses_formcontrol` viene en `false`. Así que, con el menú
abierto, el panel reescribía el valor y la elección se perdía.

**Cómo se confirmó** (MEDIDO, Playwright 1.60 en Chromium):
- se dejó un valor elegido en el desplegable sin el foco, y a los 600 ms el
  redibujado ya lo había vuelto a `''`;
- el clic seguido de teclado no sirvió para reproducirlo, porque en headless el
  teclado no maneja el menú nativo, ni siquiera en una página vacía;
- el Firefox de Playwright no arranca en este Mac, igual que en otro proyecto
  del usuario.

**Arreglo:** un control se escribe **solo cuando su valor cambia en el motor**,
nunca en cada cuadro, y no se escribe mientras se está editando (desde el
`pointerdown` hasta el `change` o el `blur`). Hay un test de navegador que lo
cubre (§9).

## 8. Errores

- **El envío de estado nunca tumba el motor** (tarjeta
  *best-effort-side-channels*). Si un cliente se cae o es lento, se lo
  desconecta y se anota una línea en el log; el motor y los otros clientes
  siguen.
- **Una orden inválida** responde `ok: false` con un motivo legible, que el panel
  muestra junto al control que la mandó.
- **Sin `--demo`, `aurasync panel` se niega a arrancar**, y explica que el motor
  real está bloqueado hasta i-7c8794-0d129c (fail-closed).
- Si se pierde la conexión, el panel muestra "Desconectado" y reintenta cada
  2 s, sin mostrar un estado viejo como si fuera actual.

## 9. Pruebas

- **`state.py` y `parse_command()`:** cada regla de §5 tiene un test que la
  rompe, incluidas las órdenes sin canal o sin parlante.
- **Motor simulado:** se lleva a instantes exactos con el reloj inyectado. Las
  asignaciones, el cambio de modo, los tonos y la calibración tienen tests de
  extremo a extremo, y guardar una medición debe fallar.
- **Seguridad:** sin token → 401; token malo → 401; `Host` ajeno → 403; `Origin`
  ajeno en el WebSocket → 403; el token de la URL se cambia por la cookie y la
  redirección lo quita.
- **Servidor:** se usa el `TestServer` de aiohttp sobre 127.0.0.1, dentro del
  mismo proceso. Se prueba la recepción de un snapshot, una orden que funciona,
  una rechazada, y que un cliente que falla no corta a los demás.
- **La interfaz:** `hatch run browser:test` corre Playwright 1.60.0 (Python, en
  un entorno de hatch) contra Chromium y WebKit, con el panel levantado dentro
  del mismo proceso.
  - Cubre: asignar un canal, el control que no se pisa (§7.4), transmitir y
    detener, servicios (detener en cascada, simular una falla, reiniciar), logs
    (llegan, se filtran), configuración, calibración aplicada, y que en 390 px no
    haya desplazamiento horizontal.
  - Va **fuera de `scripts/check.sh`**, porque necesita el caché de navegadores
    de Playwright.
- **Fidelidad del doble** (tarjeta *test-double-fidelity*): el motor simulado
  acepta lo que acepte `parse_command()`, igual que el real lo hará. Cuando exista
  `engine/real.py`, los mismos tests de órdenes corren contra los dos.

## 10. Orden de implementación

1. `state.py` y `engine/base.py`, con sus tests.
2. `engine/simulated.py`, con sus tests.
3. `panel/auth.py` y `panel/pairing.py`, con sus tests.
4. `panel/server.py`, con sus tests.
5. `panel/static/`: la vista de diagnóstico y después la de uso diario.
6. `cli.py`: el subcomando `panel`.
7. `scripts/check.sh`: la lista cerrada se amplía exactamente a estos archivos.

## 10.1 Estado de la primera iteración (2026-10-01)

Todo §10 está hecho sobre el motor simulado, con 79 tests verdes. `hatch check`
(ruff y pyrefly) y `scripts/check.sh` pasan.

**Verificado a mano**, con el panel corriendo en este Mac y capturas por el
protocolo de DevTools de Brave en headless:
- las dos vistas, en tema oscuro y claro, con ancho de PC (1366 px) y de teléfono
  (390 px);
- 401 sin token, 302 con token, 403 con un `Host` ajeno, y `--lan` con su QR en
  la terminal y en `/pairing.svg`.

**Bugs que encontró esa verificación**, todos corregidos:
- la URL con el token no salía cuando stdout iba a un archivo (faltaba `flush`);
- el atributo `hidden` perdía contra `display` de una clase;
- cambiar solo el `#hash` no cambiaba de vista;
- en el teléfono, los deslizadores quedaban sin ancho y el diagnóstico se salía
  de la pantalla;
- el QR se servía sin `xmlns`, y un `<img>` no lo dibuja (ahora hay un test);
- los eventos mostraban la hora UTC en vez de la local.

**No verificado:** el panel en un teléfono real por la red local (solo se probó
la emulación de 390 px y el QR por HTTP) y en Linux.

## 10.2 Estado de la segunda iteración (2026-10-01)

- **Tests:**
  - 131 de unidad (`hatch test`);
  - **26 de navegador** (`hatch run browser:test`: 13 casos en Chromium y 13 en
    WebKit, 94 s);
  - `hatch check` y `scripts/check.sh` pasan.
- **Lo nuevo:** el panel único, los servicios con su máquina de estados
  derivada del reloj, los logs del proceso por WebSocket con cursor por cliente,
  la configuración del motor, los ajustes por parlante, la búsqueda (aparece un
  Flip 7 simulado) y la calibración aplicable.
- **Bugs que encontró esta iteración**, corregidos:
  - el desplegable de canal (§7.4);
  - el búfer de logs se enganchaba después de crear el motor y perdía sus
    primeras líneas;
  - en el teléfono, "pid" y "retardo" quedaban alineados a la derecha por
    especificidad de CSS.
- **No verificado:** Firefox real. Su motor no arranca bajo Playwright en este
  Mac, así que el arreglo se probó reproduciendo el mecanismo en Chromium y
  WebKit. Falta que el usuario lo pruebe en su Firefox.

## 11. Fuera de alcance

- El motor real: captura, DSP, reloj, emisor, BASS y calibración con micrófono.
  Siguen bloqueados.
- Cuentas de usuario, acceso desde fuera de la red local y HTTPS. En la red de
  la casa, el token y el `SameSite=Strict` alcanzan para esta etapa; HTTPS
  requeriría certificados en el teléfono.
- Guardar perfiles desde el panel (C8): llega con M1.
