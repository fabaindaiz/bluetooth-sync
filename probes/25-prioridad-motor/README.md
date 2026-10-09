# Sonda 25 · Prioridad del hilo del motor

Sonda del experimento [23 §4](../../docs/research/experimentos/23-ensayo-ab-motor-y-ecualizacion-con-monitor.md).
Corre el `Motor` real (4 parlantes, la cadena de `HP-O16`) en un hilo que tiene que entregar un bloque
cada 85,3 ms. Cuenta los bloques que terminan después de su plazo y alterna, cada `--slice` segundos,
entre la prioridad normal y la pedida con `priority.raise_engine_priority`. No toca PipeWire ni el
servicio. Se corre mientras algo carga la CPU (la suite de tests a prioridad normal):

```bash
cd host && hatch test tests &
$(hatch env find hatch-test.py3.12)/bin/python ../probes/25-prioridad-motor/medir.py --seconds 610 --slice 60 --nice -15
```

Queda como fuente de la cifra del experimento 23 §4 (`probes/README.md`).
