# Registro de sesiones

Cada sesión que cambia algo agrega su entrada **arriba**, justo debajo del
separador que sigue, con el formato que está al final del archivo. Las sesiones
paralelas no se ven entre sí. Lo único que avisa a la siguiente es lo que salió
mal y lo que quedó pendiente.

---

## 2026-10-03 · s-7c8794-0136d1 — Microcortes en PC-Ryzen5: el procesamiento descartado y los cortes al log

**Qué.** En `PC-Ryzen5`, con los 3 Go 4, el usuario oyó microcortes "constantes, como 2/s" que
se iban al reiniciar la sesión, y pidió arreglarlos porque sospechaba del pipeline de
procesamiento. Se midió sin parlantes y se descartó: el motor con su cadena, el `step` completo
y el servicio simulado con un stream abierto quedan en 5–8 ms de los 85 ms del bloque, sin
crecer en 15–30 min. Los cortes de esa noche no se pueden clasificar: el registro de radio estaba
apagado, y los eventos de `cuts.py` vivían solo en memoria. Desde ahora el servicio los escribe en
su log (`cut_report.py`): una línea cada 10 s con fallas, y el total al cerrar la sesión.
Después el usuario sospechó un leak y fijó la regla: **el stream se procesa siempre sobre datos
de largo fijo, y el historial va aparte y asíncrono**. Con ella se sacó del hilo del motor lo que
dependía del historial: la sonoridad integrada de `QualityMeter` (a `LoudnessHistory`, un hilo
con un histograma fijo, `GatedIntegrator`), el resumen de `CutLog` que la foto armaba en cada
bloque (al hilo `aurasync-cut-summary`), la copia del registro entero en `CutReporter`
(`CutLog.since`), y el buffer sin tope de la sala simulada. Al final, el usuario propuso calibrar
con los teléfonos del panel y pidió que el patrón distinga a cada parlante: la sonda ya lo hace
(SIMULADO, 0 falsos positivos), pero el consenso de `probe_measure.measure` usaba la mediana de
todos los parlantes, incluidos los no oídos, y con la mayoría sin oír descartaba a los oídos.
Corregido; la propuesta quedó en research/13 §5.3 y el hallazgo en experimentos/16 §4.1.1.
Después, en conversación, se diseñó el **estimador base de sincronía** (spec
`docs/superpowers/specs/2026-10-03-sync-estimator-design.md`, cinco decisiones nuevas
d-7c8794-589dec, -2c6f91, -ee5d47, -e61118, -0a4586, roadmap i-7c8794-737d4e) y, con "apruebo el
plan, continúa autónomamente", se construyeron sus pasos 1 y 2 (plan
`docs/superpowers/plans/2026-10-03-sync-estimator-steps-1-2.md`): `sync_measurement`, `sync_sim`,
`sync_methods` (mínimos cuadrados robustos), `sync_estimator`, `sync_levels`, `knob_docs`,
`sync_docs`, las ops `sync_state`/`sync_set`/`sync_apply`/`sync_explain` con sus rutas REST, el
enganche del lazo en la sesión y la tarjeta "Sincronía sugerida" del panel. Una revisión final en
contexto limpio encontró, y se corrigieron con un test visto fallar cada uno: un salto hallado en
la última vuelta mataba el hilo del estimador (`KeyError`) y nada lo atrapaba; un salto real se
creía dos ajustes y se perdía (la sugerencia erraba hasta 2 ms ~6 min), ahora se arrastra de un
ajuste al siguiente; perillas enteras aceptaban decimales; el historial sobrevivía a las sesiones y
aplicar con un parlante ya no instalado cerraba la sesión; las barras de la figura del voto salían
del lienzo; el voto no caía en w/(1+w) (las filas de datos son blandas frente a los priors: se
escalaron); y una medición rechazada no decía por qué y cambiaba el id de la sugerencia.
**Archivos.** `host/src/aurasync/cut_report.py` (nuevo), `host/src/aurasync/service.py`,
`host/src/aurasync/quality.py`, `host/src/aurasync/dsp/loudness.py`, `host/src/aurasync/cuts.py`,
`host/src/aurasync/snapshot.py`, `host/src/aurasync/session.py`, `host/src/aurasync/simulated.py`,
`host/tests/test_cut_report.py`, `host/tests/test_fixed_length_engine.py` (nuevo),
`host/tests/test_quality.py`, `docs/research/experimentos/12-microcortes-con-3-go-4.md` §4.1,
`host/src/aurasync/probe_measure.py`, `host/tests/test_probe_measure.py`,
`docs/research/experimentos/16-ocho-parlantes-en-simulacion.md` §4.1.1,
`docs/research/13-dispositivos-pi-pico-y-panel-independiente.md` §5.3.
**Por qué.** Sin el tipo de corte (motor tarde, tubería vacía, xrun, radio) no se puede arreglar
la causa, y la de esa noche se perdió con la sesión.
**Arquitectura.** ✅ Cumple: el reporter lee el `CutLog` desde el hilo del motor y no agrega
trabajo a otros hilos; código nuevo en inglés, en un módulo nuevo.
**Qué salió mal en el camino.** La sospecha del usuario (el procesamiento) no se sostuvo: la
cadena del usuario cuesta lo mismo que la de fábrica. El servicio lanzado al principio de la
sesión (`aurasync service`, en segundo plano) se detuvo solo al llegar al límite de 2 h de las
tareas en segundo plano.
**Qué quedó pendiente.** La causa de los cortes: C2 de experimentos/12 con el registro de radio
prendido **antes** de que empiece a sonar. Ningún leak encontrado alcanza solo para dos cortes
por segundo (peor caso medido ~2,6 ms por bloque). La regla del largo fijo no está en
`docs/decisions.md`: queda propuesta como decisión. `ultimos()` del micrófono continuo
concatena su anillo entero (~14 s) en cada bloque con el lazo prendido: es fijo, no crece, pero
cuesta ~1–2 ms (estimado). `test_interpolation.py::test_it_costs_far_less_than_the_formula`
falla en `PC-Ryzen5` (6,5–7,4× contra el 8× pedido, umbral fijado en el Mac).
**Desvío del plan.** Ninguno.
Del estimador: el nivel de la sonda enmascarada no se pudo estimar en simulación (errores
sistemáticos de hasta 12 dB con cuatro estimadores probados; no se entrega); la calibración con
ruido como objetivo, los pasos 3 a 5 y toda prueba con parlantes quedan pendientes. En `PC-Ryzen5`
fallan, igual que en el checkout principal sin estos cambios, tres tests de navegador ya existentes:
`test_every_setting_explains_itself`, `test_the_navigation_cost_of_the_tabs_does_not_grow` (32,0
contra 31,4) y `test_pwa` (falta la compilación web).
**No verificado.** Si los cortes son la radio (el techo de `reduce bitpool` es 2/s) o el reloj
(§1.1 de experimentos/12): ninguna de las dos se midió esa noche.
**Medido.** `PC-Ryzen5`: `Motor.procesar` 5,33–5,38 ms de mediana y 10,5 ms de peor en 10 500
bloques; `AudioSession.step` 7,5–7,7 ms de mediana y 14,9 ms de peor; el servicio `--simular` 30 min en
tiempo real 6,1–6,4 ms, con 7 cortes `late` (43–66 ms) todos mientras corría `scripts/check.sh`. Con la regla del largo fijo: `quality.summary` 0,13–0,14 ms planos en 15 min
(antes 0,33 → 1,90 ms); el servicio `--simular` 15 min, 0 cortes, 19 hilos, 6 descriptores, memoria
96,2 → 97,2 MB. `scripts/check.sh`: 816 tests bien, 1 mal (el umbral de interpolación de siempre).

---

## 2026-10-02 · s-7c8794-7142ab — Microcortes, la cadena con todas sus perillas, calidad, Rust, dispositivos y el panel como PWA

**Qué.** Sesión en el **Mac** (sin parlantes), con el usuario pidiendo mejoras del motor, del
panel y de la usabilidad, pruebas planeadas, y una investigación de procesamiento de sonido
aplicada al plan. Sus prioridades: **microcortes**, después **graves y volumen**, después **más
controles visibles y entendibles**. Más tarde fijó el foco (**Linux completo primero; el repo es
sobre audio**, d-7c8794-a5b83b), la meta de **8 parlantes** (d-7c8794-3b7793), el panel como
**PWA en GitHub Pages** (d-7c8794-37f9bc) con **npm solo para la web** (d-7c8794-6da524), y explorar
**Rust** para el camino crítico (d-7c8794-36dde5). Trabajo hecho por agentes en paralelo, con
archivos repartidos:
- **Investigación:** research/11 (cómo medir la calidad, procesamiento y filtros, canales y
  códecs, visualización y usabilidad), research/12 (motor en Rust), research/13 (Pi Zero 2 W, Pico
  2 W, más de 3 parlantes, panel independiente). **Hallazgo central:** cada `reduce bitpool` del
  sink Bluetooth de PipeWire es un paquete descartado (VERIFICADO en `media-sink.c` 1.6.9);
  experimentos/10 §5.1 y §5.5 lo leían al revés y quedaron corregidos.
- **Microcortes:** `radio.py` (descartes y bitpool por parlante desde el journal, el registro por
  topic con `wpctl`, anotado y revertido), el corte `radio` en `cuts.py`, la op `radio_log` y la CLI,
  y el protocolo `probes/14-microcortes/` (experimentos/12), con `rfkill block wifi` como primera
  variable.
- **La lectura sinc del retardo tenía al motor a ~5× el tiempo real**, no 63× (Bessel por muestra
  y coeficiente; lo encontró la investigación de Rust). Corregida: salida idéntica con el retardo
  quieto, ≤1e-10 en rampa; **32× con 3 parlantes y EQ** (`tests/test_interpolation.py`). Es
  candidata a parte de los cortes "motor tarde".
- **La cadena:** `chain.py` (descriptores de 8 etapas con algoritmos y perillas), contrato
  `chain`/`chain_set`/`chain_reset`, persistencia dispersa en `chain.json` y `presets-chain.json`,
  y el motor que la lee, **bit a bit igual con los valores por defecto** (golden).
- **DSP nuevo, apagado por defecto** (d-7c8794-d1118c): crossover LR4, bajo psicoacústico NLD,
  limitador true-peak con look-ahead, cola difusa, sonoridad BS.1770, presupuesto y techo del EQ;
  integrado en `chain_stages.py`; volumen por AVRCP con lectura de vuelta (`bt_volume.py`);
  calidad en vivo (`quality.py`: LUFS, ganancia, PSR, true peak, limitador) y eventos SSE
  `quality`, `chain` y `radio`; A/B con la sonoridad igualada.
- **Panel:** medidores sin layout forzado (421 → 17 layouts/s), el stream se cierra con la pestaña
  oculta, avisos donde se mira, sincronía en una regla de zonas, accesibilidad, números con coma;
  la **pantalla Cadena** en Vite + TypeScript + Preact, generada desde los descriptores, con la
  franja de calidad, el carril de radio y el A/B con sonoridad; los tipos del contrato generados por
  Python y lo compilado verificado sin Node (`host/scripts/web_stamp.py`).
- **Servicio para la PWA:** HTTPS con raíz propia restringida por NameConstraints (`tls.py`),
  tokens por cliente (`clients.py`), emparejamiento (`pairing.py`), CORS, ticket de stream, mDNS
  opcional; y la **PWA** (`host/web/`: transporte único con token y ticket, pantalla de conexión
  y emparejamiento, administración de clientes, service worker offline al estilo de
  `thom-music-player`, `build:pwa` que falla si encuentra datos privados, y
  `.github/workflows/pages.yml` sin activar). `/pairing.svg` ya no lleva el token maestro.
- **Calibración:** **un error que también afectaba a 3 parlantes**: la pista del estimador de nivel
  era relativa al primero cuando las referencias están alineadas a la mediana; con el primero a más
  de 15 ms de la mediana la ganancia salía de la diafonía (2,7–6,6 dB) y se informaba confiable.
  Corregido (`medicion.calibrar`), con tres casos nuevos en el test.
- **Pruebas preparadas para PC-Ryzen5:** `probes/14-microcortes`, `15-graves-y-volumen`,
  `16-calidad`, `17-e-s-nativa-rust` (la prueba de concepto de E/S nativa en Rust: compila y
  testea en el Mac, la parte de PipeWire solo chequeada contra los headers), `18-costo-de-la-cadena`,
  `19-ocho-parlantes`; experimentos 12 a 16 con los criterios escritos antes de medir.

**Segunda parte (después del push, a pedido del usuario: "continúa cerrando los pendientes").**
- **El lazo de recalibración sumaba dos veces lo que medía** (la referencia se guarda después de
  la línea de retardo, y el controlador la volvía a sumar): explica que las propuestas crecieran
  en experimentos/09 §5. Lazo nuevo (`arrival_loop.py`): llegadas absolutas, aceptación por
  parlante y la deriva de cada uno; en simulación < 0,5 ms durante una hora con 3 y 8 parlantes
  a 22 y 50 ppm. Ya no corrige niveles (eso queda para la calibración).
- **La sonda enmascarada en el motor** (`dsp/probe.py`, `probe_measure.py`; i-7c8794-e3e40d paso
  2): simultánea e independiente por parlante, −20 dB bajo la música, apagada por defecto;
  conectada al contrato (`probe`, `probe_margin_db`, en vivo), al estado
  (`recalibration.probe`, `drift_ppm`) y a Ajustes → Sincronía. Ninguna de 646 mediciones aceptadas
  erró más de 1 ms (SIMULADO).
- **Calibración en dos grupos** que comparten tres parlantes desde 7 (`group_calibration.py`): 0
  de 24 salas fuera de 1 dB con 8; el ancla única del exp. 16 no sobrevivió a cambiar la semilla.
  La respuesta medida trae `coherence` y `response_error_db` por tercio, y el panel atenúa por el
  error (γ² sola cae a ~1/N con N parlantes).
- **Escala a 8:** el servicio arranca y suena con 8 (el tope del decorrelador avisa), roles por
  ángulo (`5.0`, `hex`, `7.0`, `octagon`, `rings`) que reproducen los de hoy, la sala simulada con
  8. El banco "por peor par por octava" y la asignación por mezcla **no sobrevivieron al motor
  real** (empeoraban 20 de 32 casos): descartado el primero, la asignación queda como perilla.
- **Panel:** "Deshacer" en vez de `confirm()`, nivel del micrófono antes de calibrar, la respuesta
  con los tercios poco confiables atenuados, y 8 parlantes usables (tarjetas plegables, acciones
  por grupo).
- **Servicio:** el QR de la terminal es el enlace de emparejamiento de la PWA cuando hay HTTPS (ya
  no da el token maestro); el log dice qué cliente mandó cada orden.
- **No se hizo, a propósito:** `bumble` y `lc3py` como extras. El `pyproject.toml` declara que
  fallar en otras plataformas es intencional (d-7c8794-c23c20); se revisa en la fase de
  dispositivos.
- **Tests estabilizados:** el de tiempo real del motor (décimo más rápido, piso 5×) y el de la
  descripción de la entrada (espera lo que verifica).
- **Pendiente con parlantes, además de lo de arriba:** el paso 3 de la sonda (+5 ms inyectados,
  encontrados dos veces) y el paso 4 (A/B ciego de inaudibilidad); confirmar que el lazo nuevo
  ya no hace crecer las propuestas. **Un límite a decidir:** a 50 ppm durante una hora, los
  retardos se separan ~300 ms, más que los 250 ms de la línea del motor.
- `scripts/check.sh`: ok con 802 tests; navegador en Chromium verde (Firefox no arranca en este
  Mac).

**Archivos.** `host/src/aurasync/`: `chain.py`, `chain_stages.py`, `quality.py`, `bt_volume.py`,
`radio.py`, `tls.py`, `clients.py`, `pairing.py`, `access.py`, `lan.py`, `remote.py`, `mdns.py`,
`contract_types.py`, `dsp/{crossover,virtual_bass,diffuse,loudness}.py` (nuevos); `motor.py`,
`control.py`, `service.py`, `session.py`, `snapshot.py`, `rest.py`, `cli.py`, `presets.py`,
`simulated.py`, `cuts.py`, `medicion.py`, `dsp/{eq,limiter,decorrelate,interpolation}.py`,
`panel/*`; `host/web/`; `host/scripts/`; `host/tests/` y `host/tests_browser/` (nuevos y
ajustados); `host/pyproject.toml` (`cryptography`); `scripts/check.sh`; `probes/14` a `19`;
`docs/research/{11,12,13}-…`, `experimentos/{12,13,14,15,16}-…`, correcciones en
`experimentos/10-…`; `docs/decisions.md` (11 decisiones nuevas), `docs/roadmap.md` (el plan desde
el 2026-10-02 y entradas nuevas), `docs/references.md`, `docs/superpowers/specs/2026-10-02-…`,
`CLAUDE.md`, este registro.

**Por qué.** El pedido del usuario al empezar, y sus respuestas durante la sesión (las prioridades,
el foco en Linux y audio, los 8 parlantes, la PWA, Rust, npm solo para la web).

**Arquitectura.** ✅ Cumple con las decisiones nuevas y con d-7c8794-9afee2: el motor queda
separado del emisor; el código nuevo en inglés y `motor.py` en español; lo que suena por defecto
no cambió (golden bit a bit). **Tarjetas aplicadas:** *persist-inputs-derive-verdicts* (solo se
guardan elecciones), *no-simultaneous-deploy* (archivos aparte para que un rollback arranque),
*kill-switch-reaches-every-path* (el registro de radio se revierte en cada camino de salida),
*secrets-survive-rotation* (rotar el maestro no desconecta clientes), *a-check-must-be-seen-to-fail*
(cada test nuevo se vio fallar con una mutación).

**Qué salió mal en el camino.**
- **El 63× del motor estaba vencido desde el 2026-10-01** y la spec lo citó como vigente; nadie
  re-midió el costo después del cambio a sinc. Lección anotada en experimentos/10 §1.
- **Mi primer arreglo de la pista de niveles no cambió nada:** supuse que los residuos eran
  absolutos y `calibrar_por_ventanas` los vuelve relativos al más temprano. Lo delató que el test
  siguió fallando.
- Un agente creó `probes/17-costo-de-la-cadena` con el mismo número que la prueba de concepto de
  Rust: renombrado a `18`.
- Dos agentes se cortaron por el límite de uso y se retomaron desde su transcripción.
- Un test de tiempo real del motor fallaba con la máquina cargada (mediana 8× contra un piso de
  10×): ahora usa el décimo más rápido.
- Un `hatch fmt` de un agente reformateó un archivo que yo editaba (solo formato).
- Mi `rustup show` auto-instaló el toolchain `stable` en `~/.rustup` (el usuario había instalado
  `rust` y `rustup` con Homebrew minutos antes).

**Qué quedó pendiente.**
- **Todo lo que necesita parlantes**, en `PC-Ryzen5`: experimentos 12 (microcortes: paso 0 con el
  journal y `pw-top`, C2, C3 empezando por `rfkill`), 13 (Rust, A/B intercalado), 14 (graves y
  volumen), 15 (calidad con micrófono). Las etapas nuevas siguen apagadas hasta medirlas.
- **La PWA**: **publicar** (Settings → Pages → Source: GitHub Actions, y push) lo decide el
  usuario; después, probarla en Chrome Android, Safari del iPhone (navegador y PWA instalada) y
  Firefox, con la raíz instalada y sin ella, y el aviso de red local de Chrome desde el origen real.
  El QR que imprime la terminal al arrancar el servicio sigue llevando el token maestro.
- Para 8 parlantes (experimentos/16): calibrar en dos grupos desde 7; que el tope del
  decorrelador no impida arrancar (hoy el servicio no arranca con 7); el banco y la asignación de
  filtros; la sonda simultánea; seguir la deriva por parlante; roles por ángulo; el panel por grupos.
- En el PC, la organización por pestañas ya no es la más barata con Cadena (13,7 contra 10,8 del
  menú lateral): decisión del usuario.
- Firefox de Playwright no arranca en este Mac.
- Probar `wpctl set-log-level` por topic en WirePlumber 0.5.17 real.

**Desvío del plan.** La spec se escribió como registro y se implementó sin un plan aparte, porque
el usuario pidió "implementar todos los cambios teóricos y preparar las pruebas". Los desvíos
durante la construcción están en la spec §11. La PWA, Rust, los dispositivos y los 8 parlantes se
agregaron por pedido del usuario a mitad de la sesión.

**No verificado.** Nada se escuchó ni se midió con parlantes. Lo de PipeWire de la prueba de
concepto en Rust no se ejecutó. Lo de la Zero 2 W y la Pico es estimado. La PWA no se probó en un
teléfono.

**Medido.**
- Motor con 3 parlantes y EQ en el Mac: mediana 2,7 ms y p99 3,4 ms por bloque de 85 ms (32×);
  antes de corregir la lectura sinc, 4–7 ms solo en esa lectura por parlante.
- Panel (Chromium, 390×844, freno ×4): 421 → 17 layouts/s; Cadena a la vista, 3 layouts/s.
- Stream: 16 kB/s. Prueba de concepto en Rust: ~10–27 µs por cuántum (~2000× tiempo real).
- SBC simulado con libsbc: joint 40 con L = R da 32,8 dB de SNR; mono 29 da 26,1; dual 39 da 33,7.
- `scripts/check.sh`: ok con 702 tests; navegador 75 de 75 dos veces (Chromium); vitest 25.

---

## 2026-10-01 · s-7c8794-252141 — El servicio de control construido: contrato, REST, sesión, presets y rampas

**Qué.** La primera entrega de i-7c8794-bdb678, en `PC-Ryzen5`, con la spec que el usuario
aprobó al empezar.
- **La spec quedó aprobada** con dos secciones nuevas: el panel de `panel-demo` será un
  cliente de `control.py` (§13), y la v1 no tiene notificaciones, así que se pregunta el
  estado cada tanto (§14).
- **El código nuevo, en inglés:**
  - `control.py`: el contrato, que valida todo antes de aplicar nada y tiene un código
    estable por error;
  - `rest.py`: con la biblioteca estándar, el token y `/v1/command`;
  - `service.py`: un solo hilo escribe; `service.json` con permisos `0600`; sobrevive a una
    sesión que falla;
  - `session.py`: el lazo de `cmd_run`, movido en el mismo orden;
  - `presets.py`: con escritura atómica;
  - `dsp/ramps.py`: valores suavizados, rampa en dB y el corte de 80 + 80 ms.
- **`motor.py`**, que sigue en español: `pan` y `ambiente` suavizados, el extractor que nunca
  se apaga, `volumen_db`, el decorrelador solo a través del corte, y
  `actualizar_desde_control`. El lazo sigue usando `actualizar`, que nunca corta.
- **`cli.py`:** `run` delega en `session.py`; está el subcomando `service`; y el micrófono
  sale de `--microfono`, después de `service.json` y después de la fuente por defecto. Se
  quitó `MICROFONO_POR_DEFECTO`.
- **Documentación:** `host/docs/control-api.md` (la API, en inglés), `host/README.md`,
  [experimentos/10](../../docs/research/experimentos/10-servicio-de-control-con-3-go-4.md)
  (con el protocolo con parlantes) y el roadmap.
- **Segunda parte, a pedido del usuario: las correcciones, y las pruebas preparadas antes de
  conectar los parlantes.**
  - La sesión revisa el ruteo **cada 2 s** mientras suena, y no solo al abrir. Un stream
    desviado se devuelve a su parlante; uno cuyo parlante ya no existe se **cierra**
    (`Reproductor.soltar`), porque WirePlumber podría moverlo al sink virtual.
  - `state` marca cada parlante con `playing` y avisa de los perdidos y de las reparaciones.
    La spec §7 se actualizó con esto.
  - `probes/10-servicio-de-control/` tiene un script por paso del protocolo, una comprobación
    de ruteo independiente (`ruteo.py`), la fuente que verifica haber llegado a `aurasync`,
    la grabación del micrófono, el detector de clics (`clics.py`, que mide su propia
    sensibilidad en cada grabación) y el **ensayo en seco** del paso 3 (`seco.py`).
- **Tercera parte: el panel completo sobre el motor real** (d-7c8794-09d10f, spec §15). El
  usuario pidió "todas las features conversadas" en vez de un panel mínimo.
  - Todo lo de `panel-demo` está trasladado a A2DP: roles, silencio, tono, búsqueda y
    conexión de parlantes, servicios con PID, logs, salud, niveles, configuración,
    calibración dentro de la sesión, presets y A/B ciego.
  - Módulos nuevos: `snapshot.py`, `sources.py`, `system.py`, `logbuffer.py`,
    `simulated.py` y `panel/`. El contrato creció de forma aditiva, y `delay_ms` se puede
    fijar a mano con el lazo apagado.
  - Seguridad para el navegador: cookie `HttpOnly; SameSite=Strict`, chequeo de `Host` y de
    `Origin`.
  - `aurasync service --simular`.
  - Se agregó la dependencia `segno` (el QR) y el entorno `browser` de hatch (Playwright
    1.60, Chromium y Firefox).
  - **Se corrigió `medicion.niveles`** (filtro de graves, normalización por la propia
    autocorrelación, índices circulares, ventana de 20 ms). El experimento 06 tiene una
    nota sobre esto.
- **Quinta parte: el audio "degradado", la calidad en el panel y el espectro de cada
  parlante** (experimentos/10 §6). Arreglos:
  - la tubería quedaba más chica que el ciclo de `pw-play` (~10 cortes/s) — **error mío de
    la cuarta parte**;
  - el decorrelador coloreaba ±9,5 dB por tercio de octava; ahora es plano a ±0,2 dB y
    decorrela más;
  - el panel muestra los cortes por minuto y la respuesta en frecuencia de cada parlante;
  - "Aplicar" saltea los parlantes dudosos;
  - el servicio escucha en la red de la casa (antes anunciaba la IP de la VPN WireGuard y
    rechazaba la de la casa por el chequeo de `Host`).
- **Cuarta parte: pruebas con los 3 Go 4 y música, contra el servicio vivo** (a pedido del
  usuario: el servicio corre y las pruebas son órdenes a su API; se redespliega solo cuando
  cambia el código). Incluye su pregunta sobre si se puede calibrar solo con el protocolo
  ([experimentos/10](../../docs/research/experimentos/10-servicio-de-control-con-3-go-4.md)
  §5).
  - **Correcciones:**
    - la calibración mide el residuo a través de las correcciones vigentes;
    - el motor procesa silencio sin entrada, para que "Aplicar" no quede esperando;
    - la tubería hacia `pw-play` es de dos bloques (la latencia bajó de 1,03 s a 0,50 s);
    - la alineación gruesa acepta 100 ms de separación;
    - los streams llevan `node.dont-move`;
    - el umbral de señal del lazo sigue al volumen general;
    - la calibración informa la latencia medida, y el panel la muestra;
    - **la salida combinada (`ReproductorCombinado`), por defecto** (d-7c8794-a41ec9).
  - **Herramientas:** `campana.py` (fases A–E, P, V, R), `protocolo.py`, `monitores.py`,
    el volcado `calibration_dump` y `servicio.sh redeploy`.

**Archivos.** `host/src/aurasync/{control,rest,service,session,presets}.py`,
`host/src/aurasync/dsp/ramps.py`, `motor.py`, `sonido.py`, `cli.py`, `host/pyproject.toml`,
`host/tests/test_{control,rest,service,presets,ramps,motor_live}.py`, `test_cli.py`,
`host/tests/test_session.py`, `host/docs/control-api.md`, `host/README.md`,
`host/src/aurasync/{snapshot,sources,system,logbuffer,simulated}.py`, `host/src/aurasync/panel/`,
`host/src/aurasync/medicion.py`, `host/tests/test_panel_ops.py`, `host/tests/test_medicion.py`,
`host/tests_browser/`, `docs/decisions.md`, `docs/research/experimentos/06-…`,
`probes/10-servicio-de-control/`, `docs/superpowers/specs/2026-09-29-…`,
`docs/research/experimentos/10-…`, `docs/roadmap.md`, `CLAUDE.md`, este registro.

**Por qué.** El usuario pidió seguir con el servicio de control y tener algo que se pueda
probar hoy en este equipo, con las correcciones que se habían conversado.

**Arquitectura.** ✅ Cumple con la spec y con d-7c8794-7b3093: el código nuevo está en
inglés y `motor.py` mantiene su idioma. `control.py` y `dsp/ramps.py` no hacen E/S.
**Tarjetas aplicadas:**
- *a-check-must-be-seen-to-fail*: se vieron fallar, con el código roto a propósito, los
  tests del suavizado, el del preset que corta aunque no cambie nada y el del escritor único;
- *kill-switch-reaches-every-path*: `shutdown`, Ctrl-C y `SIGTERM` cierran la sesión;
- *cleanup-belongs-to-the-supervisor*: el servicio cierra la sesión que falla, y la sesión
  cierra todo con un `ExitStack`.

**Qué salió mal en el camino.**
- **Corrí `git checkout -- .` dentro de `host/` para deshacer una mutación de prueba, y
  revirtió los cambios sin commitear de `motor.py`, `cli.py` y `sonido.py`.** Se volvieron a
  aplicar con los mismos scripts de edición. Desde entonces, las mutaciones se deshacen con
  un reemplazo de texto y no con git.
- El primer test del escritor único no detectaba la mutación: mandaba las órdenes antes de
  que la sesión diera su primer paso. Ahora espera a que la sesión esté corriendo y reparte
  las órdenes entre muchos pasos.
- El atributo `presets` (el almacén) le hacía sombra al método `presets()` del contrato:
  `preset_store`.
- La ruta por defecto de la instalación en `service.json` no respetaba `XDG_CONFIG_HOME`.
  Lo encontró la prueba de humo.
- **El primer detector de clics no veía nada:** promediaba la energía en ventanas de 5 ms y
  diluía un salto de una muestra. Su autoprueba además ponía el salto de ganancia en un
  cruce por cero del tono, donde no hay discontinuidad. Las dos cosas las delató la
  autoprueba. Ahora usa el pico de la ventana, y la autoprueba pone el salto en una fase
  cualquiera.
- **`medicion.niveles` medía mal los niveles desde antes de esta sesión, y lo destapó la
  calibración del panel simulado.** Los retardos daban exactos y las ganancias no; con una
  mezcla sintética limpia daba igual, así que no era la simulación. Había dos errores
  (experimentos/10 §3.1): hasta 11,4 dB de error según la realización del ruido, y el
  parlante que llega primero se perdía. Su validación anterior era repetibilidad con la
  misma semilla, no exactitud. **Las ganancias que escribió `calibrate` antes no son
  confiables.**
- El primer intento de corrección (normalizar sin filtrar los graves) seguía fallando, y
  el segundo dejó de detectar un parlante mudo con la ventana de 80 ms. Un barrido de
  ventana y de corte (accuracy + silencio a la vez) llevó a 300 Hz y 20 ms.
- Un test de navegador de Firefox falló una vez de cada tres: el test tocaba un control
  antes del primer dibujo, y el dibujo lo pisaba. Ahora la página marca `data-ready` y los
  tests lo esperan.
- Iniciar una sesión no dejaba ninguna línea en el log de `session`: lo encontró el test
  que filtra logs por servicio.
- **La primera campaña con parlantes no aplicó nada**: "Aplicar" dependía de que entrara
  audio. Lo delató que lo aplicado seguía en 0,00 ms; desde entonces la campaña verifica que
  aplicar cambie los retardos.
- **`servicio.sh` dejaba un bash intermedio con la tubería abierta**, y un `| tail` esperaba
  para siempre (dos veces). Ahora el lanzamiento hace `exec` con `setsid` y redirige todo.
- **Mi `pkill` se mató a sí mismo** una vez: el patrón aparecía en su propia línea de comando.
- **Un proceso lanzado con `&` desde bash nace con SIGINT ignorada:** `kill -INT` no cerraba
  `run`. Lo delató `SigIgn` en `/proc`.
- **La fase E (el lazo contra el contenido) no sirvió con la señal de prueba:** sus tonos
  son periódicos y la correlación se vuelve ambigua. El lazo necesita música.
- **El experimento SBC mono no redujo la congestión** y se revirtió (registro en
  `datos/10/cambios-de-sistema.txt`).
- **Preparar el paso 5 destapó un riesgo:** con un parlante apagado, la sesión seguía
  "sonando", porque el `pw-play` huérfano no muere y su stream puede terminar en otra
  salida. La corrección está arriba.

**Qué quedó pendiente.**
- **El protocolo con parlantes** (experimentos/10 §4): los scripts están listos y no había
  parlantes conectados.
- `fuente.sh` y la vigilancia del ruteo no se probaron con un sink `aurasync` real: crearlo
  sin parlantes habría podido cambiar el sink por defecto del sistema (la trampa de
  experimentos/09). Sí se comprobó que `pw-play --target` deja `target.object` en su nodo,
  que es lo que `fuente.sh` y `ruteo.py` leen.
- **Un candidato sin probar:** las propiedades de WirePlumber `node.dont-fallback`,
  `node.dont-reconnect` y `node.dont-move` en los `pw-play` evitarían de raíz que un stream
  termine en otro lado. No se tocaron, porque cambian el orden validado de oído y bloquearían
  el `pactl move` que hoy repara el ruteo.
- **El panel no se probó en un teléfono real**: solo con 390 px emulados (el servicio está
  en 127.0.0.1).
- **El lazo contra la música con un retraso inyectado (fase E)**: Spotify quedó en pausa.
- **La curva de volumen AVRCP del Go 4**: "−6 dB" de PipeWire se midieron como 4,1 dB.
- **El firmware de los Go 4 sigue sin leer.**
- **La deriva de ~22 ppm entre parlantes** con salida combinada: el lazo tiene que seguirla.
- El ajuste `player_latency_ms` no cambia la latencia medida; habría que quitarlo o
  explicarlo en el panel.
- Los avisos del estado (`warnings`) siguen en inglés dentro de un panel en español.
- Los scripts del probe (pasos 1 a 7) siguen hablando REST; los pasos 8 a 10 son con el
  panel.
- **El JBL Charge 6 estaba conectado** como salida A2DP al terminar (lo vio el
  observador); no es de la instalación. Si el sink por defecto es él, cuidado con lo que
  suena ahí durante las pruebas.
- Un `pw-play` que muere sin que mueran todos no cierra la sesión: se sigue con los que
  quedan, como hacía `run`. La spec decía otra cosa (§7), y queda a la vista.
- **Mover `ambience` casi siempre pasa por el corte:** cambia el retardo de Haas del
  parlante (`ambiente × retardo_traseros_ms`), y más de 1 ms de cambio supera los 2 s de
  rampa. Hay que oírlo para saber si molesta.
- La rama `panel-demo` sigue sin integrar (spec §13).
- `git fetch` falla en este equipo por la clave SSH: no se sabe si `origin` tiene algo más
  nuevo.

**Desvío del plan.** No se escribió un plan de implementación aparte: el usuario pidió
implementar directamente, y la spec alcanzaba como plan.

**No verificado.** Nada se escuchó. Que `session.py` suene igual que el `run` anterior es el
riesgo mayor de la spec (§12), y solo se ve con parlantes.

**Medido.**
- El motor procesa a 63× el tiempo real en `PC-Ryzen5`: 1,35 ms por bloque de 85 ms, igual
  con rampas y cortes.
- `pw-dump` tarda 16 ms.
- El ensayo en seco del paso 3 da 6 cortes donde se esperan, 0 donde no y 0 clics en los
  tres parlantes; con el suavizado roto encuentra un clic de +15 dB.
- La calibración simulada: retardos exactos (9,0, 4,5 y 0,0 ms) y ganancias de -4,7, -2,8
  y 0 dB frente a -4,4, -2,5 y 0 esperados.
- El estimador de nivel corregido: 0,24 dB de error máximo en 12 realizaciones; antes,
  11,4 dB.
- Los tests de navegador pasan 38 de 38 en dos corridas seguidas, en unos 100 s.
- El observador del sistema tarda 103 ms por lectura.
- Con parlantes (experimentos/10 §5):
  - cierre de la calibración: 0,35 ms;
  - retraso inyectado de +4 ms medido con 0,9 ms de error; ganancia de −6 dB, con 0,9 dB;
  - latencia de escrito a oído: 1,03 s → 0,50 s;
  - saltos de 42,67 ms: 4 de 13 y 5 de 6 con un `pw-play` por parlante, 0 de 8 con salida
    combinada;
  - deriva con salida combinada: ~1,35 ms/min;
  - PipeWire informa 145,19 ms para los tres, todo el día;
  - AVRCP −6 dB (cúbico) se oyó como 4,1 dB, y no movió el retardo (0,02 ms).
- `scripts/check.sh` pasa con 330 tests; los de navegador, 38 de 38.

**Parte 6 (noche): la guía de diseño en el panel, y la música primero.**
- **Panel**, siguiendo las guías de diseño investigadas:
  - medidores con escala, zonas de color, pico retenido 1,5 s con caída de 20 dB en 1,7 s,
    y un indicador de saturación que queda encendido hasta que se toca;
  - doble clic para volver a 0 en pan y volumen;
  - "Silenciado" con el estilo de botón presionado;
  - el gráfico de respuesta entre 20 Hz y 20 kHz, con sus marcas;
  - el A/B con su valor p binomial;
  - los pasos de la calibración numerados;
  - objetivos táctiles de 44 px y foco visible.
- **El usuario oyó la música apagada y sin bajos.** Las causas, medidas en experimentos/10
  §7:
  - la ecualización recortaba hasta 18 dB en los graves;
  - WirePlumber había dejado la salida combinada en −20 dB.

  Se cambió, con la decisión d-7c8794-e8f7e3:
  - la ecualización solo realza y lee la medición con optimismo (el tipo de parlante con la
    banda del fabricante);
  - un limitador de pico;
  - la salida al 100 %, comprobada, y en f32.
- **El lazo, encendido por defecto** (d-7c8794-f94c13). Calibrar lo pausa y aplicar lo
  reinicia.
- **Planificado:** la sonda enmascarada para medir la latencia con la música sonando
  (i-7c8794-e3e40d). La investigación está en research/03 §3.2 y la spec en
  `superpowers/specs/2026-10-01-music-first-and-masked-probe-design.md`.
- **Lo que salió mal:** la primera ecualización se diseñó apuntando a una respuesta plana sin
  preguntar qué se quería oír. La "corrección" era justo lo que el usuario no quería.
- **Pendiente:**
  - la referencia medida, directo contra aurasync;
  - el preset "música";
  - la sonda;
  - la sección de guías de diseño en research/10.
- `scripts/check.sh` pasa con 352 tests; los de navegador, 50 de 50.

**Parte 7: la salida directa sonaba mejor que el motor, la entrada analizada y el micrófono
elegible.**
- **Medido fuera de línea, etapa por etapa** (experimentos/10 §8,
  `probes/11-calidad-de-la-cadena/cadena.py`): la interpolación lineal del retardo le quitaba
  hasta 3,5 dB a 12,7 kHz, distinto a cada parlante. Ahora es un sinc de banda limitada
  (`dsp/interpolation.py`), con 16 muestras de latencia fija igual para todos.
- **La entrada:** se captura en f32, y la lectura guarda los bytes de un marco cortado (antes
  podía cambiar L por R).
- **Tarjeta Entrada** (`dsp/input_analysis.py`): tipo, correlación, lateral/central, balance,
  ancho de banda, saturación y el formato de cada aplicación.
- **Micrófono elegible** en la calibración (`microphone_set`): se guarda en `service.json` y
  el lazo sigue al nuevo. Su nivel aparece en Niveles.
- **Lo que salió mal:** la interpolación lineal estaba desde el 2026-09-29, y su docstring
  hablaba de exactitud del retardo pero no de su respuesta en frecuencia. Ningún test miraba
  los agudos.
- **Pendiente:** comparar con micrófono la salida directa contra el motor, al mismo volumen.
- `scripts/check.sh` pasa con 362 tests; los de navegador, 52 de 52.

**Parte 8: métricas en vivo por stream** (i-7c8794-530882, d-7c8794-316465, spec §17).
- `GET /v1/stream` (Server-Sent Events) manda:
  - `state` cuando cambia algo y al menos cada segundo;
  - los niveles a 20 Hz;
  - el espectro y la correlación de la entrada a 10 Hz;
  - los logs.
- **Los niveles van sincronizados con lo que se oye** (`telemetry.py`): cada trozo de 1024
  muestras se muestra a su hora de escritura más la latencia medida. El del micrófono no se
  retrasa.
- **Panel:**
  - "En vivo" o "Consultando";
  - medidores dibujados a la tasa de pantalla, con RMS integrado en 300 ms y pico retenido en
    el navegador;
  - la reducción del limitador por parlante;
  - el espectro de la entrada en tercios y un medidor de correlación;
  - los deslizadores mandan cada 80 ms (antes, 200).
- **Lo que salió mal:** la primera corrida de los tests de navegador se cortó porque el equipo
  se reinició. Al retomar no había procesos colgados.
- **Pendiente:** comparar con parlantes el pico del medidor contra el del micrófono.
- `scripts/check.sh` pasa con 372 tests; los de navegador, 56 de 56.

**Parte 9: los cortes, el panel reorganizado y el paso 1 de la sonda** (i-7c8794-7d4aec,
d-7c8794-560b54, experimentos/10 §9 y 11).
- **Cortes** (los parlantes estaban apagados, así que se investigó sin sonido):
  - el observador abría ~3 `bluetoothctl` por segundo, cada uno con un monitor de anuncios
    LE (MEDIDO en el journal: 14 498 en 80 min). Ahora lee BlueZ por D-Bus;
  - el chequeo de ruteo corre en un hilo, y la tubería guarda dos bloques de margen;
  - **se probó pasar la medición del lazo a otro proceso y se revirtió:** frenaba más al motor
    (hasta 15 ms contra 1 a 4 ms con el hilo). Además, sin guarda `__main__`, tiró abajo la
    sesión. Quedó una protección para que una medición que no arranca no corte el audio;
  - cada corte queda registrado con su causa probable (`cuts.py`) y se ve en Diagnóstico →
    Cortes.
- **Panel:**
  - la pantalla Escuchar tiene la tarjeta Ahora: estado, efectos con su explicación,
    «Calibrar y aplicar» e «Identificar parlantes»;
  - Parlantes empieza por los dispositivos, agrupados, con batería y «Olvidar»;
  - Diagnóstico tiene tres zonas;
  - cada ajuste tiene su descripción y un (?) con el detalle.
- **Sonda enmascarada, paso 1 (SIMULADO,** `probes/13-sonda-enmascarada/simular.py`): ver
  experimentos/11.
- **Lo que salió mal:**
  - el `<body>` con `data-layout` hacía que cada clic mandara `layout: "pestanas"`;
  - el log del servicio, dentro del repositorio, guardaba el token y su QR (nunca entró en
    git; censurado, y el servicio ya no lo imprime a un archivo);
  - un `pkill -f` con el patrón en su propia línea se mató a sí mismo otra vez. Usar `ps`
    más `grep "[x]"`, o matar por PID;
  - la primera simulación daba a Black y Blue la misma mezcla. Ahora las mezclas salen del
    motor real.
- **Pendiente:**
  - con parlantes: 20 minutos de música mirando Cortes, y el escaneo LE a propósito;
  - el A/B ciego de inaudibilidad de la sonda;
  - el paso 2 de la sonda (construirla en el motor).

**Cierre (2026-10-02).** La sesión cierra con los commits que pidió el usuario, uno por tema.
- **Lo que se versiona:** el texto de `datos/10` y `datos/11` (json, jsonl, log y txt,
  ~21 MB).
- **Lo que queda solo en `PC-Ryzen5`:** las grabaciones `.wav` y las calibraciones `.npz` de
  `datos/10` (~800 MB), excluidas por `.gitignore`. Nunca se versionó audio crudo en este
  repositorio.
- **No verificado:**
  - nada de lo de las partes 6 a 9 se oyó con los parlantes: estaban apagados desde el
    reinicio del equipo;
  - que el escaneo LE cortara el A2DP;
  - que la sonda no se oiga;
  - el pico del medidor contra el del micrófono.
- **Medido:** ver experimentos/10 §7-9 y experimentos/11.

---

## 2026-09-29 · s-7c8794-48c25e — Actualizar el paquete de guías de 0.0.24 a 0.0.25

**Qué.** El paquete de `.agents/` pasó de 0.0.24 a 0.0.25, copiado por el repositorio de
origen en una sesión de sincronización. La versión nueva dice que los procedimientos propios
de un repositorio ganan sobre los del método, y pide que el archivo raíz lo diga.
- **`CLAUDE.md` tiene una fila nueva** en la tabla de documentos, junto a las del
  conocimiento y la privacidad: un procedimiento de `.agents/method/` nunca manda sobre los
  de este repositorio; donde nombra un archivo, un formato, un paso o una regla de commits
  que aquí se define distinto, gana lo de aquí, y `carrier.toml` `adapted` anota la
  equivalencia. Ningún procedimiento propio cambió.
- **`docs/roadmap.md` §*Cómo mantenerlo*** anota la forma de los títulos
  (`### <Idea> · i-7c8794-…`, id después del punto medio), que ya se usaba y solo estaba
  escrita en `adapted`.
- **El revisor** se volvió a copiar de `.agents/agents/`; en 0.0.25 no cambió, así que la
  copia quedó igual.
- Ninguna propuesta pendiente: no había nada que podar.

**Archivos.** `.agents/` (la versión nueva), `CLAUDE.md`, `docs/roadmap.md`, este registro.

**Por qué.** La sincronización de 0.0.25 alcanzó a este repositorio, y su paso 3b de la
actualización pide la línea nueva del archivo raíz.

**Arquitectura.** ✅ Cumple. No toca código del host. `.agents/` no se editó a mano. Solo se
agregó texto que apunta y ordena precedencias.

**Qué salió mal en el camino.** Nada.

**Qué quedó pendiente.** Nada nuevo.

**No verificado.** Que el revisor funcione en una revisión real: no se pidió ninguna.

**Medido.** `bundle.py verify` pasa (0.0.25 verificado) y `PY=python3.14 scripts/check.sh`
pasa (60 ids definidos, 0 errores; 167 tests).

---

## 2026-09-29 · s-7c8794-e67950 — Actualizar el paquete de guías de 0.0.22 a 0.0.24

**Qué.** El paquete de `.agents/` pasó de 0.0.22 a 0.0.24, copiado por el repositorio de
origen en una sesión de sincronización.
- **El outbox se volvió propuestas.** Las tablas `.agents/tracking/candidates.md` y
  `.agents/tracking/experiments.md` ya no existen: lo que este repositorio aprende para el
  paquete sale como un archivo por propuesta en `.agents/proposals/`, escrito con
  `bundle.py propose` y nunca editado. Las tres filas que había (tres candidatos, ningún
  experimento) se convirtieron con `bundle.py proposals --from-outbox`, el origen las
  recibió en 0.0.24 (una sumada como otra ocurrencia de una nota, dos en cola) y se
  quitaron con `bundle.py proposals --prune`. Hoy no queda ninguna.
- **`upstream` en `carrier.toml`** pasó de vacío (que quería decir "este es el origen") al
  id de carrier del origen.
- **Lo que 0.0.23 pedía y esta copia no había hecho:** el subagente revisor quedó en
  `.claude/agents/knowledge-reviewer.md` (copia de `.agents/agents/`), y la fila de
  `CLAUDE.md` para cambios que tocan estado, un contrato, datos, seguridad o verificación
  ahora dice cómo usar las tarjetas nuevas (`knowledge/cards/`, desde `INDEX.md`) y cuándo
  llamar al revisor: solo si el usuario lo pide, y ofrecerlo en una línea si el cambio
  borra o reescribe datos guardados, mueve dinero o toca autenticación.
- `CLAUDE.md` §*Archivos que no se editan a mano* nombraba `tracking/`; ahora nombra las
  propuestas y la copia del revisor.

**Archivos.** `.agents/` (la versión nueva), `.claude/agents/knowledge-reviewer.md` (nuevo),
`CLAUDE.md`, este registro.

**Por qué.** La sincronización de 0.0.24 alcanzó a este repositorio; la actualización a
0.0.23 no había pasado por aquí, y su adaptación (el revisor y la fila del conocimiento)
quedó para esta.

**Arquitectura.** ✅ Cumple. No toca código del host. `.agents/` no se editó a mano.

**Qué salió mal en el camino.** Nada.

**Qué quedó pendiente.** Nada nuevo.

**No verificado.** Que el revisor funcione en una revisión real: no se pidió ninguna.

**Medido.** `bundle.py verify` pasa (0.0.24 verificado) y `PY=python3.14 scripts/check.sh`
pasa (167 tests).

---

## 2026-09-29 · s-7c8794-5439d5 — Inventario del portátil HP-O16 y diseño del servicio de control

**Qué.** Dos partes, las dos sin tocar audio ni Bluetooth (el usuario pidió no hacer pruebas
en este equipo).
- **El inventario de un tercer equipo**, el portátil `HP-O16` (HP OMEN 16, i5-11400H,
  CachyOS 7.2.8), con una tabla que lo compara con el Mac y `PC-Ryzen5`. Tiene el **mismo
  Intel AX210 con el mismo firmware** (SHA1 `0x2925677d`) y las mismas versiones de BlueZ,
  PipeWire y WirePlumber. `btmgmt info`, leído sin root, no tiene `iso-broadcaster` ni
  `sync-receiver`. `scripts/check.sh` pasa (167 tests). El probe del inventario ahora sirve
  en cualquier equipo Linux: lee `btmgmt info` sin sudo, lista `rfkill` y encuentra la
  tarjeta Wi-Fi sin direcciones PCI fijas.
- **El diseño del servicio de control** (i-7c8794-bdb678), con la spec escrita y revisada,
  **sin código**. Un programa persistente (`aurasync service`), que se inicia y se apaga a
  mano y sigue vivo aunque falle una sesión de audio. Su control es un contrato JSON
  independiente del transporte (`control.py`): REST ahora y serie en la Fase 3. Audio a
  pedido, token en la red local, presets guardados a pedido, un solo escritor del motor.
  Dos decisiones nuevas: d-7c8794-74b639 (el servicio y el contrato) y d-7c8794-7b3093
  (**el código nuevo va en inglés**). Tres entradas nuevas en el roadmap: i-7c8794-a9f161
  (transporte serie), i-7c8794-f30928 (migrar el código existente al inglés) e
  i-7c8794-f3ddf8 (A/B ciego e interfaz).

**Archivos.** `docs/research/experimentos/00-inventario-hp-o16.md` y
`datos/00/hp-o16-inventario.txt` (nuevos), `docs/research/experimentos/00-inventario-linux.md`
(una corrección), `probes/00-inventario-linux/inventario.sh`,
`docs/superpowers/specs/2026-09-29-control-service-design.md` (nuevo), `docs/decisions.md`,
`docs/roadmap.md`, `CLAUDE.md`.

**Por qué.** El usuario retomó desde un tercer equipo, pidió su inventario junto al de los
otros dos, y después seguir con el roadmap. Lo próximo era i-7c8794-bdb678. El usuario
cambió su forma durante el diseño: de un servidor dentro de `run` a un programa persistente,
con un contrato que pueda viajar por serie a la Raspberry de la Fase 3, y con la interfaz
decidida más adelante.

**Arquitectura.** ✅ Cumple. El contrato (`control.py`) y las rampas (`dsp/ramps.py`) no
hacen E/S, que es la regla de `docs/research/08` §6.1. La decisión se tomó con el usuario,
parte por parte (arquitectura, contrato, motor, errores y tests), antes de escribir la spec.

**Qué salió mal en el camino.**
- El primer `btmgmt info` colgó el comando 2 minutos: después de imprimir se queda en modo
  interactivo. Va con `timeout 5`.
- Propuse primero el servidor **dentro de `run`** (la opción A), siguiendo lo que decía el
  roadmap. El usuario quería otra cosa: un programa persistente con un contrato portable.
  Después interpreté "persistente" como un servicio de systemd, y tampoco era eso.
- **Dos afirmaciones de la spec que no tenían respaldo, corregidas en la revisión:** que el
  costo de CPU del motor "cabe de sobra en un núcleo" (no está medido; solo el de la
  calibración, en `experimentos/08`) y que `run` "detecta" al servicio, sin decir cómo.

**Qué quedó pendiente.**
- **Que el usuario revise la spec**, y después el plan de implementación (`writing-plans`).
  No hay código escrito.
- **El micrófono sigue fijo en el código** (`MICROFONO_POR_DEFECTO` en `cli.py`, el fifine de
  `PC-Ryzen5`). La spec lo resuelve (§4.3), pero hasta implementarla, en cualquier otro equipo
  hay que pasar `--microfono`.
- **`doctor` no avisa** de lo que impediría una prueba en `HP-O16`: Bluetooth bloqueado por
  `rfkill` y micrófono por defecto silenciado.
- **El sink por defecto guardado en WirePlumber de `HP-O16` son unos Sony WH-CH520.** Es el
  mecanismo de `experimentos/09`: no tenerlos encendidos durante una prueba en ese equipo.
- Ningún JBL está emparejado con `HP-O16`, y su Bluetooth está bloqueado por `rfkill`
  (no se tocó).
- **Medir el costo en tiempo real del motor**, el primer paso de la implementación (spec §12).

**Desvío del plan.** El roadmap describía un servidor dentro de `run`. La spec lo reemplaza
por un programa persistente, a pedido del usuario; la entrada del roadmap conserva la versión
anterior debajo, marcada como tal.

**No verificado.** Que A2DP con 3 Go 4 se comporte en `HP-O16` como en `PC-Ryzen5`
(INFERIDO por chip y firmware idénticos). Los bits de LE Features de `HP-O16` (necesitan
`btmon` con root, y ahí no hay sudo). Que la tarjeta de `HP-O16` sea un M.2 reemplazable. Todo
el diseño del servicio es papel: nada se probó.

**Medido.** Firmware BT 202-5.26 con SHA1 idéntico al de `PC-Ryzen5`; `supported settings`
sin `iso-broadcaster`; 167 tests en 3,01 s; Wi-Fi de `HP-O16` en 5 GHz, canal 153.

## 2026-09-29 · s-7c8794-1d3f53 — Del lazo de recalibración a la primera escucha con parlantes: dos errores del estimador y uno del sistema de audio

**Qué.**
- **P1 cerrado del lado de Linux** (i-7c8794-fd5f03): el sink virtual de `aurasync run`
  desaparece solo tras un `kill -9`, sin tocar la lista de salidas ni los 5 archivos de
  estado de WirePlumber. MEDIDO, en `experimentos/07`. No se reprodujo audio: solo se creó
  el nodo y se lo mató.
- **`dsp/retardo.py`**: retardo fraccionario por parlante que se puede cambiar **mientras
  suena**, con rampa de velocidad limitada a 0,5 ms/s (0,05 % de cambio de tono, unos 0,9
  centésimos de semitono). 12 tests; el que justifica el módulo compara el mayor salto
  entre muestras contra el de la propia señal, y contra el salto instantáneo.
- **`motor.py` usa esas líneas** y gana `actualizar()`, `retardos_actuales_ms()` y rampa de
  ganancia en dB/s. Cambiar retardo o nivel ya no exige reiniciar la reproducción.
- **`sincronia.py`, el lazo cerrado**: recibe una calibración, decide si vale y la escribe.
  Cinco filtros, cada uno contra un modo de falla del estimador (estabilidad, zona muerta de
  0,5 ms, salto máximo, confirmación, ganancia de lazo 0,5), más `deriva_ms_h`, que estima
  la deriva a partir de lo que el lazo tuvo que corregir. No mide ni reproduce: 23 tests sin
  parlantes.
- **`experimentos/08` y `probes/lazo-simulado/simular.py`**, con la semilla reiniciada por
  prueba para que los números se repitan.
- **El lazo quedó conectado a `aurasync run --recalibrar`** (segunda mitad de la sesión, a
  pedido del usuario: *corregí los pendientes para realizar una prueba de audio*). Lo que
  faltaba: `sonido.MicrofonoContinuo` (grabación que no termina, en un anillo en memoria),
  `sincronia.VentanaDeEmision` (lo emitido, que es la referencia) y
  `sincronia.MedicionEnSegundoPlano`. Nuevas opciones de `run`: `--recalibrar`, `--microfono`,
  `--cada`, `--medir`, `--registro` (JSON Lines), `--guardar` y `--volumen-db`. **Apagado por
  defecto.**
- **`doctor` ahora lista los micrófonos** y avisa si el de por defecto no está: es el
  instrumento de `calibrate` y del lazo, y el nombre del nodo es largo y fácil de equivocar.
- **El protocolo de la prueba con parlantes**, en `experimentos/08`: precondiciones, los
  comandos, qué significa cada clase de línea del registro y los tres modos de falla
  previsibles con su diagnóstico.

**Y después, la prueba de audio de verdad** (tercera parte de la sesión, con consentimiento
explícito del usuario y a volumen bajo: amplitud 0,1–0,2 y `run --volumen-db -12`). Está todo
en `experimentos/09-primera-escucha-con-3-go-4.md`, con el registro crudo en
`experimentos/datos/09-lazo-primera-sesion.jsonl`.
- **El sistema suena de punta a punta:** 3 Go 4, un canal distinto en cada uno, audio del
  sistema entrando por un dispositivo virtual. Eso queda demostrado.
- **El efecto se percibe pero más débil de lo esperado**, y la escucha fue a volumen muy bajo,
  que perjudica selectivamente la energía lateral tardía —o sea el mecanismo del
  envolvimiento—. No concluyente.
- **Arreglado el ruteo de streams** (`sonido.destinos_reales`, `mal_ruteados`,
  `reparar_ruteo`), el reparto por defecto de `init` (ningún parlante en ambiente puro), el
  falso *"no suena"* de `calibrate` y `--guardar` bajo SIGTERM.
- **`doctor` lista los micrófonos.** Nuevas opciones de `run`: `--volumen-db` entre ellas.

**Archivos.** `host/src/aurasync/{sincronia.py,motor.py,medicion.py,sonido.py,cli.py}`,
`host/src/aurasync/dsp/retardo.py`,
`host/tests/{test_sincronia.py,test_retardo.py,test_motor.py,test_medicion.py,test_sonido.py,test_cli.py}`,
`docs/research/experimentos/{07-…,08-…}.md`, `probes/{p1-huella,lazo-simulado}/`,
`docs/roadmap.md`, `docs/research/08-integracion-y-plan.md`, `host/README.md`, `CLAUDE.md`.

**Por qué.** El usuario pidió avanzar en todo lo que se pudiera **sin probar audio**
(instrucción vigente: avisar y consultar antes de volver a pruebas de audio). El lazo de
recalibración era el hueco que sostenía el diseño: si la alineación se mueve durante la
sesión, hay que corregirla sin cortar.

**Corrección a lo que esta misma entrada decía antes:** se justificó el lazo diciendo que
*"E6 midió que cada arranque de A2DP trae un desfase distinto"*. **E6 mide lo contrario** —con
2 o 3 Go 4, repetible a 0,1 ms entre reproducciones, *"sin necesidad de recalibrar en cada
arranque"*—. La variación de 4,5 ms en un canal sale de `experimentos/06`, que la deja como
pregunta abierta, y **se midió con el error de retardos negativos arreglado hoy**, cuya firma
coincide exactamente. O sea que puede no existir. Queda como lo primero que tiene que resolver
la prueba de audio: dos `calibrate` seguidos y comparar.

**Arquitectura.** ✅ Cumple. `sincronia.py` y `dsp/retardo.py` no hacen E/S y se prueban con
arrays, que es la regla de `docs/research/08` §6.1. La lista cerrada de archivos de
`host/src/aurasync/` ya no existe (se quitó con d-7c8794-9afee2); se corrigieron los dos
lugares que todavía decían lo contrario.

**Qué salió mal en el camino.**
- **Un error real en `gcc_phat`, y es el hallazgo de la sesión.** Buscaba el pico solo entre
  retardos no negativos, con la premisa de que el micrófono no capta antes de que se emita.
  Cierto contra la referencia cruda; **falso** contra la referencia ya corrida por el desfase
  grueso, que es la *mediana* entre parlantes: el que llega antes que la mediana tiene
  residuo negativo por construcción. Con tres parlantes a 0, 3,4 y 7,1 ms el error era de
  **7,10 ms con reverberación y 43,82 ms sin ella**. Lo detectó la simulación, no una
  medición: como ahí el retardo verdadero se conoce, se puede ver el error.
- **Y el filtro de validez no lo cazaba:** informaba estabilidad de **0,01 ms** y
  `confiable=True`. Los tres tamaños de ventana se equivocaban igual. Es exactamente el modo
  de falla que el docstring de `calibrar` ya advertía, ahora con un caso concreto.
- Primer intento de medir con el contenido: concluí "no funciona" viendo errores de 3 a
  16 ms. Era el error de arriba contaminando todo. El caso de control —ruido independiente,
  la configuración que E6 validó— también fallaba, y **eso** fue lo que acusó al código en
  vez de a la idea.
- Un diagnóstico intermedio usó `np.correlate(..., "full")` sobre 20 s: O(n²), no terminó en
  10 minutos. Se rehízo por FFT.
- **La primera versión de las ventanas del lazo estaba mal y no habría dado ningún error.**
  Había elegido tomar del micrófono `medir + 2 s` con un margen de 0,5 s, lo que deja la
  referencia a ~1,8 s del inicio de la grabación; `alineacion_gruesa` busca solo hasta
  **1500 ms**, así que el lazo no habría alineado nunca y en una prueba con parlantes se
  habría visto como *"el lazo no hace nada"*. Se corrigió a margen de 1,0 s y tajada de
  `medir + 1,5 s`, y se agregó un test de punta a punta con micrófono simulado. Comprobado
  después: con 900 ms de latencia de reproducción alinea, con 1600 ms no.
- Primer intento de medir el costo de `calibrar` con `hatch run python -c`: hatch interpreta
  las llaves del código como campos de plantilla (*Unknown context field `n`*). Se pasó a un
  archivo.
- **El error que más costó, y que ningún test podía encontrar:** un parlante no sonaba porque
  su stream de `pw-play` entraba al **propio sink virtual de `aurasync`**, cerrando un lazo de
  realimentación. WirePlumber tenía guardado `default.configured.audio.sink=aurasync` de una
  vez que el usuario lo eligió, así que cada aparición del sink lo vuelve el default y
  **mueve** el stream que apuntaba al default anterior, *con su `target.object` puesto*. No
  hay error, ni log, ni excepción: el parlante se calla. **Lo detectó el usuario escuchando.**
- **Y dos intentos fallidos de arreglarlo, los dos por apurarme a escribir antes de mirar:**
  (1) la comprobación corría **antes** de crear el sink virtual, cuando el desvío ocurre justo
  al crearlo, así que no veía nada; (2) la detección buscaba el `pid` en el objeto Node cuando
  está en el **Client**, así que informó *"ningún destino"* para los tres parlantes — una
  ausencia falsa producida por la comprobación hecha para detectar ausencias falsas. Recién el
  tercer intento, después de volcar `pw-dump` y mirar los objetos de verdad, funcionó.
- **Mi hipótesis del turno anterior era equivocada y lo dije como si fuera probable.** Había
  propuesto que la variación entre arranques de `experimentos/06` era el error de retardos
  negativos. Tres `calibrate` seguidos la refutaron: 15 ms de variación con el error ya
  arreglado.

**Qué quedó pendiente.** En el orden en que conviene tomarlo:
1. **Controles en vivo (i-7c8794-bdb678), que pasó a ser lo próximo.** El usuario pidió un
   **servidor con interfaz** para mover `ambiente`, `pan` y ganancias mientras suena, y poder
   ajustar desde el teléfono caminando por la pieza. La mitad difícil ya está
   (`motor.actualizar()` y la rampa de `dsp/retardo.py`); falta exponerla. Sin esto no se puede
   iterar sobre la experiencia: cada prueba cuesta un reinicio.
2. **Repetir la escucha a nivel normal**, con `ambiente` más alto. La única que hay se hizo a
   volumen muy bajo y eso ataca justo el mecanismo del efecto.
3. **Entender por qué el lazo no converge en el parlante de `ambiente` alto.** Es el que más
   aporta al envolvimiento y el que peor se mide: la idea de que *la condición del efecto es la
   condición de la medición* se rompe cuando el ambiente sube.
4. **Restar el camino acústico de la calibración** (i-7c8794-1ab281). Hoy alinea en el punto del
   micrófono y desalinea el resto de la pieza, que es lo contrario del objetivo. Necesita
   coordenadas (i-7c8794-26c302).
5. **Calibrar sin micrófono central (i-7c8794-4745b4)**, entrada nueva. El primer paso cuesta
   un minuto y decide el resto: `pw-dump | grep -E "node.name|latency|delay"` con los parlantes
   conectados. **Queda sin hacer porque los parlantes se apagaron**; era lo último que intenté.
   El AVDTP Delay Report ya está descartado (MEDIDO en E6).
6. **La cuadrícula de coordenadas y el preset LF/RF/RR/LR** (dentro de i-7c8794-26c302). Ojo con
   un choque medido: el preset son **cuatro** parlantes y E6 midió que con cuatro streams A2DP
   el enlace se desestabiliza. Hay que decidir la salida antes de diseñarlo.
7. **Portabilidad a macOS (i-7c8794-a848a0)**, entrada nueva. El núcleo debería pasar sus tests
   tal cual; `sonido.py` hay que rehacerlo entero, y el sink virtual sin huella puede no tener
   equivalente.
8. **La deriva sigue sin medir.** `run --recalibrar` la estima al cerrar, pero es INFERIDO, y en
   esta sesión no llegó a imprimirse por el defecto de SIGTERM.
9. Mitad de P1 en Mac; DBAP (i-7c8794-26c302); E9.

**Las ideas que el usuario dejó anotadas al cerrar (2026-09-29), todas en el roadmap.** No se
escribió código para ninguna: el desarrollo empieza mañana. Lo que la sesión sí hizo fue
anotarlas con **el choque que cada una tiene**, que es lo que se pierde si solo se anota la
idea: el preset de cuatro canales contra el techo de tres streams de A2DP; el origen de la
cuadrícula en la pieza y no en la persona; la tensión entre las etiquetas LF/RF/RR/LR y el
argumento de `config.py` contra ellas; y que el camino sin micrófono tiene su mecanismo obvio
—el delay report— ya descartado por medición.

**Desvío del plan.** Ninguno. El roadmap ya tenía i-7c8794-33c4bd con la recalibración
continua; esta sesión la construye y matiza una afirmación suya (ver abajo).

**No verificado.** Lo de `experimentos/08` sigue siendo **simulación** y no lo reemplaza la
sesión con parlantes, que fue corta y a volumen muy bajo. Y queda sin verificar lo que más
importa: **si el efecto envolvente funciona**, porque la única escucha se hizo en condiciones
que lo perjudican. Tampoco se verificó si mover el retardo en caliente se oye: el lazo aplicó
dos cambios y nadie estaba prestando atención a eso en ese momento. Sobre la simulación: la sala simulada no tiene la respuesta de un Go 4, ni su
compresión, ni el ruido real de la pieza. Los números son cota optimista. En particular, que
mover el retardo mientras suena **no se oiga** está acotado por diseño y comprobado como
continuidad de la forma de onda, pero *inaudible* solo se comprueba escuchando.

**Medido.**
- `gcc_phat` con el mínimo en cero vs. en −120 ms, tres parlantes a 0 / 3,4 / 7,1 ms:
  **43,82 → 0,01 ms** de error sin reverb, **7,10 → 0,01 ms** con reverb y ruido.
- `calibrar` completo antes del arreglo: error 7,10 ms, **estabilidad 0,01 ms,
  `confiable=True`**.
- Contenido como referencia: a **10 s** el error es 0,01 ms; a **4 s**, dos de cada tres
  mediciones que pasan el filtro están mal por más de 1 ms (la peor, 8,05 ms, informando
  estabilidad 0,32). La discrepancia entre segmentos confiables consecutivos es 0,01 ms a
  10 s y de 1,30 a 8,05 ms a 4 s: **el error no se repite y la confirmación lo filtra**.
- La correlación entre referencias **no** predice el acierto: 0,85 falló y 0,94 acertó.
- `medicion.calibrar` sobre 10 s y 3 parlantes: **1,00 s de CPU** en `PC-Ryzen5`. Es el
  número que obliga a medir en un hilo aparte.
- Las ventanas del lazo toleran hasta **~1 s** de latencia de reproducción: a 250, 500 y
  900 ms alinea con 0,01 ms de error; a 1600 ms no alinea.
- **Tres `calibrate` seguidos sin tocar nada: 15 ms de variación** (Black − Red: +8,57 · +4,72 ·
  −6,46 ms), con estabilidad informada de 0,00 ms en las tres.
- **El lazo con parlantes, 644 s:** 2 ajustes aplicados, 13 descartados por inestabilidad, 7 a
  la espera de confirmación, 5 sin alinear. Las propuestas rechazadas para Blue fueron +6,7 ·
  +11,3 · +6,6 · +4,0 · +4,8 ms.
- `scripts/check.sh` pasa con `PY=python3`, **167 tests**.

**Matiz a lo que ya estaba escrito.** El roadmap decía *"con música anda igual de bien que
con ruido"*. Anda igual de bien **con segmentos de 10 s**; con 4 s no, y el filtro de validez
no lo dice. Anotado en la entrada y en `experimentos/08` §2.

---

## 2026-09-28 · s-7c8794-21e1f2 — Del inventario del AX210 al MVP: E1 cerrado, A2DP medido y el núcleo de aurasync construido

**Qué.**
- **Inventario del equipo Linux** (i-7c8794-d9c834), la mitad que faltaba: el chip es
  un **Intel AX210** con firmware BT `202-5.26`, HCI 5.4, advertising extendido con
  2M. BlueZ 5.87, PipeWire **1.6.9** con `libspa-codec-bluez5-lc3.so`, WirePlumber
  0.5.17, liblc3 1.1.3, kernel 7.2.7-1-cachyos.
- **Hallazgo nuevo, y el más importante de la sesión:** los cuatro JBL ya estaban
  emparejados con este equipo, y `bluetoothctl info` muestra que **los parlantes (2
  Go 4 y el Charge 6) no exponen ni PACS (0x1850) ni BASS (0x184F)**, mientras que
  los **Tune 770NC sí exponen el stack LE Audio completo** (PACS, ASCS, VCS, MICS,
  CAS, TMAS). Si se confirma, **las dos vías del estándar para asignar un canal por
  parlante están cerradas**. Queda en `experimentos/02`.
- **`scripts/check.sh` corre en Linux** con `PY=python3.14`: cierra el pendiente de
  i-7c8794-f7f5b2.
- **E1 cerrado, y la respuesta es NO** (i-7c8794-3f730a,
  `experimentos/03-e1-iso-en-el-ax210.md`). El AX210 con firmware `202-5.26` tiene
  LE Features `ff 59 01 3c ae 00 00 00`: **le faltan los bits 30 (Isochronous
  Broadcaster), 31 (Synchronized Receiver) y 13 (LE Periodic Advertising)**. Medido
  por dos caminos independientes: debugfs y una traza de `btmon` del arranque del
  controlador, cuya lista de Supported Commands tiene **solo** comandos CIS. Sí tiene
  CIS central y peripheral, y el socket ISO del kernel funciona con la bandera.
- **Consecuencia que empeora el plan:** sin los bits 13 y 31, este adaptador tampoco
  puede *escuchar* un BIS, así que **E2 tampoco se puede hacer acá**. Se creía que al
  menos podría leer la BASE.
- **Consecuencia que lo mejora:** se agregó **E8** (i-7c8794-ef8389), unicast LE
  Audio contra los Tune 770NC. Sale de cruzar dos mediciones de esta sesión (hay CIS
  central, y los Tune exponen PACS y ASCS) y valida el camino ISO completo de Linux
  sin depender de transmitir. Es el único experimento LE Audio que este equipo puede
  hacer hoy.
- **E8 corrido en el mismo momento en que se definió, y salió bien**
  (`experimentos/04-e8-unicast-le-audio-tune-770nc.md`). Al activar el socket ISO,
  los Tune 770NC se reconectaron solos en perfil **`bap-duplex` con LC3**. Un tono de
  4 s estableció un **CIG con 2 CIS** (uno por canal), ISO interval 7,5 ms, PHY LE
  2M, SDU 60 B, CIG Synchronization Delay 4632 µs, 2214 paquetes `LE-CIS`.
  **El camino ISO de Linux funciona de punta a punta en este equipo.**
- **Hallazgo de arquitectura que mueve una disputa del proyecto:** `pw-dump` muestra
  que PipeWire crea **un nodo interno por stream isócrono**, los agrupa en un *device
  set* de BlueZ y expone un **combine-sink** como único sink estéreo. `research/02`
  §PipeWire tenía esto "en disputa, se resuelve en E5": ahora está medido para
  unicast. Con `bis[]` sigue INFERIDO.
- **Aviso para E3 y E5:** con los Tune se negociaron **32 kHz y 7,5 ms**, no los
  `48_2_x` que asume `research/02` §4. La negociación la manda el receptor.
- **`experimentos/02` cerrado casi del todo, con los parlantes encendidos y
  conectados.** `busctl tree org.bluez` muestra que el Go 4 y el Charge 6 **no tienen
  ni un objeto GATT** (solo `sep`/`avrcp` de A2DP), mientras el Tune 770NC tiene
  `pac_sink0`, `pac_source0` y varios `service00XX`. En un `scan le`, el Tune anuncia
  ServiceData de ASCS, TMAS y CAS y declara PACS y **CSIS**; los tres parlantes no
  anuncian nada. Los dos parlantes se conectaron por **BR/EDR**, así que falta el único
  caso que queda: un Go 4 **en modo Auracast**.
- **E6 a medias, con instrumento propio validado**
  (`experimentos/05-e6-a2dp-un-canal-por-parlante.md`). El **AX210 sostiene los dos
  parlantes a la vez**, así que se descarta el segundo dongle que el roadmap temía.
  `combine-stream` reparte un canal a cada parlante y se oye por el correcto.
  `probes/e6-a2dp/medir-desfase.py` mide el desfase con el **fifine USB**, y
  `test-medir-desfase.py` lo valida contra desfases conocidos.
- **E6, tanda definitiva con los 4 parlantes en standalone y todos en SBC.** Lo que
  domina es **cuántos streams suenan a la vez**: con **2 Go 4** el desfase es
  −0,34/+0,11/+0,26 ms (MAD 0,12–0,35, variación entre reproducciones **0,6 ms**); con
  **3 Go 4**, Red −2,7 y Blue +1,8 ms, **repetible a 0,1 ms**; con **4** se cae (MAD 2–30
  ms, rangos hasta 112 ms, ráfagas perdidas), **con cualquier códec**. **Techo del camino
  A2DP: tres parlantes.**
- **Códecs distintos cuestan 45–150 ms.** Se fuerza SBC con `bluez5.codecs = [ sbc ]` en
  `~/.config/wireplumber/wireplumber.conf.d/`. **En PipeWire no tiene efecto**, se probó.
- **A2DP no da nada para compensar:** cero `AVDTP Delay Report` en la traza, latencia 0
  en los nodos. La calibración con micrófono es el único mecanismo.
- **Esto corrige el veredicto anterior de E6**, que decía que A2DP para estéreo solo
  servía con calibración en cada arranque. Los 12,9 ms que lo sustentaban eran casi todo
  diferencia de códec: con el mismo códec y 2–3 parlantes iguales, la variación entre
  reproducciones baja a 0,1–0,6 ms.
- **E6 medido antes con el micrófono ubicado por el usuario (tanda descartada):** 4 reproducciones, Go 4 Black
  (SBC) contra Charge 6 (AAC). Medianas **+60,23 / +47,39 / +50,80 / +60,25 ms**, MAD
  2–4 ms. **La mediana se corre 12,9 ms entre reproducciones**, y eso no lo explica la
  distancia al micrófono, que no cambió. **Conclusión: A2DP para estéreo solo sirve con
  calibración en cada arranque**; para traseros o modo fiesta alcanza.
- **Dos factores nuevos que el plan no había previsto:**
  1. los parlantes negocian **códecs distintos** (SBC y AAC) y forzar el mismo **en
     caliente falla** (`endpoint /MediaEndpoint/A2DPSource/sbc in use`). Dos
     dispositivos **sí** comparten un códec si lo negocian al conectarse (Tune y
     Charge 6 los dos en AAC);
  2. **el par estéreo de JBL bloquea el camino A2DP igual que el Auracast:** con Blue y
     Red emparejados entre sí, el Red no llega a tener tarjeta en PipeWire aunque BlueZ
     lo reporte conectado.
- **Investigación nueva, pedida por el usuario:**
  `research/09-efecto-ambiental-y-diseno-de-la-experiencia.md`. Qué es el efecto que
  busca (**listener envelopment**, distinto de *apparent source width*), que viene de
  **energía lateral tardía decorrelacionada** y no de la cantidad de canales; la
  **decorrelación** como herramienta central (Kendall), que además **debilita el efecto de
  precedencia** y por eso vuelve al sistema tolerante al desfase; las zonas del **efecto
  de precedencia y de Haas**, que reencuadran el desfase como parámetro de diseño; **DBAP**
  para oyente móvil en arreglo irregular; **extracción de ambiente por coherencia**
  (Avendaño–Jot) en vez del `psd` de PipeWire; y los ***loudspeaker orchestras***
  (Acousmonium, BEAST), que son la tradición de 40 años del setup que se quiere armar.
- **Se leyeron dos papers completos** (paso G, que el usuario priorizó primero), y de ahí
  salió `research/09` §11, que es **VERIFICADO** y trae los parámetros para implementar:
  - **Potard y Burnett (DAFx'04):** decorrelación por **todo-paso de fase aleatoria**,
    ~**100 polos y ceros**, **máximo 5–6 señales** totalmente decorrelacionadas con filtros
    fijos, y fases elegidas por ortogonalidad máxima. **Y la advertencia que corrige este
    repositorio: en parlantes hay que evitar decorrelar con retardo, por filtrado peine.**
  - **Avendaño y Jot (AES 22nd):** índice de ambiente = 1 − coherencia, mapeo por
    **tangente hiperbólica** con umbral, rango y pendiente (μ₁ = 1, σ = 2 u 8), el criterio
    extra de energías comparables entre L y R, y la cadena del surround
    **todo-paso → retardo de 5 a 20 ms**. Más dos cosas de método: **no hay que juzgar un
    canal aislado** (los artefactos se enmascaran entre sí) y **la extracción de ambiente
    aguanta cualquier material, el índice de paneo no**.
- **Tres entradas nuevas de roadmap** que salen de eso: decorrelación por parlante
  (i-7c8794-250043), modelo de posiciones en coordenadas con DBAP (i-7c8794-26c302) y modo
  difusión en vivo (i-7c8794-bdb678).
- **Dos reencuadres que cambian prioridades:**
  1. los **2–3 ms** medidos entre los Go 4 caen en *localization dominance*, no en zona de
     eco (que empieza en ~100 ms para música). O sea que el desfase medido **no es el
     problema**;
  2. conviene **gastar el esfuerzo en decorrelación antes que en sumar un cuarto canal**,
     porque el predictor de envolvimiento es el nivel lateral tardío, no el número de
     fuentes.
- **La corrida de drift de 30 minutos se hizo y NO fue concluyente**, y la culpa es del
  estímulo que elegí. 180 ráfagas, pero con **25 ms de dispersión** contra los 0,25–0,68 ms
  de las corridas cortas. Prueba de que no es física: saltos de ±50 ms en 10 s serían
  **5000 ppm** de error de reloj. Causa: una ráfaga tonal tiene su información de tiempo
  solo en la envolvente (~1,25 ms de resolución) y la cama de ruido —necesaria para que
  PipeWire no suspendiera el nodo— la degradó. **El drift sigue sin medirse.**
- **Se registró la decisión d-7c8794-9afee2** y se quitó la lista cerrada de `check.sh`: se
  construye el núcleo compartido con A2DP como primer backend, sin cerrar Auracast.
- **Núcleo del host, con tests** (51 en total, antes 9):
  - `dsp/decorrelate.py`: todo-paso de fase aleatoria con los parámetros de Potard y
    Burnett, y el *best performance selection* de fases ortogonales;
  - `dsp/ambience.py`: ecuaciones (11) y (12) de Avendaño y Jot;
  - `config.py`: la instalación por **coordenadas** (opcionales) y no por etiquetas de
    canal, con retardo y ganancia por parlante como **etapa enchufable**;
  - `medicion.py`: GCC-PHAT, medición simultánea de todos los parlantes, niveles por
    mínimos cuadrados y calibración por ventanas;
  - `estimulos.py`: ruido rosa decorrelado, ráfagas tonales (para comparar) y barrido.
- **Comparación de estrategias de calibración** (SIMULADO,
  `experimentos/06-calibracion-rapida-y-recalibracion.md`): **2 segundos de ruido de banda
  ancha ya dan toda la precisión**, es **~170 veces mejor que las ráfagas tonales**, y
  **con música anda igual de bien**, que es lo que habilita recalibrar sin interrumpir.
  Receta: 10 s en 5 ventanas de 2, para tener mediana y dispersión.
- **El MVP: el núcleo del host, construido y con 92 tests** (empezó la sesión con 9).
  Además de `config`, `dsp/decorrelate` y `dsp/ambience`, se agregaron:
  - `motor.py`, la cadena completa estéreo → una señal por parlante, **con estado entre
    bloques** para poder alimentar un flujo;
  - `dsp/ambience.Extractor`, la versión con estado de la extracción;
  - `sonido.py`, la capa de PipeWire: descubrir parlantes, reproducir a N y grabar;
  - `cli.py` con `doctor`, `sinks`, `init`, `calibrate` y `play`, probados contra el
    sistema real (los dos primeros, que solo leen).
- **Procesamiento en vivo sobre el audio del sistema** (`aurasync run`), que era P1 del
  roadmap. `sonido.SinkVirtual` crea con `pw-record -P '{ media.class=Audio/Sink … }'` un
  dispositivo de salida que el sistema muestra como cualquier otro: se lo elige como salida
  y todo lo que suene ahí pasa por el procesamiento. **Comprobado que no deja huella**: al
  terminar el proceso, `pactl list sinks` no lo muestra más.
  - **Detalle que salió de otra medición:** cuando no hay nada reproduciéndose el nodo se
    suspende y no emite datos, así que `run` **manda silencio a los parlantes** en ese caso.
    Si sus streams A2DP se suspendieran, al volver traerían un desfase distinto del que
    acaba de medir la calibración.
- **La calibración quedó autónoma**, como pidió el usuario: `init` toma los parlantes
  conectados y `calibrate` mide retardo y ganancia. Lo único ajustable a mano es `pan` y
  `ambiente` por parlante, que es la decisión artística.
- **Tres decisiones de diseño del motor**, anotadas en el roadmap para no reabrirlas sin
  motivo: un proceso `pw-play` por parlante (y no un sink combinado, que dejaría el retardo
  fuera de nuestro alcance); la corrección de sincronía como **etapa enchufable**; y el
  camino directo retrasado los 43 ms de latencia que tiene la extracción de ambiente.
- **CLAUDE.md, `host/README.md` y el roadmap actualizados** para que no contradigan la
  decisión d-7c8794-9afee2, que ya estaba registrada pero no reflejada.
- **Probes:** `probes/e1-iso/`, `probes/02-gatt-jbl/`, `probes/e6-a2dp/` y
  `probes/calibracion/`.

**Archivos.** `docs/research/experimentos/` (`00-inventario-linux.md`,
`02-servicios-de-los-jbl-linux.md`, `03-e1-iso-en-el-ax210.md`, y `datos/00/` y
`datos/03/`), `docs/research/02-le-audio-auracast-linux.md` (§2: el AX210 pasa a
MEDIDO, más cómo leer los bits de LE Features), `probes/00-inventario-linux/`,
`probes/e1-iso/`, `probes/02-gatt-jbl/`, `scripts/check.sh`, `docs/roadmap.md`.

**Por qué.** El usuario pasó a trabajar desde el equipo Linux y pidió explorar el
adaptador, después dejar lista la regla de sudo y preparar las pruebas del stack.

**Arquitectura.** ✅ Cumple. Nada de código de producto (d-7c8794-346170): solo
probes y documentos. El cambio a `scripts/check.sh` es portabilidad, no alcance.

**Qué salió mal en el camino.**
- **Elegí mal el estímulo para medir drift, y costó 30 minutos de corrida.** Las ráfagas
  tonales tienen ~1,25 ms de resolución por su ancho de banda; con la cama de ruido encima,
  la dispersión subió a 25 ms y el resultado quedó inservible. Lo cuantifiqué después en
  `experimentos/06`: ruido de banda ancha es ~170 veces más preciso. **La lección general:
  la resolución de una medición de retardo va como 1/ancho de banda, así que un tono es
  siempre mal estímulo para esto.**
- **El umbral de confianza del estimador no detectaba sus propios fallos.** Estaba en 3;
  con ruido de sala 30 veces la señal daba confianza 5,8 —"confiable"— y 256 ms de error.
  Se subió a 15 con el barrido a la vista.
- **Llamar a la extracción de ambiente por bloque no funciona.** Una STFT no se parte sin
  dejar los bordes sin reconstruir: con bloques de 1024 muestras la salida difería **642 %**
  de la correcta. Hizo falta un extractor con estado de trama, que además tiene 43 ms de
  latencia propia y obliga a retrasar el camino directo lo mismo.
- **Agregar `[tool.ruff.lint.per-file-ignores]` al pyproject reemplaza la tabla de hatch en
  vez de extenderla**, y aparecieron 127 errores por usar `assert` en los tests. Hay que
  repetir las exenciones de hatch; quedó anotado en el propio archivo.
- **El estimador de nivel suponía ortogonalidad perfecta** y devolvía 1,38 donde la
  respuesta era 1,0. Los filtros del banco son *casi* ortogonales. Se reemplazó por mínimos
  cuadrados conjuntos. **Lo encontró un test**, no una escucha.
- **La primera comparación de calibraciones estaba sesgada a favor del método sin
  interpolación:** los retardos de prueba caían casi sobre muestras enteras. Se movieron a
  media muestra y se aplicaron con rampa de fase en vez de desplazamiento entero.
- **Se afirmó que el AX210 "va por USB, no PCIe", y el usuario lo corrigió.** Las dos
  cosas son ciertas: la tarjeta se conecta por PCIe y expone dos interfaces, Wi-Fi
  por PCIe y **Bluetooth por USB**. Lo detectó el usuario, no un comando. Ahora el
  probe captura la topología (`/sys/…/usb1/1-6` para el BT y `26:00.0` para el
  Wi-Fi) para que no dependa de la memoria de nadie.
- `scripts/check.sh` falló en Linux por la **colación del locale**: `sort` ordena
  `cli.py` antes de `__init__.py`, al revés que en macOS, así que la lista cerrada
  de archivos no coincidía. Se fijó `LC_ALL=C sort`.
- El primer `01-capacidades.sh` mostraba el firmware del **Wi-Fi** en vez del del
  Bluetooth, porque el `grep` no filtraba por `hci0:`. Lo delató la salida.
- **El gate de sudo de los dos probes probaba con `sudo -n true`, y `true` no está en
  la regla**, así que daba "falta la regla" incluso con la regla instalada. Ahora
  prueba con `sudo -n btmgmt info`.
- **El analizador de E6 falló dos veces, y las dos las destaparon los parlantes, no la
  síntesis.** (a) Contaba **13 ráfagas donde había 6** con una espuria de −145 ms: la
  envolvente ondula dentro del tono y vuelve a disparar. (b) Con un desfase real de
  ~55 ms leía **−82 ms**: la ventana tomaba 150 ms *antes* del disparo y alcanzaba la
  cola de la ráfaga anterior; ahora son 60 ms antes y 450 ms después, con mediana y MAD
  informadas. El test pasó a tener casos reverberantes de 40, 55 y 120 ms, que es el
  régimen donde aparecieron. **La primera tanda de 3 corridas quedó descartada por
  esto**, y se guardó para que se vea la diferencia.
- **Se afirmó que PipeWire no deja compartir un códec entre dos dispositivos**, a partir
  del error `endpoint … in use`. **Es falso:** después se midió al Tune y al Charge 6
  los dos en AAC. Lo que falla es el cambio de códec **en caliente**. Corregido en
  `experimentos/05`.
- **Quedaron 3 procesos `pw-cli -m` huérfanos** de corridas anteriores, creando sinks
  `jbl_combine` duplicados, porque los `pkill` fallidos no los habían matado. Se
  limpiaron. Además tanto cambio de perfil dejó los transportes A2DP trabados y hubo
  que reiniciar WirePlumber y reconectar los parlantes.
- `bluetoothctl` → `menu gatt` → `list-attributes <dirección>` imprime la ayuda en vez
  de enumerar. La enumeración confiable es `busctl tree org.bluez`.
- **El instrumento de 4 canales tuvo un defecto grave y silencioso.** Se eligieron tonos
  de 1000/2000/3000/4000 Hz, o sea en relación armónica. Cuando el Go 4 Blue se quedó sin
  transporte, su banda de 3000 Hz captó el **3.er armónico** de los 1000 Hz del Black y la
  medición devolvió un falso **+0,15 ms con MAD 0,3 ms**: el resultado *más limpio* de la
  tanda era el de un parlante que no sonaba. Se arregló (a) buscando frecuencias sin
  relación armónica —(2350, 3250, 4150, 5200), verificado por programa— y (b) comprobando
  el **nivel relativo de cada canal**, que ahora avisa "NO SUENA". El test sintético
  original no lo cubría; se le agregaron casos de canal mudo.
- **Se probaron dos mejoras del estimador de arranque y las dos fueron peores**, y queda
  anotado para no repetirlas: medir el **pico** en vez del flanco sube el error con
  reverberación de 2,2 a 7,6 ms, y restar la base o subir el umbral al 50 % no mejora
  (3,7 ms). La precisión se declaró honestamente en dos regímenes: <0,05 ms con señal
  limpia y ~2,2 ms en sala reverberante.
- Un `pkill -f`/`pgrep -f` con patrón amplio mató el propio shell del agente **tres
  veces**: el patrón aparece en la línea de comandos del propio shell. Con `timeout` en
  primer plano, o con `pgrep -x`, no pasa.
- **El socket ISO no se activó en el primer intento, en silencio.** `main.conf` tenía
  `KernelExperimental = <uuid>   # comentario`, y **BlueZ lee el comentario como parte
  del UUID** y descarta el valor (`Invalid KernelExperimental UUID`). El servicio
  arranca igual. Lo detectó el paso de verificación del probe, que mira
  `/proc/net/protocols` en vez de confiar en que el reinicio alcanzó. Quedó anotado en
  `research/02` §2, porque le va a pasar a cualquiera.
- `sudo btmgmt exp` se fue a modo interactivo y colgó 2 minutos hasta el timeout.
  `btmgmt` sin un subcomando válido abre una shell.

**Qué quedó pendiente.**
- **El sistema quedó cambiado a propósito:** `/etc/bluetooth/main.conf` tiene
  `Experimental = true` y el socket ISO activado, porque E8 lo necesita. Se revierte
  con `probes/e1-iso/03-revertir.sh` (verifica por md5). La regla de sudo sigue
  instalada: `sudo rm /etc/sudoers.d/bluetooth-sync`.
- **E8 quedó A medias:** el tono **sí se oyó** (confirmado por el usuario), pero falta
  capturar desde el cambio de perfil para tener frecuencia de muestreo y
  **presentation delay medidos** (se infirieron del SDU), y leer el firmware de los
  Tune.
- **El drift sigue sin medirse.** Hay que repetir la corrida con ruido decorrelado de banda
  ancha, que además es continuo por sí mismo y no necesita la cama de ruido que causó el
  problema.
- **Toda la comparación de calibraciones es SIMULADA, no medida.** Falta validarla con
  parlantes, y con eso revisar el umbral de confianza.
- **El usuario pidió (2026-09-28) avisar y consultar antes de cualquier prueba con audio.**
- **`aurasync run` nunca se corrió con parlantes.** El sink virtual sí se comprobó (aparece
  y desaparece), pero el lazo completo —sistema → proceso → 3 parlantes— no se probó.
- **De P1 falta la mitad del Mac** (el process tap) y el criterio de terminado formal:
  comprobar que tras un `kill -9` en plena captura no queda nada.
- **No hay modo de ajuste en vivo** (i-7c8794-bdb678) ni lazo de recalibración continua: el
  segundo depende de medir el drift, que sigue pendiente.
- **`calibrate` y `play` nunca se corrieron con parlantes.** La calibración sí se validó,
  pero a través del probe `probes/calibracion/medir_real.py`, no del comando.
- **La decisión parcial NO se registró.** El usuario la eligió (registrar que se construye
  el núcleo con A2DP como primer backend, dejando abierto Auracast hasta E4), pero después
  pidió seguir con las pruebas. **Hasta que se registre en `docs/decisions.md` y se saque
  la lista cerrada de `scripts/check.sh`, no se puede escribir código de producto**
  (d-7c8794-346170). Es el bloqueo del MVP.
- **E7 (Charge 6 por USB-C) subió de prioridad:** es la forma más barata de tener un cuarto
  canal sin pelear con el techo de 3 streams A2DP.
- **Otro cambio al sistema, reversible:** el perfil de la tarjeta del fifine pasó de
  `input:iec958-stereo` (entrada digital, graba silencio) a `input:analog-stereo`. Se
  revierte con `pactl set-card-profile`.
- No se leyó el firmware de ningún JBL.
- **E2, E3, E4 y E5 quedan bloqueados por hardware** hasta que lleguen las SuperMini.
  No requiere decidir ninguna compra.
- **La enumeración GATT no se corrió:** los parlantes estaban apagados (un `scan le`
  de 15 s no vio ninguno). `experimentos/02` está escrito con datos de la caché de
  BlueZ, no de una conexión.
- No se decidió cómo se le va a dar `CAP_NET_ADMIN` a Bumble. Se dejó fuera de la
  regla de sudo a propósito: cualquier forma de correr Python como root sin
  contraseña equivale a root, y no vale decidirlo antes de saber si el bit 30 existe.
- Apareció un candidato para leer el firmware desde Linux: el UUID
  `df21fe2c-2515-4fdb-8886-f12c4d67927c`, común a los cuatro JBL.

**Desvío del plan.** Ninguno. El roadmap pedía empezar la Fase 1 por el inventario
del equipo Linux, y es lo que se hizo. `experimentos/02` es un archivo que el
roadmap no previó: salió de mirar los emparejamientos y adelanta parte de E4.

**No verificado.**
- **`research/09` §1–§10 sigue siendo REPORTADO**, de resúmenes de búsqueda. **§11 es
  VERIFICADO**, de dos papers leídos enteros. **Bradley y Soulodre sigue sin leerse**
  (paywall): no se sabe si el nivel lateral tardío se puede estimar con un micrófono común.
- Kendall no se pudo leer (ResearchGate 403); se sustituyó por Potard y Burnett, que para
  implementar es mejor porque da números.
- Que los parlantes no expongan BASS ni PACS: `bluetoothctl info` devuelve **la
  caché de BlueZ** y el emparejamiento fue por BR/EDR. Se cierra conectando por LE
  con los parlantes encendidos.
- **Que el tono de E8 se haya oído.** Lo medido es que el CIS se estableció y que los
  paquetes salieron; nadie tenía los audífonos puestos.
- Que la frecuencia negociada en E8 sea 32 kHz: se deduce del SDU de 60 B en 7,5 ms
  (preset `32_1`) y de que el `bluez_input` reporta 32000 Hz. La configuración del ASE
  se había hecho antes de empezar la captura.
- Que la tarjeta sea un adaptador PCIe con módulo M.2 y cable USB: INFERIDO de que
  la placa (MSI B450M PRO-VDH MAX) no tiene ranura M.2 key E.
- Que los últimos bytes del UUID `excelpoint.` (`2001`, `2002`, `2014`) sean un
  índice de unidad o un rol en el par estéreo.

**Medido.**
- **El motor procesado por bloques coincide con procesarlo de una vez**: exacto muestra a
  muestra sin extracción de ambiente, y por debajo del 5 % con ella. Antes de escribir el
  extractor con estado, la diferencia era del **642 %**, o sea un clic por bloque.
- **Drift, 30 min, 3 Go 4:** 180 ráfagas; Go 4 Red mediana −8,36 ms con **MAD 25,01 ms**;
  Go 4 Blue −7,30 ms con MAD 9,03 ms. **El número de deriva que sale de ahí (+55,3 ms/h) no
  se reporta como resultado**, porque el instrumento no daba para eso.
- **LE Features del AX210 (firmware `202-5.26`): `ff 59 01 3c ae 00 00 00`.** Bits
  28 y 29 presentes; **30, 31 y 13 ausentes**. `btmgmt info` coincide:
  `cis-central cis-peripheral`, sin `iso-broadcaster` ni `sync-receiver`. La traza de
  `btmon` del arranque da **0** comandos de Periodic Advertising o de BIG.
- El socket ISO del kernel: `BTPROTO_ISO` (8) da `EPROTONOSUPPORT` sin la bandera y
  abre bien con ella. Este kernel no tiene ningún `CONFIG_BT_*ISO*`.
- Códecs sobre LE CIS: de 6 códecs locales, el único con "Codec supported over LE
  CIS" es `Transparent (0x03)`, así que LC3 va en software.
- **E8:** CIG con 2 CIS (handles 2304 y 2305), CIG Synchronization Delay **4632 µs**,
  CIS sync delay 4632 y 3702 µs, ISO interval **7,50 ms**, PHY **LE 2M**, 3
  subeventos, BN/FT 1/1, MTU **60 B**, **2214 paquetes `LE-CIS`** salientes con
  `slen 60`. 60 B cada 7,5 ms = **64 kbps por canal**.
- **E6:** el instrumento valida en **14 casos sintéticos** entre −6 y +120 ms, con y
  sin reverberación, con **error máximo 0,04 ms** y el conteo de ráfagas correcto en
  todos. Con parlantes, 4 reproducciones: medianas **+60,23 / +47,39 / +50,80 /
  +60,25 ms**, MAD **2,03 a 3,81 ms**, rangos 6,17 a 10,01 ms, 9 de 10 ráfagas medidas
  en cada corrida. **Variación entre reproducciones: 12,9 ms.**
- `scripts/check.sh` pasa en Linux con `PY=python3.14`: 9 tests en 0,27 s, con hatch
  1.16.2 armando su entorno en Python 3.12.12.
- El Bluetooth del AX210 negocia **12 Mbps (USB full-speed)** en el puerto `1-6` del
  xHCI del chipset AMD `[1022:43d5]`.
- Un `scan le` de 15 s con los parlantes apagados: **0 JBL** entre ~30 dispositivos.
- `decodifica-le-features.py` se probó con tres vectores sintéticos (solo bit 30;
  bits 12/13/28; una línea de debugfs) y acertó los tres veredictos.

---

## 2026-09-26 · s-7c8794-32c631 — Estructura base: paquete aurasync con hatch, tests de humo y plan de estructura

**Qué.**
- **Decisiones registradas**, tras preguntarle al usuario:
  - d-7c8794-f619c4: enmienda a d-7c8794-346170. Se permite la estructura base, y
    la lógica de producto sigue bloqueada;
  - d-7c8794-c23c20: Python 3.12 + Bumble + lc3py con hatch, sin lockfiles;
  - d-7c8794-5c014a: monorepo;
  - d-7c8794-92aa04: el nombre `aurasync`.
- **`host/`:**
  - `pyproject.toml` (hatchling; `bumble==0.0.235`, `lc3py==1.1.3`; entornos con
    el installer uv interno de hatch);
  - `src/aurasync/`: una CLI que solo responde `--version` y falla ante un
    subcomando desconocido;
  - 9 tests: CLI, `python -m`, versión instalada, API de Bumble para el emisor, y
    LC3 con 480 muestras, 2,5 ms de retardo e ida y vuelta con 1 y 4 canales.
- **`firmware/`:** un README general, `supermini/` (plan con
  `hci_uart_iso_timesync`) y `pico/` (plan de P3 y H2b). Sin código.
- **`scripts/check.sh`** suma tres cosas:
  - que `hatch` exista;
  - una lista cerrada de archivos en `host/src/aurasync/`;
  - `hatch fmt --check` y `hatch test`.
- **08 §6.1** tiene la estructura prevista, módulo por módulo, con su componente y
  su hito, y las reglas (`core/` sin E/S, tests de hardware en `tests/hw/` fuera
  del chequeo, sin dobles de Bumble).
- **Otros documentos:**
  - 08 §8, el roadmap (entrada i-7c8794-f7f5b2 Hecho, y "Qué hay que decidir"
    del MVP), `CLAUDE.md` (restricción, comandos, tabla) y `.gitignore`;
  - `jblsync` pasó a `aurasync` en 08 y en el roadmap.

**Archivos.** `host/`, `firmware/`, `scripts/check.sh`, `.gitignore`,
`docs/decisions.md`, `docs/roadmap.md`, `docs/research/08-integracion-y-plan.md`,
`CLAUDE.md`, `.agents/tracking/candidates.md`.

**Por qué.** El usuario pidió empezar la estructura base con tests y planificar
la estructura, considerando su hardware: 5 SuperMini, una Pico 2 W, un equipo
Linux y este Mac. A mitad de camino pidió usar hatch en vez de uv.

**Arquitectura.** ⚠️ Desvío autorizado.
- Hay código en `host/` antes de la decisión de seguir. Lo autorizó el usuario
  explícitamente, y quedó como d-7c8794-f619c4.
- El límite (solo esqueleto) lo hace cumplir `check.sh`, no solo la revisión.
- **Tarjetas aplicadas:**
  - *a-check-must-be-seen-to-fail*: se vio rojo con un test plantado, un import
    sin usar, `hatch` fuera del PATH y un módulo nuevo en `host/src/aurasync/`;
  - *ratchet-in-a-pinned-environment*: es greenfield, así que el lint parte en
    cero. Las dependencias directas van exactas; ruff lo fija la versión de hatch
    (1.18.1); las transitivas no se fijan (ver abajo);
  - *reproduce-the-checkout-not-only-the-environment*: `check.sh` se corrió desde
    una exportación limpia del índice;
  - *unrunnable-system-moves-the-gate*: 08 §6.1 dice que el chequeo no ve la radio
    ni los parlantes.

**Qué salió mal en el camino.**
- **Los lockfiles de hatch 1.18.1 borran el propio proyecto.** Con
  `lock-envs = true` e `installer = "uv"`, hatch aplica el lock con `uv pip
  sync`, que desinstala todo lo que no está en el lock, incluido `aurasync` en
  modo desarrollo. Por eso `hatch test` no encontraba el módulo. Se confirmó en el
  código de hatch (`env/virtual.py`, `lockers/uv.py`) y recreando los entornos.
- `skip-install = true` tampoco sirve, porque también omite las dependencias del
  proyecto.
- Se quedó **sin lockfiles, con las directas exactas**. Las transitivas flotan.
- Además, el lock que generó hatch salía **solo para la plataforma del Mac**. Con
  `[tool.uv.pip] universal = true` salía universal; se descartó junto con los
  lockfiles.
- En la primera versión del docstring de `__init__.py` quedó un id de decisión
  inventado, porque se escribió antes de generarlo. Se corrigió al generar los ids
  reales.

**Qué quedó pendiente.**
- Correr `scripts/check.sh` en el equipo Linux, con hatch en el home y `PY`
  apuntando a un Python ≥3.11. No está probado.
- Fijar las dependencias transitivas cuando hatch corrija el sync, o si aparece
  un problema de versiones.
- La toolchain de firmware (NCS/west, Pico SDK) no está instalada en el Mac.
- Si el controlador virtual de Bumble soporta BIG, para usarlo como doble en
  tests de `emit/`.

**No verificado.**
- Que la API de Bumble alcance para el emisor: el test solo comprueba que existan
  los nombres (`create_big`, `create_advertising_set`,
  `BasicAudioAnnouncement`, `IsoPacketStream`).

**Commit.** Uno solo, de estructura, a pedido del usuario (2026-09-27), sin
trailer de coautor. También se actualizaron las líneas de estado de `CLAUDE.md`,
`docs/research/README.md` y el roadmap, que decían "no hay código".

**Medido.**
- `scripts/check.sh` pasa: 9 tests en 0,3 s en el Mac, con Python 3.12.13 de
  hatch.
- Se vio en rojo en los cuatro casos plantados.

---

## 2026-09-26 · s-7c8794-f44ace — Software de audio del PC (07) y plan de integración e I+D (08)

**Qué.**
- **Documento 07 nuevo**, sobre el software del PC:
  - la captura del audio del sistema en Linux, macOS y Windows;
  - qué fuentes traen multicanal;
  - los algoritmos y las herramientas de upmix;
  - el ruteo de canales;
  - qué ven las apps con un sink multicanal;
  - la latencia, el lip-sync y el desfase tolerable entre parlantes;
  - la entrada de audio de Bumble;
  - cómo repartir los canales entre los 3 Go 4 y el Charge 6.
- **Documento 08 nuevo**, sobre la integración:
  - niveles de huella N0–N4 por mecanismo;
  - dónde vive el emisor (PC, Pi como tarjeta USB, nRF5340, comercial, red);
  - el lazo de reloj;
  - el stack y la arquitectura de una CLI (`jblsync`, nombre provisional);
  - el plan P1–P3, M0–M5 y Fase 3;
  - las decisiones que quedan abiertas para el usuario.
- **Roadmap:**
  - entradas nuevas P1 (i-7c8794-fd5f03), P2 (i-7c8794-cb208f), P3
    (i-7c8794-346d45), la herramienta CLI del MVP (i-7c8794-2fe665) y la Fase 3
    (i-7c8794-80f3ac);
  - la entrada de upmix (i-7c8794-c7ccb9) quedó corregida;
  - el diagrama de etapas y la tabla de la invariante quedaron actualizados.
- **Otros documentos:**
  - una corrección en 03 §2;
  - README, `CLAUDE.md` y `references.md` actualizados con 07 y 08.

**Archivos.** `docs/research/07-software-de-audio-en-el-pc.md`,
`docs/research/08-integracion-y-plan.md`, `docs/research/03-…md`,
`docs/research/README.md`, `docs/roadmap.md`, `docs/references.md`, `CLAUDE.md`,
`.agents/tracking/candidates.md`.

**Por qué.** El usuario pidió dos cosas:
1. investigar el software del PC para surround y canales (fuentes, captura de
   cualquier audio, compatibilidad);
2. evaluar cómo integrar audio y Bluetooth en herramientas que modifiquen lo
   mínimo el sistema, con un plan de I+D.

**Arquitectura.** ✅ Cumple.
- No hay código de producto. Todo el diseño de 08 está marcado INFERIDO, y las
  entradas del MVP quedan bloqueadas por la decisión de seguir (d-7c8794-346170).
- Se aplicaron las tarjetas *cleanup-belongs-to-the-supervisor*,
  *detect-by-observation-not-build-flag*, *derive-state-from-one-clock*,
  *close-the-loop-in-the-actuators-frame* y *fail-closed-defaults* (08 §2.4). Sus
  chequeos quedan como criterios de P1, P2 y M2, porque todavía no hay nada que
  ejecutar.

**Qué salió mal en el camino.**
- Dos agentes se contradijeron sobre el upmix por defecto de PipeWire: uno leyó
  "psd por defecto" en la documentación, el otro "NONE" en el código. Se revisó
  `audioconvert.c` y `channelmix-ops.c` del tag 1.6.9:
  - el flag `channelmix.upmix` arranca en true, pero el método arranca en `none`
    y los cutoffs en 0;
  - con eso no se genera ningún canal.
  El 07 se corrigió a "apagado en la práctica". **La documentación de
  pipewire-props muestra los valores de ejemplo como si fueran los de por
  defecto**: no hay que fiarse de ella para los valores por defecto.
- gitlab.freedesktop.org y la ArchWiki bloquearon a los agentes con Anubis, así
  que usaron el espejo de GitHub y `action=raw`.

**Qué quedó pendiente.**
- Las decisiones de 08 §8 (sistema de referencia, stack, comprar una Pi Zero 2 W
  para P3, nombre). **Ninguna está registrada en `decisions.md`.**
- P1 se puede hacer ya en el Mac, sin hardware.
- El experimento 1 de 07 (BlackHole o un tap con 6 canales) tampoco necesita
  parlantes.

**No verificado.**
- Todo 07 y 08 es investigación documental. Los agentes leyeron las fuentes, y
  esta sesión solo volvió a abrir el código de upmix de PipeWire.
- Nada se midió.
- Las cifras de latencia de extremo a extremo son estimaciones de
  especificación.

**Pico 2 W (segunda parte de la sesión).** El usuario avisó que ya tiene una
Raspberry Pi Pico 2 W, y quedó evaluada en 08 §3.1:
- **su radio CYW43439 no hace advertising extendido**, así que no hay BIG
  (REPORTADO, con una traza HCI en pico-sdk #2313);
- **el RP2350 puede ser el cerebro de la Fase 3 (H2b)**: TinyUSB con 4 canales +
  liblc3 + host BTstack, con una SuperMini por UART. BTstack ya tiene un port
  oficial casi igual (`rp2040-vela-if820`);
- **P3 se rehízo para la Pico**, en dos pasos: (a) benchmark de LC3 en el M33;
  (b) speaker USB de 4 canales. La Pi Zero 2 W queda como alternativa si (a)
  falla, y no hay que comprar nada por ahora;
- se sumó a P2 `bluekitchen/hci_uart_iso_timesync` (con el comando `LE Read ISO
  Clock`) como firmware candidato para las SuperMini;
- la Pico sirve también de sonda SWD (debugprobe) para recuperar una SuperMini.

**Qué salió mal en el camino (segunda parte).** El script de edición se cortó a
la mitad porque un bloque de texto no coincidía. Quedaron aplicadas solo las
primeras ediciones; se detectó por el `AssertionError` y se completó en una
segunda pasada.

**No verificado (segunda parte).**
- El costo de liblc3 en el RP2350: no hay cifras publicadas, y la estimación de
  72–150+ MHz para 4 canales es INFERIDA.
- Que TinyUSB haga 4 canales con feedback en el RP2xxx.
- Que `LICENSE.RP` cubra un controlador externo.

**Aprendizajes para el harvest.** Quedaron dos filas en
`.agents/tracking/candidates.md`:
- una segunda ocurrencia de "Documented default values drift from the code"
  (`OPEN.md`): la documentación de PipeWire mostraba valores de ejemplo como si
  fueran los de por defecto;
- `check-every-anchor-before-the-first-write`: el script de edición que se cortó
  a la mitad.

**Commits.** Dos, uno por tema: la investigación del software del PC (07) y el
plan de integración (08) con la Pico 2 W. Van sin trailer de coautor, por
preferencia del usuario.

**Medido.** Nada. `scripts/check.sh` pasa.

---

## 2026-09-26 · s-7c8794-da36f9 — Placas nRF52840 evaluadas; compradas 5 SuperMini

**Qué.**
- Se agregaron la Seeed XIAO nRF52840 y la SuperMini nRF52840 (clon de nice!nano)
  a la tabla de hardware de 06 §1, con sus diferencias frente al dongle nRF52840.
- Se revisaron la wiki del vendedor de la SuperMini y su footprint KiCad. Ninguno
  dice si lleva cristal de 32 kHz: el footprint trae solo pads.
- Se configuró `origin` (GitHub, privado) y se hizo el primer push, forzado a
  pedido del usuario: reemplazó un "Initial commit" que traía solo un README de
  una línea.
- El usuario compró 5 SuperMini. Quedó registrada la decisión d-7c8794-b82ee9, y
  el roadmap (hardware, E1 y E2) y el README (opción combinada) se actualizaron.

**Archivos.** `docs/research/06-opcion-c-nrf5340.md`, `docs/decisions.md`,
`docs/roadmap.md`, `docs/research/README.md`.

**Por qué.** El usuario preguntó por un kit Meshtastic (XIAO + Wio-SX1262), luego
por la SuperMini, y terminó comprando un pack de 5.

**Arquitectura.** ✅ Cumple: solo investigación y compra, sin código.

**Qué quedó pendiente.**
- Cuando lleguen las placas:
  - flashear `hci_uart` con `promicro_nrf52840`, copiando `nrf52840dongle_nrf52840.conf`;
  - revisar si el cristal de 32 kHz arranca, y si no, usar RC;
  - hacer E1 y E2 desde el Mac por `serial:`.
- El footprint KiCad (`SuperMini NRF52840.kicad_mod`) ya no está en el
  repositorio: lo sacó el usuario. Lo que tenía de útil quedó en 06 §1.
- No se revisó si `auracast-hackers-toolkit` corre en estas placas.

**No verificado.**
- Los precios son REPORTADOS; no se vieron en una tienda.
- Que `hci_uart` con ISO funcione en `promicro_nrf52840` o `xiao_ble` es
  INFERIDO, porque usan el mismo chip que el dongle.
- Que los 4 pads traseros de la SuperMini sean SWD es INFERIDO.

---

## 2026-09-26 · s-7c8794-00d464 — Datos crudos guardados sin máscara (continúa s-7c8794-1a0f01)

**Qué.**
- A pedido del usuario, se guardaron los datos que se habían enmascarado:
  - los 6 escaneos crudos en `docs/research/experimentos/datos/01/`;
  - `system_profiler SPBluetoothDataType` completo y `sw_vers` en
    `docs/research/experimentos/datos/00/`;
  - en los documentos 00 y 01, los bytes completos, las direcciones de los JBL y
    del Mac, y el nombre personalizado del Go 4.
- Se agregaron el histograma de company IDs del escaneo sin filtro y los JBL Tune
  770NC-LE como posible receptor LE Audio de prueba.
- Se registró la decisión d-7c8794-8374e1: estos datos se guardan mientras el
  repositorio sea privado y se limpian antes de publicarlo.

**Por qué.** El usuario dijo que no hay problema en guardarlos, porque el
repositorio no se publicará por ahora.

**Qué quedó pendiente.**
- Los nombres de dispositivos de terceros del escaneo sin filtro no se guardaron:
  no son del usuario y no aportan a la investigación.
- La salida de ese escaneo nunca se guardó en un archivo; solo queda el
  histograma.

**Medido.** Con los bytes completos se ve que los bytes 4–7 del Charge 6
(`ed 0e 88 3f`) son iguales en reposo y transmitiendo, así que son estables por
unidad.

---

## 2026-09-26 · s-7c8794-1a0f01 — E2 parcial en el Mac: anuncios de los JBL leídos con CoreBluetooth

**Qué.**
- Se instaló `bleak` 3.0.2 en un entorno virtual del scratchpad, fuera del
  repositorio, con permiso del usuario.
- Se escribió el probe `probes/e2-scan-mac/scan.py`.
- Con el usuario manejando los parlantes, se escaneó en tres estados: en reposo,
  con el Go 4 transmitiendo Auracast y con el Charge 6 transmitiendo Auracast.
- El resultado quedó en `docs/research/experimentos/01-e2-anuncios-jbl-mac.md`, y
  se actualizaron 01, el índice, las referencias y E2 en el roadmap ("A medias").

**Archivos.** `probes/e2-scan-mac/scan.py`,
`docs/research/experimentos/01-e2-anuncios-jbl-mac.md`,
`docs/research/01-parlantes-jbl.md`, `docs/research/README.md`,
`docs/references.md`, `docs/roadmap.md`.

**Por qué.** El usuario pidió intentar las pruebas en el Mac y autorizó instalar
lo necesario.

**Arquitectura.** ✅ Cumple. Es un probe desechable en `probes/`; no hay código de
producto.

**Qué salió mal en el camino.**
- El primer escaneo filtrado de 20 s no vio el Charge 6. Un escaneo sin filtro
  mostró que estaba presente. Se subió la duración a 30 s, y el probe informa
  primero cuántos dispositivos vio, para que un resultado vacío no se lea como
  "no anuncia nada".
- El escaneo sin filtro imprimió en la consola los nombres de dispositivos
  cercanos de terceros. No se copiaron al repositorio.
- En el documento se enmascararon los bytes propios de cada parlante y el nombre
  personal del Go 4.

**Qué quedó pendiente.**
- Escanear durante el emparejamiento estéreo, por si el anuncio Auracast aparece solo
  en ese momento. También medir el mismo Go 4 solo y en estéreo.
- La BASE y el BIGInfo, que necesitan un controlador accesible.
- Repetir una transmisión para saber si el Broadcast_ID cambia.
- **El probe se conserva** hasta cerrar E2. Después se borra (d-7c8794-3208b7).

**Medido.**
- Datos de fabricante de la transmisión: `0x0057 +
  00000000000000000000000000000000dffd`, idénticos en el Go 4 y el Charge 6.
- El Charge 6 anuncia PBP `04 00` y el Broadcast_ID 0x112233.
- El Go 4 anuncia el Broadcast_ID 0x008105 y no anuncia PBP.
- Dos Go 4 (rojo y azul) sincronizados en estéreo y reproduciendo: **ningún
  0x1852 en 30 s ni en 45 s**. Su anuncio de reposo tiene `09 60` en los bytes 8–9.
  El byte 2 vale negro = `01`, rojo = `02`, azul = `03`; la hipótesis es que indica
  el color.
- Un primer intento leyó el byte 2 como el rol en el par estéreo, suponiendo que
  el "Bl" era la misma unidad negra. El usuario aclaró que era un Go 4 azul, y el
  documento se corrigió.

---

## 2026-09-26 · s-7c8794-84eb42 — Inventario del Mac: Bumble no alcanza el controlador interno

**Qué.**
- Se hizo el inventario de solo lectura del Mac (`system_profiler`) y se leyó la
  documentación de Bumble para macOS.
- Se registró el resultado en `docs/research/experimentos/00-inventario-mac.md`.
- En el roadmap, el inventario pasó a "A medias".

**Archivos.** `docs/research/experimentos/00-inventario-mac.md`,
`docs/roadmap.md`.

**Por qué.** El usuario preguntó si las pruebas se pueden hacer en este Mac.

**Arquitectura.** ✅ Cumple. No se instaló ni se cambió nada en el sistema.

**Qué salió mal en el camino.** Nada. Se evitó escribir en el repositorio las
direcciones Bluetooth y los nombres de los dispositivos emparejados, aunque
`system_profiler` los muestra.

**Qué quedó pendiente.**
- El inventario del equipo Linux.
- El E2 parcial con `bleak` en el Mac: requiere instalar `bleak` en un entorno
  virtual temporal y que el usuario ponga un JBL a transmitir. Se propuso y no se
  ejecutó.

**Medido.**
- El controlador del Mac es MTK_7932, por PCIe, con LEA declarado.
- El PID del Charge 6 es 0x20E3, igual al que documenta openjbl.

---

## 2026-09-26 · s-7c8794-77b101 — Opciones A (Bumble) y C (nRF5340) investigadas en profundidad

**Qué.**
- Se escribieron `docs/research/05-opcion-a-bumble.md` y
  `docs/research/06-opcion-c-nrf5340.md`, a partir de la lectura del código de
  Bumble, Zephyr y sdk-nrf en commits fijos.
- Se agregó al índice de la investigación la comparación entre A y C, con la
  opción combinada (Bumble con un nRF por `hci_uart`) y un orden sugerido.
- Se actualizaron las opciones de compra de E1 en el roadmap y el mapa de
  `CLAUDE.md`.

**Archivos.** `docs/research/05-opcion-a-bumble.md`,
`docs/research/06-opcion-c-nrf5340.md`, `docs/research/README.md`,
`docs/roadmap.md`, `CLAUDE.md`.

**Por qué.** El usuario eligió las opciones A y C para seguir explorándolas.

**Arquitectura.** ✅ Cumple. Solo documentación.

**Qué salió mal en el camino.** La línea A sugirió que `hci_usb` de Zephyr podía
servir como controlador ISO para Bumble. La línea C mostró que no: Zephyr manda a
ACL todo lo que llega por USB bulk (issue #44013). Se corrigió en 05 §1. Dos
agentes que leen el mismo código desde lados distintos se corrigen entre sí; uno
solo no lo habría notado.

**Qué quedó pendiente.**
- El harvest de `.agents/` sigue a la espera: el usuario lo dejó para después.
- Las dos sesiones del 2026-09-25/26 (implementaciones, y opciones A y C) van en
  un solo commit de investigación, porque comparten archivos (índice, roadmap,
  `CLAUDE.md`) y son un mismo tema.
- Sigue pendiente el inventario del chip (i-7c8794-d9c834).

**No verificado.**
- Los agentes leyeron el código en copias temporales del scratchpad. Los números
  de línea corresponden a los commits citados en cada documento.
- Los precios de DigiKey son del 2026-09-25.

---

## 2026-09-25 · s-7c8794-ed065e — Investigación de implementaciones y stacks; preguntas previas del harvest

**Qué.**
- Cuarta línea de investigación: las implementaciones abiertas por capa, con su
  lenguaje, licencia y actividad revisados en cada repositorio; proyectos de
  ingeniería inversa de JBL; las cuatro opciones de stack para el prototipo.
- Se corrigieron dos datos del documento 02 (el nodo por BIS de PipeWire queda en
  disputa; el ESP32 queda descartado como emisor).
- Se actualizaron el índice de la investigación, `CLAUDE.md`, las referencias y
  E2 y E3 del roadmap.
- Se preparó el harvest del problema de `.agents/` y se le hicieron al usuario las
  preguntas previas. Todavía no se escribió nada en `tracking/`.

**Archivos.** `docs/research/04-implementaciones-y-stacks.md`,
`docs/research/02-le-audio-auracast-linux.md`, `docs/research/README.md`,
`docs/references.md`, `docs/roadmap.md`, `CLAUDE.md`.

**Por qué.** El usuario preguntó qué implementaciones existen, en qué lenguajes y
qué stack usan, y quiere resolver el problema del paquete `.agents/`.

**Arquitectura.** ✅ Cumple. Solo documentación; no hay código.

**Qué salió mal en el camino.** La línea 02 marcó como VERIFICADO, por los
comentarios del código de PipeWire, que se crea un nodo por BIS. La línea 04 no
pudo confirmarlo. Un comentario de código no basta para marcar algo VERIFICADO si
se refiere a un comportamiento en ejecución.

**Qué quedó pendiente.**
- El harvest espera la respuesta del usuario a las preguntas previas.
- El chipset de los JBL: el sitio de la FCC bloqueó el acceso.
- chicco-carone/sync-test sin revisar.

**No verificado.** Las fuentes las reunió un agente de investigación; esta sesión
no volvió a abrir cada repositorio.

---

## 2026-09-25 · s-7c8794-88a0f6 — Repositorio preparado para la fase de investigación

**Qué.**
- Se hizo la investigación de factibilidad en tres líneas: los parlantes JBL,
  LE Audio y Auracast en Linux, y A2DP con sincronización por software. Quedó en
  `docs/research/`.
- Se escribieron el registro de fuentes, las decisiones iniciales, el plan de
  desarrollo (`docs/roadmap.md`), `CLAUDE.md` y el chequeo `scripts/check.sh`.
- Se ejecutó `git init` y se hizo el primer commit en `main`, sin remoto.
- Se generó el id propio del repositorio (`r-7c8794`) y se vació el outbox.

**Archivos.** `CLAUDE.md`, `.gitignore`, `docs/`, `probes/README.md`, `scripts/check.sh`,
`.agents/carrier.toml`, `.agents/tracking/`.

**Por qué.** El usuario quiere investigar antes de decidir si vale la pena
desarrollar, y dejar un plan de desarrollo basado en la investigación.

**Arquitectura.** ✅ Cumple. Es un bootstrap reducido. Lo que quedó fuera está
en `declined`, en `.agents/carrier.toml`.

**Qué salió mal en el camino.**
- La copia de `.agents/` no se hizo con `bundle.py export`. Traía el
  `carrier.toml` del repositorio de origen (otro id, sin `upstream`), 5 filas de
  outbox ajenas y un `tools/__pycache__/`. Se borraron y se regeneraron.
- `bundle.py` falló con el `python3` por defecto (3.9, sin `tomllib`), así que se
  usa `/opt/homebrew/bin/python3.14`.
- La plantilla de roadmap del método pone el id antes del `·` (`### i-… · Idea`),
  pero `bundle.py ids` solo reconoce un id en un título cuando va después
  (`### Idea · i-…`). Con la plantilla, los 15 ids del roadmap no se revisaban. El
  contador del chequeo lo delató (7 ids definidos en vez de 22). Se invirtió el
  orden en los títulos.

**Qué quedó pendiente.**
- No se identificó el chip Bluetooth del equipo Linux (i-7c8794-d9c834).
- No hay remoto configurado (i-7c8794-c28668).
- No se leyeron los QDID del Bluetooth SIG de los JBL.
- No se hizo ningún experimento.
- El desajuste entre la plantilla y la herramienta queda para un harvest
  (`.agents/method/prompt-harvest.md`), que lo ofrecería como candidato al origen.

**No verificado.** Todo lo que está en `docs/research/` es investigación
documental (VERIFICADO, REPORTADO o INFERIDO). No se midió nada con los parlantes
propios. Los agentes de investigación reunieron las fuentes y esta sesión no volvió
a abrir cada una.

**Medido.** `scripts/check.sh` pasa y reconoce 22 ids definidos. También se vio fallar: con un id duplicado
plantado en una copia de prueba, `bundle.py ids` salió con error.

---

## Formato de una entrada (va siempre al final del archivo)

    ## AAAA-MM-DD · s-<repo6>-<content6> — <título en una línea>
    **Qué.** Qué cambió, en concreto.
    **Archivos.** Archivos o carpetas.
    **Por qué.** El motivo, incluido el pedido que lo originó.
    **Arquitectura.** ✅ Cumple · ⚠️ Desvío · REVISAR, y por qué.
    **Qué salió mal en el camino.** Qué hizo mal el primer intento y qué lo detectó.
    **Qué quedó pendiente.** La deuda creada o esquivada, con nombre.
    **Desvío del plan.** En qué se aparta de lo aprobado, y la medición que lo decidió.
    **No verificado.** Lo que no se pudo comprobar, y dónde queda la pregunta.
    **Medido.** El número, si se afirmó algo.

El id `s-` se genera con `bundle.py id s "<título>"`.
