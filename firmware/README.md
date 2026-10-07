# Firmware

Configuración y código para las placas del proyecto. **No se copian SDKs aquí**
(Zephyr, nRF Connect SDK, Pico SDK): cada carpeta dice qué versión usa y de dónde
sale.

| Carpeta | Placa | Para qué | Estado |
|---|---|---|---|
| [supermini/](supermini/) | 4× SuperMini nRF52840 (clon de nice!nano) | Controlador HCI por USB CDC-ACM (`serial:`) para Bumble en el PC; más adelante, por UART para la Pico | Vacía hasta E1 |
| [pico/](pico/) | 1× Raspberry Pi Pico 2 W (RP2350) | Probe P3: benchmark de LC3 y tarjeta USB de 4 canales. Si P3 sale bien, el cerebro del emisor dedicado (Fase 3, variante H2b) | Vacía hasta P3 |

**Qué se anota en cada medición** (`CLAUDE.md`):
- la unidad de SuperMini;
- la fuente de reloj de 32 kHz;
- la versión del SDK;
- el commit del firmware.

El firmware de un **probe** va en `probes/<nombre>/` y se borra al anotar el
resultado (d-7c8794-3208b7). Aquí queda solo lo que el producto va a usar.
