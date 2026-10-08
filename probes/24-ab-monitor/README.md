# Sonda 24 · Ensayo A/B con el monitor

Sonda desechable (d-7c8794-3208b7) del experimento
[23](../../docs/research/experimentos/23-ensayo-ab-motor-y-ecualizacion-con-monitor.md). Habla con el
servicio en marcha por su API REST, con el token de `~/.config/aurasync/service.json`; no toca PipeWire ni
Bluetooth.

```bash
python3 ab.py preparar    # respalda, pone la curva de prueba y guarda los presets ensayo-*
python3 ab.py estado      # sesión, motor, salud, puntaje del A/B
python3 ab.py motor rust  # cambia el motor (numpy | rust) y espera a que lea
python3 ab.py restaurar   # deja todo como estaba y borra los presets ensayo-*
```

La curva es sintética: sirve para que las perillas de la ecualización se oigan, no dice nada de un
parlante real. Se borra cuando el resultado está escrito en el experimento.
