"""Local fixes to the pinned Bumble (0.0.235), applied by this project instead of reporting upstream.

Each fix is a small, idempotent change to Bumble's own tables, made at runtime and checked by
`tests/test_bumble_fixes.py` against the pinned version. Call `apply()` before opening an HCI
transport. Recorded in docs/decisions.md and docs/research/experimentos/21-f1-iso-en-la-supermini.md.

Fixes:

- **LE Read ISO TX Sync: `Time_Offset` is 3 octets, not 4.** Bumble declares it as 4
  (`bumble/hci.py`, `HCI_LE_Read_ISO_TX_Sync_ReturnParameters`), so a correct 12-byte reply fails
  to parse, the packet is dropped and the command times out. The SoftDevice Controller
  (`sdc_hci_cmd_le.h`: `time_offset : 24`) and Zephyr (`hci_types.h`: `offset[3]`) both use 3 octets;
  measured on a SuperMini nRF52840 (experiment 21).
"""

from __future__ import annotations

from bumble import hci

_TIME_OFFSET = "time_offset"
_TIME_OFFSET_OCTETS = 3  # the spec, the SDC and Zephyr
_BUMBLE_TIME_OFFSET_OCTETS = 4  # what Bumble 0.0.235 declares


class BumbleFixError(RuntimeError):
    """Bumble no longer looks like the version a fix was written for: review the fix."""


def _fix_read_iso_tx_sync() -> None:
    parameters_class = hci.HCI_LE_Read_ISO_TX_Sync_ReturnParameters
    fields = list(parameters_class.fields)
    names = [name for name, _ in fields]
    if _TIME_OFFSET not in names:
        message = "Bumble changed LE Read ISO TX Sync; review aurasync.bumble_fixes"
        raise BumbleFixError(message)
    index = names.index(_TIME_OFFSET)
    size = fields[index][1]
    if size == _TIME_OFFSET_OCTETS:
        return  # already fixed (here or upstream)
    if size != _BUMBLE_TIME_OFFSET_OCTETS:
        message = f"unexpected Time_Offset size {size!r}; review aurasync.bumble_fixes"
        raise BumbleFixError(message)
    fields[index] = (_TIME_OFFSET, _TIME_OFFSET_OCTETS)
    parameters_class.fields = fields


def apply() -> None:
    """Apply every local Bumble fix. Safe to call more than once."""
    _fix_read_iso_tx_sync()
