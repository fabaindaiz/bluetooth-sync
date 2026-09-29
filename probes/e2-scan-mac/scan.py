"""E2 partial probe (i-7c8794-a999d3): what a JBL advertises, as seen by CoreBluetooth.

Throwaway (d-7c8794-3208b7): delete once docs/research/experimentos/ holds the result.
Prints how many advertisers were seen before what was found, so an empty result
cannot be mistaken for "the JBL advertises nothing".
"""

import asyncio
import sys
from collections import defaultdict

from bleak import BleakScanner

HARMAN_COMPANY_ID = 0x0057


def is_interesting(name, adv):
    return HARMAN_COMPANY_ID in adv.manufacturer_data or "jbl" in (name or "").lower()


async def main(seconds):
    seen = {}
    payloads = defaultdict(set)

    def on_adv(device, adv):
        name = adv.local_name or device.name
        seen[device.address] = name
        if not is_interesting(name, adv):
            return
        for company, data in adv.manufacturer_data.items():
            payloads[device.address].add(("mfr", f"0x{company:04x}", data.hex()))
        for uuid, data in adv.service_data.items():
            payloads[device.address].add(("svc-data", uuid, data.hex()))
        for uuid in adv.service_uuids:
            payloads[device.address].add(("svc-uuid", uuid, ""))
        payloads[device.address].add(("name", name or "", ""))
        payloads[device.address].add(("rssi", str(adv.rssi), ""))

    async with BleakScanner(detection_callback=on_adv):
        await asyncio.sleep(seconds)

    print(f"scanned {seconds}s: {len(seen)} advertisers seen, {len(payloads)} with Harman data or a JBL name")
    for n, (address, items) in enumerate(sorted(payloads.items()), 1):
        print(f"\n[device {n}]")  # addresses are per-Mac UUIDs; not printed on purpose
        for kind, key, value in sorted(i for i in items if i[0] != "rssi"):
            print(f"  {kind:8} {key} {value}")
        rssis = sorted(int(v[1]) for v in items if v[0] == "rssi")
        print(f"  rssi     {rssis[0]}..{rssis[-1]}")


if __name__ == "__main__":
    asyncio.run(main(float(sys.argv[1]) if len(sys.argv) > 1 else 20))
