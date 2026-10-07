"""What the controller says about itself: version, LE features, ISO buffers, ISO commands.

Usage: python info.py serial:/dev/ttyACM0
"""

from __future__ import annotations

import asyncio
import struct
import sys

from bumble import hci

from common import EMITTER_ADDRESS, VS_ZEPHYR_READ_VERSION_INFO, log, open_device, vendor_command

FEATURES = [
    'LE_2M_PHY',
    'LE_EXTENDED_ADVERTISING',
    'LE_PERIODIC_ADVERTISING',
    'ISOCHRONOUS_BROADCASTER',
    'SYNCHRONIZED_RECEIVER',
    'CONNECTED_ISOCHRONOUS_STREAM_CENTRAL',
    'SLEEP_CLOCK_ACCURACY_UPDATES',
]
COMMANDS = [
    'HCI_LE_CREATE_BIG_COMMAND',
    'HCI_LE_CREATE_BIG_TEST_COMMAND',
    'HCI_LE_BIG_CREATE_SYNC_COMMAND',
    'HCI_LE_READ_ISO_TX_SYNC_COMMAND',
    'HCI_LE_SETUP_ISO_DATA_PATH_COMMAND',
    'HCI_LE_READ_BUFFER_SIZE_V2_COMMAND',
    'HCI_LE_ISO_TRANSMIT_TEST_COMMAND',
]


async def main(transport_name: str) -> None:
    transport, device = await open_device(transport_name, EMITTER_ADDRESS)
    async with transport:
        host = device.host
        version = host.local_version
        log(
            'version',
            hci_version=version.hci_version,
            hci_subversion=version.hci_subversion,
            lmp_version=version.lmp_version,
            company_identifier=version.company_identifier,
            lmp_subversion=version.lmp_subversion,
        )
        features = hci.LeFeatureMask(host.local_le_features)
        log('le_features', **{name: bool(features & getattr(hci.LeFeatureMask, name)) for name in FEATURES})
        log(
            'commands',
            **{name: host.supports_command(getattr(hci, name)) for name in COMMANDS if hasattr(hci, name)},
        )
        queue = host.iso_packet_queue
        log(
            'iso_buffers',
            max_packet_size=queue.max_packet_size if queue else None,
            max_in_flight=queue.max_in_flight if queue else None,
        )
        raw = await vendor_command(device, VS_ZEPHYR_READ_VERSION_INFO)
        if raw[0] == 0 and len(raw) >= 13:
            hw_platform, hw_variant, fw_variant, fw_version, fw_revision, fw_build = struct.unpack_from(
                '<HHBBHI', raw, 1
            )
            log(
                'vs_version',
                hw_platform=hw_platform,
                hw_variant=hw_variant,
                fw_variant=fw_variant,
                fw_version=fw_version,
                fw_revision=fw_revision,
                fw_build=fw_build,
            )
        else:
            log('vs_version', raw=raw.hex())


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1]))
