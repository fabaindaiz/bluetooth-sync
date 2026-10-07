# SuperMini HCI controller: improvement plan and the features the product needs — proposal

**Date:** 2026-10-07 · **Status:** **order approved by the user on 2026-10-07 (d-7c8794-507516)**: §5 as written,
with D2 inside the controller, and boards B and D measured (R7) before choosing the emitter; the worst clock goes
to the sniffer. Originally a proposal for the user's review (asked for on 2026-10-07: "propón un plan de
mejora para el controlador de las placas y funcionalidades requeridas y deseadas para luego integrarlas al
proyecto"). Nothing here is approved or built beyond what §1 says exists. **Inputs:** experiment 21
(`docs/research/experimentos/21-f1-iso-en-la-supermini.md`), research/02 §7–§8, `firmware/supermini/README.md`,
the order agreed on 2026-10-07 (roadmap: panel and diagnostics now, the Auracast emitter backend after E4).

## 1. Where the controller stands (MEASURED unless marked)

| Area | State | Source |
|---|---|---|
| Firmware | Zephyr `hci_uart` (compiled from the SDK, not copied) + SoftDevice Controller, NCS v3.4.1, board `promicro_nrf52840/nrf52840/uf2`; one image emits and receives ISO | `firmware/supermini/hci_uart_iso/` |
| BIG | 4 BIS at 48_4 and 48_2: NSE 2, IRC 2, PTO 0 (one retransmission); 1M PHY leaves none; encryption refused (0x25); identical on boards A and C | exp. 21 §1 |
| Alignment | 4 BIS aligned at the source (80 starts) and on the air (41 starts + 60 s and 10 min runs) | exp. 21 §2–§3b, §7 |
| Radio loss | 2 SDUs of 60 055 lost on 2 of 4 BIS in 10 min, boards a few cm apart | exp. 21 §7 |
| 32 kHz clock | Boards A and C have the crystal: stable to 0.04 ppm; the calibrated RC jumps ±26–32 ppm every ~6 s | exp. 21 §6 |
| 32 MHz crystal | **Both boards run fast**: C +64 ppm, A +79 ppm against the PC's raw clock (NTP corrects the PC by only ~2 ppm), outside the ±50 ppm Bluetooth LE allows (SUPOSICIÓN on the exact spec figure). Two independent boards → likely the SuperMini design (load capacitors, INFERIDO). Both boards also have the 32 kHz crystal; board-to-board drift 20.5 ppm, matching the per-board numbers within 0.7 ppm | exp. 21 §8, §10 |
| Bootloader entry | 1200-baud touch (`src/uf2_touch.c`): no double reset needed; works from the controller | exp. 21 §6 |
| Host | Bumble 0.0.235; local fix for `LE Read ISO TX Sync` (`aurasync.bumble_fixes`, d-7c8794-570a77); the VS BIG reserved time survives HCI Reset | exp. 21 §1 |
| Known faults | Board C hung once in the warm reset right after a UF2 flash (fixed by a power cycle); cause unknown | exp. 21 §0 |

## 2. Required for the product (must)

| # | Requirement | Why (evidence) | Proposed approach | Effort |
|---|---|---|---|---|
| R1 | **Use the 32 kHz crystal when the board has one, RC otherwise, from one image** | RC jitter ±26 ppm vs 0.04 ppm (§6); the declared sleep-clock accuracy must match the source | Boot-time LFXO probe, as `clockprobe` does, before MPSL starts; then configure MPSL with the source found. **To investigate:** whether NCS lets the LF source and accuracy be chosen at runtime (MPSL takes its clock config at init); fallback: two images and the host picks by the board's report (R3) | M |
| R2 | **Software bootloader entry** | double reset failed 3 of 4 times by hand | done (1200-baud touch); add the same as an HCI command (D1) | done / S |
| R3 | **The controller says what it is**: firmware version and build hash, clock source and accuracy, board serial | the host must know if a board is crystal or RC, and which firmware it runs, before trusting timing | USB product string per image (`CONFIG_CDC_ACM_SERIAL_PRODUCT_STRING`, e.g. `aurasync HCI xtal 1`); and a VS command returning a small struct (D1) | S |
| R4 | **Never hang silently** | the warm-reset hang after flashing | enable the task watchdog so a stuck controller resets itself; investigate the hang (USB re-enumeration right after the bootloader's reset) | S–M |
| R5 | **Reproducible, pinned build** | the image is part of the measurements (CLAUDE.md: every measurement records firmware) | `firmware/supermini/build.sh`: checks NCS v3.4.1 and its commit, builds both variants, prints sha256; the README records the hashes | S |
| R7 | **Know each board's 32 MHz error before using it** | board C is +64 ppm (§1); a receiver may refuse or struggle with an emitter that far off (open, E3) | a commissioning check per board: clockprobe or D2 against `CLOCK_MONOTONIC_RAW` for 10 min, recorded with its serial in the inventory; boards beyond ±40 ppm are flagged and kept as receivers or sniffers, not emitters | S per board |
| R6 | **Host rules that the firmware cannot enforce** | the reserved time leaks across sessions; Bumble's sequence-number mode does not guarantee alignment | the host sets the BIG reserved time on every open, uses the timestamp mode, and applies `bumble_fixes` (§4) | S (host) |

## 3. Desired

| # | Feature | Value | Approach | Effort |
|---|---|---|---|---|
| D1 | **aurasync vendor HCI commands** (opcodes outside the SDC's range, e.g. `0xFE00`–`0xFE0F`) | one channel for everything the host needs: enter bootloader, identity (R3), clock report, chip temperature | `hci_internal_user_cmd_handler_register()` (NCS `subsys/bluetooth/controller/hci_internal.h`): a user handler with precedence over the SDC | M |
| D2 | **On-board clock telemetry**: the LF clock measured against the 32 MHz crystal every second (RTC→PPI→TIMER, as `clockprobe`), exposed by D1 | the drift numbers stop depending on the PC's clock (§8 of exp. 21 found the PC's monotonic clock moving) | port the clockprobe's measurement into the controller image on free peripherals (RTC2, TIMER2/3, one PPI channel), checking they do not collide with MPSL | M |
| D3 | **Status LED** (the SuperMini's blue LED) | at a glance: idle / advertising / BIG running / error | a small thread watching controller state through the user handler or HCI traffic | S |
| D4 | **Second CDC port for logs** | see controller logs without disturbing HCI | composite USB with a second CDC ACM for the Zephyr console | S–M |
| D5 | **UART HCI at 1 Mbaud with RTS/CTS** | the Pico 2 W as host (phase 3, research/13) | overlay for a UART instance; not needed while the PC is the host | S |
| D6 | **TX power control from the host** | range tests, coexistence | the existing VS `Write TX Power` (0xfc0e) via Bumble | S (host) |
| D7 | **Controller-side ISO counters** (late or flushed SDUs per BIS) | the emitter's "cut" signal for the diagnostics view | **to investigate**: the SDC is closed; maybe derivable from `LE Read ISO TX Sync`/0xfd17 sequence gaps on the host | ? |

Not proposed: encryption (the nRF52840 cannot, VERIFIED); the nRF Sniffer inside this image (separate firmware on a dedicated board, research/02 §8).

## 4. Host side, to integrate into aurasync later

Ordered by the agreed plan (diagnostics first, emitter after E4). New code in English (d-7c8794-7b3093).

1. **`aurasync/auracast/controller.py`**: find boards by USB serial and product string (R3), open with `bumble_fixes.apply()`, read identity and clock report (D1/D2), set the reserved time (R6), enter bootloader for updates.
2. **`aurasync/auracast/observe.py`**: the HCI observer (BASE, BIGInfo, Harman data) from research/02 §8.4 and `probes/22-sniffer-jbl/jbl_decode.py`.
3. **An `AuracastMonitor`** with the shape of `RadioMonitor` (`available`/`reason`/per-controller): controller present, firmware, clock source and ppm, queue, BIG state, what the JBLs advertise; published in `state` and the panel; a simulated variant like `SimulatedRadio`. → **sub-project 3 of the agreed order.**
4. **`aurasync diag auracast`** CLI over the same pieces.
5. **After E4: the emitter backend** (`PlayerLike` over Bumble `IsoPacketStream`s): LC3 ×N, the timestamp mode, a cushion ≥ 40 ms, and a clock loop that follows the controller (exp. 21 §7) driving the resampler (research/08 §4) — referenced to the audio source's clock, not `CLOCK_MONOTONIC`.

## 5. Proposed order

1. R5 build script + R3 product string + R4 watchdog + R7 commissioning check (small, make every later measurement traceable).
2. D1 vendor commands with identity and bootloader entry; then D2 clock telemetry over D1.
3. R1 single image with crystal detection (after investigating MPSL's runtime clock config).
4. D3 LED, D4 log port — when the diagnostics view needs them.
5. D5 UART — with the Pico phase.

## 6. Open questions for the user

1. Approve, change or reorder §2–§5.
2. ~~Board A~~ done on 2026-10-07: it has the crystal, runs the new controller, its 32 MHz crystal is +79 ppm.
3. Whether D2 (clock telemetry in the controller) is worth its complexity, or the separate `clockprobe` image is enough.
