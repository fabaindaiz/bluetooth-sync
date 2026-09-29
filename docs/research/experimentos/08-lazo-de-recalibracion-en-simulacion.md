# 08 · El lazo de recalibración en simulación, y un error que destapó

**Preguntas:** (1) ¿el estimador acierta cuando un parlante llega **antes** que el desfase
grueso? (2) ¿se puede recalibrar usando **el propio contenido** como referencia, que es lo
que permitiría corregir sin interrumpir la reproducción? (3) cuando falla, ¿lo dice?
(i-7c8794-33c4bd)

**Respuestas: (1) no lo hacía, era un error, y está arreglado. (2) sí, pero solo con
segmentos de 10 s y pidiendo confirmación. (3) NO, y eso es lo importante.**

**Entorno:** simulación, sin parlantes y sin micrófono. `PC-Ryzen5`, Python 3.12.12, numpy
del entorno de hatch. Fecha: 2026-09-29. **No se reprodujo ni se grabó nada**: el micrófono
está simulado sumando lo que emitió cada parlante con un retardo conocido, más una cola
reverberante de 150 ms y ruido a −34 dB.

**Qué se ejecutó:** `probes/lazo-simulado/simular.py`, con la semilla reiniciada al principio
de cada prueba para que los números se puedan repetir.

**El valor y el límite de simular.** El valor es que **el retardo verdadero se conoce**, así
que se puede medir el error del estimador, que es precisamente lo que con parlantes de verdad
no se puede saber. El límite es que la sala simulada no es una sala: los resultados se marcan
**MEDIDO EN SIMULACIÓN**, y ninguno reemplaza una medición acústica.

---

## 1. El error: el parlante que llega antes que la mediana. MEDIDO EN SIMULACIÓN

`gcc_phat` buscaba el pico **solo entre retardos no negativos**, con este comentario:

```python
# Solo retardos positivos: la referencia siempre llega después de haberse emitido.
```

La premisa es cierta contra la referencia **cruda**. Pero `calibrar` no mide contra la
referencia cruda: mide contra una **ya corrida por el desfase grueso**, y el desfase grueso
es la **mediana** entre parlantes. Con tres parlantes, el que llega antes que la mediana
queda con residuo **negativo** por construcción. Eso no se podía representar, así que el
estimador agarraba otro pico.

Con tres parlantes a 0, 3,4 y 7,1 ms y referencias de ruido independiente:

| Condición | Mínimo del buscador | Error máximo | Dispersión entre ventanas |
|---|---|---|---|
| sin reverb ni ruido | 0 ms (antes) | **43,82 ms** | 39,66 ms |
| sin reverb ni ruido | −120 ms (ahora) | **0,01 ms** | 0,00 ms |
| con reverb y ruido | 0 ms (antes) | **7,10 ms** | 6,99 ms |
| con reverb y ruido | −120 ms (ahora) | **0,01 ms** | 0,00 ms |

**Y lo peor: el filtro de validez no lo cazaba.** Corriendo `calibrar` entero con el
comportamiento viejo, con reverberación y ruido:

```
antes del arreglo    error=  7.10 ms  estabilidad=  0.01 ms  confiable=True
después              error=  0.01 ms  estabilidad=  0.00 ms  confiable=True
```

Es exactamente el modo de falla que `medicion.calibrar` ya advertía en su docstring —*varias
ventanas pueden encontrar el mismo pico equivocado y coincidir perfectamente en el error*—,
ahora con un caso concreto: **los tres tamaños de ventana se equivocaban igual**, así que la
estabilidad daba 0,01 ms y la medición se informaba como buena.

**Por qué no se había visto midiendo con parlantes.** En E6 los residuos entre Go 4 eran de
**décimas de ms**. Un residuo de −0,3 ms es pequeño comparado con el ancho que la sala le da
al pico, así que el máximo seguía asomando del lado positivo y el error quedaba en ese mismo
orden: invisible. El defecto crece con el desfase, o sea que **se manifiesta justo cuando la
calibración hace falta**. Se destapó al simular desfases de milisegundos.

**El arreglo:** `gcc_phat` acepta `retardo_minimo_ms` y busca el pico en un tramo contiguo
que trae los retardos negativos desde el final de la correlación circular. El valor por
defecto sigue siendo 0, porque contra la referencia cruda ese cero es un filtro útil contra
picos espurios; la medición fina de `calibrar` pasa −120 ms.

**Consecuencia para lo ya medido:** los números de E6 y de
[experimentos/06](06-calibracion-rapida-y-recalibracion.md) no quedan invalidados, porque
allí los residuos eran de décimas de ms. Pero **cualquier calibración con desfases de varios
ms hecha antes de hoy hay que repetirla.**

---

## 2. El contenido como referencia. MEDIDO EN SIMULACIÓN

La idea que haría la recalibración transparente: en vez de interrumpir con un estímulo,
correlacionar el micrófono contra **lo que el motor le mandó a cada parlante**, que es la
música misma. Funciona en principio porque el motor ya le manda a cada parlante una versión
decorrelacionada — **la condición del efecto envolvente es la condición de la medición**.

| Configuración | Correlación entre referencias | Estabilidad | ¿Confiable? | Error |
|---|---|---|---|---|
| contenido, 4 s | 0,95 | 0,32 ms | sí | **8,05 ms** ← falla en silencio |
| contenido, 10 s | 0,94 | 0,00 ms | sí | 0,01 ms |
| contenido, 20 s | 0,94 | 2,53 ms | no | 0,02 ms |
| sin decorrelador, 10 s | 1,00 | 3,39 ms | no | 8,52 ms |
| sin extracción de ambiente, 10 s | 0,98 | 2,79 ms | no | 3,70 ms |
| paneo total, canales disjuntos, 10 s | 0,85 | 0,49 ms | sí | **3,91 ms** ← falla en silencio |
| control: ruido independiente, 10 s | 0,01 | 0,00 ms | sí | 0,01 ms |

**Tres cosas de esta tabla:**

1. **Con el contenido se puede medir bien**: a 10 s el error es 0,01 ms, igual que con ruido
   dedicado. La idea es viable.
2. **Pero falla en silencio.** Dos filas pasan el filtro de estabilidad con errores de 8,05 y
   3,91 ms. El filtro es *conservador* —descarta mediciones buenas— y **a la vez insuficiente**,
   que es la peor combinación posible.
3. **La correlación entre referencias no predice nada.** 0,85 falló y 0,94 acertó. No sirve
   como criterio, así que no se usa. Lo que sí correlaciona con acertar es la **duración**.

---

## 3. ¿El error se repite? MEDIDO EN SIMULACIÓN

Es la pregunta que decide si pedir confirmación sirve de algo. Si el error fuera una
propiedad del material, se repetiría idéntico en cada segmento y confirmar no filtraría nada.

| Segmentos | Pasan estabilidad | De esos, con error > 1 ms | Discrepancia entre confiables consecutivos |
|---|---|---|---|
| 16 × 4 s | 3 / 16 | **2 de 3** | 1,30 a 8,05 ms |
| 10 × 10 s | 3 / 10 | **0 de 3** | 0,01 ms |

**Lo que dice:**

- **A 4 s el contenido no sirve**: dos de cada tres mediciones que pasan el filtro están mal
  por más de 1 ms.
- **A 10 s sí**: ninguna de las que pasa el filtro se equivoca por más de 1 ms, y las
  mediciones consecutivas coinciden en **0,01 ms**.
- **Confirmar funciona como filtro**, porque el error **no** se repite: entre segmentos
  confiables de 4 s la discrepancia llega a 8,05 ms, mientras que a 10 s, donde las
  mediciones son correctas, la discrepancia es 0,01 ms. O sea: un desfase verdadero se ve
  igual dos veces seguidas y un artefacto de correlación no.

---

## Veredicto

**El lazo se puede cerrar con el propio contenido, con tres condiciones:**

1. **segmentos de 10 s como mínimo** (a 4 s no);
2. **confirmación de todo cambio**, no solo de los grandes: es el modo `confirmar_todo` de
   `sincronia.Controlador`, y existe por el punto 2 de la sección anterior;
3. **el arreglo de la sección 1**, sin el cual el lazo escribiría errores de varios ms
   creyéndolos buenos.

**Qué queda sin responder, y necesita parlantes:**

- **Todo lo acústico.** La sala simulada no tiene la respuesta en frecuencia de un Go 4, ni
  su compresión, ni el ruido real de la pieza. Los números de acá son cota optimista.
- **La deriva sigue sin medir.** `Controlador.deriva_ms_h` la estima sobre la marcha a partir
  de lo que el lazo tuvo que corregir, pero eso es INFERIDO: sale del mismo estimador cuyos
  errores este documento filtra.
- **Si mover el retardo mientras suena se oye.** `dsp/retardo.py` lo acota por diseño a
  0,05 % de cambio de tono y hay un test que comprueba que no hay discontinuidad en la forma
  de onda, pero *no se oye audible* es una afirmación que solo se comprueba escuchando.
- **La tasa de aceptación.** Con 3 de 10 segmentos pasando el filtro, el lazo corrige cada
  tres o cuatro ciclos. Si la deriva resulta rápida, eso puede no alcanzar, y entonces habría
  que revisar `ESTABILIDAD_MAXIMA_MS`, que hoy está fijado en simulación.


---

## La prueba con parlantes: qué ejecutar

El lazo quedó **conectado a `aurasync run`** el 2026-09-29, así que la prueba se puede
ejecutar. Sigue **apagado por defecto**: sin `--recalibrar`, `run` se comporta como siempre.

### Antes de empezar

| Qué | Por qué | Cómo se comprueba |
|---|---|---|
| 2 o 3 Go 4 encendidos y conectados | con 4 el enlace se desestabiliza (E6); con 1 no hay nada que sincronizar | `aurasync doctor` |
| **un solo códec** | con códecs mezclados el desfase salta a 45–150 ms, que ya es eco audible | `aurasync doctor` lo marca |
| el fifine conectado y apuntando a la zona de escucha | es el instrumento | `aurasync doctor` ahora lista los micrófonos |
| `instalacion.json` con `pan` y `ambiente` puestos | es la decisión artística, no algo medible | `aurasync init` y después editarlo |
| **el volumen bajo** | pedido explícito, y además una prueba de sincronía no necesita nivel | `--volumen-db -12` y el volumen del sistema |

### Los comandos

```bash
aurasync doctor                     # parlantes, códec, micrófono e instalación
aurasync init                       # si todavía no hay instalacion.json
aurasync calibrate --amplitud 0.2   # el punto de partida; con --amplitud bajo
aurasync run --recalibrar \
    --volumen-db -12 \
    --cada 20 \
    --registro ~/aurasync-lazo.jsonl \
    --guardar
```

Después hay que **mandarle audio**: elegir `aurasync (envolvente)` como salida del sistema,
o mandarle una sola aplicación. Sin contenido sonando el lazo no mide, y lo dice
(`sin señal`).

### Qué mirar en el registro

Cada línea del `.jsonl` es una decisión, con su marca de tiempo. Las clases y qué significa
cada una:

| Clase | Qué pasó | Qué quiere decir |
|---|---|---|
| `sin señal` | no había contenido sonando | normal entre temas |
| `descartado` … *no es estable* | la medición cambió según el tamaño de ventana | el filtro funcionando; se espera ver bastantes |
| `descartado` … *se espera confirmación* | un cambio que todavía no se repitió | también normal: **todo** cambio necesita dos mediciones |
| `ajuste` … *ya está alineado* | el residuo está por debajo de 0,5 ms | **es el estado deseado** |
| `ajuste` … *aplicado* | se corrigió | el campo `retardos_ms` dice dónde quedó cada parlante |
| `descartado` … *no se pudo alinear* | la grabación no contiene la referencia | ver abajo |
| `error` | la medición reventó | el micrófono se desconectó, o el anillo tiene un salto |

Al cerrar con Ctrl-C, `run` imprime la **deriva estimada en ms/h** por parlante, que es la
primera medición de deriva que va a tener este repositorio, aunque sea INFERIDA.

### Los tres modos de falla previsibles, y qué significa cada uno

1. **Todas las líneas dicen *no se pudo alinear*.** La ventana del micrófono no contiene la
   referencia. La causa más probable es que la latencia de reproducción de los parlantes
   supere **1 segundo**: `alineacion_gruesa` busca hasta 1500 ms y la referencia ya arranca
   a 1,0 s. Está comprobado en simulación que a 900 ms todavía alinea y a 1600 ms no. Se
   arregla subiendo `MARGEN_DEL_MICROFONO_S`, pero **primero hay que medir la latencia**.
2. **Nunca se aplica nada, todo queda en *se espera confirmación*.** Las mediciones son
   correctas pero no se repiten dentro de 0,5 ms. Puede ser que la sala sea más ruidosa que
   la simulada, o que el contenido no dé para medir. Es el modo de falla *seguro*: el
   sistema no empeora nada, solo no mejora.
3. **Se oye un clic, un salto de tono o un corte al aplicar un ajuste.** Es lo que
   `dsp/retardo.py` existe para evitar y lo único que no se puede comprobar sin escuchar. Si
   pasa, el primer sospechoso es `velocidad_retardo_ms_s`, que se puede bajar.

**Y un corte de audio que sería un error nuestro:** `medicion.calibrar` cuesta **1,00 s de
CPU** sobre 10 s y tres parlantes, así que corre en un hilo aparte
(`sincronia.MedicionEnSegundoPlano`). Si igual se oyen cortes cada 20 s, el hilo no está
soltando el GIL como se supone y hay que pasarlo a un proceso.

### Qué se anota después

Este archivo, con: la versión de firmware de cada JBL, el desfase inicial que midió
`calibrate`, cuántas mediciones se aceptaron de cuántas, el residuo al que converge el lazo,
la deriva en ms/h y **si algo se oyó**. Recién entonces los resultados de arriba dejan de
ser cota optimista.
