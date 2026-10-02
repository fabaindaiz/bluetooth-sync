# 11 · La sonda enmascarada bajo la música, en simulación (paso 1)

**Pregunta:** ¿una señal de prueba moldeada por debajo de la música permite medir el retardo
de cada parlante mientras suena, cuando correlacionar contra la música misma falla?
¿A qué margen bajo la música y con qué largo de ventana? (i-7c8794-e3e40d, spec
`superpowers/specs/2026-10-01-music-first-and-masked-probe-design.md` §4, paso 1 del plan.)

**Respuesta (SIMULADO):**
- **Sí.** A −20 dB bajo la música, con ventanas de 4 s o más, el error queda bajo 0,012 ms en
  el 95 % de los casos, y **ninguna** medición de 240 se equivoca por más de 1 ms. Se cumple
  en las dos semillas, con decorrelador y sin él.
- **Correlacionar contra la música**, que es lo que hace hoy el lazo, se equivoca por más de
  1 ms en el **70 a 85 %** de los casos, aun con el decorrelador real. Casi siempre confunde a
  Black con Blue.

**Entorno:** simulación, sin parlantes ni micrófono. `PC-Ryzen5`, Python 3.12, motor real de
`aurasync` (extractor de ambiente y decorrelador). Fecha: 2026-10-02. Script:
`probes/13-sonda-enmascarada/simular.py`. Datos crudos: `datos/11/sonda-simulada.json`.

## Cómo se simuló

- **Música sintética estéreo:** acordes de 4 voces con 8 armónicos, bajo, bombo y hi-hat a
  120 negras por minuto, y una cola de reverberación distinta en cada canal. Es no
  estacionaria, con el espectro típico, y **llega correlacionada a los tres parlantes**, que
  es el caso que falla hoy. **No es música real**: no había archivos de música en el equipo.
- **Lo que recibe cada parlante sale del motor real**, con los roles FL, FR y RR (pan −0,7,
  0,7 y 0,7; ambiente 0,15, 0,15 y 0,55). Black y Blue comparten pan y se distinguen solo
  por el ambiente. Se probó con el decorrelador y sin él.
- **Sala:**
  - retardos de 3,13, 7,61 y 12,27 ms y ganancias de 1, 0,8 y 0,6;
  - una caída de agudos como la del Go 4 en el micrófono (−10 dB cerca de 10 kHz);
  - reverberación de 0,4 s y ruido de micrófono a −60 dB.
- **Sonda:** ruido distinto para cada parlante, entre 300 Hz y 8 kHz. Se moldea por tercio de
  octava y por cuadro (STFT de 1024 puntos, salto de 512) a `margen` dB por debajo de la
  música **de ese parlante**, y se apaga en el silencio (bajo −50 dBFS). En cada ventana la
  lleva un solo parlante, por turnos.
- **Estimadores:**
  - **sonda:** correlación entre el micrófono y la sonda enviada, con pesos PHAT solo dentro
    de la banda de la sonda y el pico interpolado;
  - **música:** `medicion.calibrar` contra lo que se mandó a cada parlante, como hace hoy el
    lazo.
- **Repeticiones:** 20 ensayos por condición, con música distinta en cada uno. Todo con dos
  semillas independientes (CLAUDE.md: un desfase verdadero se ve dos veces).

## Resultados. SIMULADO

Error absoluto del retardo del parlante medido, en ms. Cada celda muestra mediana · p95 ·
fracción por encima de 1 ms, primero con la semilla 0 y después con la semilla 1.

| Ventana | Decorrelador | Método | Semilla 0 | Semilla 1 |
|---|---|---|---|---|
| 2 s | sí | sonda −15 dB | 0,003 · 0,008 · 0 | 0,001 · 0,007 · 0 |
| 2 s | sí | sonda −20 dB | 0,004 · 0,011 · 0 | 0,002 · 0,012 · 0 |
| 2 s | sí | sonda −25 dB | 0,006 · 0,023 · 0 | 0,004 · 0,025 · 0 |
| 2 s | sí | sonda −30 dB | 0,010 · 0,043 · 0 | 0,008 · 0,038 · 0 |
| 2 s | sí | **música** | 4,64 · 4,66 · **0,85** | 4,66 · 4,67 · **0,85** |
| 2 s | no | sonda −30 dB | 0,008 · 0,034 · 0 | 0,009 · **11,83 · 0,20** |
| 2 s | no | **música** | 4,66 · 9,14 · **0,70** | 4,66 · 9,14 · **0,70** |
| 4 s | sí | sonda −20 dB | 0,002 · 0,006 · 0 | 0,002 · 0,007 · 0 |
| 4 s | sí | sonda −30 dB | 0,006 · 0,013 · 0 | 0,007 · 0,014 · 0 |
| 4 s | sí | **música** | 4,32 · 4,66 · **0,80** | 4,50 · 4,66 · **0,70** |
| 8 s | sí | sonda −20 dB | 0,002 · 0,005 · 0 | 0,002 · 0,003 · 0 |
| 8 s | sí | sonda −30 dB | 0,006 · 0,014 · 0 | 0,003 · 0,015 · 0 |
| 8 s | sí | **música** | 4,55 · 4,67 · **0,80** | 4,50 · 4,66 · **0,75** |

Las 60 condiciones completas están en el JSON. Las que no se muestran (−15 y −25 dB con
4 y 8 s, y todas sin decorrelador salvo las dos de la tabla) están todas por debajo de
0,025 ms de p95 y sin errores de más de 1 ms.

**Lo que dicen los números:**
- **El error de la música es de 4,6 ms, justo la diferencia entre Black y Blue**
  (12,27 − 7,61). El estimador confunde a los dos parlantes que comparten pan: su contenido
  es casi el mismo, y el decorrelador (que corre la fase hasta ±1,5 ms) no alcanza para
  separarlos. Es lo mismo que se vio con parlantes en el experimento 09 §5: el lazo no
  convergía y el parlante con más ambiente era el que peor se medía.
- **La sonda no depende de la música.** Es independiente de lo que suena, así que no hay
  nada que confundir. El error crece apenas al bajar el margen (de 0,003 a 0,010 ms de
  mediana entre −15 y −30 dB) y al acortar la ventana.
- **La única falla de la sonda es una advertencia útil:** a −30 dB, con 2 s y sin
  decorrelador, la semilla 1 tiene un 20 % de errores grandes y la semilla 0 ninguno. Con
  una sola semilla no se habría visto. A −30 dB hacen falta ventanas de 4 s o más.

## Los límites de simular

La sala simulada es lineal y sin codec, y el micrófono es perfecto salvo el ruido. Lo que la
simulación **no** dice:
- **El SBC:** cuantiza cada subbanda. Con música fuerte, una sonda a −20 dB puede quedar
  cerca del ruido de cuantización (INFERIDO; experimento 03 §3.2). Se mide con parlantes.
- **La deriva de reloj** durante la ventana (~22 ppm: 0,09 ms en 4 s) ensancha el pico. No
  alcanza para mover una medición de 1 ms, pero se suma.
- **Si se oye.** Esa es la pregunta del paso 4 del plan (A/B ciego), y ningún número de acá
  la responde.

## Veredicto

**El paso 1 da luz verde.** La sonda resuelve en simulación lo que la correlación contra la
música no puede: separar parlantes que tocan casi lo mismo. Para el paso 2 (construirla en
el motor) se parte de:
- **−20 dB y ventanas de 4 s**, con un parlante por turno;
- −25 dB como primer candidato para el A/B de inaudibilidad;
- −30 dB solo con 8 s.

Pasos siguientes del plan: 2, construirla en el motor; 3, con parlantes, un retraso inyectado
encontrado dos veces con semillas distintas; 4, A/B ciego de inaudibilidad.
