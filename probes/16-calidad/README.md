# 16 · Calidad medida con micrófono

Prepara [experimentos/15](../../docs/research/experimentos/15-calidad-medida-con-microfono.md)
(spec `docs/superpowers/specs/2026-10-02-microcuts-chain-and-quality-design.md` §6 y §8; roadmap
i-7c8794-50c1b3 e i-7c8794-10ccb4). Se corre en `PC-Ryzen5` con los 3 Go 4 y el micrófono fifine.
Los datos van a `docs/research/experimentos/datos/15/` (el `.wav` y el `.npz` no se versionan:
`.gitignore`). Se borra junto con `probes/15-graves-y-volumen/` cuando los dos experimentos tengan
su resultado (d-7c8794-3208b7): `15-…` usa `sistema.py` y `servicio.py` de esta carpeta.

| Script | Qué responde | Usa el servicio | Dura |
|---|---|---|---|
| `suma_go4.py` | ¿el Go 4 suma L+R o elige un canal? | no (sesión **detenida**) | ~1 min por parlante |
| `respuesta.py` | la respuesta de cada parlante con su coherencia, en 3 colocaciones | sí, sonando | ~1 min por colocación |
| `directo_vs_motor.py` | ¿el motor cambia el sonido respecto de la música sin él? | sí, sonando, sin música | ~7 min por sesión (3 canciones de 40 s) |
| `comparar.py` | los criterios sobre varias corridas | no | — |

Piezas: `analisis.py` (funciones puras, con tests), `sistema.py` (PipeWire: reproducir, grabar,
**verificar en `pw-dump`** dónde quedó cada stream, el volumen del parlante con lectura de vuelta,
`Restaurador`), `servicio.py` (la API REST con el token de `service.json`, y dejar una etapa de la
cadena como estaba).

## Antes de empezar (una vez por día)

```bash
cd ~/bluetooth-sync && git pull
PY="$(cd host && hatch env find default)/bin/python"
pactl list short sources          # el micrófono; si no es el de service.json, --microfono <nodo>
pactl get-source-volume <mic>     # anotarlo: la ganancia del micrófono queda fija todo el día
```

- Wi-Fi del AX210 apagado, como en experimentos/10 (la congestión tiene que ser solo Bluetooth).
- Anotar por corrida (los scripts lo piden como opciones y lo guardan en el `.json`): `--sesion`,
  `--microfono-en` (altura, distancia, hacia dónde apunta), `--posiciones`, `--bateria` (la que
  muestra la app de JBL o `state.devices[].battery_pct`), `--firmware` si se leyó en la app, y
  `--nota`. Kernel, PipeWire, WirePlumber, BlueZ, versión de aurasync y hora los anota el script.
- El volumen Bluetooth de cada parlante lo lee el script y lo guarda; **no lo cambia** ninguno de
  esta carpeta.

## Orden recomendado (las dos carpetas)

1. **`16-calidad/suma_go4.py`** — primero: si el Go 4 elige un canal en vez de sumar, cambia cómo
   se leen todas las demás mediciones (el motor manda a cada Go 4 la misma señal en L y R).
2. **`15-graves-y-volumen/curva_avrcp.py`** — la curva de volumen, sesión 1.
3. `16-calidad/respuesta.py` × 3 colocaciones.
4. `16-calidad/directo_vs_motor.py`, sesión 1.
5. `15-graves-y-volumen/proteccion.py`, sesión 1, y `ab_graves.py`, sesión 1.
6. **Otro día o con el micrófono en otro lugar:** la sesión 2 de `curva_avrcp`, `directo_vs_motor`,
   `proteccion` y `ab_graves` (la repetición entre mediciones independientes es la que filtra los
   artefactos, CLAUDE.md).
7. `comparar.py` de cada carpeta sobre todos los `.json`, y el resultado a los experimentos.

## 1 · `suma_go4.py`

```bash
# la sesión de aurasync detenida (panel → Detener) y nada sonando
$PY probes/16-calidad/suma_go4.py --parlante Red --microfono-en "1 m al frente, a la altura del parlante" \
    --bateria "Red=80" --nota "suma L+R"
```

Un tono de 1 kHz a amplitud 0,1 por canal: L solo, R solo, L = R, L = −R, dos vueltas (la segunda
al revés), con un ruido de marcador al principio para ubicar todo en la grabación. Antes de sonar
comprueba que nadie más alimenta el sink del parlante; después comprueba en `pw-dump` que el
`pw-play` quedó en ese sink y que `pw-record` graba del micrófono. **Qué escuchar:** el tono en las
cuatro condiciones; si L = −R suena igual de fuerte que L = R, el parlante no suma.

Decisión (`analisis.decidir_suma`): **suma** si L = −R queda ≥ 30 dB bajo L = R; **elige_L / R** si
L = −R suena como L = R y un canal solo no suena; **inconcluso** si el piso de ruido está a menos
de 35 dB de L = R (repetir con menos ruido en la pieza) o si lo que suena no se repite a 1 dB
entre las dos vueltas; **otro** si no es ninguna. Conviene
correrlo en los tres (`--parlante Black`, `--parlante Blue`): son el mismo modelo, pero el firmware
de cada uno puede no serlo.

## 3 · `respuesta.py`

```bash
# servicio sonando (la música puede estar: la calibración la corta mientras dura)
$PY probes/16-calidad/respuesta.py --colocacion 1 --segundos 20 --microfono-en "punto de escucha, 1,2 m de alto"
$PY probes/16-calidad/respuesta.py --colocacion 2 --segundos 20 --microfono-en "50 cm a la izquierda"
$PY probes/16-calidad/respuesta.py --colocacion 3 --segundos 20 --microfono-en "50 cm atrás, 1 m de alto"
$PY probes/16-calidad/comparar.py docs/research/experimentos/datos/15/*respuesta*.json
```

Usa `calibrate` (ruido rosa **independiente** por parlante, todos a la vez, amplitud 0,1) y
`calibration_dump`, copia el `.npz` a `datos/15/` y lo analiza: las tres respuestas a la vez por
mínimos cuadrados por frecuencia, la coherencia que tendría cada parlante solo, y la deriva de reloj
corregida trozo a trozo (`analisis.respuesta_multiple`). La ecualización en uso se **apaga mientras
dura** (entra en lo medido) y se vuelve a dejar exactamente como estaba (`--con-eq` para medirla).
No aplica nada: **no tocar "Aplicar" en el panel** después (la calibración queda como la última).

Criterio: por parlante, de 100 Hz a 8 kHz donde γ² ≥ 0,9 en las tres, la curva normalizada
(a la mediana de 400 Hz–2,5 kHz) no se aleja más de 1,5 dB de la media de las tres colocaciones;
y en cada corrida no cambia más de 0,5 dB entre segmentos de 8192, 16384 y 32768.
`--analizar <archivo.npz>` repite el análisis de una calibración guardada.

## 4 · `directo_vs_motor.py`

```bash
# servicio sonando, salida combinada, cadena por defecto, volumen del panel ≤ -14 dB,
# fuente "system" y la música en pausa (nada puede entrar al sink aurasync)
$PY probes/16-calidad/directo_vs_motor.py cancion1.wav cancion2.wav cancion3.wav \
    --desde 30 --segundos 40 --sesion 1 --microfono-en "punto de escucha"
$PY probes/16-calidad/comparar.py docs/research/experimentos/datos/15/*directo*.json
```

Las canciones: WAV a 48 kHz (`ffmpeg -i x.flac -ar 48000 x.wav`), elegidas por el oyente y
distintas entre sí (una con graves fuertes, una vocal, una amplia); las mismas en las dos sesiones.
Por canción suena **a** (la mezcla de cada parlante sin el motor —pan, ganancia y retardo de
calibración— al sink combinado `aurasync_salida`, un canal `AUX` por parlante), **b** (la canción
por el motor) y **a** otra vez. La sonoridad del directo se iguala en digital antes de sonar
(motor corrido fuera de línea con los valores vivos; ±0,2 LU o no sigue). Apaga el lazo de
recalibración mientras mide y lo vuelve a encender.

Criterio: |ΔLUFS| ≤ 0,5 LU y ≤ 1 dB por sexto de octava de 100 Hz a 8 kHz, en 3 canciones × 2
sesiones; una canción cuenta solo si las dos pasadas del directo se repiten (≤ 0,25 LU y ≤ 0,5 dB)
y el Δ no cambia más de 0,5 dB entre segmentos de 8192 y 32768. `--sin-alinear` mide el directo
sin los retardos (como lo escuchó el usuario en experimentos/10 §8); `--cadena-actual`, con otra
cadena (queda anotada).

**Qué escuchar:** si b suena distinto de a, anotarlo con la hora; es lo que el número tiene que
confirmar o no.

## Cómo queda el sistema

Ningún script de esta carpeta cambia el sistema (`/etc`, módulos, volúmenes de los sinks).
Cambian el **servicio** y lo devuelven al terminar, también con Ctrl-C o SIGTERM (`Restaurador`):
`respuesta.py` la etapa `eq`, `directo_vs_motor.py` el lazo de recalibración. Si algo no se pudo
restaurar, el script lo dice con `✗ NO se pudo restaurar` y queda en el `.json` (`restauracion`).
A mano: `curl -s -H "Authorization: Bearer $T" http://127.0.0.1:8731/v1/command -d '{"v":1,"op":"chain"}'`
muestra la cadena; `recalibrate {"active": true}` vuelve a encender el lazo.

## Probarlo sin parlantes (Mac)

```bash
cd host && hatch test ../probes/15-graves-y-volumen ../probes/16-calidad   # los tests del análisis

# el servicio simulado, con su propia configuración y sus datos fuera del repositorio
S=/tmp/prueba; mkdir -p $S
(cd host && XDG_CONFIG_HOME=$S/xdg XDG_DATA_HOME=$S/data hatch run aurasync service --simular --bind 127.0.0.1 --port 8799) &
export XDG_CONFIG_HOME=$S/xdg DATOS_PRUEBAS=$S/datos PUERTO=8799
# (iniciar la sesión por la API) y después:
$PY probes/16-calidad/respuesta.py --colocacion 1 --segundos 5           # entero: la sala simulada
$PY probes/16-calidad/directo_vs_motor.py cancion.wav --ensayo           # servicio y ganancias, sin sonar
$PY probes/16-calidad/suma_go4.py --parlante Red                         # falla al principio: sin pw-play
```

`DATOS_PRUEBAS` manda los datos a otra carpeta, para que un ensayo no quede en `docs/`.
