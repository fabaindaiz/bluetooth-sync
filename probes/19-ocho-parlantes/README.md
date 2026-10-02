# 19 · Ocho parlantes en simulación

Scripts del [experimento 16](../../docs/research/experimentos/16-ocho-parlantes-en-simulacion.md):
qué se rompe y qué escala si `aurasync` pasa de 3 a 8 parlantes. Todo es **SIMULADO** y se
corre en cualquier equipo. **No tocan `host/`**: el tope de 6 del decorrelador se levanta solo
dentro del proceso (`comun.lifted_limit`).

```bash
cd host
hatch run python ../probes/19-ocho-parlantes/costo.py              # 1. costo del motor, N = 3..8
hatch run python ../probes/19-ocho-parlantes/decorrelador.py       # 2. el tope de 6: bancos y métricas
hatch run python ../probes/19-ocho-parlantes/decorrelador_bandas.py  # 2. por banda, y lo que reciben los parlantes
hatch run python ../probes/19-ocho-parlantes/calibracion.py 4      # 3. calibrar N a la vez con un micrófono
hatch run python ../probes/19-ocho-parlantes/lazo.py 8             # 4. la sonda con 8, y seguir una deriva
```

Cada uno escribe su JSON en `docs/research/experimentos/datos/16/`, con el equipo, la versión de
Python y numpy y la carga del equipo al terminar.

| Archivo | Qué hace |
|---|---|
| `comun.py` | instalación de N parlantes en anillo (pan y ambiente por ángulo, compatibles con `control.ROLES`), el tope levantado, dónde se guardan los datos |
| `costo.py` | el motor real con N = 3..8, por defecto y con todo encendido, con y sin decorrelador; los motores se alimentan intercalados para que la carga del equipo pegue igual a todos los N |
| `decorrelador.py` | el banco actual forzado a 8, filtros de 512 y 1024, más candidatos, un buscador del peor par (minimax) y velvet noise; correlación con ruido rosa por pares, por octava y con desfase de ±1 ms, y planitud por tercio |
| `decorrelador_bandas.py` | un banco elegido por el peor par **por octava**, y la correlación de lo que el motor real le manda a cada parlante con los roles del anillo |
| `calibracion.py` | `medicion.calibrar` con N ruidos rosas independientes en una sala simulada con retardos y ganancias conocidos; 10 y 20 s, dos semillas; y N = 8 en dos grupos con un parlante ancla |
| `lazo.py` | la sonda enmascarada de `probes/13` con N = 3 y 8, por turnos y simultánea; y un seguimiento de deriva de una hora con las reglas del lazo |

Se borra cuando el experimento 16 quede anotado y sus propuestas pasen al roadmap
(d-7c8794-3208b7).
