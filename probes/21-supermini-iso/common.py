"""Shared helpers for the SuperMini ISO probes (experiment 21).

Throwaway probe code (d-7c8794-3208b7): deleted once the result is written in
docs/research/experimentos/21-*.md. Every probe prints one JSON object per line.
"""

from __future__ import annotations

import json
import struct
import sys
import time

from bumble import hci
from bumble.device import Device, DeviceConfiguration
from bumble.transport import open_transport

# Static random addresses, so the receiver can find the emitter without parsing names.
EMITTER_ADDRESS = 'F2:00:00:00:00:21'
RECEIVER_ADDRESS = 'F2:00:00:00:00:22'
EMITTER_SID = 3

# SDC vendor-specific commands (nrfxlib/softdevice_controller/include/sdc_hci_vs.h, NCS v3.4.1).
VS_ZEPHYR_READ_VERSION_INFO = 0xFC01
VS_ISO_READ_TX_TIMESTAMP = 0xFD17
VS_BIG_RESERVED_TIME_SET = 0xFD18

# Payload of every probe SDU: frame counter (u32), BIS index (u8), then padding.
PAYLOAD_HEADER = struct.Struct('<IB')


def log(kind: str, **fields) -> None:
    print(json.dumps({'t': round(time.monotonic(), 6), 'kind': kind, **fields}), flush=True)


def fail(message: str) -> None:
    log('error', message=message)
    sys.exit(1)


async def open_device(transport_name: str, address: str):
    transport = await open_transport(transport_name)
    config = DeviceConfiguration(name='aurasync-probe', address=hci.Address(address))
    device = Device.from_config_with_hci(config, transport.source, transport.sink)
    await device.power_on()
    return transport, device


async def vendor_command(device: Device, op_code: int, parameters: bytes = b'') -> bytes:
    """Send a vendor-specific command; return its raw return parameters (status first)."""
    command = hci.HCI_Command(parameters, op_code=op_code)
    response = await device.host.send_sync_command_raw(command)  # type: ignore[arg-type]
    return_parameters = response.return_parameters
    data = getattr(return_parameters, 'data', None)
    if data is None:
        return bytes([int(return_parameters.status)])
    return bytes(data)


def payload(counter: int, bis_index: int, size: int) -> bytes:
    head = PAYLOAD_HEADER.pack(counter & 0xFFFFFFFF, bis_index)
    return head + bytes(max(0, size - len(head)))
