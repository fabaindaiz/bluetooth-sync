# JBL Go 4 y JBL Charge 6: qué soportan y cómo funciona su modo multiparlante

Investigación del 2026-09-25. Es una de las tres líneas de la fase de investigación
(ver [README](README.md)).

**Qué significa cada marca:**
- **VERIFICADO**: leído en una fuente primaria (ficha técnica o guía de JBL).
- **REPORTADO**: reseña, foro, discusión o fragmento de un resultado de búsqueda.
- **INFERIDO**: deducción que nadie comprobó.

Los números entre corchetes remiten a las fuentes del final.

Parlantes disponibles: **3× JBL Go 4 y 1× JBL Charge 6.**

## Resumen

- En el papel, ambos solo declaran **A2DP 1.4 y AVRCP 1.6** (Bluetooth clásico).
  **No declaran ningún perfil de LE Audio**, aunque en la práctica transmiten y
  reciben Auracast (BIS con LC3).
- El Auracast de JBL funciona como **un relé**: el teléfono se conecta por A2DP
  clásico a un parlante y ese parlante retransmite por Auracast a los demás.
  REPORTADO por el mantenedor de Bumble.
- **Un JBL sí recibe una transmisión Auracast de terceros**, siempre que el
  anuncio lleve un **bloque de datos de fabricante de Harman**. Con Bumble está
  probado en un Go 4. Las transmisiones de Samsung, Pixel o Windows no llevan ese
  bloque, así que casi seguro los JBL las ignoran.
- **Ninguno de los dos admite LE Audio unicast** (INFERIDO). Por eso la función de
  compartir audio del Pixel, que exige accesorios LE Audio, no les sirve.
- El estéreo de JBL solo se ofrece entre **dos parlantes del mismo modelo**, y What
  Hi-Fi critica su **mala sincronización**.
- Dato útil: **el Charge 6 acepta audio por USB-C desde un laptop**, sin pérdida,
  habilitado por actualización de firmware. Es una posible vía cableada.

## 1. Versión de Bluetooth, perfiles y códecs

| | JBL Go 4 | JBL Charge 6 |
|---|---|---|
| Bluetooth | 5.3 | 5.4 |
| Perfiles declarados | A2DP 1.4, AVRCP 1.6 | A2DP 1.4, AVRCP 1.6 |
| Multipoint | Sí | (no revisado) |
| Multiparlante | "Multi-speaker connection by Auracast" | Auracast |
| Estéreo | "Pair two Go4s for stereo" | "stereo pair two Charge 6 speakers" |
| Otras entradas | — | **USB-C audio "lossless" desde un laptop** (agregado por OTA) |
| Fuente | VERIFICADO [1][2] | VERIFICADO [3][4] |

- **Códecs:** JBL no publica ninguno. SBC es obligatorio en A2DP (INFERIDO). No
  encontré evidencia de AAC, LDAC ni aptX.
- **LE Audio:** JBL no menciona BAP, PBP, TMAP, CAP, BASS ni unicast LE. Que el Go
  4 transmita y reciba un BIS LC3 (sección 3) demuestra que tiene un stack de
  transmisión y recepción LE Audio. INFERIDO a partir de [5][6].
- **Sin LE Audio unicast:** ninguna ficha lo menciona. INFERIDO.
- **Registros del Bluetooth SIG (Launch Studio / QDID):** no se pudieron obtener
  (la API devolvió 502). Queda pendiente. Existe el FCC ID **APIJBLGO4D** [7].

## 2. Cómo funcionan el Auracast y el estéreo de JBL

### Topología
- El teléfono se conecta a un parlante por A2DP clásico. Si se presiona el botón
  Auracast **con esa conexión activa**, el parlante pasa a ser **transmisor**. Si
  se presiona **sin conexión clásica**, pasa a ser **receptor** y busca
  transmisiones. REPORTADO por el mantenedor de Bumble, a partir de sus pruebas
  [6].
- Por lo tanto, el Auracast de JBL es un relé: entra audio por A2DP clásico y sale
  por BIS de LE Audio. INFERIDO con confianza alta.

### Estéreo y modo fiesta
- **Estéreo:** JBL solo lo documenta para **dos parlantes del mismo modelo**
  (VERIFICADO [1][3]). SoundGuys dice que la app empareja "another Charge 6 for
  stereo" (REPORTADO [8]). No encontré nada que muestre un Go 4 en estéreo con un
  Charge 6, así que se asume que no está soportado (INFERIDO).
- **Modo fiesta** (mono, se pueden mezclar modelos): soportado entre "multiple JBL
  Auracast-enabled speakers". VERIFICADO [1][3].

### Detalles del protocolo
- **Cómo viaja L/R por el aire:** desconocido. No hay información sobre si usa BIS
  separados o asignación de canal.
- **Cifrado:** no se sabe si JBL cifra con un Broadcast Code. El comando `receive`
  de Bumble logró decodificar una transmisión de un Go 4 [5]. Eso sugiere que no
  va cifrada o que usa un código conocido, pero no está confirmado.
- **Máximo de parlantes:** JBL no da cifra. La prensa dice "ilimitados"
  (REPORTADO [9]).
- **PartyBoost:** Auracast lo reemplaza y **no son compatibles entre sí**. El
  Xtreme 4 es el único modelo que hace de puente entre ambos (REPORTADO [9][10]).

## 3. Recibir una transmisión de terceros (el hallazgo clave)

- **El stream sigue el estándar, pero con un filtro propietario.** En modo
  receptor, un JBL solo acepta transmisiones cuyo anuncio incluya un bloque de
  datos de fabricante propio de Harman. Que sea el company ID 87 (0x0057, Harman)
  es INFERIDO a partir de la lista de números asignados de Bluetooth.
- **El ejemplo de Bumble que funciona** con un Go 4 [5]:
  ```
  bumble-auracast transmit --manufacturer-data 87:00000000000000000000000000000000dffd ...
  ```
  La documentación aclara que el valor "dffd" **puede cambiar en otros modelos**.
  Por eso hay que verificar el Charge 6. REPORTADO y documentado.
- **Por lo tanto, un Linux o un macOS con Bumble y un controlador USB compatible
  con LE Audio puede transmitir LC3 a parlantes JBL.**
- **Detalle del Clip 5:** un usuario vio que el Clip 5 se colgaba hasta que subió
  el presentation delay de 40 000 a **80 000 µs (80 ms)**. Con ese cambio funcionó,
  incluso con una conexión clásica activa. Los datos de anuncio son idénticos entre
  el Go 4 y el Clip 5 (REPORTADO, marzo y abril de 2026 [6]).
- **Samsung, Pixel y Windows 11:** no agregan los datos de fabricante de Harman,
  así que muy probablemente **los JBL los ignoran** (INFERIDO a partir de [5]).
  SoundGuys informa que el JBL PartyBox "solo recibe Auracast de otros equipos JBL"
  y que JBL prometió corregirlo por OTA (REPORTADO [11]).
- **Compartir audio del Pixel:** Google dice que solo funciona con "accesorios
  Bluetooth LE Audio" y los conecta como receptores LE Audio (VERIFICADO [12]). Como
  estos parlantes no tienen unicast LE, se infiere que **no funcionan** con esa
  función.
- **Rutas sin reportes verificados:**
  - la opción de Samsung para escuchar una transmisión con un Go 4, Charge 6, Flip 7
    o Clip 5;
  - un dongle Creative BT-W5 o BT-W6, un nRF5340 o Windows 11 como emisor.

  Un sitio (bestsounds.net) dice que funcionó con un Samsung S24 con menos de
  30 ms. Parece contenido generado de baja calidad y **se descartó**.
- **App JBL Portable:** no hay evidencia de que pueda buscar o unirse a
  transmisiones Auracast de terceros. Solo administra los modos fiesta y estéreo
  de JBL [8]. La app *Headphones* sí agregó recepción Auracast y unicast LE, pero
  solo para audífonos (Tour Pro 3, Tune) (REPORTADO [13]).

## 4. Latencia y drift de reloj

- **No hay cifras medidas de latencia de punta a punta para estos parlantes.**
- **Presentation delay:** Bumble usa 40 ms y el Clip 5 necesitó 80 ms [6]. Ese
  valor corresponde solo al tramo BIS; no incluye el tramo A2DP.
- **El estéreo sincroniza mal:** la reseña del Go 4 de What Hi-Fi lista como
  defectos "Poor sound synchronisation of Auracast in stereo mode" y "a noticeable
  delay from one speaker to the other" (REPORTADO [14]).
  - **Contradicción con el estándar:** en un BIG, los receptores quedan alineados
    con pocas muestras de diferencia (ver
    [02-le-audio-auracast-linux.md](02-le-audio-auracast-linux.md) §5). Si el
    estéreo de JBL suena desfasado, puede ser porque el parlante transmisor
    reproduce lo que le llega por A2DP mientras el receptor reproduce el BIS, es
    decir, **dos caminos distintos**. INFERIDO. Con un emisor externo, todos los
    parlantes serían receptores del mismo BIG y esa asimetría desaparecería.
    Conviene medirlo.
- **Drift de reloj:** dentro de un BIG, los receptores se enganchan a la
  temporización ISO del emisor, así que en principio el drift queda acotado
  (INFERIDO). No hay datos específicos de JBL.

## 5. Actualizaciones de firmware (2024–2026)

Las páginas de soporte de JBL devuelven 403. Lo que sigue sale solo de fragmentos
de resultados de búsqueda, y las fechas parecen mal extraídas (REPORTADO):

- **Go 4** [15]:
  - guarda la preferencia de estéreo;
  - "improved Auracast performance with other JBL Auracast-enabled speakers";
  - la versión 0.5.3.0 agregó soporte multiparlante con el PartyBox Club 120 y el
    Stage 320. Eso sugiere que la compatibilidad entre modelos depende del
    firmware.
- **Charge 6** [16]:
  - 2.1.8.1 (enero de 2026): mejoras de rendimiento;
  - 3.0.7.1 (abril de 2026): soporte para micrófono EasySing.
- No encontré nada que diga que alguno de los dos ganó recepción Auracast estándar
  de terceros, unicast LE o códecs nuevos.

## Lo que no se pudo determinar

- Los QDID del Bluetooth SIG y la lista de roles (roles BAP, PBP, BASS/Scan
  Delegator).
- Códecs además de SBC.
- Si JBL cifra su transmisión y cómo transporta L/R (un BIS por canal o asignación
  de canal).
- **Si un receptor JBL elige un BIS o canal concreto dentro de una transmisión con
  varios BIS.** Es crítico para el surround y hay que probarlo con Bumble.
- Si el Charge 6 acepta los mismos datos de fabricante que el Go 4.
- Máximo de parlantes y latencia medida.

## Experimentos que esto sugiere (sin ejecutar)

1. Escanear con Bumble o `bluetoothctl` el anuncio de un Go 4 y del Charge 6 en
   modo transmisor. Así se obtienen sus datos de fabricante reales (el sufijo
   "dffd" de cada modelo), la BASE (cuántos BIS tiene y con qué asignación) y si va
   cifrado. **Este experimento no necesita comprar hardware si el escaneo funciona
   con el controlador que ya hay** (por confirmar).
2. Poner dos Go 4 en estéreo JBL y leer su BASE. Así se sabe cómo transporta L/R
   el propio JBL, lo que indica cómo hay que transmitirles.
3. Probar la entrada USB-C del Charge 6 desde Linux (aparece como tarjeta de sonido
   USB), para usarlo como canal cableado de latencia fija.

## Fuentes

1. Ficha técnica JBL Go 4: https://www.jbl.com/on/demandware.static/-/Sites-masterCatalog_Harman/default/dwec5ad861/pdfs/JBL%20Go%204_%20Specsheet_EN.pdf
2. Guía rápida JBL Go 4: https://www.jbl.com/on/demandware.static/-/Sites-masterCatalog_Harman/default/dw2819bc2c/pdfs/PA_JBL_GO%204_QSG_Global_SOP.pdf
3. Ficha técnica JBL Charge 6: https://www.jbl.com/on/demandware.static/-/Sites-masterCatalog_Harman/default/dw6197d6e0/pdfs/JBL_Charge_6_Specsheet_EN.pdf
4. Guía rápida JBL Charge 6: https://www.jbl.com/on/demandware.static/-/Sites-masterCatalog_Harman/default/dwfaa61296/pdfs/PA_JBL_Charge%206_QSG_Global_SOP_V10_online.pdf
5. Documentación de Bumble Auracast: https://google.github.io/bumble/apps_and_tools/auracast.html (fuente: https://raw.githubusercontent.com/google/bumble/main/docs/mkdocs/src/apps_and_tools/auracast.md)
6. Discusión de Bumble #894: https://github.com/google/bumble/discussions/894
7. FCC ID APIJBLGO4D: https://fccid.io/APIJBLGO4D
8. Reseña del Charge 6 en SoundGuys: https://www.soundguys.com/jbl-charge-6-review-132332/
9. What Hi-Fi, PartyBoost frente a Auracast: https://www.whathifi.com/speakers/wireless-speakers/what-is-jbl-partyboost-is-it-the-same-as-connect-and-auracast
10. SoundGuys, adiós a PartyBoost: https://www.soundguys.com/goodbye-jbl-partyboost-hello-auracast-134004/
11. SoundGuys, qué es Auracast: https://www.soundguys.com/what-is-auracast-149977/
12. Google, compartir audio en Pixel: https://support.google.com/pixelphone/answer/16483797?hl=en-GB
13. Notas de versión de la app JBL Headphones: https://support.jbl.com/howto/jbl-headphone-app-software-update-release-notes-us/000043762.html
14. Reseña del Go 4 en What Hi-Fi: https://www.whathifi.com/reviews/jbl-go-4
15. Notas de firmware del Go 4 (403, solo fragmento): https://support.jbl.com/us/en/howto/go-4-software-update-release-notes-us/000043859.html
16. Notas de firmware del Charge 6 (403, solo fragmento): https://support.jbl.com/ca/en/howto/charge-6-software-update-release-notes-mjuk4y8pst6-us/000053397.html
