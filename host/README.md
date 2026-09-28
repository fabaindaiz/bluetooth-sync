# aurasync (host)

El paquete Python que corre en el PC (Linux o macOS): la CLI `aurasync`.

**Hoy es solo el esqueleto.** La lógica de producto (captura, DSP, reloj, emisor,
parlantes, calibración) está bloqueada hasta la decisión de seguir o no
(i-7c8794-0d129c). d-7c8794-f619c4 permite, antes de esa decisión, solo esto:
- la estructura;
- el tooling;
- tests de humo del stack.

La estructura prevista, con qué componente va en cada módulo, está en
[docs/research/08-integracion-y-plan.md](../docs/research/08-integracion-y-plan.md) §6.1.

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
hatch fmt --check           # lint y formato (ruff, con la versión que fija hatch)
hatch run aurasync --version
```

Desde la raíz, `scripts/check.sh` corre todo esto junto con el chequeo del bundle.

## Versiones

- `bumble` y `lc3py` van con versión exacta en `pyproject.toml`, porque Bumble
  todavía no llega a 1.0 y cambia su API.
- Las dependencias transitivas no se fijan: los lockfiles de hatch 1.18.1 borran el
  propio proyecto del entorno. La explicación está en el comentario de
  `pyproject.toml` y en d-7c8794-c23c20.
