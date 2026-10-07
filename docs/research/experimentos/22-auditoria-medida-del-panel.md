# 22 · Auditoría medida del panel (modo simulado), 2026-10-07

**Pregunta:** ¿cómo está el panel frente a WCAG 2.2 AA y en el uso en PC (primero) y teléfono, medido sobre el
panel real y no leyendo el código? Es la parte medida de la auditoría que pidió el usuario el 2026-10-07; la
comparación con las apps del rubro y los estándares, y la lista priorizada que junta las dos, están en
[research/10](../10-panel-de-control.md) §9–§10.

**Veredicto (MEDIDO):**
- **En lo grueso, bien:** axe en tema claro deja 3 violaciones en las 6 pantallas, los objetivos de 24 px
  pasan en todas, todo se alcanza con Tab, y el movimiento reducido y las pausas funcionan.
- **Cuatro defectos que conviene arreglar ya**, porque se usan siempre: el volumen de la cabecera **sin
  nombre accesible**; en Parlantes, **13 controles que se recrean cada segundo y hacen perder el foco**;
  **los deslizadores por parlante de 33 px** en PC; y **el contraste** del texto gris en oscuro, los bordes de
  los campos y los medidores en claro.
- **El espacio en PC está mal aprovechado:** la cabecera ocupa 161 px, el contenido se corta en 1248 px y
  el A/B pide 7 o más acciones.

Las capturas que quedan en el repositorio son una selección: PC 1440 en claro y oscuro, más las especiales.
El resto (77 en total, 14 MB) y el axe-core descargado (MPL-2.0, de terceros) no entran. Los scripts están
en `probes/23-auditoria-panel/` (sonda desechable).


Todo lo de este documento es **MEDIDO** en el navegador salvo lo marcado **INFERIDO** (razonado a
partir de una medición, sin confirmarlo con la tecnología que lo decide, p. ej. un lector de
pantalla). Ningún número viene de memoria.

## 0. Entorno y método

| Cosa | Valor |
|---|---|
| Equipo | `HP-O16` (Intel i5-11400H, 12 hilos, 30 GB), Linux 7.2.9-1-cachyos |
| Repositorio | rama `auracast-supermini-f1`, HEAD `6895ae0`, **con cambios sin commitear en `host/src/aurasync/panel/`** (app.js, index.html, cadena.js, cadena.build.json): se midió el árbol de trabajo tal como estaba |
| Navegador | Chromium **148.0.7778.96** (el de Playwright, sin interfaz, `device_scale_factor=1`) |
| Playwright | **1.60.0** (entorno hatch `browser` del repositorio) |
| axe-core | **4.11.0** (bajado a `vendor/`, inyectado en la página; no entró al repositorio) con las etiquetas `wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa` |
| Lighthouse | **12.8.2** (`vendor/lh/`, Chromium 148 sin interfaz) |
| Servicio | el `Running` de `host/tests_browser/test_panel.py`: `Service(simulated=True)` con `SimulatedSession`, `SimulatedObserver`, `SimulatedRadio`, `SimulatedVolumes`, `SimulatedMonitor`; `XDG_CONFIG_HOME`/`XDG_DATA_HOME` apuntados al directorio de trabajo. **Ningún Bluetooth, sink de PipeWire, audífono ni parlante se tocó; no se abrió `/dev/ttyACM*`; no se tocó ningún servicio de systemd.** |
| Estado realista | 3 Go 4 simulados + **1 parlante virtual** (botón «Agregar parlante virtual»), sonando con la señal de prueba, **calibración simulada** de 5 s aplicada, 2 presets («cerrado», «amplio»), **monitor binaural** hacia «Audífonos (simulados)», «Mantener sincronía» encendido, medidores corriendo |
| Organización | la de siempre (`pestanas`): Escuchar, Cadena, Parlantes, Calibrar, Diagnóstico, Ajustes |
| Temas | el panel **sigue al sistema** (`prefers-color-scheme`, sin selector propio): se midió con `color_scheme=light` y `dark` |
| Ventanas | PC **1440×900** y **1920×1080**; teléfono **390×844** (`is_mobile`, `has_touch`) |

Scripts (todos fuera del repositorio): `scripts/measure.py` (pasada principal), `scripts/kb.py`
(teclado), `scripts/probe2.py` (foco perdido, Escape, aviso «Deshacer», nombres), `scripts/probe3.py`
(anchos de deslizadores, tabla de servicios), `scripts/probe4.py` (anillo de foco de los botones
primarios), `scripts/serve.py` (servicio para Lighthouse), `scripts/audit.js` (funciones dentro de
la página), `scripts/report.py` (tablas). Datos crudos: `datos/22-auditoria-panel/results.json`, `datos/22-auditoria-panel/probe2.json`,
`datos/22-auditoria-panel/probe3.json`, `datos/22-auditoria-panel/probe4.json`, `datos/22-auditoria-panel/lighthouse-{desktop,mobile}.json`,
`datos/22-auditoria-panel/aria-{pc1440,phone390}-<pestaña>.yaml`.

Cómo se midió cada cosa:

- **axe**: `axe.run(document)` con la pestaña visible (las vistas ocultas quedan fuera porque tienen
  `hidden`), una vez por pestaña × ventana × tema.
- **Contraste no textual**: dentro de la página, para cada forma sin texto de ≤ 16 px de lado o con
  `role=meter`, cada trazo SVG y cada borde de campo, el color calculado (pasado a sRGB con un canvas,
  porque Tailwind 4 usa `oklch`) compuesto sobre el fondo efectivo de sus ancestros; para los
  controles nativos (deslizador, casilla, radio), **muestreo de píxeles** de una captura del control
  (colores dominantes contra el color del borde de la captura).
- **Tamaño de objetivo**: rectángulo de cada interactivo visible (el de su `<label>` si es casilla o
  radio envuelta); bajo 24 px se prueba la excepción de espaciado de 2.5.8 (círculo de 24 px que no
  toque otro objetivo).
- **Teclado**: clic en el margen vacío de la cabecera (3,3) para poner el punto de partida arriba y
  `Tab` hasta volver al primero; en cada parada, `:focus-visible`, contorno calculado, si
  `elementFromPoint` del centro cae en otro elemento (foco tapado), y **diferencia de píxeles** entre
  la captura con foco y sin foco (píxeles cambiados y la mayor razón de contraste del cambio).
- **ARIA**: `locator('body').aria_snapshot()` por pestaña y el árbol completo por CDP
  (`Accessibility.getFullAXTree`) para controles sin nombre y medidores sin valor.
- **Movimiento**: un `MutationObserver` sobre todo el documento durante 5 s (lotes distintos por
  segundo por elemento), uno por región viva, y `requestAnimationFrame` para los cuadros por segundo;
  repetido con `reduced_motion="reduce"` y con los dos «Pausar».
- **Rendimiento**: `Performance.getMetrics` por CDP antes y después de 10 s quietos en cada pestaña
  (ocupación del hilo principal = Δ`TaskDuration`/10 s), `PerformanceObserver('longtask')`, y
  Lighthouse 12.8.2 (escritorio y móvil) contra `scripts/serve.py`.

> **Corrección de método.** La primera pasada de teclado marcó «sin foco visible» en «Calibrar»
> (contorno blanco, 0 px cambiados). `probe4.py` mostró que es un artefacto: `.btn` tiene
> `transition` y el `outline-color` parte de `currentColor` (blanco en un botón primario) y llega a
> sky-600 en 150 ms; la captura se tomó antes. Medido después de la transición: 526 px cambiados,
> razón 4,02 (claro) y 4,40 (oscuro). Ese caso **no** es un hallazgo. Se deja anotado porque el anillo
> sí tarda 150 ms en verse en los botones primarios.

Capturas: `datos/22-auditoria-panel/shots/<ventana>-<tema>-<pestaña>-fold.png` (lo que se ve sin desplazar) y `-full.png` (la
página entera), con `<ventana>` ∈ {`pc1440`, `pc1920`, `phone390`}, `<tema>` ∈ {`light`, `dark`},
`<pestaña>` ∈ {`escuchar`, `cadena`, `parlantes`, `calibrar`, `diagnostico`, `ajustes`}. Más:
`datos/22-auditoria-panel/shots/pc1440-light-ab-vivo.png` (A/B en curso), `datos/22-auditoria-panel/shots/w1440-diagnostico-servicios.png`,
`datos/22-auditoria-panel/shots/w1920-diagnostico-servicios.png`, `datos/22-auditoria-panel/shots/w390-diagnostico-servicios.png`,
`datos/22-auditoria-panel/shots/pc1440-light-calibrar-foco-tab.png`.

## 1. axe-core (WCAG 2.2 AA)

### 1.1 Violaciones por pantalla (regla y nodos)

| Contexto | Escuchar | Cadena | Parlantes | Calibrar | Diagnóstico | Ajustes |
|---|---|---|---|---|---|---|
| PC 1440 claro | 0 | 0 | 0 | color-contrast 2 | scrollable-region-focusable 1 | 0 |
| PC 1440 oscuro | color-contrast 9 | color-contrast 5 | color-contrast 19 | color-contrast 17 | color-contrast **286** · scrollable-region-focusable 1 | color-contrast 5 |
| PC 1920 claro | 0 | 0 | 0 | color-contrast 1 | scrollable-region-focusable 1 | 0 |
| PC 1920 oscuro | color-contrast 9 | color-contrast 5 | color-contrast 19 | color-contrast 11 | color-contrast 143 · scrollable-region-focusable 1 | color-contrast 5 |
| Teléfono claro | 0 | 0 | 0 | color-contrast 2 · scrollable-region-focusable 2 | scrollable-region-focusable 2 | 0 |
| Teléfono oscuro | color-contrast 9 | color-contrast 5 | color-contrast 3 | color-contrast 14 · scrollable-region-focusable 2 | color-contrast 173 · scrollable-region-focusable 2 | color-contrast 4 |
| A/B en curso (PC 1440 claro) | 0 | | | | | |

(El número de Diagnóstico depende de cuántas líneas de log hay: cada línea aporta dos nodos.)

### 1.2 Las reglas, con sus elementos

| Regla | WCAG | Qué falla (medido por axe) | Dónde |
|---|---|---|---|
| color-contrast | 1.4.3 | **Tema oscuro: `text-zinc-500` (#71717b) sin variante `dark:`** sobre #171719/#18181b → **3,67–3,70:1**, y sobre #09090b → 4,12:1 (texto de 9–10,5 pt, necesita 4,5) | pestañas no activas de la barra de arriba (5 en cada pantalla, siempre a la vista), barra de abajo en el teléfono, escala del medidor (`#meter-scale`, −60…0), encabezados de todas las tablas (dispositivos, parlantes, calibración, sugerencia, cortes, servicios), hora y servicio de cada línea de log, `.room-front`/`.room-custom` del plano |
| color-contrast | 1.4.3 | **Botón primario: blanco sobre sky-600 (#0084d1) = 4,06:1** con texto de 14 px normal | `#cal-run` «Calibrar», `#est-apply` «Aplicar sugerencia» (ambos temas) |
| scrollable-region-focusable | 2.1.1 | región desplazable sin nada enfocable | `#logs`; en el teléfono también `#cal-table`, `#est-table` y la tabla de Cortes |

Sobre `scrollable-region-focusable`: Chromium 148 hace enfocables los contenedores desplazables (la
pasada de teclado **sí** llegó a `ol#logs`), así que en Chromium funciona; en Firefox y Safari no se
midió (**INFERIDO**: ahí sí fallaría).

Revisión manual que axe dejó (incompletos, PC 1440 claro): 29 `color-contrast` (fondos con
transparencia o degradado), 3 `aria-prohibited-attr` (`aria-label` en un `div` sin rol: el espectro
`.input-live`, la correlación `.corr` y el plano `#room`: **ese nombre no llega al lector**), 1
`th-has-data-cells` (`.devices-table`).

## 2. Contraste no textual (WCAG 1.4.11, 3:1)

### 2.1 Gráficos y estados

| Elemento | Claro | Oscuro | Veredicto |
|---|---|---|---|
| Medidor de nivel: verde (#16a34a) contra la parte vacía (máscara) | **2,60** (máscara #e4e4e7) | 4,52 (#27272a) | falla en claro |
| Medidor: zona ámbar (#f59e0b) contra la parte vacía | **1,69** | 6,94 | falla en claro |
| Medidor: zona roja (#e11d48) contra la parte vacía | 3,70 | 3,17 | pasa |
| Medidor: verde contra la tarjeta | 3,30 | — | pasa |
| Medidor: pista vacía contra la tarjeta (dónde termina la escala) | **1,27** | **1,19** | falla (el valor numérico a la derecha lo compensa) |
| Medidor: raya de pico contra el verde | 5,38 (#18181b) | **3,00** (#f4f4f5) | límite en oscuro |
| Espectro de entrada: barra sky-500 contra su fondo zinc-100 | **2,46** | — | falla en claro |
| Correlación L/R: extremos del degradado contra la tarjeta | **1,52** (verde) / **1,92** (rojo) / 1,27 (centro) | **1,84** / **1,77** / 1,19 | falla; el marcador (zinc-900) sí se ve |
| Micrófono (Calibrar): ventana verde (emerald-300/70) contra la pista | **1,15** | **1,54** | falla (el texto «en la ventana» lo dice) |
| Puntos de estado `.status-dot` verde (#00bc7d) / gris (#9f9fa9) contra blanco | **2,47** / **2,62** | pasan | falla en claro; siempre van con texto («sonando»), así que la información no se pierde |
| Barra de sincronía `.sync-zone` (verde lima, ámbar, verde) | **1,95 / 2,13 / 2,47** | — | falla en claro; va con texto |
| Puntos de cortes: xrun ámbar / fuente sky | **2,13 / 2,71** | pasan | falla en claro; cada tipo tiene forma propia (1.4.1 cubierto) |
| Respuesta en frecuencia: tercios de coherencia baja (opacidad 0,25) | **1,32–1,33** | **1,39–1,43** | intencional («tenue: poco confiable»), pero esa diferencia de confiabilidad no se ve |
| Colores de serie s1–s8 contra blanco | 3,40–5,60 | — | pasan |
| Colores de serie s1–s8 contra zinc-900 | — | 3,16–5,21 | pasan (s8 3,16 y s7 3,31 por poco) |
| Círculo del mapa espacial (opacidad 0,3) | **1,94** | **2,57** | falla |
| **Bordes de campos** (`select`, `input` de texto y número): zinc-300 #d4d4d8 / zinc-700 #3f3f47 | **1,47–1,48** | **1,70–1,72** | **falla en todos los campos de todas las pantallas**, incluidos «Fuente» y «Modo» de la cabecera |
| Interruptores `.toggle` (Ambiente, Separar…): borde marcado sky-500 / sin marcar zinc-200 contra blanco | 2,71 / **1,27** | — | el estado también lo dice la casilla (pasa por la casilla) |
| Pestaña activa: fondo sky-50 contra blanco | 1,07 | 1,28 | el estado lo llevan el color y el peso del texto, no el fondo |

### 2.2 Controles nativos (muestreo de píxeles)

| Control | Claro (color: razón contra el fondo) | Oscuro |
|---|---|---|
| Deslizador: parte llena y pulgar | #0084d1: 3,96–4,02 | #0084d1: 4,40–4,49 |
| Deslizador: parte vacía de la pista | #efefef: **1,13–1,15** | #efefef: 15,4 |
| Casilla marcada | #0084d1: 3,77 | 4,08 |
| Casilla sin marcar (borde) | #767676: 4,54 | #858585: 4,80 |
| Radio marcado | #0084d1: 3,77 | 4,08 |
| Radio sin marcar (borde) | #828282: 3,84 | #7c7c7c: 4,24 |

La pista vacía del deslizador en claro (1,13) es la de Chromium (`accent-color` solo pinta la parte
llena); el pulgar y la parte llena pasan.

### 2.3 Anillos de foco (con el teclado, diferencia de píxeles)

| Anillo | Claro | Oscuro |
|---|---|---|
| `.btn`, `select`, `input`, pestañas: 2 px sky-600 (#0084d1) | 3,96–4,02 | 4,40–4,49 |
| Botón de ayuda `(?)` (`.help-btn`): 2 px **sky-500 (#00a6f4)** | **2,71** (19 botones: 10 en Cadena, 9 en Ajustes) | pasa |
| Elementos sin estilo propio (`link-btn`, `summary`, `meter-clip`, `reset-btn`, chip «cortes»): anillo `auto` de Chromium | visible (el cambio medido llega a 12–18:1) | visible (el cálculo del color, 1,07, no sirve para `auto`: Chromium pinta un anillo doble) |
| Botones primarios | el anillo tarda 150 ms en aparecer (transición desde blanco) | ídem |

## 3. Tamaño de objetivo

| Contexto | Interactivos por pantalla | < 24 px | < 24 px sin la excepción de espaciado (falla 2.5.8) | < 44 px (táctil) |
|---|---|---|---|---|
| PC 1440 (claro y oscuro) | 26–88 | 8, todos en Cadena | **0** | — |
| PC 1920 | 25–88 | 8, Cadena | **0** | — |
| Teléfono 390 | 26–63 | 0 | **0** | Escuchar 18 · Cadena 22 · Parlantes 4 · Calibrar 12 · Diagnóstico 16 · Ajustes 21 |

- Los 8 de Cadena son `button.spk-toggle` (nombre del parlante, 257,9×16 px) y pasan por espaciado.
- En el teléfono, bajo 44 px: `.help-btn` 24×24 (×19), `.link-btn` 44,6×24 (×11), `.flow-stage`
  72,4×24 (×9, el índice de etapas de Cadena), `.meter-clip` 32×24 (×7), `#chip-quality` 73×24 (en
  cada pantalla), «Pausar» de Niveles y Entrada 57,5×24, los `select` de la cabecera 176×36 y
  174×36, y los campos de 40 px de alto. 44 px es la recomendación táctil (2.5.5, AAA, y las guías
  de iOS/Android), no un requisito AA.
- **Anchura útil de los deslizadores** (`probe3.py`), lo que importa con el mouse:

| Deslizador | PC 1440 | PC 1920 | Teléfono |
|---|---|---|---|
| Volumen general (cabecera), 60 pasos | 546 px · 9,1 px/paso | 546 px · 9,1 | 266 px · 4,4 |
| **Parlantes en detalle: Pan (40 pasos)** | **33 px · 0,83 px/paso** | **33 px · 0,83** | (tarjetas plegadas) |
| **Parlantes en detalle: Ambiente (20)** | **33 px · 1,67** | **33 px · 1,67** | — |
| **Parlantes en detalle: Volumen (92)** | **33 px · 0,36** | **33 px · 0,36** | — |
| Cadena: perillas de etapa | 196 px · 4–10 | 196 px | 82 px · 1,7–4,3 |
| Cadena: «Semilla» (9999 pasos) | — | — | **82 px · 0,01** |
| Cadena: Pan/Ambiente por parlante | 146 px · 3,65 / 7,3 | 146 px | — |
| Monitor: Nivel (100 pasos) | 129 px · 1,29 | 129 px · 1,29 | 129 px |

En «Parlantes en detalle» un píxel mueve el volumen de un parlante casi 3 pasos: a 1920 px de ancho
el deslizador sigue midiendo 33 px porque el contenido se corta en 1248 px.

## 4. Teclado

### 4.1 Recorrido con Tab (PC 1440)

| Pantalla | Paradas de Tab (claro / oscuro) | Interactivos no alcanzables | Foco visible | Foco tapado por la cabecera fija | Contorno < 3:1 |
|---|---|---|---|---|---|
| Escuchar | 60 / 59 | 0 | todas | 0 | 0 |
| Cadena | 78 / 78 | 0 | todas | 0 | 10 (`(?)`, claro) |
| Parlantes | 79 / 78 | **9 / 10** | todas | 0 | 0 |
| Calibrar | 25 / 26 | 0 | todas (ver §0) | 0 | 0 |
| Diagnóstico | 38 / 38 | 0 | todas | 0 | 0 |
| Ajustes | 32 / 32 | 0 | todas | 0 | 9 (`(?)`, claro) |

Elementos con cursor de mano y sin foco: 0 en todas las pantallas. Ningún `tabindex` positivo.
`scroll-padding-top` cumple: ningún elemento quedó bajo la cabecera fija al recibir el foco (2.4.11).

### 4.2 Lo que falla

| Prueba | Resultado medido | WCAG |
|---|---|---|
| **Controles que se recrean solos** (`probe2.py`: se marcan todos los interactivos, se espera 3 s sin tocar nada, se cuentan los desconectados) | Parlantes: **13 de 74** se recrean en cada actualización del estado (≈1/s): «Desconectar» (×3), «Emparejar y conectar», «Usar «Automático»» (`#room-hint` hace `replaceChildren` con un botón nuevo cada vez) y los «Principal / Ambiental» de Espacial (×8). Las otras 5 pantallas: 0 | 2.1.1, 2.4.3 |
| **Foco sobre esos controles, 3 s después** | «Desconectar», «Usar «Automático»», «Ambiental»: el elemento ya no existe y el foco cae en `body`. Los de control (Cargar preset, Silenciar, Detener un servicio, `meter-clip`, deslizadores y `select` de Parlantes en detalle) conservan el foco | 2.1.1, 2.4.3 |
| Deslizadores con flechas | Volumen −20→−19→−20, Pan 0,70→0,65→0,70, Nivel del monitor 30→31→30: funcionan. `aria-valuetext` solo en los de Cadena («-0,70»); el volumen general y los de Parlantes se leen sin unidad («-20») | 4.1.2 (menor) |
| **Ayudas `(?)` con el mouse encima → Escape** | siguen visibles (claro y oscuro) | **1.4.13** (descartable) |
| **Ayudas `(?)` con foco → Escape** | siguen visibles | **1.4.13** |
| **Ayudas `(?)`: llevar el mouse del botón al texto** | se esconden: hay 8 px entre el botón (24 px) y la ventana (`top-8`), y al cruzar ese hueco la ventana pasa a `visibility:hidden` | **1.4.13** (sobrevolable) |
| Ayudas `(?)` de Cadena | `aria-label="Ayuda: Ambiente"`, sin `aria-expanded`; Enter no cambia nada (la ayuda es la misma ventana por CSS que se abre con el foco) | — |
| `<details>` («Configuración y qué cambia», Calibrar) | Enter abre; Escape no cierra (no lo exige WCAG) | — |
| **Aviso «Deshacer»** (al borrar un preset) | visible **10,4 s**; con el mouse encima, **10,3 s** (no se detiene); tapa «Tono» y «Silenciar» de un parlante de la tarjeta Parlantes (rectángulo 560×44 abajo al centro); después de borrar con el teclado el foco queda en **`body`** y «Deshacer» está a **13 Tabs** (índice 12 de 58) | 2.2.1, 2.4.3, 2.4.11 |
| Diálogo «Conectar teléfono» | no se midió: el botón está oculto en el panel local | — |
| Bloques repetidos | 12 paradas de la cabecera antes del contenido en cada pantalla; hay `banner`, `navigation` y `main`, sin enlace para saltar | 2.4.1 (cumple por los landmarks) |

## 5. ARIA

| Pantalla | Nodos del árbol | Controles sin nombre | Medidores (sin valor) |
|---|---|---|---|
| Escuchar | 1499 | **1: `input#volume`** | 7 (0): «Nivel Entrada L» → «-16,1 dB, pico -11,7 dB», etc. |
| Cadena | 1892 | 1 (`#volume`) | 0 |
| Parlantes | 1550 | 1 (`#volume`) | 0 |
| Calibrar | 1292 | 1 (`#volume`) | 1 (0): «Nivel del micrófono» → «-36 dBFS, en la ventana (ventana -55 a -10 dBFS)» |
| Diagnóstico | 2135 | 1 (`#volume`) | 0 |
| Ajustes | 1233 | 1 (`#volume`) | 0 |

- **El deslizador de volumen de la cabecera no tiene nombre accesible.** Causa medida: el
  `<label class="field">` envuelve `<span>Volumen <output id="volume-out">` y después el `<input>`;
  el primer elemento etiquetable dentro del `label` es el `<output>`, así que `label.control` es
  `volume-out` y `#volume.labels` está vacío. El árbol queda `status "Volumen": "-20 dB"` y
  `slider: "-20"` sin nombre (`datos/22-auditoria-panel/aria-pc1440-escuchar.yaml`, línea 10). Lo mismo pasa con «Nivel
  30 %» del monitor (`label.control` = `#monitor-gain-value`), pero ese deslizador se salva porque
  tiene `aria-label="Nivel del monitor"`. axe no lo detectó.
- **Regiones vivas que se reescriben con el mismo texto** (5 s, PC 1440 claro): `#monitor-state`
  (visible en Escuchar), `#room-hint` (visible en Parlantes), `#engine-state` (Ajustes) y
  `#radio-note` (Diagnóstico) cambian **60 veces por minuto** cada una, todas `role=status`
  (cortés); `#monitor-state`, `#room-hint` y `#engine-state` con **1 solo texto distinto** en esos
  5 s (`textContent =` sin comparar), `#radio-note` con 6 textos. Los lectores de pantalla anuncian
  una región viva cuando cambia su contenido; reemplazar el nodo de texto por uno idéntico **puede**
  provocar un anuncio por segundo según el lector (**INFERIDO**, no se probó con NVDA/Orca/VoiceOver).
- Los medidores (`role=meter`) actualizan `aria-valuenow`/`aria-valuetext` 9,2 veces por segundo; no
  son regiones vivas, así que no se anuncian solos.
- `aria-label` en `div` sin rol (espectro, correlación, plano de la sala): el nombre no se expone.
- Las instantáneas completas están en `datos/22-auditoria-panel/aria-pc1440-*.yaml` y `datos/22-auditoria-panel/aria-phone390-*.yaml`.

## 6. Movimiento y actualizaciones

| Pantalla (PC 1440 claro, 5 s) | Cuadros/s | Elementos que cambian > 3 veces/s | Con `prefers-reduced-motion` | Con «Pausar» en Niveles y Entrada |
|---|---|---|---|---|
| Escuchar | 60 | máscaras de los medidores **30/s** (transform), barras del espectro 9,8/s, marcador de correlación 9,8/s, número de correlación 9,8/s, `aria-valuenow` de los medidores 9,2/s, raya de pico 9/s, número del medidor 8,4/s | todo a **≤ 5/s** (4,2–5); transiciones 19 → 0 | **ninguno** > 3/s |
| Cadena | 60 | el contador de perillas de 9 etapas (`span.knobs-count`) **6 escrituras de texto/s** (characterData) de un número que no cambia a la vista | 4,2/s | — |
| Parlantes, Calibrar, Diagnóstico, Ajustes | 60 | ninguno | — | — |

- `prefers-reduced-motion` se respeta: CSS quita transiciones y animaciones y app.js pinta a 5 Hz.
- 2.2.2: lo que se mueve más de 3 veces por segundo (Niveles y Entrada) tiene su «Pausar»; con
  ambos pausados no queda nada sobre 3/s. Los valores «En vivo» de Cadena no cambiaron en 5 s (131
  observados, 0 cambios): el 6/s de Cadena es trabajo sin efecto visible, no movimiento.
- El aviso «Deshacer» trae una barra de tiempo animada (no se midió aparte; dura 10,4 s).

## 7. Distribución en PC y tareas principales

### 7.1 Espacio

| Ventana | Pantalla | Alto total (pantallas) | Contenido útil | Cabecera fija | Tarjetas con espacio en blanco > 120 px |
|---|---|---|---|---|---|
| 1440×900 | Escuchar | 1783 px (2,0) | 1248 de 1440 px | **161 px (18 % del alto)** | **A/B 345 px**, **Presets 187 px** |
| 1440×900 | Cadena | **3705 px (4,1)** | 1248 | 161 | — |
| 1440×900 | Parlantes | 2273 px (2,5) | 1248 | 161 | (Sala ocupa media columna; la otra mitad, vacía) |
| 1440×900 | Calibrar | 1431 px (1,6) | 1248 | 161 | (Respuesta en frecuencia ocupa media columna) |
| 1440×900 | Diagnóstico | 2179 px (2,4) | 1248 | 161 | — |
| 1440×900 | Ajustes | 1011 px (1,1) | 1248 | 161 | — |
| 1920×1080 | Escuchar | 1763 px (1,6) | **1248 de 1920 px: 672 px (35 %) sin usar** | 161 | A/B 329, Presets 183 |
| 1920×1080 | Cadena | 3705 px (3,4) | 1248 | 161 | — |
| 390×844 | Escuchar | 3101 px (3,7) | 358 de 390 | **233 px + 53 px de barra abajo = 34 % del alto, fijo** | — |
| 390×844 | Cadena | **6197 px (7,3)** | 358 | 233 + 53 | — |
| 390×844 | Diagnóstico | 4233 px (5,0) | 358 | 233 + 53 | — |

Sin desplazamiento horizontal en ninguna. Pero la **tabla de Servicios** (Diagnóstico) mide 609 px
en una columna de 582 px a 1440 y a 1920: los dos «Reiniciar» quedan **cortados** (desplazamiento
horizontal dentro de la tarjeta); en el teléfono la tabla mide 625 px en 324 px y quedan fuera 5
botones (`datos/22-auditoria-panel/shots/w1440-diagnostico-servicios.png`). La columna «Servicio» parte palabras
(«pip ewire», «sist ema») y la dirección de cada parlante también («s bc») por `break-all`.

### 7.2 Tareas (desde Escuchar, PC 1440×900)

| Tarea | Clics o acciones | Dónde está (y, en px) | ¿Sin desplazar? |
|---|---|---|---|
| Cambiar el volumen | 0 clics de navegación: el deslizador está en la cabecera fija (3 Tabs desde arriba) | 68 | sí, en todas las pantallas |
| Cambiar el modo (Clásico/Espacial/Frente/Directo), la perilla principal | 1 (el `select` de la cabecera) | 56 | sí, en todas las pantallas |
| Una perilla de etapa en Cadena | 1 clic (pestaña) + desplazar: la etapa 2 empieza en y≈850 y la 9 (Limitador) en y≈3200 de 3705 (captura `pc1440-light-cadena-full.png`), o 1 clic más en el índice de etapas (`.flow-stage`) | 659 (primer deslizador) | solo la etapa 1 |
| Ver si un parlante va atrasado | 0: fila «Sincronía» de Ahora (pero solo un resumen; en la simulación dijo «sin medición» aun con la calibración y el lazo encendidos); por parlante: 1 clic a Diagnóstico («Retardo que aplica el lazo») | 228 / 674 | sí |
| Calibrar | 1 («Calibrar y aplicar» en Ahora) o 2 (pestaña Calibrar → Calibrar) | 478 / 246 | sí |
| A/B ciego | **≥ 7 acciones** la primera vez: nombre + «Guardar el actual», cambiar algo, nombre + guardar otra vez, elegir A, elegir B, «Empezar» | 1044 | **no** a 1440×900 (0,2 pantallas abajo); 1920×1080: 0,03; teléfono: 1,45 pantallas |

## 8. Rendimiento

### 8.1 Página quieta, 10 s por pestaña (PC 1440 claro, sonando)

| Pantalla | Hilo principal ocupado | Script | Layout | Estilo | Layouts/s | Recálculos de estilo/s | Tareas largas | Heap JS |
|---|---|---|---|---|---|---|---|---|
| Escuchar | **6,1 %** | 1,1 % | 0,42 % | 0,37 % | 19,8 | 37,5 | 0 | 8,4 MB |
| Cadena | 3,8 % | 2,0 % | 0,03 % | 0,05 % | 2,0 | 7,0 | 0 | 27,1 MB |
| Parlantes | 3,2 % | 1,1 % | 0,11 % | 0,04 % | 2,0 | 2,0 | 0 | 9,3 MB |
| Calibrar | 3,2 % | 1,2 % | 0,06 % | 0,04 % | 2,0 | 4,8 | 0 | 9,7 MB |
| Diagnóstico | 3,6 % | 1,1 % | 0,23 % | 0,08 % | 3,1 | 3,1 | 0 | 10,3 MB |
| Ajustes | 2,6 % | 1,1 % | 0,06 % | 0,02 % | 3,0 | 3,0 | 0 | 12,0 MB |
| Escuchar, movimiento reducido | 3,3 % | 0,7 % | 0,22 % | 0,35 % | 10,6 | 12,3 | 0 | 6,9 MB |
| Escuchar, Niveles y Entrada pausados | 1,8 % | 0,5 % | 0,07 % | 0,04 % | 2,7 | 2,7 | 0 | 8,6 MB |

Ninguna tarea larga (> 50 ms) en ninguna pantalla; 60 cuadros por segundo. Ningún pedido HTTP en
los 10 s: todo llega por el stream. Chromium sin interfaz (sin GPU): en un navegador con interfaz
los números pueden cambiar.

### 8.2 Lighthouse 12.8.2 (carga en frío de Escuchar, tema claro)

| | Escritorio | Móvil (limitación simulada) |
|---|---|---|
| Rendimiento / Accesibilidad / Buenas prácticas | 78 / 96 / 96 | 58 / 96 / 96 |
| FCP · LCP | 0,6 s · 0,8 s | 2,6 s · 4,3 s |
| TBT | 20 ms | 30 ms |
| **CLS** | **0,513** | **0,788** |
| Peso total | 618 KiB | 587 KiB |
| Sin compresión | 366 KiB ahorrables (app.js, cadena.js, tailwind.css, `/v1/command`) | ídem |
| DOM | 2662 elementos | 2717 |

El desplazamiento de diseño viene de `main#views` (las tarjetas se mueven a su vista con JS
después de pintar), de `section#player` y de un párrafo de Niveles. Además: un 404 de `/favicon.ico`
en la consola y una redirección de `/?t=…` a `/` (160 ms).

## 9. Lo que no se pudo medir

- Lectores de pantalla reales (NVDA, Orca, VoiceOver): si las regiones vivas que se reescriben 1/s
  se anuncian de verdad queda **INFERIDO**.
- Firefox y Safari (solo Chromium): `scrollable-region-focusable` y los anillos `auto` pueden
  comportarse distinto.
- Un teléfono real con el dedo (solo emulación de 390×844 con `has_touch`); el teclado y el
  rendimiento solo se midieron en PC.
- La alerta de «parlante atrasado» (`#chip-alert`): la sala simulada nunca produjo un parlante
  atrasado, así que no se midió cuántos clics lleva verla.
- El diálogo «Conectar teléfono» (oculto en el panel local) y la PWA por HTTPS.
- Las otras organizaciones (`?layout=inicio`, `lateral`, `pagina`): solo la de siempre.
- Rendimiento con GPU en un navegador con interfaz.

## 10. Otras observaciones (no de accesibilidad)

- Después de la calibración simulada la cabecera mostró **«Latencia -27 ms»** (antes «≥ 399 ms»):
  una latencia negativa (`datos/22-auditoria-panel/shots/pc1440-light-escuchar-fold.png`); puede ser propio de la simulación.
- El retardo en «Parlantes en detalle» se muestra como «4.501» (punto y tres decimales) mientras el
  resto del panel usa coma (`datos/22-auditoria-panel/shots/pc1920-light-parlantes-fold.png`).
- En una captura de la primera pasada los logs aparecieron repetidos; no se reprodujo (`probe2.py`:
  32 líneas, 32 distintas). No se cuenta como hallazgo.

## Hallazgos priorizados (PC primero)

Ordenados por gravedad × frecuencia de uso en PC. «Frecuencia» es cuántas veces por sesión toca esa
parte quien usa el panel; «gravedad», cuánto impide la tarea.

| # | Hallazgo | Gravedad | Frecuencia | Evidencia |
|---|---|---|---|---|
| 1 | **El volumen de la cabecera no tiene nombre accesible**: el `<label>` apunta al `<output>` que va antes del `<input>`; un lector anuncia «deslizador, -20» sin decir qué es | alta (lector de pantalla) | la más alta: el control más usado, en todas las pantallas | §5; `datos/22-auditoria-panel/aria-pc1440-escuchar.yaml` l. 10; `datos/22-auditoria-panel/probe2.json` `volume_name`, `label_mismatch` |
| 2 | **Parlantes: 13 controles se recrean cada segundo y el foco se pierde** («Desconectar», «Emparejar y conectar», «Usar «Automático»», Principal/Ambiental de Espacial): con el teclado el foco cae en `body` | alta (teclado y lector) | media (Parlantes, al conectar o repartir roles) | §4.2; `datos/22-auditoria-panel/probe2.json` `replaced`, `focus_loss` |
| 3 | **Deslizadores de «Parlantes en detalle» de 33 px** a 1440 y a 1920: 0,36 px por paso de volumen, 0,83 de pan; ajustar un parlante con el mouse es casi imposible y el contenido no crece más allá de 1248 px | alta (usabilidad en PC) | media-alta (el ajuste por parlante) | §3; `datos/22-auditoria-panel/probe3.json`; `datos/22-auditoria-panel/shots/pc1920-light-parlantes-fold.png` |
| 4 | **Tema oscuro: texto gris (`text-zinc-500`, #71717b) a 3,67–3,70:1** en las pestañas, encabezados de tabla, escala del medidor y logs; 5 nodos solo en la barra de pestañas de cada pantalla, 286 en Diagnóstico | media | alta si el sistema está en oscuro (toda pantalla) | §1.2; `datos/22-auditoria-panel/results.json` `pc1440-dark.*.axe`; `datos/22-auditoria-panel/shots/pc1440-dark-*` |
| 5 | **Bordes de todos los campos a 1,47–1,72:1** (los `select` «Fuente» y «Modo» de la cabecera incluidos): no se distingue dónde está el campo | media | alta | §2.1; `results.json` `header_graphics`, `graphics` |
| 6 | **Ayudas `(?)` no cumplen 1.4.13**: Escape no las cierra y se esconden al llevar el mouse al texto (hueco de 8 px); además su anillo de foco es 2,71:1 en claro | media | media (19 en Cadena y Ajustes, donde se aprende el panel) | §4.2; `datos/22-auditoria-panel/probe2.json` `help_popup`; §2.3 |
| 7 | **Medidores en claro**: zona ámbar contra la parte vacía 1,69:1 y verde 2,60:1; pista vacía contra la tarjeta 1,27:1; espectro 2,46:1; correlación 1,52–1,92:1. El número al lado compensa, la lectura de un vistazo no | media | alta (Escuchar, siempre a la vista mientras suena) | §2.1 |
| 8 | **Tabla de Servicios cortada** a 1440 y 1920 (609 px en 582 px): los «Reiniciar» quedan fuera y la columna parte palabras; Diagnóstico deja Servicios en media columna | media | baja-media (cuando algo falla) | §7.1; `datos/22-auditoria-panel/shots/w1440-diagnostico-servicios.png`, `datos/22-auditoria-panel/probe3.json` |
| 9 | **Aviso «Deshacer» de 10,4 s que no se detiene con el mouse**, deja el foco en `body` (Deshacer a 13 Tabs) y tapa «Tono»/«Silenciar» de un parlante | media | baja-media (al borrar) | §4.2; `datos/22-auditoria-panel/probe2.json` `undo` |
| 10 | **Espacio en PC**: cabecera fija de 161 px (18 % de 900), contenido cortado en 1248 px (35 % sin usar a 1920), Cadena de 4,1 pantallas de alto, A/B con 345 px en blanco y Presets con 187; A/B necesita ≥ 7 acciones y su «Empezar» queda bajo el pliegue a 1440×900 | media (eficiencia) | alta | §7; `datos/22-auditoria-panel/shots/pc1440-light-*-full.png`, `datos/22-auditoria-panel/shots/pc1920-light-*-fold.png` |
| 11 | **Cuatro regiones vivas (`role=status`) reescritas 60 veces por minuto con el mismo texto** (`#monitor-state`, `#room-hint`, `#engine-state`, `#radio-note`): riesgo de anuncios repetidos (**INFERIDO**) | media (lector) | alta (Escuchar con el monitor encendido) | §5; `results.json` `pc1440-light.*.watch.live` |
| 12 | **Desplazamiento de diseño al cargar**: CLS 0,513 (escritorio) y 0,788 (móvil) | baja-media | una vez por carga | §8.2; `datos/22-auditoria-panel/lighthouse-desktop.json` |
| 13 | Blanco sobre sky-600 a 4,06:1 en «Calibrar» y «Aplicar sugerencia» (texto de 14 px) | baja-media | media | §1.2 |
| 14 | `aria-label` en `div` sin rol (espectro, correlación, plano de la sala): el nombre se pierde; `#logs` y las tablas desplazables solo son enfocables en Chromium | baja | baja | §1.2 |

Lo que está bien, también medido: axe en claro deja solo 3 violaciones en las 6 pantallas; 2.5.8
(24 px) pasa en todas las pantallas y ventanas; todo control es alcanzable con Tab salvo los 13 que
se recrean; ningún foco queda bajo la cabecera fija; los deslizadores responden a las flechas; los
medidores son `role=meter` con valor y texto; `prefers-reduced-motion` y los dos «Pausar» funcionan
(sin nada sobre 3 cambios por segundo); el hilo principal está ocupado 2,6–6,1 % sin tareas largas.
