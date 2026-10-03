# 09 · La primera escucha con 3 Go 4, y los tres errores que destapó

**Preguntas:** ¿el sistema completo suena? ¿se percibe el efecto envolvente? ¿el lazo de
recalibración corrige algo con parlantes de verdad? (i-7c8794-33c4bd, i-7c8794-2fe665)

**Respuestas: suena, pero el efecto es más débil de lo esperado; y el camino hasta ahí
destapó tres errores, dos de ellos invisibles en cualquier test.**

**Entorno:** `PC-Ryzen5`, CachyOS, kernel 7.2.8-1-cachyos, PipeWire 1.6.9, WirePlumber
0.5.17, BlueZ 5.87. **3× JBL Go 4** (Red `90:F2:60:75:4A:83`, Black `90:F2:60:DA:66:6D`,
Blue `90:F2:60:E3:07:39`), todos en **SBC** forzado. Micrófono fifine en el escritorio.
Fecha: 2026-09-29, de noche. **Volumen muy bajo** durante toda la sesión, a pedido del
usuario y por la hora: calibraciones a amplitud 0,1–0,2 y `run --volumen-db -12`.

**Qué se ejecutó:** `aurasync doctor`, `init`, tres `calibrate` y cuatro arranques de
`run --recalibrar`. El registro del lazo de la sesión útil está en
[datos/09-lazo-primera-sesion.jsonl](datos/09-lazo-primera-sesion.jsonl).

---

## 1. El resultado perceptual: se siente, pero menos de lo esperado. OBSERVADO

Palabras del usuario: *"probé el efecto a volumen muy bajo por la hora y se siente algo pero
no tanto como esperaba"*.

**Esto no es concluyente, y el motivo importa:** el envolvimiento depende de la **energía
lateral tardía** (`../09-efecto-ambiental-y-diseno-de-la-experiencia.md` §1), y a volumen muy
bajo esa energía cae por debajo del ruido de fondo de la pieza antes que el sonido directo,
que llega más fuerte. O sea que el volumen bajo ataca selectivamente justo el mecanismo que
produce el efecto. **Hay que repetirlo a nivel normal antes de concluir cualquier cosa.**

Lo que sí deja: **hacen falta controles que se puedan mover mientras suena**. Con una
calibración por sesión y un JSON que se lee al arrancar, probar un valor de `ambiente`
distinto cuesta reiniciar todo, y comparar dos ajustes de memoria no sirve. Queda como
i-7c8794-bdb678.

## 2. Error 1: un parlante mudo por un lazo de realimentación. MEDIDO

**Síntoma:** *"escucho solo por Black, Blue tiene un sonido muy difuso y Red no suena"*.

Red no sonaba porque **su stream estaba entrando al propio sink virtual de `aurasync`** en
vez de al parlante. Lo que el motor calculaba para Red volvía a entrar por la entrada.

| stream | destino real | debía ser |
|---|---|---|
| `pw-play` #1 | **`aurasync`** | Red |
| `pw-play` #2 | Blue | Blue |
| `pw-play` #3 | Black | Black |

**La causa, y es de las que no se adivinan.** WirePlumber guarda la salida que el usuario
elige en `~/.local/state/wireplumber/default-nodes`, y ahí estaba
`default.configured.audio.sink=aurasync`. Entonces **cada vez que el sink virtual aparece se
vuelve la salida por defecto**, y WirePlumber **mueve** el stream que apuntaba al default
anterior —que era Red—, *aunque ese stream tenga su `target.object` puesto*:

```
Sink: 2542                                          ← aurasync
    node.name = "pw-play"
    target.object = "bluez_output.90_F2_60_75_4A_83.1"   ← pide Red
```

**Por qué ningún test lo habría encontrado.** No hay error, ni excepción, ni log: un parlante
simplemente deja de sonar. Y `Reproductor.escribir` descarta un parlante en silencio cuando
su tubería se rompe, así que tampoco había por dónde enterarse. **Lo detectó el oído del
usuario**, y solo porque el parlante mudo era el de un canal que se nota.

**El arreglo, en tres partes:** los parlantes arrancan antes que el sink virtual y se les
manda medio segundo de silencio para que sus enlaces se formen mientras el sink todavía no
existe; `Reproductor.destinos_reales()` comprueba por `pw-dump` dónde cayó cada stream; y
`reparar_ruteo()` lo devuelve con `pactl move-sink-input`. Si después de reparar sigue mal,
`run` aborta en vez de sonar a medias.

**Y matiza P1** ([07](07-p1-la-captura-no-deja-huella.md)): el nodo desaparece sin huella,
pero **la preferencia de salida queda guardada**. No es una huella del programa —la escribe
WirePlumber cuando el usuario elige el dispositivo— pero es una huella de haberlo usado, y es
la que provoca este error en el arranque siguiente.

## 3. Error 2: un parlante en ambiente puro no se percibe como parlante. OBSERVADO

`init` ponía el tercer parlante en `ambiente = 1.0`: solo el ambiente extraído por coherencia
entre canales. Se escuchó como *"un sonido muy difuso"*, y tiene sentido: con material
corriente el ambiente extraído es poco, sin transitorios y sin contenido correlacionado, o
sea que **no da al oído nada con qué ubicar la fuente**. El efecto de precedencia necesita un
primer frente de onda, y ahí no hay.

**Cambiado el reparto por defecto:** 0,15 / 0,15 / **0,55**. Ninguno queda en ambiente puro.
Con 0,55 el tercer parlante lleva algo de directo, se ubica en la pieza, y sigue aportando la
energía lateral tardía que produce el envolvimiento. Hay un test que fija la invariante
(`0 < ambiente < 1` para todos).

## 4. Error 3: `--guardar` no guardaba. MEDIDO

El lazo corrigió Blue a **+5,82 ms** durante la sesión y el archivo quedó en **0,00**.
`--guardar` corre al cerrar, dentro del manejo de `KeyboardInterrupt`; un **SIGTERM** se lleva
el proceso sin pasar por ahí. Arreglado tratando SIGTERM como Ctrl-C. La deriva estimada
tampoco se imprimía, por lo mismo.

---

## 5. El lazo de recalibración con parlantes: funciona, conservador, y no converge. MEDIDO

**644 segundos, 32 decisiones**, con música por el sink virtual:

| Qué decidió | Veces |
|---|---|
| descartado: *la medición no es estable* | 13 |
| descartado: *se espera confirmación* | 7 |
| descartado: *no se pudo alinear* | 5 |
| **aplicado, confirmado** | **2** |
| *ya está alineado dentro del ruido* | 2 |
| sin señal (entre temas) | 3 |

**Lo que funcionó, y es el diseño haciendo su trabajo.** Las propuestas para Blue fueron
+6,7 · +11,3 · +6,6 · +4,0 · +4,8 ms — **todas distintas**, así que la confirmación las
rechazó a todas. Es exactamente para lo que existe: un desfase verdadero se ve igual dos veces
seguidas y un artefacto de correlación no. **Sin ese filtro se habrían escrito hasta 11 ms de
corrección equivocada.**

**Lo que no funcionó.** Dos cambios sí se confirmaron y se aplicaron:

| t | Red | Black | Blue |
|---|---|---|---|
| 403,4 s | 0,000 | +0,634 | **+2,465** |
| 463,6 s | 0,000 | +0,737 | **+5,822** |

Y las propuestas siguientes para Blue **crecieron** (+11,3 ms), cuando con ganancia de lazo
0,5 y un objetivo estable tendrían que encogerse. **Eso no es convergencia.** Después del
segundo ajuste, el filtro de estabilidad rechazó todo lo que quedaba de sesión.

**Nota del 2026-10-02: había además un error del lazo que explica este crecimiento**
([experimentos/11](11-sonda-enmascarada-en-simulacion.md), paso 2). La ventana de emisión guarda
la referencia **después** de la línea de retardo, así que lo que mide el lazo es la latencia propia
de cada parlante, sin las correcciones ya aplicadas; pero el controlador la trataba como un
residuo y la **volvía a sumar** en cada vuelta. Comprobado con el motor real: retardos aplicados
de 0/0/0, 0/5/0 y 4/0/2 ms daban las mismas correcciones. Una propuesta que crece después de
aplicar la anterior es justo esa firma. El lazo nuevo (`arrival_loop.py`) mide llegadas absolutas
y sigue la deriva de cada parlante; queda por confirmar con parlantes que las propuestas dejen de
crecer. La dificultad de medir a Blue contra la música, que sigue abajo, es real e independiente.

**El patrón señala a Blue, y Blue es el parlante de `ambiente` alto.** Su señal es la más
decorrelacionada y la que menos se parece a un frente directo, o sea la más difícil de medir
por correlación. La condición del efecto y la condición de la medición, que
[06](06-calibracion-rapida-y-recalibracion.md) presentaba como la misma, **empiezan a
separarse cuando el ambiente sube**: el parlante que más aporta al envolvimiento es el que
peor se mide.

## 6. Y la variación entre arranques: existe, y es grande. MEDIDO

Tres `calibrate` seguidos, sin tocar nada (el tercero y el segundo a amplitud 0,1):

| diferencia de llegada | c1 | c2 | c3 |
|---|---|---|---|
| Black − Red | +8,57 ms | +4,72 ms | **−6,46 ms** |
| Blue − Red | +3,73 ms | +12,81 ms | +7,60 ms |

**15 ms de variación**, con estabilidad informada de 0,00 ms en las tres. Esto **refuta la
hipótesis** de que la variación entre arranques que vio [06](06-calibracion-rapida-y-recalibracion.md)
fuera el error de retardos negativos de `gcc_phat`: está arreglado
([08](08-lazo-de-recalibracion-en-simulacion.md) §1) y la variación es mayor. Y **contradice
a E6** ([05](05-e6-a2dp-un-canal-por-parlante.md)), que midió repetibilidad de 0,1 ms y
concluyó *"sin necesidad de recalibrar en cada arranque"*.

**Consecuencia arquitectónica:** cada `calibrate` abre streams A2DP nuevos, así que su
calibración **muere con su stream**. Guardarla en el JSON para usarla en la sesión siguiente
no sirve. La corrección tiene que medirse **dentro del mismo stream que reproduce**, que es lo
que hace `run --recalibrar`. `calibrate` queda útil solo como diagnóstico.

## 7. El hueco que queda al descubierto: la calibración fabrica un sweet spot

`gcc_phat` mide el retardo **total**, que incluye el vuelo por el aire —**34 cm son 1 ms**—,
y la calibración escribe ese total. O sea que **alinea en el punto donde está el micrófono y
lo desalinea en el resto de la pieza**, que es lo contrario del objetivo declarado del
proyecto (`../09-efecto-ambiental-y-diseno-de-la-experiencia.md` §4: parlantes en los bordes,
oyente caminando).

Lo que conviene corregir es el desfase **electrónico**, igual en toda la pieza. `config.py`
tiene `retardo_acustico_ms` justamente para poder restarlo, pero **la calibración no lo usa**:
solo lo usa `alinear_por_geometria`, que nadie llama. Hace falta las coordenadas de los
parlantes y del micrófono, hoy opcionales y vacías.

Y hay un número que lo vuelve urgente: E6 midió 2,7 ms entre dos Go 4 y notó que son
**compatibles con 92 cm de diferencia de camino**. Si el desfase electrónico es casi nulo,
corregir el total puede ser **peor que no corregir nada** para alguien que se mueve.

---

## Veredicto

**El sistema funciona de punta a punta: tres parlantes, un canal distinto en cada uno, audio
del sistema entrando por un dispositivo virtual.** Eso está demostrado.

**Lo que no está demostrado es que produzca el efecto buscado**, y la única escucha se hizo en
condiciones que lo perjudican.

**Lo que hay que hacer, en orden:**

1. **Repetir la escucha a nivel normal**, con `ambiente` más alto, antes de cualquier otra
   conclusión perceptual.
2. **Controles en vivo** (i-7c8794-bdb678). Sin poder mover `ambiente`, `pan` y el retardo
   mientras suena, ajustar la experiencia es inviable: cada prueba cuesta un reinicio y la
   comparación queda en la memoria del oyente.
3. **Restar el camino acústico** de la calibración, o el sistema seguirá optimizando un punto
   en vez de una pieza.
4. **Entender por qué el lazo no converge en el canal de ambiente**, que es el que más importa.
