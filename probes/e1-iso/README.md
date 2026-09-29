# E1 · ¿el controlador puede transmitir por ISO?

Probe de i-7c8794-3f730a en el equipo Linux (`PC-Ryzen5`, Intel AX210).
Se borra cuando el resultado esté en `docs/research/experimentos/`
(d-7c8794-3208b7).

## Antes: la regla de sudo

Casi todo esto necesita root, y este equipo no tiene sudo sin contraseña. La regla
está acotada a comandos con ruta y argumentos fijos, **sin comodines**: en sudoers un
`*` en los argumentos también matchea `..`, así que un `cat ruta/*` dejaría leer
cualquier archivo del sistema.

Qué permite:

| Para qué | Comandos |
|---|---|
| Leer capacidades y trazas | `btmgmt`, `btmon`, `bluetoothctl`, `dmesg` |
| Los bits de LE Features | `cat /sys/kernel/debug/bluetooth/hci0/features` (ruta exacta) |
| Reiniciar el servicio | `systemctl {start,stop,restart,status} bluetooth` |
| Activar y revertir el socket ISO | `cp` desde los dos `main.conf.*` de esta carpeta, con origen y destino fijos |

**Quitarla al terminar:**
```bash
sudo rm /etc/sudoers.d/bluetooth-sync
```

Lo que **no** permite, a propósito: correr Python como root. Bumble necesita
`CAP_NET_ADMIN` para tomar el controlador con `hci-socket:0`, y cualquier forma de
darle eso sin contraseña equivale a root. Se decide recién si el bit 30 aparece, o
sea si vale la pena.

## Los pasos

```bash
probes/e1-iso/01-capacidades.sh | tee /tmp/e1-capacidades.txt   # solo lee
probes/e1-iso/02-activar-iso.sh                                 # CAMBIA EL SISTEMA
probes/e1-iso/03-revertir.sh                                    # lo deshace
```

`01` es el que decide y no toca nada. Conviene correrlo y leer el veredicto antes de
seguir: si el controlador no declara **Isochronous Broadcaster (bit 30)**, activar el
socket ISO no sirve para transmitir, y E1 pasa a las SuperMini nRF52840
(d-7c8794-b82ee9) o a una tarjeta MT7921/BE200.

Igual vale correr `02` aunque el bit 30 falte, por dos motivos:
- si está el **bit 28 (CIS Central)**, se puede probar unicast LE Audio contra los
  **Tune 770NC**, que sí exponen PACS y ASCS
  ([experimentos/02](../../docs/research/experimentos/02-servicios-de-los-jbl-linux.md)).
  Eso valida todo el camino ISO de Linux (bandera, socket, LC3, PipeWire) sin
  depender de poder transmitir;
- si está el **bit 31 (Synchronized Receiver)**, este equipo puede **recibir** un BIS
  y leer el BIGInfo de los propios JBL, que es lo que falta de E2
  (i-7c8794-a999d3).

## El cambio al sistema, y cómo se revierte

`02-activar-iso.sh` escribe `/etc/bluetooth/main.conf`. bluetoothd **solo** lee ese
archivo (no hay `conf.d`, confirmado en `man bluetoothd`), así que no hay forma de
hacerlo con un drop-in.

- `main.conf.original`: copia exacta del archivo del sistema del 2026-09-28,
  md5 `2bd6a06abf1b0a1d76c252098e00819e`.
- `main.conf.experimental`: el mismo, con dos líneas cambiadas
  (`diff main.conf.original main.conf.experimental`):
  ```ini
  Experimental = true
  KernelExperimental = 6fbaf188-05e0-496a-9885-d6ddfdb4e03e   # socket ISO
  ```

`02` se niega a correr si el md5 del sistema no coincide con el respaldo, para no
pisar un cambio de otra persona ni de una actualización de bluez. `03` restaura y
**verifica por md5** que quedó byte a byte como estaba.
