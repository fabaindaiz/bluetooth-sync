# 17 · Modo espacial con 3 Go 4: ¿gana al estéreo solo y al clásico?

**Pregunta:** con los 3 Go 4 en la disposición `auto` (±60° y uno atrás), ¿el modo espacial se
prefiere, a ciegas y con la sonoridad igualada, frente al **estéreo solo** y frente al **clásico**?
El usuario (2026-10-04): "siento que a veces estéreo solo le ha ganado al algoritmo con 3 parlantes y
no debería ser así". Roadmap i-7c8794-eb1f78; decisión d-7c8794-be2477; spec
`superpowers/specs/2026-10-04-spatial-mode-and-auto-layout-design.md` §6.

**Estado: protocolo listo, sin medir.** Se corre en `PC-Ryzen5`.

## 1. Lo que dice la simulación (SIMULADO, 2026-10-04, `PC-Ryzen5`)

Con los tres principales de `auto` y una fuente paneada del todo a un lado, cuántos dB suena el
parlante de la fuente sobre el principal más lejano (`spatial_docs.metrics`, ruido de prueba):

| Modo | Separación | Ambiente (% de la energía, mezcla de coherencia ~0,75) |
|---|---|---|
| Clásico | 22 dB (el de atrás recibe la mitad de lo que viene de la izquierda) | 0,2 % |
| Espacial, carácter 0 | > 60 dB | ~0 % |
| Espacial, carácter 0,5 | > 60 dB | sube con el carácter |
| Espacial, carácter 1 | > 60 dB | > 10 % (test `test_full_character_takes_real_ambience`) |

**Lectura (INFERIDO):** en el clásico cada instrumento sale por los tres parlantes, y el decorrelador
emborrona también el directo; el espacial pone cada instrumento en su lado y decorrela solo el
ambiente. Es la hipótesis de por qué el estéreo solo podía ganar. Solo el A/B la confirma.

Dos defectos que la simulación encontró antes de llegar a los parlantes, ya corregidos: con dos
principales a ±90° un instrumento a un lado "daba la vuelta por atrás" y se filtraba al otro (17,6 dB);
y el carácter casi no movía el ambiente (el umbral del extractor, 0,5, dejaba pasar ~2 %).

### 1.1 Frente intacto y las líneas de base (SIMULADO, 2026-10-04, `PC-Ryzen5`)

Lo que recomendó [14](../14-el-proyecto-en-su-contexto.md) §4 y §6: un tercer modo, **frente
intacto** (`render=front`, el principio de Logic7: el par de adelante suena como el estéreo, sin
decorrelador ni Haas; el resto, solo el ambiente realzado y con Haas), y comparar contra líneas de base
ajenas. `probes/17-lineas-de-base/compare.py` pasa la misma señal por cada render con el `Motor` real
(decorrelador incluido) y por `surround` de FFmpeg 9.0.2 (5.1 plegado a los tres: FL + FC/√2,
FR + FC/√2, (BL + BR)/√2), en el anillo `auto` de 3, carácter 0,5. Señal sintética: una fuente al
centro y sala independiente en cada lado, 8 s.

| Render | Frente intacto (correlación con L/R) | Atrás contra adelante | Coherencia adelante–atrás | Separación (fuente a un lado) |
|---|---|---|---|---|
| Estéreo solo | 1,00 | — (mudo) | — | 60 dB |
| Clásico | 0,32 | −4,9 dB | 0,24 | 22 dB |
| Espacial | 0,91 | −16,9 dB | 0,17 | 60 dB |
| **Frente intacto** | **0,99** | **−7,7 dB** | **0,19** | 60 dB |
| FFmpeg `surround` | 0,97 | −9,8 dB | **0,91** | 60 dB |

(60 dB es el tope de la medida.) **Lectura (INFERIDO):** el clásico es el que más cambia el frente
(0,32: el decorrelador pasa por todo); el espacial lo conserva casi entero pero manda poco atrás; el
frente intacto conserva el frente y manda atrás el doble que el espacial, con contenido distinto
(coherencia 0,19). FFmpeg manda atrás casi una copia del frente (0,91): según 14 §0, eso es justo lo
que el parlante más cercano se "roba" cuando el oyente camina. Ninguna de estas cifras dice cómo
suena: es lo que sale hacia cada parlante.

**Falta:** el `psd` de PipeWire (es una mezcla de canales dentro de un stream; se mide grabando lo que
sale, no offline) y correr el script con música real (`--wav`). **Para escucharlas (2026-10-04):** la
fuente **«render multicanal»** del panel (`multichannel.py`, `source kind=multichannel`) toca un WAV con
un canal por parlante, en el orden de la sala, pasando solo por lo de cada parlante (su retardo, EQ,
ganancia, volumen y limitador: sin upmix ni decorrelador; `test_motor_direct.py`). `compare.py --out
DIR` escribe `estereo-solo.wav` y `ffmpeg-surround.wav`, y `stems.py` escribe `pistas.wav`. El A/B ciego
del panel compara presets, no fuentes: por ahora se cambia de fuente a mano entre el tema (para los
modos del motor) y el render.

### 1.2 Piloto de pistas separadas (2026-10-04, `PC-Ryzen5`, imagen `ml`)

La opción B de [14](../14-el-proyecto-en-su-contexto.md) §6: contenido distinto de verdad en cada
parlante. `probes/17-lineas-de-base/stems.py` separa un tema con **HTDemucs** (Demucs 4, PyTorch CPU,
en la imagen `ml`: d-7c8794-6b1a15) y reparte: s0 = voz I + batería I + bajo/√3, s1 = voz D + batería D
+ bajo/√3, s2 = "otros" (L + R)/√2 + bajo/√3; escribe un WAV por parlante y lo mide como `compare.py`.

**MEDIDO (la tubería):** 10 s de audio separados en ~4 s en la CPU del Ryzen 5 (≈ 2,7 veces más
rápido que el tiempo real), con el modelo bajado la primera vez al volumen de la imagen. **Sin
resultado todavía:** la única entrada fue una canción sintética (un tono como "voz", golpes, bajo y
ruido de sala), que HTDemucs no separa como separaría una voz real; sus números (frente 0,997, atrás
−2,1 dB, coherencia adelante–atrás 0,90) no dicen nada de las pistas. **Falta:** correrlo con temas
del usuario y escucharlo (hoy, reproduciendo cada WAV en su parlante; con la alineación del motor,
cuando exista la fuente multicanal de §1.1).

La coherencia adelante–atrás se mide **sobre 200 Hz** en los dos scripts: un bajo mandado a todos a
propósito hacía parecer copias a cualquier par de parlantes.

## 2. Protocolo (criterio escrito antes de medir)

1. Los 3 Go 4 en sus lugares de siempre, firmware anotado, la calibración del día aplicada.
2. Disposición `auto`, los tres principales. Volumen de prueba bajo (memoria del proyecto: nunca
   más de 0,2 en las pruebas de calibración; para escuchar, el nivel habitual del usuario).
3. Música del usuario: al menos tres temas distintos (uno con mezcla seca, uno con mucha sala, uno
   con instrumentos muy paneados).
4. **A/B ciego del panel con la sonoridad igualada** (`match`), 10 ensayos por comparación:
   - espacial (carácter 0,5) contra **estéreo solo** (los tres parlantes con la misma mezcla L+R, sin
     motor: el preset "estéreo" o `render=classic` con ambiente 0 y decorrelador apagado);
   - espacial contra **clásico** (los valores de fábrica).
   - **frente intacto** contra estéreo solo y contra el espacial (2026-10-04, research/14 §6);
   - en **tres posiciones**: el punto de siempre, caminando, y **al lado de un parlante** (la
     precedencia se juega ahí: research/14 §0).
5. **Criterio:** el espacial gana **al menos 8 de 10** en cada comparación (p ≈ 0,055 contra el azar).
   Si gana, se propone hacerlo el de fábrica (d-7c8794-be2477). Si no, se anota el resultado, qué
   tema perdió, y se revisan carácter, arco y Haas antes de repetir.
6. Repetir otro día (CLAUDE.md: un resultado verdadero aparece dos veces).

## 3. Resultados

Pendiente.

## Veredicto

Pendiente.
