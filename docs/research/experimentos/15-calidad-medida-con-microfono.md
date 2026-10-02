# 15 · Calidad medida con micrófono: suma L+R, directo contra motor, respuesta por parlante

**Pregunta:** con los parlantes y el micrófono, ¿el motor cambia el sonido respecto de la música
sin él, con la sonoridad igualada? ¿Cuál es la respuesta de cada Go 4 en el punto de escucha, y
cuánto de ella es del parlante y cuánto de dónde quedó el micrófono? ¿El Go 4 suma L+R o elige un
canal? Roadmap i-7c8794-50c1b3 (medidores de calidad: lo que falta con parlantes) e
i-7c8794-10ccb4 ("la referencia medida": directo contra motor); spec
`superpowers/specs/2026-10-02-microcuts-chain-and-quality-design.md` §6 y §8.

**Estado: protocolo y scripts listos, sin medir.** Se corre en `PC-Ryzen5` con los 3 Go 4 y el
micrófono fifine (`probes/16-calidad/`).

**Entorno:** se anota en cada `.json` de `datos/15/` (equipo, kernel, PipeWire, WirePlumber, BlueZ,
versión de aurasync, hora, batería, posiciones, colocación del micrófono, volumen AVRCP de cada
parlante, firmware si se leyó, la cadena y el estado del servicio). Pendiente de copiar aquí con
el resultado.

## 1. Por qué

- **"La salida directa suena mejor que el envolvente"** (el usuario, experimentos/10 §8). Se
  corrigieron dos causas medidas fuera de línea (la interpolación lineal del retardo y la entrada
  en 16 bits), pero quedó una sin medir: el directo no pasaba por el volumen del panel y sonaba
  más fuerte, y más fuerte suele oírse mejor (INFERIDO). Hace falta la comparación con micrófono y
  **la sonoridad igualada** (BS.1116 y MUSHRA lo exigen antes de comparar, VERIFICADO, research/11
  §1.3).
- **El micrófono no está calibrado:** solo sirve para comparar A contra B con el mismo micrófono,
  posición, ganancia y material, donde el cociente cancela micrófono y sala (INFERIDO, research/11
  §1.5). Por eso directo y motor se graban en el mismo lugar, en la misma tanda, y el directo dos
  veces (A-B-A): lo que varía la medición sola es la vara para leer el Δ.
- **La respuesta por parlante** que hoy entrega cada calibración (útil de ~100 Hz a 8–10 kHz,
  experimentos/10 §6) es de una sola colocación y sin coherencia: no separa el parlante de la
  posición del micrófono. Tres colocaciones y la coherencia lo separan (research/11 §1.1).
- **Si el Go 4 suma L+R no está documentado** (research/11 §3.3). Importa para que nada del motor
  ponga en un Go 4 dos versiones de fase opuesta, y para leer cualquier otra medición: hoy cada Go 4
  recibe la misma señal en L y R.

## 2. Lo que se construyó para medirlo (2026-10-02, en el Mac)

`probes/16-calidad/` (paso a paso en su `README.md`):

- `suma_go4.py`: a un Go 4, directo a su sink, un tono de 1 kHz (0,1 por canal) en L, R, L = R y
  L = −R, dos vueltas, con un marcador de ruido para ubicar todo en la grabación. Decide **suma**
  (L = −R ≥ 30 dB bajo L = R), **elige_L / elige_R**, **inconcluso** (piso de ruido a menos de 35 dB)
  u **otro**. Comprueba antes que nadie más alimenta ese sink, y después en `pw-dump` dónde quedaron
  el `pw-play` y el `pw-record`.
- `directo_vs_motor.py`: por canción, **a** (la mezcla de cada parlante sin el motor —pan, ganancia
  y retardo de calibración— al sink combinado, un canal `AUX` por parlante), **b** (la canción por el
  motor con la cadena por defecto), **a** otra vez. La ganancia del directo sale de correr el motor
  fuera de línea con los valores vivos del servicio y comparar la sonoridad integrada
  (`aurasync.dsp.loudness`); queda dentro de ±0,2 LU o no sigue. Compara ΔLUFS y Δ por sexto de
  octava de 100 Hz a 8 kHz, con segmentos de 8192 y 32768.
- `respuesta.py`: la calibración del servicio (`calibrate` + `calibration_dump`: ruido rosa
  independiente por parlante, todos a la vez) analizada con la coherencia, 3 colocaciones;
  `comparar.py` aplica el criterio entre colocaciones.
- `sistema.py`: reproducir y grabar con `pw-play`/`pw-record` en crudo (el mapa de canales es el
  pedido), **verificando en `pw-dump`** por el PID del proceso dónde quedó cada uno; el volumen
  del parlante con lectura de vuelta; y `Restaurador`, que anota cada cambio con su reversión antes
  de hacerlo y lo deshace al terminar, también con Ctrl-C o SIGTERM. `servicio.py`: la API REST y
  `restaurar_etapa`, que deja las elecciones de una etapa de la cadena exactamente como estaban.

### 2.1 Lo que se aprendió al construirlo. MEDIDO en señales sintéticas (tests)

- **Con los tres parlantes sonando a la vez, la coherencia común no sirve.** Con tres ruidos
  independientes por tres filtros conocidos y SNR de 40 dB, la coherencia de cada referencia con
  el micrófono queda **por debajo de 0,5** en toda la banda aunque la medición es buena: los otros
  dos parlantes cuentan como ruido. Resolviendo las tres a la vez por frecuencia (H = Gxx⁻¹·Gxy;
  Bendat y Piersol, entradas múltiples) y dando a cada parlante la coherencia que tendría solo con
  el ruido que de verdad quedó, γ² > 0,95 de 100 Hz a 8 kHz y |H| dentro de 0,3 dB del filtro
  conocido. Es el mismo mecanismo que research/11 §1.1 anotó como trampa del proyecto.
- **La deriva de reloj hay que corregirla trozo a trozo, y con trozos de 1 s.** Con 40 ppm entre
  "micrófono" y "parlante" en 16 s, sin corregir la deriva la coherencia de 4–8 kHz cae a ~0,01;
  corregida, queda > 0,95. Estimándola con trozos de 2 s, la propia deriva dentro del trozo ensancha
  el pico de correlación y la pendiente salía hasta 4 % corrida; con trozos de 1 s, dentro de
  0,05 %. Y cada trozo se ubica en la recta por su centro, no por su inicio: por el inicio, la
  ordenada salía 1,6 muestras corrida a 40 ppm.
- **El pico de GCC-PHAT no se interpola con una parábola:** es una sinc, y tres puntos lo sesgan
  hasta 0,12 muestras (5000,18 en vez de 5000,3). Interpolado con sinc alrededor del pico, dentro
  de 0,1.

Tests: 25 en `probes/16-calidad/test_analisis_calidad.py` (suma con un parlante sintético que suma,
que elige R y que no hace ninguna de las dos, con una sala y 370 ms de latencia; directo contra
motor con un realce conocido de 1,5 dB en un sexto; respuesta con tres filtros conocidos; WAV de
16 y 24 bits y de coma flotante; el grafo de `pw-dump`; el restaurador). Cada uno se vio fallar con una mutación:
sin duplicar el lado único del espectro, la comparación de suma invertida, el signo de la fracción
de retardo, la deriva por el inicio del trozo, el ruido residual sin los otros parlantes, el
criterio sin las bandas, la suma sin exigir que las vueltas se repitan, el destino leído del nodo propio, el restaurador en orden directo, y un
nombre de parlante que coincide con parte del nodo ("blue" está en todos los `bluez_output`, un
error real que apareció al probar).

**Probado en el Mac contra `aurasync service --simular` (SIMULADO, no es una medición):**
`respuesta.py` entero (γ² ≥ 0,94 hasta 8 kHz, curvas estables entre segmentos a 0,11 dB, la `eq`
apagada mientras dura y devuelta exactamente a su elección previa); `directo_vs_motor.py --ensayo`
(las comprobaciones, el motor fuera de línea y la ganancia: −22,3 dB con el panel a −20 dB y el
ambiente en uso; un WAV a 44,1 kHz se rechaza con el comando para convertirlo); `suma_go4.py` falla
al principio con "faltan pw-play, pw-record, pw-dump, pactl".

## 3. Criterios (escritos antes de medir)

| Prueba | Se acepta si |
|---|---|
| A · suma L+R | **suma** si L = −R queda ≥ 30 dB bajo L = R, con el piso de ruido ≥ 35 dB bajo L = R; las dos vueltas dentro de 1 dB en lo que suena |
| B · directo contra motor | **\|ΔLUFS\| ≤ 0,5 LU y ≤ 1 dB por sexto de octava de 100 Hz a 8 kHz, en 3 canciones × 2 sesiones**. Una canción cuenta solo si las dos pasadas del directo se repiten (≤ 0,25 LU, ≤ 0,5 dB por sexto) y el Δ no cambia más de 0,5 dB entre segmentos de 8192 y 32768; las bandas con menos de 10 dB sobre el ruido de la pieza no cuentan y se listan |
| C · respuesta por parlante | entre **3 colocaciones**, la curva normalizada (a la mediana de 400 Hz–2,5 kHz) dentro de **±1,5 dB de la media de 100 Hz a 8 kHz donde γ² ≥ 0,9** en las tres; y en cada colocación estable a ≤ 0,5 dB entre segmentos de 8192, 16384 y 32768 |

Si B no se cumple, el Δ por sexto dice dónde: lo siguiente es buscar la etapa con
`probes/11-calidad-de-la-cadena/cadena.py` en esa banda, no tocar el motor a ciegas. Si C no se
cumple en un tercio, ese tercio es de la sala o de la posición, no del parlante, y la ecualización
no debería corregirlo.

## 4. Protocolo

`probes/16-calidad/README.md`: primero `suma_go4.py`, después la curva AVRCP de
[experimentos/14](14-graves-y-volumen-con-3-go-4.md), después `respuesta.py` en 3 colocaciones y
`directo_vs_motor.py` en dos sesiones. `comparar.py` sobre todos los `.json` de `datos/15/`.

**Por decidir en PC-Ryzen5:**
- Las tres canciones (WAV a 48 kHz), las mismas en las dos sesiones: el oyente elige.
- Las tres colocaciones del micrófono, a describir con `--microfono-en`.
- Si el directo se compara alineado (por defecto, aísla el procesamiento) o también sin alinear,
  como lo escuchó el usuario (`--sin-alinear`), si el resultado alineado cumple y el oído sigue
  prefiriendo el directo.
- El mapa de canales del sink combinado: el script lo busca en `pw-dump` (`combine.audio.position`)
  y si no lo encuentra usa el orden de los parlantes del servicio, marcado como no verificado en el
  `.json`. Si sale no verificado, comprobarlo antes de leer el resultado.

## 5. Resultados

Pendiente.

## Veredicto

Pendiente.
