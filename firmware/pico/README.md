# Raspberry Pi Pico 2 W (RP2350)

**Estado:** vacía. Se llena con P3 (i-7c8794-346d45), y en la Fase 3 (i-7c8794-80f3ac)
si P3 sale bien.

**La radio CYW43439 de esta placa no sirve para Auracast**, porque no tiene
advertising extendido ([08](../../docs/research/08-integracion-y-plan.md) §3.1). Lo
que se usa es el RP2350.

## Plan

1. **P3(a), benchmark de LC3.** Decide si la Fase 3 cabe: las cifras publicadas sugieren 35–60 MHz
   por canal en un M33 o M4F, o sea 140–240 MHz para 4 canales, más que un núcleo de 150 MHz
   ([research/13](../../docs/research/13-dispositivos-pi-pico-y-panel-independiente.md) §2.5). Si no cabe:
   2 canales por Pico, o el LC3 en el PC. liblc3 en el Cortex-M33, con 4 canales a 48 kHz y
   tramas de 10 ms, contando ciclos con DWT CYCCNT. Es un probe: va en
   `probes/p3-pico-lc3/` y se borra al anotar el resultado.
2. **P3(b), tarjeta USB de 4 canales** con TinyUSB master: UAC1 en full-speed, o
   UAC2, con feedback asíncrono.
3. **Fase 3 (H2b), solo si (1) y (2) salen bien:** TinyUSB + BTstack como host +
   liblc3, con una SuperMini por UART H4. Se parte del port oficial
   `btstack/port/rp2040-vela-if820`. Ese código sí vivirá aquí.

**Toolchain prevista** (INFERIDO; no está instalada en el Mac):
- Pico SDK ≥2.3.1, con el submódulo TinyUSB actualizado;
- `arm-none-eabi-gcc`, `cmake` y `picotool`.
