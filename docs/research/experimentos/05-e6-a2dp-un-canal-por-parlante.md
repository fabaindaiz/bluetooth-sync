# 05 · E6: A2DP con un canal por parlante, y el desfase medido

**Pregunta:** ¿se puede mandar un canal a cada parlante con A2DP clásico y
`libpipewire-module-combine-stream`, y qué desfase queda? (i-7c8794-24ea65)

**Estado: A medias, pero con el número que decide ya medido.** El mecanismo funciona,
el instrumento está validado, y hay cuatro reproducciones medidas. Falta la corrida
larga y la comparación entre dos parlantes del mismo modelo.

**Entorno:**
- `PC-Ryzen5`, **Intel AX210** (firmware BT `202-5.26`), kernel 7.2.7-1-cachyos,
  BlueZ **5.87**, PipeWire **1.6.9**, WirePlumber 0.5.17.
- Parlantes: **JBL Go 4 Black** (`90:F2:60:DA:66:6D`) en FL y **JBL Charge 6**
  (`78:66:F3:93:1D:B7`) en FR. Firmware de los parlantes **sin leer** (pendiente).
- Micrófono: **fifine USB** (`3142:a010`).
- Fecha: 2026-09-28.

**Datos crudos:** [datos/05/desfase-preliminar.txt](datos/05/desfase-preliminar.txt),
con la validación del instrumento y la corrida.

**Qué se ejecutó:**
```bash
probes/e6-a2dp/test-medir-desfase.py     # valida el instrumento, sin hardware
probes/e6-a2dp/medir.sh 8                # arma el sink combinado y mide
```

## Resultado

### El AX210 sostiene tres dispositivos A2DP a la vez: MEDIDO
Go 4 Black (SBC), Charge 6 (AAC) y Tune 770NC (AAC) tuvieron sinks simultáneos y
recibieron audio. **No hizo falta un segundo dongle**, que era la duda que el roadmap
anotaba para este experimento.

### El par estéreo de JBL vuelve a los Go 4 no direccionables: MEDIDO
Con los Go 4 **Blue y Red configurados como par estéreo** desde los propios parlantes
(según el usuario), **el Go 4 Red no llega a tener tarjeta en PipeWire**
(`pactl set-card-profile` responde `No such entity`) aunque BlueZ lo reporte
`Connected: yes`. Solo el Go 4 Black, que está suelto, es usable como sink
independiente.

**Es exactamente la limitación de la que el proyecto quiere escapar**, y aparece
también en el camino A2DP: mientras dos Go 4 están en par estéreo de JBL, no se les
puede mandar un canal a cada uno desde el PC. **Para medir con dos Go 4 hay que
deshacer el par estéreo en los parlantes.**

### `combine-stream` reparte un canal a cada parlante: MEDIDO
Con `libpipewire-module-combine-stream` se creó el sink `jbl_combine`, y el grafo
quedó así:

```
output.jbl_combine_bluez_output.90_F2_60_DA_66_6D.1   combine.audio.position = [ FL ]
output.jbl_combine_bluez_output.78_66_F3_93_1D_B7.1   combine.audio.position = [ FR ]
```

Un tono de 440 Hz en FL y 880 Hz en FR salió por el parlante que le correspondía. La
configuración está en `probes/e6-a2dp/combine.json`, con
`combine.latency-compensate = true`.

**Detalle de operación:** `pw-cli load-module` sin `-m` carga el módulo y termina, y el
módulo **muere con el proceso**, así que el sink no aparece. Con `pw-cli -m` el proceso
queda vivo. Eso mismo es la propiedad que busca P1: el sink desaparece solo al matar el
proceso, sin dejar nada.

### Cada parlante negoció un códec distinto: MEDIDO
| Parlante | Códec A2DP | Perfiles que ofrece |
|---|---|---|
| JBL Go 4 Black | **SBC** | `a2dp-sink` (SBC), `a2dp-sink-sbc_xq` |
| JBL Charge 6 | **AAC** | `a2dp-sink` (AAC), `a2dp-sink-sbc`, `a2dp-sink-sbc_xq` |

**Esto importa para el desfase:** codificadores distintos tienen latencias distintas,
así que parte del desfase es sistemático y viene de acá, no del transporte. Es un factor
que el roadmap no había previsto.

**Forzar el mismo códec falló, y la razón es sutil.** Al pasar el Charge 6 a
`a2dp-sink-sbc` con el Go 4 ya en SBC:

```
spa.bluez5: media codec switch: endpoint /MediaEndpoint/A2DPSource/sbc in use
spa.bluez5.device: failed to switch codec (-19)
```

PipeWire registra **un endpoint por códec** (`/MediaEndpoint/A2DPSource/{sbc,aac,ldac,…}`;
solo `aptx_ll` tiene variantes `_0` y `_1`).

**Pero eso NO significa que dos dispositivos no puedan compartir un códec:** después se
midió al **Tune 770NC y al Charge 6 los dos en AAC al mismo tiempo**, funcionando. Lo
que falla es el **cambio de códec en caliente** sobre un dispositivo ya conectado
mientras otro ocupa ese endpoint. Un dispositivo que negocia el códec al conectarse sí
lo comparte. **Queda pendiente** conseguir que el Charge 6 elija SBC en el momento de
conectarse, no por cambio posterior.

### Cuántos parlantes aguanta el enlace: MEDIDO, y es el número que manda
Con los 4 parlantes en **standalone** (el usuario deshizo el par estéreo) y **todos en
SBC**, se midió el desfase respecto del Go 4 Black variando cuántos parlantes suenan a
la vez. Cada fila es una reproducción distinta, 12 ráfagas.

| parlantes | Δt entre ellos | MAD | rango | ráfagas |
|---|---|---|---|---|
| **2 Go 4** | −0,34 / +0,11 / +0,26 ms | **0,12–0,35 ms** | 0,50–1,12 ms | 12/12 en las 3 |
| **3 Go 4** | Red −2,7 ms · Blue +1,8 ms | **0,25–0,68 ms** | 1,10–4,48 ms | 12/12 en las 3 |
| **4 (3 Go 4 + Charge 6)** | de −8 a +113 ms, salta | **2–30 ms** | 17–112 ms | 1 a 12 de 12 |

**Con dos parlantes iguales, la alineación es submilimétrica en el tiempo:** menos de
0,35 ms de dispersión y **0,6 ms de variación entre reproducciones**. Muy por dentro del
umbral de 5 ms para imagen estéreo.

**Con tres, sigue siendo excelente y sobre todo repetible:** Red queda en −2,7 ms y Blue
en +1,8 ms, **iguales a 0,1 ms entre reproducciones distintas**. Y esos pocos
milisegundos son compatibles con simple geometría: 2,7 ms son 92 cm de diferencia de
camino al micrófono. O sea que el desfase *electrónico* entre tres Go 4 puede ser
prácticamente nulo.

**Con cuatro se cae.** Dispersión de 2 a 30 ms, rangos de hasta 112 ms, ráfagas perdidas
y saltos dentro de una misma reproducción. **Y no es el códec:** se midió con los cuatro
en SBC y el resultado es el mismo. **El límite es la cantidad de streams A2DP
simultáneos**, no el codificador.

Antes de forzar SBC apareció también un fallo duro: con los 4 parlantes **más el Tune
770NC** conectado, un transporte no se pudo levantar.

```
Acquire /org/bluez/hci0/dev_90_F2_60_E3_07_39/sep2/fd4 returned error: org.bluez.Error.Failed
```

### El códec cuesta entre 45 y 150 ms: MEDIDO
Con el Charge 6 en **AAC** y los Go 4 en **SBC**, el Charge 6 aparecía entre **+45 y
+150 ms** respecto de un Go 4, y con dispersión alta. Con todos en SBC ese corrimiento
enorme desaparece (queda dentro del desorden de los 4 streams).

**Cómo se consigue el mismo códec en todos.** El cambio en caliente **falla** cuando otro
dispositivo ocupa el endpoint:

```
spa.bluez5: media codec switch: endpoint /MediaEndpoint/A2DPSource/sbc in use
spa.bluez5.device: failed to switch codec (-19)
```

Pero **varios dispositivos sí comparten un códec si lo negocian al conectarse**: se
midieron los **3 Go 4 los tres en SBC**, y antes el Tune y el Charge 6 los dos en AAC. Lo
que hay que hacer es limitar los códecs que se ofrecen, en la configuración de
WirePlumber:

```
# ~/.config/wireplumber/wireplumber.conf.d/50-aurasync-sbc.conf
monitor.bluez.properties = {
  bluez5.codecs = [ sbc ]
}
```

**Va en WirePlumber, no en PipeWire:** puesto en `~/.config/pipewire/pipewire.conf.d/`
como `context.properties` **no tiene efecto** (se probó). Con el archivo bien puesto y
`systemctl --user restart wireplumber`, los cuatro negocian SBC.

### El protocolo no aporta nada para compensar: MEDIDO
No hay **ni un `AVDTP Delay Report`** en la traza HCI de las conexiones y la
reproducción, y los nodos de PipeWire informan latencia 0. Era una de las preguntas de
este experimento: **A2DP no entrega la latencia del receptor**, así que la única vía para
alinear es medir con micrófono. La calibración acústica no es un extra: es el mecanismo.

### Medición vieja, con códecs distintos y sin deshacer el par estéreo
La primera tanda (Go 4 Black en SBC contra Charge 6 en AAC, 4 reproducciones) dio
medianas de **+60,23 / +47,39 / +50,80 / +60,25 ms**, con MAD de 2 a 4 ms y **12,9 ms de
variación entre reproducciones**. **Ese 12,9 ms era casi todo diferencia de códec**, no
del transporte: con el mismo códec y dos o tres parlantes iguales, la variación entre
reproducciones baja a 0,1–0,6 ms. Queda anotado porque fue el número que llevó a una
conclusión equivocada durante un rato. Datos en
[datos/05/desfase-4corridas.txt](datos/05/desfase-4corridas.txt).

### La corrida de 30 minutos: NO CONCLUYENTE, y la culpa es del estímulo
Se corrieron 30 minutos continuos con los 3 Go 4, una ráfaga cada 10 s sobre una cama de
ruido rosa —la cama hace falta para que PipeWire no suspenda el nodo entre ráfagas, porque
si no se estaría midiendo el reinicio del stream y no el drift—. Salieron **180 ráfagas**.

| parlante | mediana | MAD | deriva ajustada |
|---|---|---|---|
| Go 4 Red | −8,36 ms | **25,01 ms** | +55,3 ± 15,9 ms/h |
| Go 4 Blue | −7,30 ms | **9,03 ms** | +11,2 ± 10,7 (no significativa) |

**Ese "+55,3 ms/h" no es un resultado.** Es una recta ajustada sobre datos con 25 ms de
dispersión, cuando los mismos tres parlantes, medidos minutos antes con el mismo
instrumento pero **sin la cama de ruido**, daban MAD de **0,25 a 0,68 ms**.

**Y hay una prueba de que la dispersión no es física:** los valores saltan ±50 ms entre
muestras separadas 10 segundos. Un desfase de reloj de 50 ms en 10 s serían **5000 ppm**,
un absurdo para cualquier cristal. Un reloj de consumo anda en 20 ppm.

**La causa, y es un error de diseño del estímulo.** La resolución temporal de una medición
de retardo va como **1 / ancho de banda**. Una ráfaga tonal filtrada a ±400 Hz tiene toda
su información de tiempo en la envolvente, y eso da ~1,25 ms en el mejor caso. Al agregarle
la cama de ruido rosa —necesaria por otro motivo— se levantó el piso en todas las bandas y
esa envolvente se degradó. El instrumento que había servido para corridas de 12 segundos
sin cama de ruido no servía para esta.

**Está cuantificado** en [06](06-calibracion-rapida-y-recalibracion.md): con ruido de
conversación de fondo, el método de ráfagas tonales pasa de 1,5 ms de error a más de 25 ms,
mientras que ruido de banda ancha con GCC-PHAT se mantiene en 0,00 ms.

**Conclusión: el drift de A2DP sigue sin medirse.** Hay que repetir la corrida con ruido
decorrelado de banda ancha como estímulo, que además **no necesita cama de ruido aparte**,
porque el propio estímulo es continuo. Los datos quedan en
[datos/05/drift-30min.txt](datos/05/drift-30min.txt) como registro de qué no funciona.

## El instrumento, y por qué se validó primero

`probes/e6-a2dp/medir-desfase.py` reproduce **al mismo tiempo** un tono distinto por
canal (1 kHz en FL, 3 kHz en FR), graba con el micrófono, separa la grabación en dos
bandas por FFT, saca la envolvente analítica de cada una y compara el instante en que
cada una cruza el 20 % de su pico. La diferencia es el desfase.

`test-medir-desfase.py` sintetiza lo que grabaría el micrófono con un desfase conocido
y comprueba que el análisis lo recupera, usando **las mismas funciones** que la medición
real, no una copia.

**Y encontró dos errores reales, los dos destapados por parlantes y no por síntesis.**

1. **Contaba 13 ráfagas donde había 6**, con valores repetidos y una espuria de
   −145 ms: la envolvente ondula dentro del tono, baja del umbral y vuelve a disparar.
   Arreglado deduplicando los flancos que caen dentro de medio período, y exigiendo
   pico claro en **las dos** bandas.
2. **Con un desfase real grande, leía valores negativos absurdos** (−82 ms cuando el
   resto de la corrida daba +55 ms). La ventana de análisis tomaba **150 ms antes** del
   disparo, y con 55 ms de desfase real eso alcanza la cola de la ráfaga anterior. El
   disparo lo produce la ráfaga que llega primero, así que atrás no hay nada que buscar:
   ahora el margen es **60 ms antes y 450 ms después**. Además se informan **mediana y
   MAD**, que son robustas, y se marcan las ráfagas lejanas a la mediana.

Los casos sintéticos limpios no mostraban ninguno de los dos. Por eso el test ahora
tiene **casos con reverberación y desfases de 40, 55 y 120 ms**, que es el régimen donde
aparecieron: **14 casos, error máximo 0,04 ms**.

La precisión declarada es ~1 ms, por el semiancho de los filtros (500 Hz). Alcanza para
los umbrales del roadmap (5 ms para estéreo, 20 ms para traseros).

## Veredicto

**A2DP es mucho mejor plan B de lo que parecía, con un techo claro de tres parlantes.**

- **Con 2 o 3 parlantes iguales y el mismo códec, el desfase es de pocos milisegundos y
  repetible a 0,1 ms.** Eso está dentro del umbral de 5 ms para imagen estéreo, sin
  necesidad de recalibrar en cada arranque. Es el resultado que más cambia el plan.
- **Con 4 se cae**, con cualquier códec. El límite es la cantidad de streams A2DP
  simultáneos en este adaptador. Para un quad por A2DP haría falta un segundo adaptador,
  o bajar la calidad de SBC para que quepan (sin probar).
- **Hay que forzar el mismo códec.** Con códecs distintos el desfase salta a 45–150 ms.
  Se resuelve con `bluez5.codecs = [ sbc ]` en la configuración de WirePlumber.
- **El protocolo no ayuda:** no hay `AVDTP Delay Report`. La calibración con micrófono es
  el único mecanismo disponible, no una mejora opcional.
- **El par estéreo de JBL bloquea el camino A2DP igual que el Auracast:** dos Go 4
  emparejados entre sí no son direccionables por separado desde el PC. Hay que deshacerlo
  en los parlantes.

**Para el uso que se quiere** (parlantes en los bordes de una sala, el oyente moviéndose,
solo música, sin video): con 3 parlantes esto ya es usable, porque caminar un metro mueve
el tiempo de llegada ~3 ms, o sea tanto como el desfase electrónico medido.

## Qué falta para cerrarlo

1. **Repetir la corrida de drift con ruido decorrelado de banda ancha.** La que se hizo con
   ráfagas tonales quedó inservible (ver arriba). El estímulo nuevo es continuo por sí
   mismo, así que tampoco necesita la cama de ruido que causó el problema.
2. **Probar si 4 caben bajando la calidad de SBC**, o con un segundo adaptador.
3. **Separar geometría de electrónica:** medir las distancias de los 3 parlantes al
   micrófono con cinta y restar el término acústico, para saber si el desfase electrónico
   entre Go 4 es realmente ~0.
4. Leer el firmware de los parlantes.

## Cambios al sistema, y cómo se revierten

- **Códec forzado a SBC** (necesario para que el desfase no lo domine el codificador):
  ```bash
  rm ~/.config/wireplumber/wireplumber.conf.d/50-aurasync-sbc.conf
  systemctl --user restart wireplumber
  ```

- **Perfil de la tarjeta del micrófono.** El fifine estaba en
  `output:iec958-stereo+input:iec958-stereo`, o sea con la **entrada digital**
  seleccionada, que para un micrófono USB graba silencio. Se cambió a
  `output:analog-stereo+input:analog-stereo`. Se revierte con:
  ```bash
  pactl set-card-profile alsa_card.usb-3142_fifine_Microphone-00 \
    'output:iec958-stereo+input:iec958-stereo'
  ```
- **El sink combinado no deja nada:** vive en el proceso `pw-cli -m` y desaparece
  cuando termina el probe.
