# El efecto ambiental de varios parlantes alrededor, y cómo diseñarlo

Investigación del 2026-09-28. Responde a una pregunta nueva del usuario: **qué produce
el efecto de estar rodeado de parlantes con canales ligeramente distintos, cómo
potenciarlo, y qué hay que tener en cuenta al implementarlo.**

El caso de uso concreto, que condiciona todo lo que sigue: **parlantes en los bordes de
una pieza, el oyente moviéndose por ella, música (no cine ni juegos), y la intención de
mostrárselo a otras personas con su propia música.**

**Qué significa cada marca:**
- **VERIFICADO**: leído en una fuente primaria (el paper, la norma, el código).
- **REPORTADO**: blog, foro, o **el resumen de un paper sin haber leído el paper**.
- **INFERIDO**: deducción que nadie comprobó.

**Aviso honesto sobre las marcas de este documento:** las secciones 1 a 10 son sobre todo
**REPORTADO** — se leyeron resúmenes y páginas de referencia, no los papers completos.
**La sección 11 es VERIFICADO:** ahí están los parámetros concretos sacados de **dos
papers leídos enteros** (Potard y Burnett sobre decorrelación, Avendaño y Jot sobre
extracción de ambiente). **Si vas a implementar algo, leé la 11**; las anteriores dan el
marco. El único que sigue sin leerse es Bradley y Soulodre (paywall).

## Resumen

- **Lo que se busca tiene nombre en la literatura y se mide:** es **listener
  envelopment** (LEV), la sensación de estar inmerso y rodeado, y es **distinta** de
  *apparent source width* (ASW), el ancho aparente de la fuente. No son lo mismo y no se
  consiguen igual.
- **LEV viene de la energía lateral tardía**, no del ancho de banda ni de la cantidad de
  canales. ASW viene de las reflexiones laterales tempranas. REPORTADO.
- **La herramienta central para producirlo con pocos parlantes es la decorrelación**:
  mandar a cada parlante una versión del mismo material con forma de onda distinta pero
  que suena igual. Baja la correlación entre canales, y eso **evita la imagen fantasma y
  el filtrado peine**, y produce un campo difuso. REPORTADO.
- **El desfase no es solo error: es un parámetro de diseño.** Por debajo de 2 ms hay
  *summing localization*; entre 2 y 5 ms manda el parlante que llega primero; el eco
  separado recién aparece **por encima de ~50 ms en voz y ~100 ms en música**. Los 2–3 ms
  que se midieron entre los Go 4 caen en la zona donde el efecto es de imagen, no de eco.
- **El setup que se quiere armar tiene una tradición de 40 años:** los *loudspeaker
  orchestras* (Acousmonium del GRM, BEAST de Birmingham), donde parlantes **de timbres y
  tamaños distintos** se reparten por la sala y la obra se "difunde" en vivo. Eso
  reencuadra el Charge 6 al lado de los Go 4: la heterogeneidad es un recurso, no un
  defecto a corregir.
- **Para un oyente que se mueve, el panning correcto no es el de cine.** VBAP y
  ambisonics asumen un *sweet spot*. **DBAP** (Distance-Based Amplitude Panning) se
  diseñó justamente para arreglos irregulares sin suponer dónde está el oyente.
  REPORTADO.

## 1. Qué es el efecto, en términos que se pueden medir

La investigación de acústica de salas separa dos percepciones que se suelen confundir:

| Percepción | Qué es | De qué depende |
|---|---|---|
| **ASW** (apparent source width) | qué tan ancha se percibe la fuente | reflexiones laterales **tempranas**; se predice con IACC y con la fracción lateral temprana (J_LF) |
| **LEV** (listener envelopment) | la sensación de estar inmerso y rodeado | energía lateral **tardía**; se predice con el nivel lateral tardío (L_J) |

REPORTADO: [Bradley & Soulodre, late lateral energy y envolvimiento](https://www.sciencedirect.com/science/article/abs/pii/S0003682X00000554),
[relaciones entre IACC, LFE y ASW](https://www.researchgate.net/publication/13613500_Relations_among_interaural_cross-correlation_coefficient_IACCE_lateral_fraction_LFE_and_apparent_source_width_ASW_in_concert_halls),
[psicoacústica de ASW, espaciosidad y envolvimiento](https://www.researchgate.net/publication/233610015_The_Psychoacoustics_of_Apparent_Source_Width_Spaciousness_and_Envelopment_in_Performance_Spaces).

**Por qué importa para este proyecto:** el objetivo declarado es **LEV**, no ASW. Y LEV
se consigue con **energía que llega desde los costados y desde atrás, tarde y
decorrelacionada**. Eso significa que:

1. **No hace falta mucha precisión de imagen.** La imagen precisa es un requisito de ASW
   y del estéreo clásico, no del envolvimiento.
2. **Sí hace falta decorrelación.** Si los parlantes traseros reproducen lo mismo que los
   delanteros, la energía tardía está correlacionada con la temprana y el resultado es
   coloración, no envolvimiento.
3. **La cantidad de parlantes importa menos de lo que parece.** Tres fuentes bien
   decorrelacionadas y bien ubicadas pueden envolver más que seis correlacionadas.
   INFERIDO, pero se deduce directamente de que el predictor es el nivel lateral tardío.

Un trabajo reciente midió envolvimiento con síntesis granular espacial y ambisonics de
tercer orden, y sirve como referencia de método experimental:
[evaluación perceptual de LEV con síntesis granular espacial](https://arxiv.org/pdf/2301.10210) (REPORTADO).

## 2. La decorrelación: la herramienta principal

**Qué es:** transformar una señal en varias salidas con formas de onda distintas entre
sí, pero que suenan igual que el original.

**Qué produce, según Kendall** (el trabajo canónico, *The Decorrelation of Audio Signals
and Its Impact on Spatial Imagery*), en cinco familias de efectos: coloración tímbrica y
filtrado peine, **campos difusos**, externalización en auriculares, **ausencia de
desplazamiento de imagen** y **falla del efecto de precedencia**. REPORTADO:
[Kendall, PDF](https://www.researchgate.net/profile/Gary-Kendall-2/publication/240294548_The_Decorrelation_of_Audio_Signals_and_Its_Impact_on_Spatial_Imagery/links/540725bf0cf23d9765a83b7f/The-Decorrelation-of-Audio-Signals-and-Its-Impact-on-Spatial-Imagery.pdf),
[Semantic Scholar](https://www.semanticscholar.org/paper/The-Decorrelation-of-Audio-Signals-and-Its-Impact-Kendall/c97a6d0ca2fd6fee5a619f73082341a74f626cf6).

**Las dos consecuencias que este proyecto puede aprovechar directamente:**

- **"Ausencia de desplazamiento de imagen":** con señales decorrelacionadas, el sonido
  **no se corre hacia el parlante más cercano** cuando el oyente se mueve. Para alguien
  que camina por la pieza, eso es exactamente lo que se quiere.
- **"Falla del efecto de precedencia":** la decorrelación debilita la dominancia del
  primer frente de onda. O sea que **hace al sistema menos sensible al desfase**, que es
  justo la debilidad del camino A2DP. INFERIDO en cuanto a la magnitud, pero la dirección
  del efecto está REPORTADA.

**Y la métrica:** la correlación entre canales cerca de 0 da una imagen difusa y
espacialmente grande; cerca de 1, una imagen angosta. REPORTADO (mismo trabajo, y
[técnicas de decorrelación para ASW en 3D audio](https://beyondlistening.myblog.arts.ac.uk/files/2023/03/P_280.pdf)).

**Cómo se implementa:** ver §11, que tiene los parámetros concretos sacados de los
papers leídos completos.

**Corrección (2026-09-28):** una versión anterior de este documento listaba "retardos
cortos y distintos por canal" como técnica de decorrelación. **Está mal para parlantes.**
Potard y Burnett son explícitos: el retardo es la forma más simple de decorrelar, pero
**"on speakers, this technique should however be avoided due to the possible
comb-filtering effects caused by delays"** (VERIFICADO, §11). El retardo sirve para otra
cosa —el efecto Haas en los traseros— y va **después** del todo-paso, no en su lugar.

## 3. El desfase como parámetro de diseño, no solo como error

Esto reordena todo lo que se midió en E6. Las zonas del **efecto de precedencia**
(REPORTADO: [Wikipedia, precedence effect](https://en.wikipedia.org/wiki/Precedence_effect),
[SFU, Precedence Effect](https://www.sfu.ca/sonic-studio-webdav/handbook/Precedence_Effect.html),
[QSC, Haas effect](https://blogs.qsc.com/live-sound/what-is-haas-effect-and-how-to-take-advantage-of-it/)):

| Retardo entre dos fuentes | Qué se percibe |
|---|---|
| **< 2 ms** | *summing localization*: un solo sonido, ubicado **entre** las dos fuentes |
| **2 – 5 ms** | *localization dominance*: un solo sonido, ubicado en **la que llega primero** |
| **~5 – 9 ms** (clics) | empieza a aparecer el umbral de eco para transitorios |
| **10 – 30 ms** | zona de **Haas**: el retardado no domina la localización **aunque esté hasta 10 dB más fuerte** |
| **hasta ~35 ms** (voz) | siguen fusionados |
| **> ~50 ms (voz), > ~100 ms (música)** | se oye como **eco separado** |

**Tres conclusiones que se aplican directamente a lo medido:**

1. **Los 2–3 ms entre los tres Go 4 caen en *localization dominance*.** No producen eco
   ni coloración audible: corren la imagen hacia el parlante que llega primero. Para un
   campo envolvente eso es casi inocuo, y con decorrelación lo es todavía menos (§2).
2. **Los 45–150 ms del Charge 6 en AAC contra los Go 4 en SBC estaban en zona de eco
   audible.** Por eso forzar el mismo códec no es cosmético: era la diferencia entre
   "suena mal" y "suena bien".
3. **La zona de Haas es un recurso.** Se puede mandar a los parlantes traseros la señal
   con 10–25 ms de retardo **y más nivel**, y siguen sin robar la localización. Es la
   receta clásica para agrandar la sensación de espacio sin perder el frente. Y es
   gratis: es un retardo, no hardware.

**Y una salvedad que hay que medir, no suponer:** el *drift* de A2DP. Un desfase estable
de 3 ms es inocuo; uno que crece sin límite eventualmente entra en zona de eco. Es lo que
falta medir de E6 (la corrida de 30 minutos).

## 4. Panning para un oyente que se mueve: DBAP, no VBAP

La mayoría de las técnicas de espacialización **suponen un oyente en el sweet spot**
rodeado de parlantes. Para instalaciones y salas eso no sirve.

**DBAP** (Distance-Based Amplitude Panning, de Trond Lossius y colegas) extiende el
panning de intensidad constante a un arreglo de cualquier tamaño, **sin suposiciones
sobre las posiciones de los parlantes ni sobre dónde está el oyente**. La ganancia de
cada parlante se determina por su distancia a la fuente virtual. VERIFICADO en el paper:
[DBAP, ICMC 2009 (PDF)](https://jamoma.org/publications/attachments/icmc2009-dbap-rev1.pdf),
[versión en línea](https://quod.lib.umich.edu/i/icmc/bbp2372.2009.111/1/--dbap-distance-based-amplitude-panning?page=root&size=100&view=text).

**Por qué es la elección correcta acá:** los parlantes van en los bordes de una pieza, en
posiciones irregulares, y el oyente camina. Es literalmente el caso que DBAP resuelve, y
el caso que VBAP y ambisonics manejan mal.

**Consecuencia para la herramienta:** la configuración no debería ser "qué canal va a qué
parlante" (FL/FR/RL/RR), sino **las coordenadas de cada parlante en la pieza**. Con eso,
DBAP calcula las ganancias, y de paso la distancia da el retardo acústico a compensar.
Es un cambio de modelo de datos, y conviene tomarlo antes de escribir el código.

## 5. De dónde saca los "canales ligeramente distintos" una fuente estéreo

La técnica de referencia es la de **Avendaño y Jot**, *Ambience extraction and synthesis
from stereo signals for multi-channel audio up-mix*: se comparan las STFT de los canales
izquierdo y derecho, se calcula un **índice de coherencia entre canales**, y con una
función de mapeo no lineal se identifican las regiones tiempo-frecuencia que son
mayormente **ambiente**. Esas regiones se extraen y se sintetizan para alimentar los
canales de surround. REPORTADO:
[PDF del paper](https://www.irisa.fr/prive/kadi/Sujets_CTR/Emmanuel/Vincent_sujet1_article_avendano.pdf),
[Semantic Scholar](https://www.semanticscholar.org/paper/Ambience-extraction-and-synthesis-from-stereo-for-Avenda%C3%B1o-Jot/f9599dbae395df29fdb50dfdee18cc3a9209641a).

Una **medida de similitud** en el mismo marco permite estimar los coeficientes de paneo
de cada fuente y **re-panearlas a una cantidad arbitraria de canales**. REPORTADO (mismo
paper).

**Por qué esto es mejor que el upmix que ya trae PipeWire:** el `psd` de PipeWire manda a
los dos traseros la misma señal L−R en contrafase
([07](07-software-de-audio-en-el-pc.md) §4.2), o sea **dos canales correlacionados entre
sí**, que es lo contrario de lo que pide LEV. La extracción por coherencia da material
genuinamente distinto por canal.

**Ruta de implementación por etapas** (de barato a caro), INFERIDO:
1. **L−R más decorrelación distinta por parlante.** Casi gratis, y ya rompe la
   correlación que arruina el `psd`.
2. **Extracción de ambiente por coherencia** (Avendaño–Jot) en Python con numpy: STFT,
   coherencia, mapeo, síntesis. Es el salto de calidad grande.
3. **Descomposición primario/ambiente** más moderna, con extracción de canal central.
   Referencia: [descomposición primario-ambiente motivada geométricamente](https://arxiv.org/pdf/2206.02125) (REPORTADO).

## 6. La tradición que ya hace esto: loudspeaker orchestras

Lo que se quiere armar —parlantes repartidos por la sala, el oyente en el medio, la
música "difundida"— es la práctica de los ***loudspeaker orchestras***:

- **Acousmonium** (GRM, París): más de 80 parlantes **de tipos, tamaños y timbres
  distintos**, alrededor y arriba del público. El proceso de repartir la obra entre ellos
  se llama **difusión**, y lo hace una persona en vivo, ajustando distribución espacial y
  volumen durante la reproducción. REPORTADO:
  [Acousmonium](https://en.wikipedia.org/wiki/Acousmonium),
  [Sound diffusion](https://en.wikipedia.org/wiki/Sound_diffusion).
- **BEAST** (Birmingham): hasta más de 100 canales, en pares y anillos, más subgraves y
  "árboles" de tweeters colgados sobre el público. **Se diseñó originalmente como un
  sistema tipo acousmonium para difundir obras estéreo**, que es exactamente el caso de
  acá. REPORTADO: [BEAST](https://en.wikipedia.org/wiki/Birmingham_ElectroAcoustic_Sound_Theatre),
  [Sound on Sound, Loudspeaker Orchestras](https://www.soundonsound.com/techniques/loudspeaker-orchestras),
  [Tutschku, sobre la interpretación en acousmonium y BEAST](https://tutschku.com/texts-type/on-the-interpretation-of-multi-channel-electroacoustic-works-on-loudspeaker-orchestras-some-thoughts-on-the-grm-acousmonium-and-beast/).

**Tres cosas que esta tradición aporta al diseño, y que no salen de la ingeniería:**

1. **La heterogeneidad de los parlantes es un recurso.** El Acousmonium usa timbres
   distintos a propósito. Entonces el Charge 6 al lado de tres Go 4 **no es un problema a
   igualar**: es una voz distinta que se le puede asignar el material que le convenga
   (por ejemplo los graves, que es lo que mejor hace).
2. **La difusión es una performance, no un preset.** Lo que hace que la experiencia sea
   vívida es **ajustar en vivo** mientras suena. Para la herramienta, eso pide un modo de
   ajuste interactivo —niveles y retardos por parlante en tiempo real— y no solo un
   archivo de configuración.
3. **Estéreo difundido a muchos parlantes es un formato legítimo**, con 40 años de
   práctica. No hace falta material multicanal para que valga la pena.

## 7. Qué considerar al implementarlo

Ordenado por cuánto afecta el resultado, con lo medido en este repositorio al lado.

| # | Qué | Por qué | Estado acá |
|---|---|---|---|
| 1 | **Mismo códec en todos los parlantes** | códecs distintos = 45–150 ms = zona de eco | **resuelto**: `bluez5.codecs = [ sbc ]` ([experimentos/05](experimentos/05-e6-a2dp-un-canal-por-parlante.md)) |
| 2 | **Decorrelación por parlante** | es lo que produce LEV y lo que hace al sistema tolerante al desfase | **sin implementar** |
| 3 | **Retardo por parlante** (acústico + electrónico) | alinea, y además permite usar la zona de Haas a propósito | instrumento de medición **listo y validado** (`probes/e6-a2dp/`) |
| 4 | **Nivel y EQ por parlante** | los parlantes son distintos; sin igualar nivel, el campo se desbalancea | **sin implementar** |
| 5 | **Posiciones en coordenadas, no etiquetas de canal** | habilita DBAP y el oyente móvil | **decisión de diseño pendiente** |
| 6 | **Extracción de ambiente** en vez de `psd` | `psd` da traseros correlacionados entre sí | **sin implementar** |
| 7 | **Drift a lo largo del tiempo** | un desfase creciente termina en eco audible | **sin medir**: falta la corrida de 30 min |
| 8 | **Cantidad de streams** | 4 streams A2DP desestabilizan el enlace | **medido**: techo de 3 |

## 8. Cómo aprovechar el stack actual, y hasta dónde llega

**El techo medido hoy: tres parlantes por A2DP desde este equipo**
([experimentos/05](experimentos/05-e6-a2dp-un-canal-por-parlante.md)). Con tres, el
desfase es de 2–3 ms y repetible a 0,1 ms; con cuatro, salta a decenas de ms y se pierden
paquetes.

**Por qué el límite está ahí (INFERIDO, con un dato duro):** SBC estéreo típico va a
~328 kbps y A2DP 1.2 obliga a soportar hasta 512 kbps
([SBC codec](https://en.wikipedia.org/wiki/SBC_(codec)),
[README-SBC-XQ de PipeWire](https://github.com/pop-os/pipewire/blob/master_jammy/spa/plugins/bluez5/README-SBC-XQ.md)).
Tres streams son ~1 Mbps y cuatro ~1,3 Mbps, sobre un enlace BR/EDR compartido con el
tiempo de radio de cuatro conexiones ACL. **WirePlumber no expone un control de bitpool
de SBC** (solo `bluez5.enable-sbc-xq` y calidad para LDAC), así que **bajar el bitrate de
SBC no es una perilla disponible** (VERIFICADO en
[la doc de WirePlumber 0.5.17](https://pipewire.pages.freedesktop.org/wireplumber/daemon/configuration/bluetooth.html)).

**Tres formas de pasar de tres canales, ordenadas por costo:**

1. **El Charge 6 por cable USB-C** (E7, i-7c8794-b30648). **No consume ancho de banda
   Bluetooth**, así que da un cuarto canal dentro del techo medido: 3 Go 4 por A2DP + 1
   Charge 6 por USB. Hay que medir su latencia y compensarla con un retardo fijo, que es
   precisamente lo que el instrumento ya sabe hacer. **Es la opción más barata y la que
   conviene probar primero.** Y si se compra un segundo Charge 6, uno puede ir por cable
   y el otro por Bluetooth.
2. **Un segundo adaptador Bluetooth USB** (~US$10, un RTL8761B). Reparte 2+2 y duplica el
   tiempo de radio. **Pregunta abierta:** los dos adaptadores tienen relojes
   independientes, pero los sinks los alimenta el mismo grafo de PipeWire, así que el
   ritmo lo marca PipeWire y no el adaptador. **INFERIDO que se mantienen alineados; hay
   que medirlo** con el instrumento que ya existe.
3. **Auracast cuando lleguen las SuperMini.** Es el camino que resuelve el problema de
   raíz: un solo BIG, hasta 31 BIS, reloj común por protocolo
   ([02](02-le-audio-auracast-linux.md) §5). Pero depende de E4, que sigue sin poder
   hacerse.

**Y la observación que más simplifica el MVP:** por §1, **el envolvimiento no viene de la
cantidad de canales sino de la energía lateral tardía decorrelacionada**. Tres parlantes
bien ubicados y bien decorrelacionados pueden envolver más que cuatro correlacionados.
**Conviene gastar el esfuerzo en decorrelación antes que en sumar un cuarto canal.**
INFERIDO, pero es la consecuencia directa de que el predictor de LEV sea el nivel lateral
tardío y no el número de fuentes.

## 9. Un plan de experiencia para calibrar en la pieza y después mostrar

Pensado para iterar solo primero y presentar después. Cada paso produce algo audible.

**Etapa 0 — la base honesta.** Tres Go 4 en los bordes, mismo códec, nivel igualado de
oído, retardo acústico compensado con el instrumento. Reproducir estéreo simple (el mismo
L/R repartido). Sirve de referencia contra la que comparar todo lo demás.

**Etapa 1 — decorrelación.** Agregar un filtro todo-paso de fase aleatoria distinto por
parlante. **Es el A/B que más va a impresionar**, porque es donde el campo pasa de "tres
parlantes sonando" a "la pieza sonando". Conviene poder prenderlo y apagarlo en vivo.

**Etapa 2 — la zona de Haas.** Retardar los traseros 10–25 ms y subirles el nivel. Probar
varios valores de oído y anotar cuál gusta más, por género musical.

**Etapa 3 — ambiente extraído.** Reemplazar el L−R por extracción de ambiente por
coherencia. Aporta el material genuinamente distinto por canal.

**Etapa 4 — posiciones y movimiento.** Pasar a coordenadas y DBAP, y probar caminando.
Acá se nota si el campo se mantiene estable al moverse o si colapsa al parlante más
cercano.

**Para mostrárselo a alguien**, tres detalles que importan más de lo que parecen:
- **que traiga su propia música**, porque el efecto se juzga contra lo conocido;
- **un A/B instantáneo** (estéreo normal contra el campo envolvente), porque sin
  comparación el efecto se naturaliza en segundos;
- **dejarlo caminar**, porque el efecto de §2 (ausencia de desplazamiento de imagen) solo
  se aprecia moviéndose.

## 10. Qué conviene leer completo, y qué falta

**Estado de los tres que importaban** (actualizado 2026-09-28):
1. ~~**Kendall**, *The Decorrelation of Audio Signals…*~~ — ResearchGate devuelve 403. **Se
   resolvió con un sustituto mejor para implementar:** Potard y Burnett (DAFx'04), que sí
   da largos de filtro, cantidad máxima de señales decorrelacionadas y la advertencia
   sobre el filtrado peine. **LEÍDO, §11.1.**
2. ~~**Avendaño & Jot**~~ — **LEÍDO entero, §11.2**, con las ecuaciones (11) y (12), los
   parámetros de la tangente hiperbólica, la cadena del surround y los 5–20 ms de retardo.
3. **Bradley & Soulodre**, sobre nivel lateral tardío — **sigue sin leerse** (paywall). La
   pregunta abierta es si L_J se puede estimar con un micrófono simple.

**Lo que no se investigó:**
- Cómo medir LEV con un micrófono común en vez de un arreglo esférico o figura-de-ocho.
- Si hay literatura sobre campos envolventes con **parlantes Bluetooth de consumo**, con
  sus latencias y respuestas dispares.
- Umbrales de drift tolerable para música, distintos de los umbrales de eco.
- Si la decorrelación tiene costo perceptual en material muy transitorio (percusión).

## 11. Los parámetros concretos, sacados de los papers leídos completos

Esta sección es **VERIFICADO**: se leyeron los dos papers enteros, no resúmenes.

### 11.1 Decorrelación — Potard y Burnett (DAFx'04)
*Decorrelation Techniques for the Rendering of Apparent Sound Source Width in 3D Audio
Displays*, 7th Int. Conference on Digital Audio Effects, Nápoles, 2004.
[PDF](https://beyondlistening.myblog.arts.ac.uk/files/2023/03/P_280.pdf)

| Qué | Valor / regla |
|---|---|
| Mecanismo | **filtros todo-paso con respuesta de fase aleatoria, tipo ruido** |
| Por qué todo-paso | preserva el espectro de amplitud, y el oído es insensible a la fase: las salidas son **perceptualmente iguales pero estadísticamente ortogonales** |
| Implementación | FIR, IIR o Feedback Delay Network |
| **Largo del filtro** | **típicamente 100 polos y ceros** |
| **Cuántas salidas decorrelacionadas se pueden sacar** | **solo 5 o 6** con filtros fijos. Más allá, el largo finito hace que un par termine correlacionado |
| Cómo elegirlas | las fases tienen que ser **máximamente ortogonales**, por un proceso de **selección por mejor desempeño** (generar muchas, quedarse con el mejor conjunto) |

**Y la advertencia que corrige lo que este documento decía antes:**

> *"The simplest way to obtain decorrelated signals is to introduce a small time delay
> between them... the upper permissible delay is restricted by the perception of an echo;
> this is typically around 40 ms. **On speakers, this technique should however be avoided
> due to the possible comb-filtering effects caused by delays.**"*

**Para este proyecto eso cierra la discusión: la decorrelación va por todo-paso, no por
retardo.** Y 4 parlantes caben cómodos dentro del límite de 5–6 señales.

**Variantes, por si hacen falta después:**
- **Decorrelación dinámica:** fase aleatoria nueva en cada trama. Da más señales
  decorrelacionadas y genera "micro-variaciones que simulan las fluctuaciones del aire en
  movimiento". Conviene estructura **lattice** (FIR o IIR), que aguanta el cambio
  frecuente de coeficientes. **Pero los autores avisan que puede distraer y cansar**,
  porque se notan objetos cambiando de posición. Queda a criterio del diseñador.
- **Decorrelación por sub-bandas:** banco de filtros, decorrelación distinta por banda y
  *cross-faders* para graduar cuánta. Permite, por ejemplo, graves decorrelacionados y
  agudos correlacionados.
- **Decorrelación variable en el tiempo:** reinyectar periódicamente la señal original.
  Con la correlación variando entre 0 y 1 **hasta 10 Hz** da una fuente de extensión
  cambiante; **por encima de 10 Hz el efecto se destruye**, porque el sistema binaural no
  sigue el cambio de IACC.

**Dos resultados del experimento que sirven para ubicar los parlantes:**
- **Una densidad de fuentes demasiado alta produce una extensión percibida más angosta**,
  no más ancha. Argumenta a favor de **separar los parlantes hacia los bordes**, que es lo
  que se planea.
- **La capacidad de juzgar la extensión disminuye para sonidos que vienen de atrás.** O
  sea que no conviene gastar precisión en los traseros.

### 11.2 Extracción de ambiente — Avendaño y Jot (AES 22nd, 2002)
*Frequency Domain Techniques for Stereo to Multichannel Upmix*.
[PDF](https://www.irisa.fr/prive/kadi/Sujets_CTR/Emmanuel/Vincent_sujet1_article_avendano.pdf)

**El algoritmo, con las ecuaciones del paper:**

1. **STFT** de los canales, `X_i(m,k)`, con `m` tiempo y `k` frecuencia. La síntesis es
   por **inverse STFT con overlap-and-add**.
2. **Correlaciones cruzadas de corto plazo** con un **factor de olvido λ** (suavizado
   exponencial), para que el sistema sea causal y siga la no-estacionariedad. *Se pueden
   usar valores distintos de λ en bandas distintas.*
3. **Coherencia entre canales** `φ(m,k) = |Φ₁₂| / √(Φ₁₁·Φ₂₂)`, real y acotada en [0,1].
   Cerca de 1 = componente directo; cerca de 0 = ambiente.
4. **Índice de ambiente** `Φ(m,k) = 1 − φ(m,k)`.
5. **Criterio adicional, que hay que implementar o el resultado es malo:** los canales
   izquierdo y derecho tienen que tener **energías comparables durante unas cuantas
   tramas**. Sin esto, una fuente paneada totalmente a un lado también da coherencia baja
   y se confunde con ambiente.
6. **Modificación de las transformadas** con una función no lineal del índice:

   ```
   A_i(m,k) = X_i(m,k) · Γ[Φ(m,k)]                                    (11)

   Γ[Φ] = ((μ₁ − μ₀)/2) · tanh{σ·π·Φ̂} + ((μ₁ + μ₀)/2),   Φ̂ = Φ − Φ₀   (12)
   ```

   | Parámetro | Qué hace | Valor que da el paper |
   |---|---|---|
   | `Φ₀` | umbral del índice de ambiente | el codo de la curva |
   | `μ₁` | techo de la salida | **1**, "since we do not wish to enhance the non-coherent regions" |
   | `μ₀` | piso de la salida (cuánto se atenúa lo coherente) | es la perilla principal |
   | `σ` | pendiente | la figura 2 del paper usa **σ = 2 y σ = 8** |

   Se eligió la tangente hiperbólica porque la función tiene que ser **suave para evitar
   artefactos**.

**La cadena del surround, tal cual la dibuja el paper (figura 9):**

```
ambiente extraído → G(z) todo-paso decorrelador → z⁻ᴰ retardo → canal trasero
```

Y el paper dice **por qué van los dos, en ese orden**:
- el **todo-paso** porque el ambiente se extrae de los canales frontales, así que los
  traseros quedarían **algo correlacionados con el frente**, y eso "might create undesired
  phantom images to the sides of the listener";
- el **retardo** "to avoid de-localization due to the precedence effect and to simulate
  rooms of different sizes".

**El valor del retardo: 5 a 20 ms** — *"Applying a small delay (typically 5 to 20 ms) on
the rear-channel signals to alleviate unwanted localization artifacts caused by any
leakage of primary signals into the rear channels"*. Es más ajustado que los 10–25 ms que
este documento estimaba antes desde la literatura general de Haas.

El filtro todo-paso que citan es **Schroeder (1958)**, *An Artificial Stereophonic Effect
Obtained from Single Audio Signal*, JAES vol. 6, pp. 74–79.

**Dos cosas del paper que cambian cómo evaluar el MVP:**

1. **No hay que juzgar un canal aislado.** *"While distortion is sometimes audible when
   the signals are played individually, the **simultaneous playback of the five signals
   masks the distortion** and creates the desired envelopment in the sound field with very
   high fidelity."* O sea: si un parlante suena raro solo, eso **no** es motivo para
   descartar la configuración.
2. **La extracción de ambiente es la parte robusta; el índice de paneo no.** *"The
   techniques presented work mainly for studio mixes that use amplitude panning. While the
   performance of the **ambience extraction algorithm does not degrade significantly in
   live mixes**, the other methods based on the panning index suffer performance
   degradation."* **Consecuencia directa para el orden de implementación: primero el
   índice de ambiente, que aguanta cualquier material; el índice de paneo después, y
   sabiendo que es frágil.**

### 11.3 Lo que sigue sin leerse
**Bradley y Soulodre** sobre nivel lateral tardío: no se consiguió el texto completo (está
detrás de paywall). La pregunta abierta sigue siendo **si LEV se puede estimar con un
micrófono común**, o si hace falta uno figura-de-ocho. Mientras tanto, el envolvimiento se
juzga de oído, que para este proyecto alcanza.

### 11.4 La receta que sale de todo esto, en orden de implementación

```
estéreo de entrada
   │
   ├─► [1] índice de ambiente (coherencia + tanh)  ──► ambiente
   │                                                      │
   │                                              [2] todo-paso decorrelador
   │                                                  (distinto por parlante,
   │                                                   ~100 polos, ortogonales)
   │                                                      │
   │                                              [3] retardo 5–20 ms
   │                                                      │
   └─► frente (residuo)                                   └─► traseros
                    │
                    └──► [4] retardo y ganancia por parlante, medidos con micrófono
                    └──► [5] DBAP sobre coordenadas, cuando el oyente se mueve
```

Los pasos **[2] y [4] son los que más rinden con menos código**, y los dos se pueden hacer
con lo que ya está medido funcionando.
