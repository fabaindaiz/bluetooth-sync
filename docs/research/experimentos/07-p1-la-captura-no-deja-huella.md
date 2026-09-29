# 07 · P1: la captura del audio del sistema no deja huella

**Pregunta:** ¿el dispositivo de salida que crea `aurasync run` desaparece del todo cuando
el proceso muere, incluso sin darle oportunidad de limpiar? (i-7c8794-fd5f03, la mitad de
Linux)

**Respuesta: sí. MEDIDO.**

**Entorno:** `PC-Ryzen5`, PipeWire 1.6.9, WirePlumber 0.5.17, kernel 7.2.8-1-cachyos.
Fecha: 2026-09-29. No se reprodujo ni se grabó nada audible: solo se creó el nodo y se lo
mató.

**Qué se ejecutó:** `probes/p1-huella/verificar.py`, que crea el sink virtual con
`pw-record -P '{ media.class=Audio/Sink … }'`, espera 3 s, lo mata con **SIGKILL** y compara
el antes con el después.

**Por qué `kill -9` y no un cierre ordenado.** Un cierre ordenado prueba poco: cualquier
programa limpia bien cuando se le da la oportunidad. Lo que importa es qué queda cuando el
proceso muere sin poder ejecutar nada, que es lo que pasa si el programa falla. La tarjeta
*cleanup-belongs-to-the-supervisor* dice eso: la limpieza no puede depender de código que
tal vez no llegue a correr.

## Resultado: MEDIDO

```
antes:   5 archivos de estado · salida por defecto: bluez_output.88_92_CC_68_91_C0.1
durante: el dispositivo aparece · salida por defecto: bluez_output.88_92_CC_68_91_C0.1
después: el dispositivo desapareció · salida por defecto: bluez_output.88_92_CC_68_91_C0.1
```

Las tres cosas que el criterio pide:

| Qué | Resultado |
|---|---|
| el dispositivo tras el `kill -9` | **desapareció solo** |
| la lista de salidas y la salida por defecto | **iguales** |
| los 5 archivos de `~/.local/state/wireplumber/` | **byte a byte iguales** |

**Y un riesgo que no se materializó, pero que era el que había que vigilar:** crear un
dispositivo de salida nuevo podría haber hecho que WirePlumber lo tomara como salida por
defecto. Si eso pasara **y se persistiera**, el usuario quedaría con el audio ruteado a un
dispositivo que ya no existe. No ocurrió: la salida por defecto no se movió en ningún
momento.

## Veredicto

- **La mitad de Linux de P1 está cumplida**, incluido su criterio de terminado. El
  mecanismo está en `host/src/aurasync/sonido.SinkVirtual` y se usa con `aurasync run`.
- **No cambia ninguna decisión**: confirma la propiedad que ya se asumía al elegir
  `pw-record` en vez de crear módulos persistentes de PipeWire.

## Qué falta de P1

- **La mitad del Mac**: el process tap privado (PyObjC o audiotee), y si `CATapMuted`
  silencia los parlantes. No se puede hacer desde este equipo.
- Repetir esta comprobación **con audio fluyendo** de verdad. Acá el nodo estuvo suspendido
  los 3 segundos, porque no había ninguna aplicación ruteada. Es un caso más benigno: con
  un stream activo, WirePlumber tiene más razones para tocar su estado.
