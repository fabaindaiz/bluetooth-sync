# 15 · Graves y volumen con 3 Go 4

Prepara [experimentos/14](../../docs/research/experimentos/14-graves-y-volumen-con-3-go-4.md)
(spec `docs/superpowers/specs/2026-10-02-microcuts-chain-and-quality-design.md` §5 y §8; roadmap
i-7c8794-765571). Se corre en `PC-Ryzen5` con los 3 Go 4 y el micrófono fifine. Los datos van a
`docs/research/experimentos/datos/14/` (el `.wav` no se versiona). Usa `sistema.py` y
`servicio.py` de `probes/16-calidad/`: las dos carpetas se borran juntas cuando los experimentos
14 y 15 tengan su resultado (d-7c8794-3208b7).

| Script | Qué responde | Usa el servicio | Cambia el sistema | Dura |
|---|---|---|---|---|
| `curva_avrcp.py` | volumen AVRCP → dB, por Go 4 | no (sesión **detenida**) | **sí**: volumen de cada sink | ~1 min por parlante |
| `proteccion.py` | ¿`bass=protect` retrasa la protección de graves del firmware? | sí, sonando, sin música | **sí**: volumen de un sink | ~2 min por parlante |
| `ab_graves.py` | ¿se oye el bajo psicoacústico? ¿se prefiere? | sí, con música | no | 15–25 min por sesión |
| `comparar.py` | los criterios sobre dos sesiones | no | no | — |

`analisis.py` tiene las funciones puras (nivel por tercio, curva, caída de graves, binomial), con
tests sobre señales sintéticas de respuesta conocida.

## Antes de empezar

Lo mismo que en [`16-calidad/README.md`](../16-calidad/README.md) ("Antes de empezar"): `PY`, el
micrófono, su ganancia fija, Wi-Fi apagado, y las anotaciones de cada corrida (`--sesion`,
`--microfono-en`, `--posiciones`, `--bateria`, `--firmware`, `--nota`). **El orden recomendado
de las dos carpetas está ahí:** primero `16-calidad/suma_go4.py`, después `curva_avrcp.py`,
después el resto.

## 2 · `curva_avrcp.py`

```bash
# la sesión de aurasync detenida y nada sonando
$PY probes/15-graves-y-volumen/curva_avrcp.py --sesion 1 --microfono-en "1 m al frente, 1 m alto" \
    --bateria "Red=80,Black=75,Blue=90"
# sesión 2: otro día o con el micrófono en otro lugar (eso es lo que la hace independiente)
$PY probes/15-graves-y-volumen/curva_avrcp.py --sesion 2 --microfono-en "1,5 m, a la izquierda"
$PY probes/15-graves-y-volumen/comparar.py docs/research/experimentos/datos/14/*curva*.json
```

Por cada Go 4, uno a la vez y directo a su sink: el mismo ruido rosa (pico 0,1) a 20, 40, 60, 80 y
100 % de volumen y de vuelta (80, 60, 40, 20: la vuelta mide la histéresis). Cada volumen se pide
con `pactl` y **se lee de vuelta** (un paso AVRCP de tolerancia); cada `pw-play` se verifica en
`pw-dump`. A 100 % y pico 0,1 suena fuerte unos segundos: avisar en la casa.

Criterio: la curva (dB relativos al 100 %) sube en cada paso más que lo que se repite (0,5 dB) y
se repite a ±0,5 dB entre las dos sesiones. El `.json` trae también lo que diría la curva cúbica de
PipeWire (`60·log10(%)`): la diferencia es lo que `volume.avrcp` del servicio tendría que corregir.

## 5a · `proteccion.py`

```bash
# servicio sonando, volumen en modo digital, música en pausa (nada entrando al sink aurasync)
$PY probes/15-graves-y-volumen/proteccion.py --parlante Red --sesion 1 --microfono-en "1 m al frente"
$PY probes/15-graves-y-volumen/comparar.py docs/research/experimentos/datos/14/*proteccion*.json
```

Un Go 4 suena (los otros, en silencio por la API); ruido rosa por aurasync con `bass=off` y después
`bass=protect` (90 Hz, orden 4, sin armónicos), con el volumen Bluetooth de 40 a 100 % en pasos de
10. El volumen del panel se ajusta para que al parlante le llegue pico 0,2 (el tope de las pruebas)
y el script corta si el true peak de salida que mide el servicio lo pasa. Mide el tercio de 125 Hz y
la banda de 63–100 Hz relativos a 500 Hz–2 kHz.

**Por qué ruido rosa y no música:** es estacionario y tiene energía en cada tercio; el nivel de
125 Hz se compara entre volúmenes y entre sesiones sin depender de qué compás sonó. Con `--wav`
mide con música (el mismo pasaje en cada paso), para confirmar lo que el ruido muestre.

Criterio: con `protect`, la caída de 125 Hz (3 dB menos que a 40 %) empieza a un volumen mayor o
no aparece, en las dos sesiones. **Si sin `protect` no hay caída, es "inconcluso":** con pico 0,2
el firmware puede no llegar a actuar; subir el nivel es relajar el tope de las pruebas y **se
conversa antes**.

## 5b · `ab_graves.py`

```bash
# servicio sonando con música a volumen de escucha normal (Spotify u otra, por aurasync)
$PY probes/15-graves-y-volumen/ab_graves.py --sesion 1 --ensayos 30
```

Crea por la API (y lo dice) dos presets desde el estado actual que difieren **solo** en `bass`:
`ab-graves-off` y `ab-graves-protect-90hz-+0db` (armónicos en 0 dB: la energía de armónicos igual a
la del grave que quita el pasa-altos; `--armonicos` lo cambia). Si ya existen, los carga y
comprueba que difieran solo en `bass`; `--recrear` los vuelve a hacer (hace falta si cambió la
calibración o el pan). El A/B es el del servicio con la sonoridad igualada: antes del primer ensayo
suenan A y B ~5 s cada uno hasta que el servicio midió las dos (Δ ≤ 0,5 LU).

En cada ensayo: `a`, `b`, `x` para escuchar (las veces que haga falta), `xa` / `xb` para responder
qué es X, y después `pa` / `pb` para la preferencia. `q` termina antes (lo hecho se guarda). Qué
preset es A se sortea y no se muestra; los aciertos tampoco, hasta el final. **Con los ojos en el
teclado, no en el panel** (el panel muestra el preset de A y B).

Criterio: "se oye" con ≥ 20 de 30 en **cada una** de dos sesiones (p ≤ 0,05, binomial exacta);
"mejor" con la preferencia de todas las sesiones (prueba de signos, p ≤ 0,05), y solo si se oye.

## Cómo queda el sistema

- **Volumen de los sinks** (`curva_avrcp.py`, `proteccion.py`): antes de tocarlo, el script lee el
  valor previo y escribe en `docs/research/experimentos/datos/14/cambios-de-sistema.txt` qué cambia
  y el comando exacto para revertirlo (`pactl set-sink-volume <sink> <previo>%`). Al terminar lo
  restaura y lo lee de vuelta, también con Ctrl-C o SIGTERM, y lo anota ("revertido…"). Si una
  reversión falla, el script lo dice y queda "ATENCIÓN" en ese archivo: correr a mano el comando
  anotado y comprobarlo con `pactl get-sink-volume <sink>`.
- **El servicio** (`proteccion.py`): el silencio de los otros parlantes, el volumen del panel, la
  etapa `bass` y el lazo de recalibración vuelven a como estaban (se comprueba la etapa).
- **`ab_graves.py`** guarda el estado previo en un preset temporal `_ab-graves-antes`, lo vuelve a
  cargar al terminar y lo borra. **Los presets `ab-graves-*` quedan** (los usa la sesión 2); al
  terminar el experimento se borran con
  `curl … /v1/command -d '{"v":1,"op":"preset_delete","name":"ab-graves-off"}'` (y el otro).

## Probarlo sin parlantes (Mac)

Tests: `cd host && hatch test ../probes/15-graves-y-volumen ../probes/16-calidad`. Contra
`aurasync service --simular` (ver `16-calidad/README.md`): `ab_graves.py` corre entero (las
respuestas por la entrada estándar, p. ej. `printf 'x\nxa\npa\nq\n' | $PY … ab_graves.py`);
`proteccion.py --ensayo` recorre la parte del servicio sin sonar, grabar ni tocar el volumen
Bluetooth; `curva_avrcp.py` falla al principio porque el Mac no tiene `pw-play` ni `pactl`.
