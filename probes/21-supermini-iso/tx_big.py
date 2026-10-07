"""Emitter: create a BIG on the SuperMini, read what the controller chose, optionally stream.

Questions (experiment 21):
  F1  - does the nRF52840 + SDC create a 4-BIS BIG at 48_4 (120 B) and 48_2 (100 B), with which
        NSE/BN/PTO/IRC; what does it answer to Encryption=1.
  A   - are the 4 BIS aligned at the source: `LE Read ISO TX Sync` per BIS gives the controller
        timestamp of the last SDU sent; with the same frame counter on every BIS, the offset
        between BIS is (ts_i - psn_i * interval) - (ts_1 - psn_1 * interval). 0 = aligned,
        a multiple of the interval = one BIS a frame late. Bumble's default is the sequence-number
        mode, which the SDC docs say does not synchronize BIS; `--mode ts` uses the timestamp
        mode the SDC docs recommend.
  H   - can the host keep 4 SDUs every 10 ms fed: minimum SDUs still queued in the controller
        per BIS, per second (0 = the controller may have had nothing to send).
  D   - drift between this PC's clock and the controller's ISO clock: pairs of
        (time.monotonic, tx_time_stamp) to fit offline.

Usage:
  python tx_big.py serial:/dev/ttyACM0 --max-sdu 120 --rtn 4                # create + report
  python tx_big.py serial:/dev/ttyACM0 --encrypt                             # Encryption=1
  python tx_big.py serial:/dev/ttyACM0 --seconds 60 --mode seq               # stream
  python tx_big.py serial:/dev/ttyACM0 --seconds 3 --repeat 20 --mode seq    # alignment stats
"""

from __future__ import annotations

import argparse
import asyncio
import struct
import time

from bumble import hci
from bumble.device import (
    AdvertisingEventProperties,
    AdvertisingParameters,
    BigParameters,
    PeriodicAdvertisingParameters,
)

from common import (
    EMITTER_ADDRESS,
    EMITTER_SID,
    VS_BIG_RESERVED_TIME_SET,
    VS_ISO_READ_TX_TIMESTAMP,
    fail,
    log,
    open_device,
    payload,
    vendor_command,
)


class BisFeeder:
    """Flow-controlled writer for one BIS that can add a Time_Stamp (Bumble's write cannot)."""

    def __init__(self, bis_link, depth: int) -> None:
        self.link = bis_link
        self.queue = bis_link.data_packet_queue
        self.depth = depth
        self.sent = 0
        self.done = 0  # Number_Of_Completed_Packets for this BIS (install_completion_counter)
        self.flow = asyncio.Event()
        self.queue.on('flow', self.flow.set)
        self.min_in_controller = depth

    def in_controller(self) -> int:
        """SDUs handed to the controller and not yet reported completed, for this BIS."""
        return self.sent - self.done

    def completed(self) -> int:
        return self.done

    def on_completed(self, count: int) -> None:
        self.done += count

    async def send(self, sdu: bytes, sequence_number: int, time_stamp: int | None) -> None:
        while self.in_controller() >= self.depth:
            self.flow.clear()
            await self.flow.wait()
        # What the controller still holds when the host gets to write: 0 = it may have run dry.
        self.min_in_controller = min(self.min_in_controller, self.in_controller())
        header = 8 if time_stamp is not None else 4
        packet = hci.HCI_IsoDataPacket(
            connection_handle=self.link.handle,
            data_total_length=header + len(sdu),
            time_stamp=time_stamp,
            packet_sequence_number=sequence_number & 0xFFFF,
            pb_flag=0b10,
            packet_status_flag=0,
            iso_sdu_length=len(sdu),
            iso_sdu_fragment=sdu,
        )
        self.queue.enqueue(packet, self.link.handle)
        self.sent += 1


FEEDERS: dict[int, BisFeeder] = {}  # BIS handle -> feeder of the current cycle


def install_completion_counter(device) -> None:
    """Count Number_Of_Completed_Packets per BIS handle (the host queue only counts the total).

    Installed once; each cycle replaces the contents of FEEDERS.
    """
    host = device.host
    original = host.on_hci_number_of_completed_packets_event

    def counting(event) -> None:
        for handle, count in zip(event.connection_handles, event.num_completed_packets):
            if (feeder := FEEDERS.get(handle)) is not None:
                feeder.on_completed(count)
        original(event)

    host.on_hci_number_of_completed_packets_event = counting


async def read_tx_sync(device, handle: int) -> tuple[int, int, int] | None:
    """(psn, tx_time_stamp, 0) of the last SDU sent on this BIS.

    Uses the SDC's VS ISO Read TX Timestamp (0xfd17): the standard LE Read ISO TX Sync answers
    correctly, but Bumble 0.0.235 declares its Time_Offset as 4 octets (the spec, the SDC and
    Zephyr say 3), fails to parse the reply and times out (experiment 21). The project's fix is
    aurasync.bumble_fixes.apply() (d-7c8794-570a77); this probe predates it.
    """
    reading = await read_vs_tx_timestamp(device, handle)
    return None if reading is None else (reading[0], reading[1], 0)


async def read_vs_tx_timestamp(device, handle: int) -> tuple[int, int] | None:
    raw = await vendor_command(device, VS_ISO_READ_TX_TIMESTAMP, struct.pack('<H', handle))
    if raw[0] != 0 or len(raw) < 9:
        log('vs_tx_timestamp_error', handle=handle, raw=raw.hex())
        return None
    _handle, sequence_number, time_stamp = struct.unpack_from('<HHI', raw, 1)
    return sequence_number, time_stamp


async def create_big(device, advertising_set, args) -> object | None:
    parameters = BigParameters(
        num_bis=args.num_bis,
        sdu_interval=args.sdu_interval,
        max_sdu=args.max_sdu,
        max_transport_latency=args.latency,
        rtn=args.rtn,
        phy=hci.PhyBit.LE_2M if args.phy == '2m' else hci.PhyBit.LE_1M,
        broadcast_code=bytes(range(16)) if args.encrypt else None,
    )
    try:
        big = await device.create_big(advertising_set, parameters=parameters)
    except hci.HCI_Error as error:
        log('big_create_failed', error=str(error), error_code=getattr(error, 'error_code', None), **vars_of(args))
        return None
    log(
        'big_created',
        request=vars_of(args),
        big_sync_delay_us=big.big_sync_delay,
        transport_latency_us=big.transport_latency_big,
        phy=int(big.phy),
        nse=big.nse,
        bn=big.bn,
        pto=big.pto,
        irc=big.irc,
        max_pdu=big.max_pdu,
        iso_interval_ms=big.iso_interval,
        bis_handles=[link.handle for link in big.bis_links],
    )
    return big


def vars_of(args) -> dict:
    keys = ('num_bis', 'sdu_interval', 'max_sdu', 'latency', 'rtn', 'phy', 'encrypt', 'mode')
    return {key: getattr(args, key) for key in keys}


async def stream(device, big, args, cycle: int) -> None:
    FEEDERS.clear()
    for link in big.bis_links:
        await link.setup_data_path(direction=link.Direction.HOST_TO_CONTROLLER)
        FEEDERS[link.handle] = BisFeeder(link, args.depth)
    ordered = [FEEDERS[link.handle] for link in big.bis_links]
    interval = args.sdu_interval

    base_time_stamp = None
    started = time.monotonic()
    next_poll = started + args.poll
    next_second = started + 1.0
    frame = 0
    stop_at = started + args.seconds
    while time.monotonic() < stop_at:
        for index, feeder in enumerate(ordered):
            sdu = payload(frame, index + 1, args.max_sdu)
            if args.mode == 'seq':
                await feeder.send(sdu, frame, None)
            elif frame == 0 and index == 0:
                # Time-of-arrival for the first SDU, then read the timestamp it got (SDC docs).
                await feeder.send(sdu, 0, None)
                while feeder.completed() < 1:
                    feeder.flow.clear()
                    await feeder.flow.wait()
                reading = await read_vs_tx_timestamp(device, feeder.link.handle)
                if reading is None:
                    fail('VS ISO Read TX Timestamp failed; timestamp mode impossible')
                base_time_stamp = reading[1]
                log('ts_base', cycle=cycle, handle=feeder.link.handle, sequence_number=reading[0], time_stamp=reading[1])
            else:
                await feeder.send(sdu, frame, (base_time_stamp + frame * interval) & 0xFFFFFFFF)
        frame += 1

        now = time.monotonic()
        if now >= next_second:
            log(
                'feed',
                cycle=cycle,
                frame=frame,
                min_in_controller=[feeder.min_in_controller for feeder in ordered],
            )
            for feeder in ordered:
                feeder.min_in_controller = args.depth
            next_second += 1.0
        if now >= next_poll:
            readings = []
            for feeder in ordered:
                before = time.monotonic()
                reading = await read_tx_sync(device, feeder.link.handle)
                readings.append(
                    None
                    if reading is None
                    else {
                        'host_t': (before + time.monotonic()) / 2,
                        'psn': reading[0],
                        'tx_ts': reading[1],
                        'offset': reading[2],
                    }
                )
            log('tx_sync', cycle=cycle, frame=frame, bis=readings, offsets_us=alignment(readings, interval))
            next_poll += args.poll
    log('stream_end', cycle=cycle, frames=frame, seconds=round(time.monotonic() - started, 3))


def alignment(readings: list, interval: int) -> list | None:
    """Offset of each BIS against BIS 1, in µs, from (psn, tx_ts); psn is the shared frame counter."""
    if any(reading is None for reading in readings):
        return None
    reference = readings[0]['tx_ts'] - readings[0]['psn'] * interval
    offsets = []
    for reading in readings:
        anchor = reading['tx_ts'] - reading['psn'] * interval
        # tx_ts is a 32-bit µs counter; bring the difference into ±2^31.
        offsets.append(((anchor - reference + 2**31) % 2**32) - 2**31)
    return offsets


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('transport')
    parser.add_argument('--num-bis', type=int, default=4)
    parser.add_argument('--sdu-interval', type=int, default=10000)
    parser.add_argument('--max-sdu', type=int, default=120)
    parser.add_argument('--latency', type=int, default=60, help='max transport latency, ms')
    parser.add_argument('--rtn', type=int, default=4)
    parser.add_argument('--phy', choices=('2m', '1m'), default='2m')
    parser.add_argument('--encrypt', action='store_true')
    parser.add_argument('--reserved-us', type=int, default=None, help='VS BIG reserved time')
    parser.add_argument('--seconds', type=float, default=0.0)
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--mode', choices=('seq', 'ts'), default='seq')
    parser.add_argument('--depth', type=int, default=2, help='SDUs per BIS kept in the controller')
    parser.add_argument('--poll', type=float, default=0.5, help='seconds between TX sync reads')
    args = parser.parse_args()

    transport, device = await open_device(args.transport, EMITTER_ADDRESS)
    async with transport:
        install_completion_counter(device)
        if args.reserved_us is not None:
            raw = await vendor_command(device, VS_BIG_RESERVED_TIME_SET, struct.pack('<I', args.reserved_us))
            log('big_reserved_time_set', reserved_us=args.reserved_us, status=raw[0])
        advertising_set = await device.create_advertising_set(
            advertising_parameters=AdvertisingParameters(
                advertising_event_properties=AdvertisingEventProperties(
                    is_connectable=False, is_scannable=False
                ),
                primary_advertising_interval_min=100,
                primary_advertising_interval_max=150,
                advertising_sid=EMITTER_SID,
                own_address_type=hci.OwnAddressType.RANDOM,
            ),
            random_address=hci.Address(EMITTER_ADDRESS),
            advertising_data=bytes([12, 0x09]) + b'aurasync-21',
            periodic_advertising_parameters=PeriodicAdvertisingParameters(
                periodic_advertising_interval_min=80,
                periodic_advertising_interval_max=100,
            ),
            periodic_advertising_data=bytes([3, 0xFF, 0xFF, 0xFF]),
        )
        await advertising_set.start_periodic()
        log('advertising', address=EMITTER_ADDRESS, sid=EMITTER_SID)

        for cycle in range(args.repeat):
            big = await create_big(device, advertising_set, args)
            if big is None:
                break
            if args.seconds > 0:
                await stream(device, big, args, cycle)
            await big.terminate()
            log('big_terminated', cycle=cycle)


if __name__ == '__main__':
    asyncio.run(main())
