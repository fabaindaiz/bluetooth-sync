# Sonda 23 · auditoría medida del panel (desechable)

Scripts de la medición de [experimentos/22](../../docs/research/experimentos/22-auditoria-medida-del-panel.md).
Corren el panel en modo simulado con Playwright (el `Running` de `host/tests_browser`) y no tocan Bluetooth,
PipeWire, audífonos ni parlantes.

- `measure.py` inyecta **axe-core**, que **no está en el repositorio** (es de terceros, MPL-2.0). Para volver a
  correrlo, descargar `axe.min.js` (por ejemplo, de npm `axe-core`) en un directorio propio y apuntar
  `AXE` a ese archivo.
- `audit.js` son los ayudantes dentro de la página (contraste, tamaños, foco); `probe2-4.py` son las pruebas
  de teclado, foco y espacio; `report.py` arma las tablas.

Se borra cuando la auditoría quede aplicada y su resultado escrito (d-7c8794-3208b7).
