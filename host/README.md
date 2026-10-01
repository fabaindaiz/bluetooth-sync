# aurasync (host)

El paquete Python que corre en el PC (Linux o macOS): la CLI `aurasync`.

**Hoy son el esqueleto y el panel de control sobre un motor simulado**
(d-7c8794-b1eaac, [docs/research/09](../docs/research/09-panel-de-control.md)). La
lógica de producto real (captura, DSP, reloj, emisor, parlantes, calibración) está
bloqueada hasta la decisión de seguir o no (i-7c8794-0d129c).

Antes de esa decisión se permite solo esto:
- la estructura, el tooling y tests de humo del stack (d-7c8794-f619c4);
- el panel de control con su motor simulado (d-7c8794-b1eaac).

El panel imprime una URL con un token que vale mientras corre ese proceso. Sin el
token no se puede usar, ni siquiera desde el mismo equipo.

La estructura prevista, con qué componente va en cada módulo, está en
[docs/research/08-integracion-y-plan.md](../docs/research/08-integracion-y-plan.md) §6.1.

## Qué funciona hoy en este Mac (2026-10-01)

| Funciona | No funciona todavía, y por qué |
|---|---|
| `scripts/check.sh` y los tests | **No suena nada.** El motor real está bloqueado hasta la decisión de seguir (E4) |
| `aurasync panel --demo [--lan]`: el panel completo con datos **simulados** | **El Bluetooth interno del Mac no sirve**: Bumble no llega al MT7932 en macOS (experimento 00). Hace falta una SuperMini por `serial:` |
| El QR para el teléfono en la red local | Falta el firmware de la SuperMini (`hci_uart_iso_timesync`), y no hay toolchain para compilarlo en el Mac (west, NCS) |
| | La captura del audio del Mac (process tap, P1) no está hecha. Pedirá el permiso de grabación de audio a la terminal, y la captura global es estéreo |

## Requisitos

- **hatch** (probado con 1.18.1). Maneja el Python 3.12 y los entornos por su
  cuenta, fuera del repositorio, y usa uv internamente para instalar. No hay que
  instalar uv aparte.
  - macOS: `brew install hatch`.
  - Linux: con el binario oficial de hatch o `pipx install hatch`. Queda en el home
    del usuario y no requiere root.
- **macOS arm64 o Linux x86_64.** `lc3py` solo publica ruedas para esas dos
  plataformas; en cualquier otra, la instalación falla (a propósito).

## Comandos

```bash
cd host
hatch test                  # tests (pytest)
hatch check                 # lint, formato y tipos (ruff y pyrefly, con las versiones que fija hatch)
hatch run aurasync --version
hatch run aurasync panel --demo          # panel de control, solo en este equipo
hatch run aurasync panel --demo --lan    # también desde el teléfono, con QR en la terminal
hatch run browser:test                   # el panel en Chromium y WebKit con Playwright 1.60
```

Desde la raíz, `scripts/check.sh` corre todo esto junto con el chequeo del bundle,
**salvo `browser:test`**: ese necesita el caché de navegadores de Playwright
(`~/Library/Caches/ms-playwright`, con chromium-1223 y webkit-2287). El Firefox de
Playwright no arranca en este Mac.

## Versiones

- `bumble` y `lc3py` van con versión exacta en `pyproject.toml`, porque Bumble
  todavía no llega a 1.0 y cambia su API.
- Las dependencias transitivas no se fijan: los lockfiles de hatch 1.18.1 borran el
  propio proyecto del entorno. La explicación está en el comentario de
  `pyproject.toml` y en d-7c8794-c23c20.
