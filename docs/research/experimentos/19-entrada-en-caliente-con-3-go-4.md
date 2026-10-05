# 19 · Entrada en caliente con 3 Go 4: ¿entrar y salir con la sesión sonando mantiene la alineación?

**Pregunta:** con la sesión sonando, ¿un parlante que sale y vuelve a entrar (a mano, o solo tras
perderse) deja la alineación entre parlantes como estaba, y el cambio se oye limpio? Roadmap
i-7c8794-757041 (fase 2); decisión d-7c8794-618666; spec
`superpowers/specs/2026-10-05-virtual-speakers-and-hot-join-design.md` §5 a §8.

**Estado: protocolo listo, sin medir.** Se corre en `PC-Ryzen5` con los 3 Go 4 y el micrófono fifine.
La fase 2 está construida y probada solo con tests y `--simular`: nada se ha escuchado ni medido.

## 1. Entorno que se anota (antes de medir)

- Equipo: `PC-Ryzen5`. Kernel, PipeWire, WirePlumber y BlueZ (se leen el día de la medición).
- Los 3 Go 4: versión de firmware de cada uno (si se puede leer), batería y códec negociado.
- Versión de `aurasync` y la instalación. El modo de salida (`combinado` y `separado`), la
  recalibración (encendida y apagada) y el micrófono usado.
- Marca de cada cifra: MEDIDO solo si salió de una medición con micrófono; lo demás, INFERIDO.

## 2. Qué se mide

1. **Entrada y salida a mano.** Sesión sonando con los 3 parlantes: `speaker_leave` a uno, esperar
   10 s, `speaker_join`. Los otros dos tienen que seguir sonando sin cortes; se escucha el empalme y
   se cuentan los cortes (los que muestra el panel).
2. **Apagar y encender un parlante (regreso automático).** Apagar un Go 4 con el botón, esperar a que
   pase a `perdido`, volver a encenderlo y reconectar el Bluetooth **a mano** (el servicio nunca
   reconecta). Esperado: «volvió <nombre>» en el log en un intento (cada 10 s) y de nuevo «sonando».
3. **El freno.** Repetir la caída 3 veces dentro de 5 minutos: a la tercera tiene que dejar de
   intentar y el panel ofrece **Reintentar**. Anotar si el freno cuenta bien (ver 5.2).
4. **El desfase antes y después de entrar, con micrófono.** Calibrar con los 3, sacar uno, volver a
   hacerlo entrar y medir de nuevo el desfase entre parlantes con el mismo protocolo del
   experimento 12 (≥ 2 repeticiones independientes, estabilidad ante cambios del análisis). Es la
   cifra que decide si el aviso «recalibrá» del panel es necesario o exagerado. Con el lazo de
   recalibración encendido, medir además cuánto tarda en volver a la alineación.
5. **Lo que dejaron pendiente las revisiones** (nada de esto se midió):
   1. **El empalme entre el fundido de salida y el de entrada.** El corte (`motor.cortar`) es de
      80 + 80 ms y el cambio reconstruye la parte real: entre el fin del fundido viejo y el principio
      del nuevo puede haber hasta ~110 ms (INFERIDO, sin medir). Grabarlo con el micrófono en los
      parlantes que no cambian y buscar un hueco o un salto de fase.
   2. **Una vuelta automática que falla, ¿cuenta dos veces como caída?** El servicio la cuenta como
      una caída más (`note_lost` en el fallo) y el parlante ya contaba la que lo perdió. Sobre una
      sesión real, ver si con 2 caídas y 1 intento fallido el freno actúa antes de lo esperado.
   3. **El camino `separado`.** Repetir 1, 2 y 4 en `separado`: el spec §5 se enmendó para que
      también reconstruya toda la parte real (INFERIDO que cuesta lo mismo que `combinado`).
      `PC-Ryzen5` debe correr este camino además de `combinado`.

## 3. Cómo se decide

- **Pasa:** los parlantes que no cambian no tienen cortes oíbles ni un salto de desfase mayor que
  el piso de medición en el empalme; el desfase tras entrar queda dentro del que ya tenía la sesión
  sin cambios (repetido); el regreso automático ocurre en un intento y el freno actúa a la tercera caída.
- **Falla:** hay un hueco o un salto en el empalme, el desfase cambia más que su incertidumbre (el
  aviso de recalibrar pasa a ser obligatorio y el lazo debería apurarse), o el freno actúa antes o
  después de lo dicho. Cada falla se escribe con su cifra.

## 4. Resultado

Pendiente: sin medir.
