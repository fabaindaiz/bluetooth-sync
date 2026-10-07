# Auracast with the SuperMini nRF52840: E1 to E5 — execution plan

**Date:** 2026-10-07 · **Status:** for the user's review (no toolchain installed yet). **Machine:** `HP-O16`, with the
JBL speakers brought to it (user, 2026-10-07). **Hardware:** 4× SuperMini nRF52840 (units A–D, labelled before use),
1× Pico 2 W (SWD recovery). **Decision this feeds:** i-7c8794-0d129c (follow Auracast or not).
**Sources:** roadmap E1–E5, research/01, 02, 05 §3/§7, 06 §1, 08 §2–§4, firmware/supermini/README.md.

These are **experiments with throwaway probes** (`probes/<name>/`, deleted once the result is written,
d-7c8794-3208b7), not product code. Every result goes to `docs/research/experimentos/` with its mark, the unit
(A–D), the 32 kHz clock source, the NCS version and firmware commit, the JBL firmware versions, and is repeated
independently (CLAUDE.md).

## 0. Prerequisites — system changes, each written down with its revert before doing it

Checked on HP-O16 on 2026-10-07: the user is **already in `uucp`**, **ModemManager is inactive**, 150 GB free in `/home`.
So only the toolchain row applies.

| Change | Why | Revert |
|---|---|---|
| user in group `uucp` (`sudo usermod -aG uucp fadiaz`, then log out/in) — the user runs it | open `/dev/ttyACM*` without root (research/08 §2.1, level N2) | `sudo gpasswd -d fadiaz uucp` |
| ModemManager must not probe the SuperMini (check `systemctl is-active ModemManager`; if active, a udev rule `ID_MM_DEVICE_IGNORE=1` for its VID:PID) — only if needed | it opens new ttyACM devices and corrupts H4 | delete the rule file, `udevadm control --reload` |
| `nrfutil` from AUR (`paru -S nrfutil`, 8.2.0: Nordic's own binary pinned by sha256, one file in `/usr/bin`) — **the user installs it** (2026-10-07); update it with paru, never `nrfutil self-upgrade` | the entry point Nordic documents for a command-line install | `paru -Rns nrfutil` |
| nRF Connect SDK + its toolchain in `~/ncs` via `nrfutil sdk-manager install <version>` (no sudo; several GB; 150 GB free) — the latest stable ≥ v3.2.1, the version recorded in `00-inventario-hp-o16.md`. Not mixed with AUR `zephyr-sdk`/`python-west`/system `cmake`: the bundle brings its own | build the controller firmware | `rm -rf ~/ncs ~/.nrfutil` |
| No udev rules, no J-Link, no `nrf-udev` | UF2 flashing is USB mass storage, and the ttyACM is already covered by `uucp` | — |

## 1. F1 — the controller firmware, and E1 on the SuperMini (revised 2026-10-07 after the primary-source validation, research/02 §7)

**Done 2026-10-07 (units A and B): yes** — see `docs/research/experimentos/21-f1-iso-en-la-supermini.md`. Built
on NCS v3.4.1 (`firmware/supermini/hci_uart_iso/`), RC image; the 32 kHz crystal check is still open.

- **Base: NCS's own `hci_uart` sample with the SoftDevice Controller** (ISO Broadcaster supported on the nRF52840 since
  NCS v2.6.0, VERIFIED). Pin NCS ≥ v3.2.1 and the commit in `firmware/supermini/`. **No bluekitchen fork** (it does not
  support the nRF52840; BIS alignment uses the SDC's VS `ISO Read TX Timestamp` 0xfd17 or `LE Read ISO TX Sync`).
- Board `promicro_nrf52840`: **CDC-ACM is already the HCI UART** (VERIFIED) — no dongle conf, no overlay.
- Kconfig: `CONFIG_BT_ISO_BROADCASTER=y` (+ the SDC broadcaster options), `CONFIG_BT_CTLR_PHY_2M=y`,
  `CONFIG_BT_ISO_TX_BUF_COUNT` 8–10 (**default 1**), `CONFIG_BT_CTLR_SDC_ISO_TX_HCI_BUFFER_COUNT` 8–10 (default 3),
  `CONFIG_BT_ISO_TX_MTU` ≥ 120. **Unencrypted only** (the nRF52840 cannot encrypt ISO, VERIFIED).
- **32 kHz first:** look for the crystal and boot once with `K32SRC_XTAL`; if absent, RC with calibration declared at
  500 ppm. Record it per unit.
- Flash: UF2 (double-tap reset). Recovery: Pico 2 W `debugprobe_on_pico2.uf2` v2.3.1 + OpenOCD `nrf52_recover`.
- **Done when (MEDIDO, unit A, repeated on unit B):** `bumble-controller-info` shows ISO broadcaster; a **4-BIS BIG is
  created with 48_4 (120 B) and with 48_2 (100 B)**, and `LE Create BIG Complete` is read: the NSE, IRC and PTO the SDC
  actually chose (it caps RTN by itself: with RTN 4, four 120 B BIS need ~13.5 ms of a 10 ms interval, INFERIDO); the
  reply to `Encryption=1` recorded; the 32 kHz source recorded. This is the cheapest experiment that clears the
  hardware risk (~1 h, no speakers).

## 2. E2 — what the JBL advertise (scanner)

- `bumble-auracast scan serial:/dev/ttyACM0` (Bumble 0.0.235 as pinned) with: a Go 4 in Auracast mode, the Charge 6 as
  transmitter, a Go 4 stereo pair **for 60 s while it forms**, party mode.
- Record BASE (BIS count, channel allocation, presentation delay, codec config), BIGInfo (PHY, framing, interval,
  encryption), Broadcast_ID (fixed or random: two power cycles), Broadcast Name, PBP features.
- **Done when (MEDIDO):** each of the four cases recorded twice; encryption of the Go 4 known; the stereo-pair question
  answered.

## 3. E3 — does a mono BIS from us play on a Go 4 and on the Charge 6

- `bumble-auracast transmit serial:/dev/ttyACM0 --input file:mono48k.wav --broadcast-name T --manufacturer-data
  87:00000000000000000000000000000000dffd`; each speaker in receiver mode (Auracast button, no classic connection).
- Presentation delay 40 ms and 80 ms (the Clip 5 needed 80, research/01) — 80 needs a small probe-local patch
  (Bumble fixes 40000 µs).
- **Second emitter (alternative B, research/02 §7.1):** the same mono BIS from BlueZ + PipeWire, with the SuperMini
  attached to the kernel by `btattach` (H4 over ttyACM) and BlueZ's experimental modes enabled (a system change: written
  down with its revert first). Records which emitter was less fragile — the roadmap's "Bumble vs PipeWire".
- **Done when (MEDIDO):** plays yes/no per model × delay, two attempts each, on two Go 4 units; the receiver's
  negotiated config read back where possible.

## 4. E4 — the decisive test: stereo in 2 BIS, who plays what

- Stereo WAV, L = 440 Hz, R = 880 Hz; one BIG, BIS1 FL, BIS2 FR (Bumble's stereo path).
- (a) **No intervention:** each Go 4 alone, then two Go 4 at once — which tone does each play (mix / first BIS / its
  location)? Classified by the dominant frequency recorded with the microphone near each speaker (HP-O16's internal mic is
  enough for frequency; see §7 for timing).
- (b) **Broadcast Assistant:** unit B as assistant (`bumble-auracast pair`, `assist --command monitor-state` — does the
  Go 4 expose BASS over LE at all?, then `add-source`); if BASS exists, a probe-local patch sets `bis_sync = 1 << (idx-1)`
  per speaker.
- (c) **Separate mono BIG per speaker** (the SIG's recommendation): two units, two BIGs — expected to play but not aligned
  (d-7c8794-203de2 says one BIG); record whether it plays and the offset.
- **Done when (MEDIDO):** for each of a/b/c, which BIS each speaker plays, two repetitions, on two Go 4 units; verdict:
  "each JBL plays its assigned BIS" yes / no / only with (b).

## 5. E5 — only if E4 says yes: one BIG with 4 BIS and the alignment

- Probe-local Bumble patch (research/05 §3): >2 channels, one subgroup with BIS index i+1, `--channel-map` for
  `audio_channel_allocation`, exposed delay/RTN/latency/PHY; 4-channel input (WAV or ffmpeg → stdin).
- Radio time is known to be tight (F1 reads what the SDC chose): prefer **48_2 (100 B)** or accept RTN 1–2; 24 kHz only
  if 48_2 fails (record the quality cost).
- Align the 4 BIS with one controller timestamp (VS 0xfd17 / `LE Read ISO TX Sync`), the same `Time_Stamp` on every
  BIS of an interval (the SDC doc's recommendation).
- Measure the speaker-to-speaker offset with a microphone (a click per channel, the repository's GCC-PHAT measurement) at
  start and after 30 min, two independent runs.
- **Done when (MEDIDO):** offsets at 0 and 30 min, twice, against the tolerance the user sets (proposal from the roadmap:
  < 5 ms front stereo, < 20 ms rear).

## 6. The decision

Record i-7c8794-0d129c: follow Auracast if E4 = yes and E5 within tolerance; otherwise A2DP stays the main backend
(as today) and Auracast is closed with its evidence. Update d-7c8794-203de2 (one BIG) with E5's measurement.

## 6b. Alternative paths (research/02 §7.1)

- **B. BlueZ + PipeWire** with the SuperMini as a kernel controller: tried in E3 as a second emitter; if it holds, it is
  the product path (it plugs into today's PipeWire engine without Bumble).
- **C. Zephyr's open controller (LL_SW)** instead of the SDC: only if the SDC fails opaquely (inspectable, explicit
  errors; raise `BT_CTLR_ADV_ISO_STREAM_MAX` to 4).
- **D. nRF52833 / nRF5340 Audio DK:** only if encryption is needed or the SuperMini's clock fails — a purchase, ask first.
- **E. One mono BIG per SuperMini:** E4(c); alignment across BIGs would need each controller's ISO clock.
- **F. If E4 says no:** A2DP stays the main path (as today).

## 7. Open points for the user

1. ~~Microphone for E5~~ **decided 2026-10-07 (d-7c8794-910d28): E5 runs on `PC-Ryzen5` with the fifine**; the
   SuperMini boards, the firmware and Bumble travel there. E4's tone classification can use HP-O16's internal mic.
2. ~~E5 tolerance~~ **decided: < 5 ms front / < 20 ms rear.**
3. **Toolchain of §0:** the user installs it (2026-10-07: `nrfutil` by paru, the SDK by `nrfutil sdk-manager`) and
   reports the versions; they go to `00-inventario-hp-o16.md` before F1.
4. **Docs to fix as part of F1:** "5 SuperMini" still in d-7c8794-b82ee9 and `firmware/README.md:61`, and the roadmap's
   E2/E3 status text.
