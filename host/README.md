# aurasync (host)

El paquete Python que corre en el PC (Linux o macOS): la CLI `aurasync`.

**Estado (2026-09-28):** el núcleo está construido, con **A2DP como primer backend
emisor** (d-7c8794-9afee2). Auracast sigue abierto hasta E4.

## Qué hay en cada módulo

| Módulo | Qué hace | De dónde salen sus parámetros |
|---|---|---|
| `config.py` | la instalación: qué parlantes hay y cómo se corrige cada uno. Descritos por **coordenadas opcionales**, no por etiquetas de canal | [09](../docs/research/09-efecto-ambiental-y-diseno-de-la-experiencia.md) §4 |
| `dsp/decorrelate.py` | filtros todo-paso de fase aleatoria: lo que produce el envolvimiento | Potard y Burnett, [09](../docs/research/09-efecto-ambiental-y-diseno-de-la-experiencia.md) §11.1 |
| `dsp/ambience.py` | extracción de ambiente por coherencia entre canales, y su versión con estado para flujos | Avendaño y Jot, [09](../docs/research/09-efecto-ambiental-y-diseno-de-la-experiencia.md) §11.2 |
| `motor.py` | la cadena completa: estéreo → una señal por parlante | figura 9 de Avendaño y Jot |
| `estimulos.py` | las señales de calibración | [experimentos/06](../docs/research/experimentos/06-calibracion-rapida-y-recalibracion.md) |
| `medicion.py` | GCC-PHAT, medición simultánea, niveles y calibración autónoma | [experimentos/06](../docs/research/experimentos/06-calibracion-rapida-y-recalibracion.md) |
| `sonido.py` | la capa de PipeWire: descubrir parlantes, el **sink virtual** del sistema, reproducir a N y grabar | P1 del roadmap |
| `cli.py` | `doctor`, `sinks`, `init`, `calibrate`, `run`, `play` | — |

## Cómo se usa

```bash
aurasync doctor      # ¿está todo en su lugar?
aurasync init        # crea la instalación con los parlantes conectados
aurasync calibrate   # mide retardo y ganancia con el micrófono
aurasync run         # el modo de uso real (ver abajo)
aurasync play tema.wav --sin-decorrelar   # el A/B que muestra el efecto
```

**`run` es el modo de uso real.** Crea un dispositivo de salida que el sistema muestra como
cualquier otro; se lo elige como salida —o se le manda una aplicación sola— y todo lo que
suene ahí pasa por el procesamiento. **No deja huella**: el dispositivo vive en el proceso y
desaparece al cerrarlo.

`play` hace lo mismo desde un archivo, para probar sin depender de otra aplicación.

**Nada pide números.** `init` toma lo que hay conectado y `calibrate` mide lo demás. Lo
único que tiene sentido ajustar a mano en `instalacion.json` es `pan` y `ambiente` de cada
parlante: qué reproduce cada uno, que es una decisión artística y no algo medible.

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
