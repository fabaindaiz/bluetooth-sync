"""aurasync: un canal de audio distinto a cada parlante Bluetooth, sincronizados.

**Estado (2026-09-28):** se construye el núcleo compartido, con **A2DP como primer
backend emisor** (d-7c8794-9afee2). La decisión sobre Auracast sigue abierta hasta E4,
que hoy es imposible porque el adaptador de este equipo no puede transmitir
(`docs/research/experimentos/03-e1-iso-en-el-ax210.md`).

El objetivo del MVP no es la fidelidad de imagen estéreo sino el **envolvimiento**:
parlantes en los bordes de una pieza y el oyente moviéndose. Eso cambia qué importa —la
decorrelación más que la precisión de sincronía— y está fundamentado en
`docs/research/09-efecto-ambiental-y-diseno-de-la-experiencia.md`.

Los módulos:

- `config`: la instalación, con los parlantes descritos por **coordenadas** y no por
  etiquetas de canal, para poder usar DBAP con un oyente que se mueve.
- `dsp.decorrelate`: filtros todo-paso de fase aleatoria (Potard y Burnett).
- `dsp.ambience`: extracción de ambiente por coherencia entre canales (Avendaño y Jot).
"""

__version__ = "0.0.0"
