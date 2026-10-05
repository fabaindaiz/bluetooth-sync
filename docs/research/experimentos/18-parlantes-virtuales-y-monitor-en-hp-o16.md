# 18 · Parlantes virtuales y monitor en HP-O16: ¿llegan los bloques en tiempo real?

**Pregunta:** con todos los parlantes virtuales (`sink: null`) y solo el monitor de audífonos como
salida, ¿la sesión lleva el reloj en tiempo real y el monitor suena sin cortes? Roadmap
i-7c8794-757041; decisiones d-7c8794-0e5063 y d-7c8794-05bdd6; spec
`superpowers/specs/2026-10-05-virtual-speakers-and-hot-join-design.md` §4 y §8.

**Estado: protocolo listo, sin medir.** Se corre en `HP-O16` (el portátil, mismo AX210). Fase 1: solo
tests hasta hoy; nada se ha escuchado.

**Precondición:** el usuario permite usar los audífonos **WH-CH520** en **A2DP** (hoy nada toca los
audífonos ni PipeWire en `HP-O16` sin su permiso). Unos audífonos en HFP suenan mono a 16 kHz y
falsean todo lo de abajo.

## 1. Entorno que se anota (antes de medir)

- Equipo: `HP-O16`. Kernel (`uname -r`), PipeWire 1.6.9, WirePlumber 0.5.17, BlueZ 5.87 (las versiones
  de 2026-10-05; se vuelven a leer).
- Audífonos: modelo WH-CH520, firmware si se puede leer, perfil activo (debe decir A2DP).
- **Estado de energía y carga:** batería o red (`cat /sys/class/power_supply/ADP1/online`; el
  estado del puerto USB-C no es el de la batería), el gobernador de la CPU
  (`cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor`) y la carga (`uptime`) durante la
  medición. El 2026-10-05 un test de costo falló de forma intermitente por la carga de otros
  procesos, no por la batería (i-7c8794-be46cb). Se corre sin otros procesos pesados.
- Fecha, versión de `aurasync` y la instalación (cuántos parlantes virtuales).

## 2. Qué se mide

1. **Bloques en tiempo real con todo virtual.** El spec §4 dice, **INFERIDO**, que sin un dispositivo
   que lo maneje PipeWire asigna el sink `aurasync` a su Dummy-Driver, que corre en tiempo real. Este
   experimento lo **confirma o corrige**: con 3 parlantes virtuales, una aplicación sonando y la
   sesión en marcha, contar los bloques entregados por segundo durante 5 minutos (esperado:
   `rate / block`) y la desviación del reloj. Si no llegan en tiempo real, se anota qué hace en su
   lugar.
2. **Cortes del monitor por minuto.** Con el modo `mix` y luego `binaural`, leer `state.monitor.drops`
   al inicio y al final de 5 minutos y dividir. El monitor no está sincronizado y tira un bloque si
   va atrasado; el reloj de la entrada y el de los audífonos derivan, así que se espera uno
   ocasional (INFERIDO). Repetir en dos corridas independientes, como pide `CLAUDE.md`.
3. **Ningún `pw-play` hacia un parlante.** Con la sesión en marcha, `pw-dump` no debe mostrar ningún
   stream de reproducción dirigido a un parlante virtual ni a un parlante Bluetooth. Se comprueba
   en `pw-dump`, no se supone (`CLAUDE.md`: lo que se le pide a PipeWire se verifica).

## 3. Qué se ejecuta

(Los comandos exactos se anotan aquí al correr.)

```bash
cd host && hatch run aurasync service            # sin --simular
# panel: agregar 3 parlantes virtuales, arrancar, elegir los WH-CH520 en Monitor, modo mix
pw-dump > /tmp/e18-pw-dump.json                  # comprobación 3
# comprobación 2: leer state.monitor.drops del snapshot al inicio y a los 5 min
```

## 4. Resultado

**MEDIDO:** pendiente. Sin número, nada se da por bueno.

## 5. Veredicto

Pendiente. Según el resultado confirma o corrige la línea INFERIDO del Dummy-Driver en el spec §4, y
cambia el estado de i-7c8794-757041 en `docs/roadmap.md`.
