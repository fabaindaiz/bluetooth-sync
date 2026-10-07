# 21 · F1/E1 en la SuperMini nRF52840: el BIG de 4 BIS, su alineación y quién lo alimenta

**Preguntas** (plan `docs/superpowers/plans/2026-10-07-auracast-supermini-e1-e5.md` §1, más lo que agregó
[research/02](../02-le-audio-auracast-linux.md) §7.2). Todas se responden **sin parlantes**:

0. **Las placas:** qué bootloader traen (`INFO_UF2.TXT`), si tienen cristal de 32 kHz.
1. **F1:** ¿el nRF52840 con la SDC crea un BIG de 4 BIS en 48_4 (120 B) y en 48_2 (100 B)? ¿Qué NSE, BN,
   PTO e IRC elige? ¿Qué contesta a `Encryption=1`?
2. **En el aire:** con otra SuperMini como receptor, ¿llegan los 4 BIS, y cuántas SDU se pierden?
3. **Alineación:** ¿los 4 BIS llevan el mismo cuadro en el mismo evento? Con Bumble tal cual (modo
   número de secuencia, que la SDC dice que no sincroniza) y con el modo timestamp.
4. **Deriva:** entre los relojes de dos placas, y entre el del PC y el de la placa (ppm).
5. **Alimentación:** ¿Python mantiene 4 SDU cada 10 ms sin dejar a la SDC sin datos, con el equipo
   tranquilo y cargado?

**Veredicto (MEDIDO, 2026-10-07, placas A y C, imagen `rc`): SÍ, la SuperMini sirve como controlador
Auracast de 4 BIS.**
- La SDC crea el BIG de 4 BIS en 48_4 y 48_2, con **NSE 2 / IRC 2 / PTO 0** (una retransmisión), igual en
  las dos placas, y rechaza el cifrado (0x25).
- La otra placa lo recibe entero.
- **Los 4 BIS van alineados**, medido de dos formas independientes:
  - en la fuente, 80 arranques, en los modos secuencia y timestamp, con y sin carga;
  - en el aire, 2 corridas de 60 s más 20 + 19 arranques, sin un solo cuadro corrido.

Hay tres condiciones medidas para el emisor:
- **≥ 40 ms de colchón** (con 20 ms y el equipo cargado, la cola se vació 6 veces en un minuto);
- **seguir el reloj del controlador**, que va decenas de ppm por delante del PC;
- **fijar siempre el tiempo reservado del BIG al abrir**, porque sobrevive al HCI Reset.

Actualización de la noche (§6, MEDIDO en C):
- **la placa C tiene cristal de 32 kHz**, estable a 0,04 ppm, y el controlador ya corre con él;
- **la deriva inconsistente de §4 era el RC**, que salta ±26 ppm cada ~6 s;
- el controlador entra al bootloader por software (1200 baudios).

Actualización del cierre (§6, §8 y §10):
- la placa A también tiene cristal de 32 kHz;
- **el cristal de 32 MHz de las dos placas corre rápido** (A +79, C +64 ppm frente al reloj crudo del PC, que
  NTP corrige solo 2 ppm): fuera de los ±50 ppm;
- entre las dos placas con cristal la deriva es de 20,5 ppm y cuadra con lo medido en cada una.

Quedan abiertos:
- si un JBL acepta un emisor con el cristal 60–80 ppm desviado (E3);
- el alcance y la robustez (el emisor autónomo está listo);
- que el colchón se agote cerca del minuto 18 a lazo abierto (la corrida se cortó a los 16,7 min);
- la prueba LC3 de punta a punta.

Apareció un error de Bumble (`LE Read ISO TX Sync`), corregido dentro del proyecto (d-7c8794-570a77).

## Entorno

- Equipo `HP-O16` (i5-11400H), CachyOS, kernel 7.2.9-1-cachyos. Sin parlantes.
- **Firmware:** `samples/bluetooth/hci_uart` de Zephyr compilado dentro de **NCS v3.4.1** (`nrf` en
  `b20f8619`, 2026-09-17; toolchain `8285d8ad56`), placa `promicro_nrf52840/nrf52840/uf2`, con los
  fragmentos de `firmware/supermini/hci_uart_iso/`: `iso.conf` (emisor y receptor en una sola imagen) y,
  si no hay cristal, `rc.conf` (RC calibrado, declarado a 500 ppm). La SoftDevice Controller es la de NCS
  (`BT_LL_SOFTDEVICE`). Imagen de 207 KB de flash y 82 KB de RAM; se carga en `0x26000`, detrás de la
  SoftDevice S140 que trae el bootloader de tipo nice!nano (INFERIDO hasta leer `INFO_UF2.TXT`).
- **Valores compilados (MEDIDO en `.config`):** `BT_CTLR_ADV_ISO=y`, `BT_CTLR_SYNC_ISO=y`,
  `ADV_ISO_STREAM_MAX=4`, `SYNC_ISO_STREAM_MAX=4`, `PHY_2M=y`, `SDC_ISO_TX_HCI_BUFFER_COUNT=10`,
  `SDC_ISO_TX_PDU_BUFFER_PER_STREAM_COUNT=3` (por defecto: limita el PTO), `SDC_BIG_RESERVED_TIME_US=1600`,
  `ADV_DATA_LEN_MAX=251`, `ADV_SET=2`; reloj: `K32SRC_XTAL` a 50 ppm (o `K32SRC_RC` + calibración a 500 ppm).
- **Host:** Bumble 0.0.235 (el fijado en `host/pyproject.toml`), Python 3.12 del entorno de hatch.
  Sondas en `probes/21-supermini-iso/` (se borran cuando el resultado quede aquí, d-7c8794-3208b7).
- **Placas:** A y C, por su número de serie USB (`00-inventario-hp-o16.md`). Cada resultado anota la placa.
  La segunda placa se llamó "B" mientras se medía; el usuario la rotuló **C**. Los nombres de los archivos de
  datos (`unitB`, `AtoB`, `BtoA`) conservan la letra B, que es esta misma placa C.
- Sin cambios de sistema: `uucp` y ModemManager ya estaban bien; montar el disco UF2 es de usuario.

## Método

| Paso | Qué | Sonda | Unidades |
|---|---|---|---|
| 0 | Doble reset → disco UF2 → leer `INFO_UF2.TXT`. Grabar la imagen de cristal y ver si el HCI responde (INFERIDO: si el cristal no está, MPSL espera al LFCLK y el HCI no contesta; se confirma si después la imagen de RC sí responde en la misma placa). Si no responde, grabar la de RC | `info.py` | A, C |
| 1 | Crear el BIG con `num_bis=4`, `max_sdu` 120 y 100, RTN 4 y 2, latencia 60 ms, 2M; leer `LE Create BIG Complete`. Repetir con `--encrypt` | `tx_big.py` (sin `--seconds`) | A; repetir en C |
| 2–4 | A emite 60 s; C recibe y cuenta SDU, huecos, contadores por BIS y la pendiente de sus timestamps | `tx_big.py --seconds 60` + `rx_big.py --seconds 50` | A→B, luego B→A |
| 3b | Arranques repetidos: 20 ciclos de crear BIG → 3 s → leer `LE Read ISO TX Sync` por BIS → terminar, en `--mode seq` y en `--mode ts` | `tx_big.py --repeat 20 --seconds 3` | A |
| 5 | Lo mismo que 2 con el equipo cargado (12 procesos que ocupan la CPU) y `--depth` 2 y 4 | `tx_big.py` | A |

**Cómo se lee la alineación**, con dos mediciones independientes, como pide el CLAUDE.md:
- **En la fuente:** `LE Read ISO TX Sync` da, por cada BIS, la secuencia y el timestamp de la última SDU
  enviada. Como el contador de cuadro es igual en los 4 BIS, `(ts_i − psn_i·10 ms) − (ts_1 − psn_1·10 ms)`
  vale 0 si están alineados, o un múltiplo de 10 ms si un BIS va corrido.
- **En el aire:** el receptor compara, para un mismo evento del BIG (la misma secuencia recibida en los 4
  BIS), el contador que viene en cada SDU.

Un desfase verdadero se ve en las dos. Si aparece en una sola, es un artefacto de la sonda.

**Criterios fijados antes de medir:**
- F1 se da por bueno si el BIG de 4 BIS se crea en las dos unidades, y en las dos con la misma NSE, PTO e
  IRC para la misma petición.
- "Alineado" significa desfase 0 en los 20 arranques, en las dos mediciones. Con un solo arranque desfasado,
  el modo queda descartado para E5.
- La alimentación se da por buena si, en 60 s, el mínimo de SDU en cola por BIS nunca llega a 0 y el
  receptor no ve huecos atribuibles al emisor.

## Resultado

### 0 · Las placas (MEDIDO, 2026-10-07)

| Unidad | Serie USB (`ID_SERIAL_SHORT`) | Bootloader (`INFO_UF2.TXT`) | Flash de fábrica | Imagen grabada |
|---|---|---|---|---|
| A | `25351136B0E21CB1` | UF2 Bootloader 0.6.0, Model nice!nano, Board-ID `nRF52840-nicenano`, **SoftDevice S140 6.1.1**, 2021-06-19 | solo la SoftDevice (`0x1000`–`0x25E00`): **no traía aplicación**, por eso arranca directo en modo UF2 | `rc` (sha256 `348e5b67…`): funciona |
| C | `893169D3E93C0F4E` | idéntico al de A | idéntico al de A (el mismo sha256 `9d2ac4f2…`) | `rc`, la misma imagen: se colgó en el reinicio después de grabar; funciona tras desconectarla (abajo) |

- La imagen se carga en `0x26000`, justo detrás de la S140: coincide (VERIFICADO con `INFO_UF2.TXT` y el
  mapa de `CURRENT.UF2`). El respaldo del flash de fábrica quedó **fuera del repositorio**, en
  `~/supermini-respaldo/` (es binario de terceros).
- Al grabar, la placa se enumera como `2fe3:0004` "Zephyr Project CDC ACM serial backend", con el mismo número
  de serie, en `/dev/ttyACM0` (`root:uucp`). Montar el disco UF2 con `udisksctl mount` no pidió root.
- **Cristal de 32 kHz: sin determinar.** Se empezó por la imagen de RC, porque con la de cristal, si este
  falta, la placa podría colgarse antes del USB (INFERIDO), y para volver al bootloader hace falta un doble
  reset a mano.

**La placa C no arrancó con la misma imagen (MEDIDO, 13:05).** El bootloader recibió el UF2 y se reinició
(los errores de E/S del disco al reiniciar son normales: también salieron con A). Después, el kernel ve
el dispositivo pero no puede leer su descriptor (`usb 3-4: device descriptor read/64, error -110`, cada
~16 s, sin parar), cuando A se enumeró en 1 s. El firmware enciende el USB y se cuelga antes de contestar.
El kernel desistió a las 13:07 (`unable to enumerate USB device`). **El usuario la desconectó y la volvió a
conectar a las 13:13, y se enumeró al instante** (`2fe3:0004`, Zephyr, su número de serie) y funcionó en todo
lo que sigue. O sea, el firmware está bien. Lo que se colgó fue el **reinicio en caliente** que hace el
bootloader después de grabar, que en A no falló. La causa sigue sin determinar; la regla práctica es
**desconectar y volver a conectar después de grabar** si el puerto no aparece. Conexión: A por un puerto USB-A,
C por USB-C (sin hub).

### 1 · F1: el BIG que elige la SDC (MEDIDO, unidad A, imagen `rc`)

`info.py`: HCI versión 17, company 89 (Nordic), `ISOCHRONOUS_BROADCASTER` y `SYNCHRONIZED_RECEIVER` en las
LE features; `LE Create BIG`, `LE BIG Create Sync`, `LE Read ISO TX Sync` y `LE ISO Transmit Test` soportados;
**10 búferes ISO de 255 B** (LE Read Buffer Size V2).

4 BIS, SDU cada 10 ms, sin cifrar, salvo donde se indica:

| Petición | NSE | BN | PTO | IRC | Sync delay | Latencia de transporte |
|---|---|---|---|---|---|---|
| 120 B (48_4), RTN 4, latencia 60 ms, 2M | 2 | 1 | 0 | 2 | 5242 µs | 5,24 ms |
| 120 B, RTN 2 | 2 | 1 | 0 | 2 | 5242 µs | 5,24 ms |
| 120 B, RTN 4, latencia 20 ms y 100 ms | 2 | 1 | 0 | 2 | 5242 µs | 5,24 ms |
| 100 B (48_2), RTN 4 y RTN 2 | 2 | 1 | 0 | 2 | 4602 µs | 4,60 ms |
| 120 B, RTN 4, **tiempo reservado 0 µs** (VS 0xfd18) | 3 | 1 | **4** | 2 | 7938 µs | **47,9 ms** |
| 120 B, RTN 4, tiempo reservado 800 o 2500 µs | 2 | 1 | 0 | 2 | 5242 µs | 5,24 ms |
| 120 B, RTN 4, **1M** | **1** | 1 | 0 | 1 | 4610 µs | 4,61 ms |
| 120 B, RTN 4, **2 BIS** (reservado 1600 o 800 µs) | 5 | 1 | 1 | 2 | 6590 µs | 36,6 ms |
| 120 B, RTN 4, 2 BIS, reservado 2500 µs | 4 | 1 | 2 | 2 | 5242 µs | 45,2 ms |
| 120 B, RTN 4, **1 BIS** | 5 | 1 | 1 | 2 | 3220 µs | 33,2 ms |
| 120 B, RTN 4, **`Encryption=1`** | — | — | — | — | — | **rechazado: 0x25 `ENCRYPTION_MODE_NOT_ACCEPTABLE`** |

- **Repetición (MEDIDO, corrida 2, misma unidad, datos en `datos/21-supermini-iso/1-f1-sweep-unitA-run2.jsonl`):**
  iguales todas las filas. La única que difirió en la primera pasada fue la de 2 BIS, y la causa quedó
  medida: **el tiempo reservado del VS 0xfd18 sobrevive al HCI Reset** que hace Bumble al abrir cada
  sesión, y solo se borra al reiniciar la placa (`1-reserved-time-persistence-unitA.jsonl`: poner 0 y abrir
  dos sesiones nuevas sin tocarlo sigue dando NSE 3). En la primera pasada, la fila de 2 BIS heredó los
  2500 µs del paso anterior. Es justo el tipo de parámetro "que no debería importar" que el CLAUDE.md
  pide vigilar: **un emisor tiene que fijar ese valor siempre al abrir, no darlo por defecto.** La placa
  quedó en 1600 µs.
- **Placa C (MEDIDO, `1-f1-sweep-unitB.jsonl`): las 12 filas idénticas a las de A**, con el mismo `info.py`.
  El criterio de F1 (las dos unidades, los mismos NSE, PTO e IRC) **se cumple**.
- **El BIG de 4 BIS se crea, en 48_4 y en 48_2.** La SDC limita el RTN pedido: con 4 BIS en 2M cada PDU sale
  **dos veces** (NSE 2, IRC 2), es decir, **una retransmisión, sin pretransmisión**. El cálculo previo decía
  que RTN 4 no cabía (INFERIDO en research/02 §7, afirmación 3); ahora está MEDIDO.
- Cada subevento dura ≈ 655 µs (5242 µs / 8 subeventos), cerca de los ~674 µs estimados.
- Para ganar repeticiones hay que quitarle tiempo al periodic advertising (reservado 0 → NSE 3, PTO 4), a
  costa de 48 ms de latencia y quizá de los receptores, que necesitan el BIGInfo del PA: se verá en el paso 2.
- **1M no deja retransmisiones con 4 BIS**: hay que usar 2M.
- **El cifrado se rechaza limpio**: confirma la afirmación 2 de research/02 §7 con lo que dice la SDC.

### Un error de Bumble que apareció en el camino (MEDIDO)

`LE Read ISO TX Sync` no se puede usar con Bumble 0.0.235. La SDC contesta bien, con 12 bytes de parámetros
(`040e0f0161200033010300957a7e05000000`: handle 0x133, psn 3, ts 92174997, offset `000000`), pero Bumble
declara `Time_Offset` de **4 octetos** (`bumble/hci.py` L5709-5712), no puede analizar el paquete
(`unpack_from requires a buffer of at least 13 bytes`), lo descarta, y el comando termina por timeout a los
10 s. La especificación de la SDC (`sdc_hci_cmd_le.h` L3406, `time_offset : 24`) y la de Zephyr
(`hci_types.h` L2241, `offset[3]`) dicen **3 octetos**. Las sondas usan el VS 0xfd17, que da la misma
secuencia y el mismo timestamp. Antes de mandar datos, el comando estándar devuelve `COMMAND_DISALLOWED`
(0x0C), lo esperable. Comprobado el 2026-10-07: el error sigue en `main` de google/bumble (`hci.py`
L5709-5714) y en la última versión publicada (v0.0.235, 2026-09-25), y no hay ningún issue sobre "ISO TX
Sync".

**Corregido dentro del proyecto, sin reportarlo** (pedido del usuario, d-7c8794-570a77):
`host/src/aurasync/bumble_fixes.py`, cuyo `apply()` cambia ese campo a 3 octetos en la tabla de Bumble.
Lo prueba `host/tests/test_bumble_fixes.py` con la respuesta real de arriba. **Verificado con la placa
(MEDIDO, unidad A, `datos/21-supermini-iso/bumble-fix-txsync-unitA.jsonl`):** con el arreglo, el comando
estándar responde, y en 40 lecturas sobre los 4 BIS coincide con el VS 0xfd17. 39 dan la misma secuencia y
el mismo timestamp. La restante va una SDU atrás (secuencia −1, −10 000 µs exactos), porque el VS se mandó
justo después y ya vio la siguiente. `time_offset` vale 0 en todas.

### 2 · En el aire, con la otra placa como receptor (MEDIDO, 2026-10-07 13:13–13:17)

Emisor: `tx_big.py --seconds 75 --mode seq --depth 4` (4 × 120 B, el BIG por defecto). Receptor:
`rx_big.py --seconds 60`, que busca la dirección del emisor, se sincroniza con su periodic advertising, lee
el BIGInfo y se sincroniza con los 4 BIS. Las placas estaban a unos centímetros (RSSI −41 dBm).

| Sentido | BIGInfo leído | SDU recibidas por BIS | Huecos | Inválidas | Eventos comparados | Con el mismo cuadro en los 4 BIS |
|---|---|---|---|---|---|---|
| A → C | NSE 2, IRC 2, PTO 0, 120 B, 2M, sin cifrar | 6005 / 6005 / 6005 / 6006 | 0 | 0 / 0 / 0 / 1 | 5980 | **5980** |
| C → A | igual | 6005 × 4 | 0 | 0 | 5981 | **5981** |

**Arranques repetidos en el aire (MEDIDO, 13:17–13:26, A emite, C recibe; `datos/…/2-air-repeat/`):** en
cada arranque, la A crea el BIG y transmite 12 s; la C se sincroniza de cero y recibe 5 s.

| Modo | Arranques con receptor sincronizado | Con los 4 BIS alineados | Eventos comparados | Huecos | Inválidas |
|---|---|---|---|---|---|
| número de secuencia | 20 / 20 | **20** | 9998 | 0 | 13 |
| timestamp | 19 / 20 | **19** | 9497 | 0 | 14 |

- Las inválidas son **una por arranque** (en 13 de 20), casi siempre en el BIS 4, y siempre en el primer
  segundo después de sincronizar; después, ninguna. Es el arranque del receptor (INFERIDO: la ruta de datos
  del último BIS se configura última), no la transmisión.
- En el arranque 18 del modo timestamp, el receptor no escribió nada, mientras el emisor transmitió normal
  (`ts-c18-tx.jsonl`). La salida de error estaba descartada: **la causa no se conoce**. Se cuenta como falla
  de la sonda, no como un desfase.
- En la fuente, las 480 lecturas de estos 40 arranques dieron desfase 0.

- **El BIG sale al aire, y otra nRF52840 lo recibe entero**, a corta distancia y sin interferencia
  provocada. No es una prueba de alcance ni de robustez.
- **En el aire, los 4 BIS llevan el mismo cuadro**, que es la segunda medición independiente de la
  alineación. Coincide con la de la fuente (§3b).
- El periodic advertising (100 ms) y el BIGInfo llegan con el tiempo reservado por defecto (1600 µs).

### 3b · Alineación en la fuente, en arranques repetidos (MEDIDO, placa A)

Las corridas 3b, 4 y 5 se hicieron con el tiempo reservado en 2500 µs, heredado del barrido de F1 (ver
arriba). Con 4 BIS da el mismo BIG que el valor por defecto: los 128 BIG creados en esas corridas fueron
NSE 2, PTO 0, IRC 2, PDU de 120 B (comprobado en sus registros).

20 arranques por modo: se crea el BIG, se transmite 3 s con 120 B, se leen la secuencia y el timestamp de
la última SDU de cada BIS una vez por segundo, y se termina el BIG.

| Condición | Modo | Lecturas con desfase 0 en los 4 BIS | Otros desfases | Mínimo en cola por BIS después del 1.er s |
|---|---|---|---|---|
| equipo tranquilo (load 0,7–1,4) | número de secuencia (el de Bumble) | 60 / 60 (20 arranques) | ninguno | 1 |
| equipo tranquilo | timestamp (VS 0xfd17 + el mismo `Time_Stamp`) | 60 / 60 | ninguno | 1 |
| 12 procesos ocupando la CPU (load hasta 17,6) | número de secuencia | 60 / 60 | ninguno | 1 |
| 12 procesos ocupando la CPU | timestamp | 60 / 60 | ninguno | 1 |

- **En la fuente, los 4 BIS salieron alineados en los 80 arranques, en los dos modos y con o sin carga.**
  El modo secuencia no está garantizado por la SDC (research/02 §7.2, afirmación 9), y aun así no falló: Bumble
  escribe las 4 SDU de un cuadro seguidas, y llegan antes del primer evento. La segunda medición
  independiente, en el aire, se hizo después (§2) y también dio 0 desfase.
- La cola en 0 durante el primer segundo de cada arranque es el arranque mismo (empieza vacía), no una falla.

### 4 · Deriva de los relojes (MEDIDO, pero **sin un número firme**)

**Controlador frente al PC.** Se ajustó con mínimos cuadrados el `tx_ts` del emisor contra el
`time.monotonic` del host, con una lectura por segundo (`drift.py`):

| Corrida | Unidad | Duración | Controlador frente a `CLOCK_MONOTONIC` | Mitades |
|---|---|---|---|---|
| `4-drift-pc-unitA` (uso normal) | A | 600 s | +79 ppm | +81 / +70 |
| `5-load-feed-depth4` (con carga) | A | 60 s | +119 ppm | +122 / +113 |
| `5-load-feed-depth2` (con carga) | A | 60 s | +142 ppm | +123 / +178 |
| `2-air-AtoB-tx` | A | 75 s | +51 ppm | +70 / +34 |
| `2-air-BtoA-tx` | C | 75 s | +48 ppm | +47 / +32 |

**Entre placas, en el aire.** Del receptor se ajustó su timestamp de cada evento contra la secuencia:

| Sentido | Emisor frente a receptor |
|---|---|
| A → C | −6,2 ppm (los 4 BIS coinciden en ±0,001) |
| C → A | −21,6 ppm |

- **Las dos mediciones entre placas no son consistentes:** al invertir los papeles el signo debería
  invertirse, y no se invirtió. O el método tiene un sesgo que no entiendo (por ejemplo, cómo fecha la SDC
  receptora un evento que sigue el ancla del emisor), o los RC derivaron decenas de ppm en dos minutos. **No
  se da por buena una deriva entre placas.**
- **Frente al PC, el controlador va entre +48 y +142 ppm según la corrida**, y las mitades de una misma
  corrida difieren hasta 55 ppm. La latencia variable del host (residuo de 0,3–0,8 ms) sesga los ajustes
  cortos; la corrida de 10 min (+79) es la más estable. NTP corrige al reloj del PC en +2,9 ppm (`adjtimex`,
  `timedatectl timesync-status`), así que el desfase sería del controlador, pero eso supone que NTP ya
  convergió: INFERIDO.
- **Lo que sí queda (INFERIDO de lo medido):** controlador y PC difieren en **decenas de ppm, y esa
  diferencia cambia con el tiempo**. La sonda no lo sufre, porque escribe al ritmo de las completaciones.
  Pero el audio real llega al ritmo del PC, y a 80 ppm un colchón de 40 ms se vacía o se llena en ~8 min.
  **El emisor Auracast tiene que seguir el reloj del controlador** (remuestrear guiándose por 0xfd17, o por
  el ritmo de las completaciones), igual que hoy el motor sigue a cada parlante A2DP.
- **Para medirlo bien hace falta** una corrida larga (≥ 30 min) por unidad, con la imagen de cristal si la
  placa lo tiene, y el timestamp de las completaciones en vez del de una lectura por comando.

**Revisión del 2026-10-07 por la tarde (MEDIDO): las corridas cortas dispersan cientos de ppm.** Los 39
arranques repetidos en el aire (5 s cada uno, 50 timestamps del receptor, sin pasar por el PC) dan una deriva
de A frente a C entre **−188 y +144 ppm**, sin una tendencia en los 9 minutos. Si los timestamps fueran
estables, 5 s de datos fijarían la pendiente en ±0,2 ppm, así que lo que se mueve es el reloj. Encaja con el
RC: la imagen lo recalibra **cada 4 s** (`CLOCK_CONTROL_NRF_CALIBRATION_PERIOD=4000`, con un salto como mucho
si la temperatura no cambió, `.config` compilado), y entre una calibración y la siguiente el RC se aparta de
su valor. Con dos placas RC, cada ventana corta cae en un tramo distinto de los dos relojes. **Hipótesis
(INFERIDO), no comprobada:** la deriva "de fondo" son decenas de ppm, con saltos de ±150 ppm entre
calibraciones. Se comprueba guardando cada timestamp (para ver escalones cada 4–8 s) o midiendo el reloj de
32 kHz contra el cristal de 32 MHz dentro de la propia placa, y comparando con la imagen de cristal.

### 6 · Los relojes de 32 kHz, medidos dentro de la placa (MEDIDO, placa C, 2026-10-07 15:3x–16:0x)

**Instrumento:** `probes/21-supermini-iso/clockprobe/` (Zephyr, sin Bluetooth). Hace dos cosas:
- **Antes de que el sistema arranque el reloj lento**, intenta encender el cristal de 32 kHz (LFXO) con un
  límite de 2 s. Si arranca, lo mide 1 s contra el cristal de 32 MHz (HFXO) y lo apaga. **Sin cristal no se
  puede colgar.**
- Después mide, **por hardware**, cada segundo del reloj lento contra el HFXO: RTC2 COMPARE0 → PPI →
  TIMER1 CAPTURE0 a 16 MHz.

Entra al bootloader cuando el PC pone el puerto a 1200 baudios (`stty -F /dev/ttyACMn 1200`). La lectura
la hace `readclock.py` (sello `time.monotonic` por línea) y el análisis, `clock_stats.py`. "ppm" positivo
quiere decir que el reloj lento corre rápido frente al HFXO.

**¿Tiene cristal de 32 kHz? Sí (MEDIDO).** Respuesta del primer arranque: `X 1 360 -874 00010001`.
- El LFXO arrancó en **360 ms**.
- `LFCLKSTAT` = `0x00010001`: corriendo, con fuente **cristal**.
- Frecuencia: **−8,7 ppm** frente al HFXO.

**La placa A también lo tiene (MEDIDO, 17:04):** `X 1 376 -437`, es decir, arranca en 376 ms y va a −4,4 ppm
frente a su HFXO. Su RC calibrado salta igual que el de C: desvío 32 ppm, 146 saltos de más de 20 ppm en
580 s (`8-clock-rc-raw-unitA.txt`). Un oscilador sin cristal no da 32 768 Hz con 9 ppm de error. Lo que el usuario vio como "una pieza muy
pequeña, blanca con un cuadrado en medio" es, con toda probabilidad, ese cristal (INFERIDO: base cerámica
con tapa metálica). **La hipótesis previa de que la SuperMini no lo traía era falsa**, para las dos placas.
La línea `X` mide 1 s por sondeo justo cuando arranca el cristal, con ±1–2 ppm de error en los bordes: es
aproximada. Los valores de referencia son los de la medición continua (−9,2 ppm en C).

**El RC calibrado frente al HFXO** (imagen `prj.conf` del clockprobe, la misma recalibración cada 4 s que el
controlador; 589 s, `datos/21-supermini-iso/6-clock-rc-unitC.txt`):

| Medida | Valor |
|---|---|
| media | **−44,4 ppm** |
| desvío | 25,7 ppm |
| mínimo / máximo de un segundo | −115,2 / +25,3 ppm |
| saltos de más de 20 ppm entre segundos seguidos | **101 en 589 s** (uno cada ~6 s, como la recalibración cada 4–8 s) |
| cuánto varía la media según el largo de la ventana | **114 ppm** en ventanas de 4 s · 70 en 10 s · **18 en 60 s** |

- **Esto explica §4:** con el RC, una medición de deriva de 5 s puede errar más de 100 ppm, y una de 60 s
  ~18 ppm. Las corridas cortas del aire (−188 a +144 ppm) y la falta de simetría A↔C eran este ruido, no el
  método.

**Con el cristal** (`clockprobe` + `xtal.conf`; 577 s, `6-clock-xtal-unitC.txt`, 15:44–15:54, load 0,5):
el reloj lento va a **−9,23 ppm** frente al HFXO, con un **desvío de 0,04 ppm**, entre −9,37 y −9,12, y
**ningún salto**. La media varía 0,06 ppm en ventanas de 4 s y 0,04 en ventanas de 60 s. El segundo
arranque repitió la prueba del cristal: `X 1 373 -874`. **Con el cristal, el reloj es unas 600 veces más
estable que con el RC** (0,04 frente a 25,7 ppm de desvío).

**El controlador con cristal (MEDIDO):** grabado en C (`hci_uart_iso` + `iso.conf`, sha256 `99e4e31f…`).
`info.py` da las mismas capacidades, y el BIG de 4 × 120 B, RTN 4, sale igual (NSE 2, IRC 2, PTO 0). El
toque a 1200 baudios **desde el propio controlador** lo pasó al bootloader en ~1 s (kernel: `239a:00b3` a
las 15:55:17) y volvió a arrancar. **La placa C ya no necesita doble reset.**

**El HFXO de C frente al PC:** **+67,6 ppm** en 588 s (ajuste de las ticks acumuladas contra la hora de
llegada de cada línea; se descartan las 30 primeras, que llegan en ráfaga al abrir el puerto). Es mucho
para un cristal (lo típico es ±10–40 ppm, de memoria: SUPOSICIÓN), y es del mismo orden que el
"controlador frente al PC" de §4. La segunda corrida, independiente (con cristal), dio **+70,3 ppm**. **Resuelto
en §8: el desvío es del cristal de 32 MHz de la placa, no del PC.** Se separa midiendo el HFXO de A, o contra otra referencia.

### 7 · Alimentar al ritmo del PC, a lazo abierto (MEDIDO, placa C con cristal emitiendo, A recibiendo)

`feed_pc.py --mode open --prefill 6`: precarga 6 cuadros, y después escribe 4 SDU (contador, 120 B) cada
10 ms de `time.monotonic`, **sin mirar al controlador**, como llegaría el audio real. La A escucha con
`rx_big.py`, que ahora además cuenta los cuadros que faltan o se repiten en el contenido de cada BIS.

**10 min (16:21–16:31, `7-open-*.jsonl`):**
- en el aire llegaron **60 055 SDU por BIS, sin huecos** y con los 4 BIS alineados en 59 823 eventos;
- se perdieron 2 SDU en el BIS 2 y 2 en el BIS 3, las mismas que el receptor marcó como inválidas: son
  **pérdidas de radio**, unas 3 de cada 100 000, con una sola retransmisión y las placas a centímetros.

**Dos lecturas de esta corrida que no valen (y por qué):**
- **El "controlador frente al PC = −1,7 ppm" es un artefacto.** En el modo de número de secuencia, el
  timestamp que devuelve 0xfd17 para la SDU *k* es el instante del evento *k*. Como *k* lo avanza el reloj
  del PC, el ajuste da pendiente 1 por construcción. La deriva solo se mide así cuando *k* lo avanza el
  controlador (§4, `tx_big.py`).
- **El contador de "underruns" del emisor no sirve.** La SDC da por completada una SDU al pasarla de su
  búfer HCI al suyo interno, no al transmitirla, así que "enviadas − completadas" queda casi siempre en 0–2,
  aunque haya 60 ms en cola. **Lo que se queda sin colchón solo se ve en el receptor.**

**La predicción que se está probando:** con el reloj ISO de C unos **+55 ppm** por delante del PC
(+64 ppm del cristal de 32 MHz, §8, y −9 del cristal de 32 kHz frente a él, §6), el controlador consume 55
µs por segundo más de lo que el PC entrega, y el colchón de 60 ms se acaba en **~18 min**. Desde ahí deberían
faltar cuadros, uno cada ~3 min.

**La corrida de 26 min (16:46, `7b-open-long-*.jsonl`) quedó cortada:** el doble reset que el usuario hizo en
la placa A a las 17:02 detuvo el receptor a los **~16,1 min** (96 534 SDU por BIS), antes del minuto ~18 en
que se esperaba ver faltar cuadros. Hasta ahí hubo **0 cuadros perdidos por falta de colchón** y 1 pérdida
de radio en el BIS 1 (inválida y con un salto en el contador). **La predicción sigue sin probar.**

### 8 · ¿El reloj de quién? El del PC frente al de la placa (MEDIDO)

- **`CLOCK_MONOTONIC` frente a `CLOCK_MONOTONIC_RAW`** durante 3 min a las 16:3x: **−1,0 ppm**. NTP
  (systemd-timesyncd) le corregía al PC +2,9 ppm a las 13:0x, con un desfase de −50 ms, y −2,0 ppm a las
  16:3x, con +4 ms. Así que el reloj del PC se mueve unos pocos ppm, **no decenas**.
- **El cristal de 32 MHz de C frente a `CLOCK_MONOTONIC_RAW`** (clockprobe con cristal, 565 s, cada línea
  sellada con los dos relojes del PC): **+63,7 ppm**; frente a `CLOCK_MONOTONIC`, +65,2. En las tres
  corridas: +67,6, +70,3 y +63,7/65,2.
- **La placa A, igual (MEDIDO, 580 s, 17:04–17:14):** su HFXO va a **+79,0 ppm** frente a
  `CLOCK_MONOTONIC_RAW` (+80,8 frente al monotónico), con NTP corrigiendo −1,9 ppm y un desfase de 0,36 ms.
- **Conclusión:** el desvío es del **cristal de 32 MHz de las placas** (C +64, A +79): dos placas
  independientes con el mismo sesgo apuntan a un **error del diseño de la SuperMini**, no de una unidad
  (INFERIDO). **Fuera del ±50 ppm que pide
  Bluetooth LE** para el reloj activo (de memoria: SUPOSICIÓN, falta la cita del Core Spec). La causa
  probable es que los condensadores de carga del cristal no corresponden (INFERIDO). El nRF52840 no permite
  ajustarlo por software (de memoria: SUPOSICIÓN). **Mi hipótesis de la tarde, que el reloj del PC era el que
  se movía decenas de ppm, era falsa.**
- **Qué importa:**
  - entre los BIS de un mismo BIG no cambia nada, porque comparten el reloj;
  - el emisor tiene que seguir a la placa, como ya decía §4;
  - **queda abierto si un JBL acepta un emisor 60 ppm desviado** (E3);
  - **la placa A tiene el mismo problema**: su cristal de 32 MHz va a +79 ppm (ver abajo).
- **Para las sondas siguientes:** sellar con `CLOCK_MONOTONIC_RAW` (`readclock.py` ya lo hace) y leer la
  corrección de NTP antes y después.

### 9 · El LC3 (MEDIDO en `HP-O16`, i5-11400H; `lc3_cost.py`, `9-lc3-cost-hp-o16.jsonl`)

Con lc3py 1.1.3 (el fijado en `host/pyproject.toml`), 4 canales a 48 kHz, cuadros de 10 ms y 120 B por
canal (48_4), un solo hilo, 3000 cuadros, dos corridas que coinciden:

| | Mediana | p99 | Máximo | Parte de los 10 ms (mediana) |
|---|---|---|---|---|
| Codificar los 4 canales | 0,15 ms | 0,17 ms | 0,38 ms | 1,5 % |
| Decodificar los 4 canales | 0,19 ms | 0,21 ms | 0,41 ms | 1,9 % |

**El códec no es un problema para un emisor en el PC.** Incluye pasar el PCM de numpy a lista, que el
producto evitaría. En la Pico 2 W es otra historia (research/13 §2.5, sin medir). La prueba de punta a
punta (un tono por BIS, decodificado en la otra placa) está más abajo.

### 10 · El emisor autónomo y la deriva entre placas con cristal (MEDIDO, 17:3x)

**El emisor autónomo** (`probes/21-supermini-iso/standalone_tx/`): un firmware que arma **solo**, sin PC,
el mismo BIG de las sondas (4 BIS, 120 B cada 10 ms, dirección F2:00:00:00:00:21, SID 3, contador en cada
SDU), con el cristal de 32 kHz, un LED que cambia de estado cada segundo al transmitir (0,5 Hz) y el toque a 1200 baudios. Sirve para
alejar la placa con un cargador y medir alcance y pérdidas.

- **Lo que salió mal primero:** la primera versión no configuraba la **ruta de datos HCI** de cada BIS
  (`bt_iso_setup_data_path`). La SDC armó el BIG igual, pero descartó las SDU **sin dar ningún error**:
  el receptor recibía un paquete por evento, todos marcados inválidos, y el emisor se trababa con
  `sent=0`. Lo delató la línea de estado que se le agregó.

**En la mesa** (C emitiendo sola, alimentada por el USB del PC; A con el controlador con cristal,
recibiendo 40 s; `10-standalone-bench-rx-unitA.jsonl`):
- **4020 SDU por BIS**, 0 cuadros perdidos, los 4 BIS alineados en los 4015 eventos, 1 SDU inválida;
- RSSI −37 dBm, y 2 CRC malos y 1 paquete no recibido en los contadores del enlace (`LE Read ISO Link
  Quality`, opcode 0x2075, mandado crudo porque Bumble no lo tiene).

**La deriva entre placas, ahora con cristal en las dos: 20,53 ppm** (±0,0003 entre los 4 BIS, 40 s), y
**cuadra con los relojes medidos por separado**:
- **C:** HFXO +64 y LFXO −9,2 frente a él, así que su cristal de 32 kHz va unos +54,8 ppm frente al PC;
- **A:** HFXO +79 y LFXO −4,4, así que +74,6 ppm;
- **la diferencia predicha es 19,8 ppm, contra 20,5 medidos.**

Son dos métodos independientes (el contador del clockprobe contra el PC, y el ancla del BIG en el aire) que
coinciden en 0,7 ppm. Las inconsistencias de §4 eran el RC. Signo: el valor que imprime `rx_big.py` es
positivo cuando el emisor es el más lento.

**La prueba de distancia** (la C alejada con un cargador) quedó para otra sesión por pedido del usuario.

### 5 · Alimentación desde Python con el equipo cargado (MEDIDO, unidad A)

60 s transmitiendo 4 × 120 B cada 10 ms, con 12 procesos ocupando la CPU:

| `--depth` (SDU por BIS en el controlador) | Segundos con el mínimo en cola = 0 | Mínimo en el resto |
|---|---|---|
| 2 (20 ms de colchón) | **6 de 59** | 1 |
| 4 (40 ms de colchón) | 0 de 59 | 3 |

- **Con 20 ms de colchón, el host llega justo**: 6 veces en un minuto, la SDC se quedó sin la siguiente SDU
  de algún BIS en la cola cuando el host volvió a escribir. Si eso dejó un evento vacío (un corte audible) no
  se ve desde el emisor; el receptor no estaba escuchando en esa corrida, así que sigue sin medirse en el aire
  (INFERIDO). **Con 40 ms nunca bajó de 3.**
  Esto pide un colchón de ≥ 40 ms en el emisor, del mismo tipo que el de los parlantes virtuales.

## Cómo reproducirlo

```fish
# compilar (NCS v3.4.1 instalado con nrfutil sdk-manager)
cd ~/ncs/v3.4.1
nrfutil sdk-manager toolchain launch --ncs-version v3.4.1 -- west build -p always \
  -b promicro_nrf52840/nrf52840/uf2 zephyr/samples/bluetooth/hci_uart -d <build> -- \
  -DEXTRA_CONF_FILE=<repo>/firmware/supermini/hci_uart_iso/iso.conf
# grabar: doble reset, copiar <build>/hci_uart/zephyr/zephyr.uf2 al disco que aparece
# sondas, con el Python del entorno de host/
python probes/21-supermini-iso/info.py serial:/dev/ttyACM0
python probes/21-supermini-iso/tx_big.py serial:/dev/ttyACM0 --max-sdu 120 --rtn 4
```
