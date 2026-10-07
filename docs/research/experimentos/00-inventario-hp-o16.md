# 00 · Inventario del portátil `HP-O16`, y los tres equipos lado a lado

**Pregunta:** ¿qué tiene el tercer equipo, y qué del proyecto puede hacerse en él?
Completa el inventario (i-7c8794-d9c834), cuyas otras dos mitades son
[00-inventario-mac.md](00-inventario-mac.md) y
[00-inventario-linux.md](00-inventario-linux.md) (`PC-Ryzen5`).

**Entorno:**
- Portátil **HP OMEN 16-b0xxx** (SKU `4P618LA#AKH`, BIOS F.46), hostname `HP-O16`.
- Intel Core **i5-11400H** (Tiger Lake-H, 12 hilos), 30 GiB de RAM, batería al 100 %.
- **CachyOS**, kernel **7.2.8-1-cachyos** (`PC-Ryzen5` tenía 7.2.7).
- BlueZ **5.87**, PipeWire **1.6.9**, WirePlumber **0.5.17**, liblc3 **1.1.3**:
  las mismas versiones que `PC-Ryzen5`.
- `python3` 3.14.7; hatch 1.16.2 en `~/.local/bin` (crea sus entornos con 3.12.12).
- Sin sudo sin contraseña: la regla `/etc/sudoers.d/bluetooth-sync` de `PC-Ryzen5`
  **no existe acá**.
- Fecha: 2026-09-29. Sin adaptadores Bluetooth USB externos.

**Datos crudos:** [datos/00/hp-o16-inventario.txt](datos/00/hp-o16-inventario.txt).

**Qué se ejecutó** (solo lectura; no se instaló ni se cambió nada del sistema):
```bash
probes/00-inventario-linux/inventario.sh
timeout 5 btmgmt info        # sin root
rfkill list; wpctl status; iw dev
PY=python3.14 scripts/check.sh
cd host && hatch run aurasync doctor
```

## Resultado

### El chip es el mismo que en `PC-Ryzen5`, con el mismo firmware: MEDIDO
| Campo | Valor |
|---|---|
| Modelo | **Intel AX210** (`8087:0032`), Wi-Fi `8086:2725` |
| Transporte del Bluetooth | **USB full-speed (12 Mbps)**, `btusb` → `hci0`, en el xHCI del chipset (`00:14.0`, puerto `3-7`) |
| Transporte del Wi-Fi | PCIe `2e:00.0`, detrás del root port `00:1c.7` |
| Firmware | `intel/ibt-0041-0041.sfi`, **202-5.26**, timestamp 2026.5, build 82122, SHA1 `0x2925677d` |
| HCI Version | `0x0d` = Bluetooth 5.4 |
| Dirección | `BC:09:1B:FE:C7:3D` |

El SHA1 del firmware es **idéntico** al de `PC-Ryzen5`. Es el mismo binario.

### Capacidades mgmt: MEDIDO, y sin root
`btmgmt info` **se puede leer sin root**: el kernel acepta comandos de lectura en un
socket mgmt no privilegiado. En `PC-Ryzen5` se instaló una regla de sudo creyendo que
hacía falta; para esta lectura no hacía falta. Con `timeout 5`, porque después de
imprimir btmgmt se queda esperando en modo interactivo (la primera vez colgó el
comando por 2 minutos).

```
supported settings: … le advertising … cis-central cis-peripheral ll-privacy
```

`iso-broadcaster` y `sync-receiver`: **ausentes**, igual que en E1
([03](03-e1-iso-en-el-ax210.md)). **Este equipo tampoco transmite ni escucha
Auracast.** Los bits de LE Features no se leyeron (necesitan `btmon`, con root);
con el mismo firmware bit a bit y los mismos settings, no hay motivo para que
difieran. INFERIDO.

### El Bluetooth está apagado: MEDIDO
`rfkill` muestra `hci0` con **bloqueo por software** (`Soft blocked: yes`), y BlueZ
lo reporta como `PowerState: off-blocked`. Es el estado en que lo dejó el usuario o
el escritorio (Plasma lo recuerda entre arranques, INFERIDO); no es una falla. **Antes de
cualquier prueba hay que desbloquearlo** (`rfkill unblock bluetooth`, o el applet de
Plasma) y se revierte con `rfkill block bluetooth`. No se tocó en esta sesión.

### Ningún JBL está emparejado con este equipo: MEDIDO
Hay tres dispositivos emparejados, ninguno del proyecto: unos audífonos **Sony
WH-CH520**, unos **BLIK-SOUL900** y un teléfono. Los 3 Go 4 y el Charge 6 están
emparejados con `PC-Ryzen5` y con el Mac. Usarlos acá exige emparejarlos de nuevo.
Un JBL guarda una lista limitada de equipos emparejados (INFERIDO: cuántos, no se
midió), así que emparejarlo con un tercer equipo puede desplazar a otro.

### El audio: dos trampas medidas, las dos ya conocidas
- **El sink por defecto guardado en WirePlumber son los Sony WH-CH520**
  (`default.configured.audio.sink=bluez_output.14_06_A7_6B_E3_F0.1`). Es exactamente
  el mecanismo de [09](09-primera-escucha-con-3-go-4.md): si los Sony se conectan
  mientras `aurasync` reproduce, WirePlumber los vuelve el default y puede **mover**
  un stream aunque tenga `target.object`. `sonido.reparar_ruteo` existe para eso, pero
  conviene no tener los Sony encendidos durante una prueba.
- **El micrófono por defecto (el digital interno) está silenciado** en PipeWire
  (`vol: 1.00 MUTED`). El otro, `Stereo Microphone` (`HiFi__Mic2__source`), no lo está.
  `calibrate` con el micrófono silenciado graba ceros.
- Salidas: el parlante interno (silenciado) y cuatro HDMI/DisplayPort; la GPU es una
  NVIDIA GA106.

### El host corre tal cual: MEDIDO
`PY=python3.14 scripts/check.sh` pasa completo: **167 tests en 3,01 s**, lint y los
chequeos del bundle. `hatch run aurasync --version` → `0.0.0`.

**`aurasync doctor` corre, pero trae un supuesto de otro equipo:** avisa
*"el micrófono por defecto (`alsa_input.usb-3142_fifine_Microphone-00…`) no está"*.
El nombre del nodo del fifine USB de `PC-Ryzen5` está fijo en el código
(`MICROFONO_POR_DEFECTO`, `host/src/aurasync/cli.py`). En este equipo hay que pasar
`--microfono` siempre, o arreglarlo (ver el veredicto).

`doctor` tampoco avisa de las dos cosas que impedirían una prueba acá: el Bluetooth
bloqueado por `rfkill` y el micrófono silenciado.

### Radio y coexistencia: MEDIDO
El Wi-Fi está conectado en **5 GHz (canal 153)**, así que hoy no comparte los
2,4 GHz con el Bluetooth. En una red de 2,4 GHz sí los compartiría, con la misma
antena combo: es una variable que `PC-Ryzen5` (Ethernet) no tiene. Anotar la banda
del Wi-Fi en cada medición hecha en este equipo.

### La ranura de la tarjeta: INFERIDO
El AX210 cuelga de un root port PCIe propio (`00:1c.7`), no del CNVi del chipset.
En un OMEN 16 eso corresponde a un **módulo M.2 2230 key E reemplazable**. Es el primer
equipo del proyecto donde cambiar la tarjeta por una **Intel BE200** (reportada con
Auracast, [02](../02-le-audio-auracast-linux.md) §2) es físicamente directo, y con
plataforma Intel, que evita la duda de compatibilidad de la BE200 con placas AMD que
tenía `PC-Ryzen5`. No se abrió el equipo ni se verificó si el BIOS de HP restringe
qué tarjetas acepta. **No es una recomendación de compra:** las SuperMini ya compradas
cubren Auracast (d-7c8794-b82ee9). Queda como alternativa si fallan.

## Los tres equipos lado a lado

| | Mac | `PC-Ryzen5` | `HP-O16` |
|---|---|---|---|
| Tipo | MacBook, Apple Silicon | escritorio, Ryzen 5 | portátil, i5-11400H |
| Sistema | macOS 27.0 | CachyOS, kernel 7.2.7 | CachyOS, kernel 7.2.8 |
| Chip Bluetooth | MediaTek MT7932 | Intel AX210 | Intel AX210 |
| Transporte del BT | **PCIe** | USB (la tarjeta va en PCIe) | USB (la tarjeta va en M.2, INFERIDO) |
| Firmware BT | — | 202-5.26 | 202-5.26 (mismo SHA1) |
| Transmitir Auracast | no (Bumble no llega al chip) | **no** (MEDIDO, E1) | **no** (MEDIDO, settings) |
| Unicast LE Audio (CIS) | ? | sí (MEDIDO, E8) | capaz (MEDIDO: `cis-central`) |
| A2DP a varios parlantes | no probado | **sí, 3 Go 4** (MEDIDO, E6 y 09) | mismo chip; no probado |
| Bumble con `hci-socket` | imposible | posible | posible |
| PipeWire / LC3 | — | 1.6.9 / sí | 1.6.9 / sí |
| `aurasync` | no portado (`sonido.py` es de PipeWire; i-7c8794-a848a0) | corre, probado con parlantes | tests pasan; sin parlantes |
| Micrófono | interno | **fifine USB** (el de las mediciones) | interno (silenciado) |
| JBL emparejados | Charge 6, 1 Go 4 | 3 Go 4, Charge 6 | ninguno |
| Tune 770NC-LE emparejados | sí | sí | no |
| sudo acotado | — | sí (`/etc/sudoers.d/bluetooth-sync`) | no |
| Cambiar la tarjeta BT | no | incierto (sin ranura M.2) | directo, INFERIDO |

## Veredicto

- **`HP-O16` es, para el proyecto, un segundo `PC-Ryzen5` que se puede llevar.** Mismo
  chip, mismo firmware, mismo stack de audio: todo lo que funciona en `PC-Ryzen5` con
  A2DP debería funcionar acá. INFERIDO hasta repetir una medición de E6 en este equipo.
- **No cambia nada de Auracast:** ninguno de los tres equipos transmite. E2–E5 siguen
  esperando las SuperMini, que se conectan por USB a cualquiera de los tres.
- **Lo que sí aporta que ser portátil:** se puede llevar al centro de la pieza, que es
  donde tiene que estar el micrófono de calibración. Con el micrófono interno, el
  instrumento queda en la zona de escucha sin cables. Pero el micrófono interno de un
  portátil no es el que validó E6, así que **una calibración hecha con él no se da por
  buena hasta compararla con el fifine** en la misma posición (la regla de
  `CLAUDE.md`: que sobreviva a cambiar lo que no debería importar).
- **Para usar el proyecto en más de un equipo hay un arreglo que hacer en el host:**
  el micrófono no puede estar fijo en el código. Lo natural es que salga de la
  configuración de la instalación (o del default de PipeWire) y que `doctor` avise si
  el Bluetooth está bloqueado o el micrófono silenciado.
- **Qué no cambia en `docs/research/`:** nada se contradice. Se corrige un supuesto de
  [00-inventario-linux.md](00-inventario-linux.md): `btmgmt info` no necesitaba root.


## Cambios al sistema del 2026-10-05 (con su reversión)

- **ufw:** el usuario abrió los puertos 8731 y 8443 para conectarse desde el celular en la red del
  cowork. La regla exacta no la vio el agente; la propuesta fue
  `sudo ufw allow from 192.168.100.0/24 to any port 8731,8443 proto tcp`, o desde la IP del celular.
  **Revertir:** `sudo ufw status numbered` y `sudo ufw delete <n>`.
- **WirePlumber** guardó en `~/.local/state/wireplumber/stream-properties` una línea
  `Audio/Sink:node.name:hrtfprobe_null` (la sonda del HRTF) y volvió a guardar las claves
  `aurasync monitor` y `pw-play`. **Revertir:** con WirePlumber detenido, borrar esas líneas; las
  recrea con valores por defecto (experimentos/18).
- **Unidad de usuario `aurasync-fase1`** (transitoria, `systemd-run --user`): detenida. No queda
  instalada.
- **rustup** y la toolchain del motor en Rust: en la sección siguiente.

## Cambios en el sistema: Rust (2026-10-05)

Para el motor en Rust (i-7c8794-fd9732, `engine/README.md`). Versiones MEDIDAS el mismo día.

- **`rustup` instalado con pacman:** `pacman -Q rustup` → `1.29.1-1.1`; da `rustc` y `cargo`
  **1.99.0**. Revertir: `sudo pacman -Rns rustup; rm -rf ~/.rustup ~/.cargo`.
- **La toolchain 1.99.0** la bajó `engine/rust-toolchain.toml` la primera vez que se corrió
  `cargo` en `engine/`. Revertir: `rustup toolchain uninstall 1.99.0`.
- hatch 1.16.2: un miembro de workspace con backend maturin fuera de `host/` falla ("No members
  could be derived"); la extensión se compila con un script (d-7c8794-dc712e).
- Costo medido de la lectura sinc en este equipo: [20](20-costo-de-la-lectura-sinc-en-rust.md).


## Hardware recibido el 2026-10-07

- **4× SuperMini nRF52840** (clon de nice!nano). Llegaron el 2026-10-07, todavía sin
  flashear ni etiquetar. Cada medición anota qué unidad se usó; conviene marcarlas como A, B, C y D.
- **1× Raspberry Pi Pico 2 W**, la del usuario: sonda SWD de recuperación (debugprobe) y el cerebro
  de la Fase 3.
- Las pruebas de Auracast (E2 a E5) se hacen en este equipo, `HP-O16`, con los JBL traídos
  hasta aquí (decisión del usuario del 2026-10-07).
- **Unidades identificadas (MEDIDO, 2026-10-07)** por el número de serie USB, que es el mismo en el
  bootloader y en el firmware. Todas con UF2 Bootloader 0.6.0 nice!nano y S140 6.1.1, y de fábrica sin
  aplicación:
  - **A:** `25351136B0E21CB1`. **Tiene cristal de 32 kHz**, y su cristal de 32 MHz va a +79 ppm. Desde el
    2026-10-07 a las 17:2x tiene el controlador con cristal y con el toque a 1200 baudios (`hci_uart_iso` +
    `iso.conf`).
  - **C:** `893169D3E93C0F4E` (la segunda que se conectó; el usuario la rotuló C), con la misma imagen; se
    **Tiene cristal de 32 kHz**, y su cristal de 32 MHz va a +64 ppm. Al cierre del 2026-10-07 tiene el
    **emisor autónomo** (`probes/21-supermini-iso/standalone_tx/`); vuelve al controlador por software
    (`stty -F /dev/ttyACMn 1200` y copiar el UF2).
    colgó en el reinicio después de grabar y funciona tras desconectarla (experimentos/21).

  Hay respaldo del flash de fábrica fuera del repositorio, en `~/supermini-respaldo/`.

## Toolchain de firmware (instalado por el usuario el 2026-10-07)

- `nrfutil` 8.2.0 (`c910332`, 2026-04-21), desde AUR con `paru -S nrfutil`: el binario de Nordic en
  `/usr/bin/nrfutil`.
- **nRF Connect SDK v3.4.1** con `nrfutil sdk-manager install v3.4.1`, en `~/ncs/v3.4.1` (`nrf` en
  `b20f8619`, 2026-09-17). El toolchain `8285d8ad56` está en `~/ncs/toolchains/`. Ocupa 13 GB.
- Para revertirlo: `rm -rf ~/ncs ~/.nrfutil` y `paru -Rns nrfutil`. Sin reglas udev ni J-Link: no hacen falta.
