"""Receiver: a second SuperMini syncs to the emitter's BIG and checks what is on the air.

Questions (experiment 21):
  R  - does the BIG really go on the air: SDUs received and lost per BIS.
  A  - are the 4 BIS aligned on the air: the emitter writes the same frame counter on every BIS,
       so for one BIG event (the same received packet sequence number on every BIS) the counters
       must be equal. counter_i - counter_1 != 0 means BIS i carries another frame.
  D  - drift between the two boards' clocks: the receiver timestamps each BIG event in its own
       clock; the fitted slope of (timestamp vs sequence number) against the SDU interval gives
       the emitter-vs-receiver drift in ppm.

Usage: python rx_big.py serial:/dev/ttyACM1 --seconds 60
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import time

from bumble import hci
from bumble.device import Advertisement, BigSyncParameters

import struct

from common import (
    EMITTER_ADDRESS,
    EMITTER_SID,
    PAYLOAD_HEADER,
    RECEIVER_ADDRESS,
    fail,
    log,
    open_device,
    vendor_command,
)

LE_READ_ISO_LINK_QUALITY = 0x2075  # Core spec LE Read ISO Link Quality (Bumble has no class for it)
LINK_QUALITY_FIELDS = ('tx_unacked', 'tx_flushed', 'tx_last_subevent', 'retransmitted', 'crc_error',
                       'rx_unreceived', 'duplicate')


async def read_link_quality(device, handle: int) -> dict | None:
    try:
        raw = await vendor_command(device, LE_READ_ISO_LINK_QUALITY, struct.pack('<H', handle))
    except Exception:  # noqa: BLE001 - optional on a controller
        return None
    if raw[0] != 0 or len(raw) < 31:
        return {'status': raw[0]}
    return dict(zip(LINK_QUALITY_FIELDS, struct.unpack_from('<7I', raw, 3)))


class BisStats:
    def __init__(self, index: int, lc3_decoder=None) -> None:
        self.index = index
        self.lc3_decoder = lc3_decoder  # rx --lc3: decode the SDU instead of reading a counter
        self.pcm: list = []
        self.lc3_errors = 0
        self.received = 0
        self.invalid = 0  # Packet_Status_Flag != 0 or empty SDU
        self.last_psn: int | None = None
        self.gaps = 0
        self.by_psn: dict[int, int] = {}  # psn -> payload counter (recent window)
        self.points: list[tuple[int, int, float]] = []  # (unwrapped psn, rx ts, host time)
        self._psn_base = 0
        self._prev_raw: int | None = None
        self.last_counter: int | None = None
        self.counter_skips = 0  # frames missing from the content (the emitter's counter jumped)
        self.counter_repeats = 0  # the same frame again (a flushed SDU replaced, or a replay)

    def unwrap(self, psn: int) -> int:
        if self._prev_raw is not None and psn < self._prev_raw and self._prev_raw - psn > 0x8000:
            self._psn_base += 0x10000
        self._prev_raw = psn
        return self._psn_base + psn

    def on_packet(self, packet: hci.HCI_IsoDataPacket) -> None:
        now = time.monotonic()
        if packet.packet_sequence_number is None:
            return  # continuation fragment; probe SDUs fit one packet
        psn = self.unwrap(packet.packet_sequence_number)
        if self.last_psn is not None and psn != self.last_psn + 1:
            self.gaps += psn - self.last_psn - 1
        self.last_psn = psn
        self.received += 1
        data = packet.iso_sdu_fragment
        if self.lc3_decoder is not None:
            if packet.packet_status_flag or not data:
                self.invalid += 1
                return
            try:
                self.pcm.extend(self.lc3_decoder.decode(data))
            except Exception:  # noqa: BLE001 - a corrupt frame is counted, not fatal
                self.lc3_errors += 1
            return
        if packet.packet_status_flag or len(data) < PAYLOAD_HEADER.size:
            self.invalid += 1
            return
        counter, _bis = PAYLOAD_HEADER.unpack_from(data)
        if self.last_counter is not None:
            if counter > self.last_counter + 1:
                self.counter_skips += counter - self.last_counter - 1
            elif counter <= self.last_counter:
                self.counter_repeats += 1
        self.last_counter = counter
        self.by_psn[psn] = counter
        if len(self.by_psn) > 2000:
            for key in sorted(self.by_psn)[:1000]:
                del self.by_psn[key]
        if packet.time_stamp is not None and (psn % 10 == 0):
            self.points.append((psn, packet.time_stamp, now))


def dominant_hz(pcm: list, rate: int) -> dict | None:
    """Dominant frequency of the decoded PCM, and how far above the rest it stands (dB)."""
    import numpy as np

    if len(pcm) < rate:
        return None
    x = np.asarray(pcm[-rate * 4 :], dtype=float) * np.hanning(min(len(pcm), rate * 4))
    spectrum = np.abs(np.fft.rfft(x))
    peak = int(np.argmax(spectrum[1:])) + 1
    rest = np.delete(spectrum, range(max(0, peak - 3), peak + 4))
    return {
        'hz': round(peak * rate / len(x), 1),
        'peak_over_median_db': round(20 * np.log10(spectrum[peak] / max(np.median(rest), 1e-12)), 1),
    }


def slope_ppm(points: list[tuple[int, int, float]], interval: int) -> float | None:
    """Least-squares slope of rx timestamp (µs, unwrapped) vs psn, as ppm against interval."""
    if len(points) < 10:
        return None
    xs, ys, base, prev = [], [], 0, None
    for psn, ts, _ in points:
        if prev is not None and ts < prev and prev - ts > 2**31:
            base += 2**32
        prev = ts
        xs.append(psn)
        ys.append(ts + base)
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    return (sxy / sxx / interval - 1.0) * 1e6


async def find_emitter(device, timeout: float) -> Advertisement:
    found: asyncio.Future = asyncio.get_running_loop().create_future()

    def on_advertisement(advertisement: Advertisement) -> None:
        if (
            str(advertisement.address).startswith(EMITTER_ADDRESS)
            and advertisement.sid == EMITTER_SID
            and not found.done()
        ):
            found.set_result(advertisement)

    device.on('advertisement', on_advertisement)
    # Scanning stays on: the periodic sync needs the AUX_ADV_IND with its SyncInfo.
    # 1M only: the image does not enable the Coded PHY.
    await device.start_scanning(
        legacy=False, active=False, filter_duplicates=False, scanning_phys=(hci.HCI_LE_1M_PHY,)
    )
    return await asyncio.wait_for(found, timeout)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('transport')
    parser.add_argument('--seconds', type=float, default=30.0)
    parser.add_argument('--num-bis', type=int, default=4)
    parser.add_argument('--scan-timeout', type=float, default=20.0)
    parser.add_argument('--lc3', action='store_true', help='decode LC3 (48 kHz, 10 ms) per BIS')
    args = parser.parse_args()

    transport, device = await open_device(args.transport, RECEIVER_ADDRESS)
    async with transport:
        try:
            advertisement = await find_emitter(device, args.scan_timeout)
        except asyncio.TimeoutError:
            fail(f'emitter {EMITTER_ADDRESS} sid {EMITTER_SID} not seen in {args.scan_timeout} s')
        log('emitter_found', address=str(advertisement.address), sid=advertisement.sid, rssi=advertisement.rssi)

        pa_sync = await device.create_periodic_advertising_sync(
            advertiser_address=advertisement.address, sid=advertisement.sid, sync_timeout=5.0
        )
        biginfo_future: asyncio.Future = asyncio.get_running_loop().create_future()
        pa_sync.on(
            'biginfo_advertisement',
            lambda info: None if biginfo_future.done() else biginfo_future.set_result(info),
        )
        # Bumble starts the sync itself; BIGInfo only arrives once it is established.
        try:
            biginfo = await asyncio.wait_for(biginfo_future, 15.0)
        except asyncio.TimeoutError:
            fail(f'no BIGInfo in 15 s; periodic sync state {pa_sync.state.name}')
        log('pa_synced', interval_ms=pa_sync.periodic_advertising_interval, phy=pa_sync.advertiser_phy)
        rssi_window: list[int] = []
        pa_sync.on('periodic_advertisement', lambda report: rssi_window.append(report.rssi))
        log(
            'biginfo',
            num_bis=biginfo.num_bis,
            nse=biginfo.nse,
            iso_interval_ms=biginfo.iso_interval,
            bn=biginfo.bn,
            pto=biginfo.pto,
            irc=biginfo.irc,
            max_pdu=biginfo.max_pdu,
            sdu_interval=biginfo.sdu_interval,
            max_sdu=biginfo.max_sdu,
            phy=int(biginfo.phy),
            framing=int(biginfo.framing),
            encryption=int(biginfo.encryption),
            advertiser_clock_accuracy=getattr(pa_sync, 'advertiser_clock_accuracy', None),
        )
        if int(biginfo.encryption):
            fail('BIG is encrypted; the probe has no Broadcast Code')

        big_sync = await device.create_big_sync(
            pa_sync,
            BigSyncParameters(big_sync_timeout=0x100, bis=list(range(1, args.num_bis + 1))),
        )
        await device.stop_scanning()
        log(
            'big_synced',
            nse=big_sync.nse,
            bn=big_sync.bn,
            pto=big_sync.pto,
            irc=big_sync.irc,
            max_pdu=big_sync.max_pdu,
            iso_interval_ms=big_sync.iso_interval,
            transport_latency_us=big_sync.transport_latency_big,
        )
        if args.lc3:
            import lc3

            stats = [BisStats(i + 1, lc3.Decoder(10000, 48000)) for i in range(len(big_sync.bis_links))]
        else:
            stats = [BisStats(i + 1) for i in range(len(big_sync.bis_links))]
        for link, stat in zip(big_sync.bis_links, stats):
            link.sink = stat.on_packet
            await link.setup_data_path(direction=link.Direction.CONTROLLER_TO_HOST)

        lost = asyncio.Event()
        big_sync.on(big_sync.Event.TERMINATION, lambda *_: lost.set())
        interval = biginfo.sdu_interval
        started = time.monotonic()
        offsets_total: collections.Counter = collections.Counter()
        while time.monotonic() - started < args.seconds and not lost.is_set():
            await asyncio.sleep(1.0)
            common = set.intersection(*(set(stat.by_psn) for stat in stats)) if stats else set()
            second: collections.Counter = collections.Counter()
            for psn in common:
                reference = stats[0].by_psn[psn]
                second[tuple(stat.by_psn[psn] - reference for stat in stats)] += 1
            offsets_total.update(second)
            for stat in stats:
                stat.by_psn.clear()
            quality = [await read_link_quality(device, link.handle) for link in big_sync.bis_links]
            rssi = [r for r in rssi_window if -127 <= r <= 20]
            rssi_window.clear()
            log(
                'rx',
                rssi_dbm=round(sum(rssi) / len(rssi), 1) if rssi else None,
                link_quality=quality,
                received=[stat.received for stat in stats],
                invalid=[stat.invalid for stat in stats],
                gaps=[stat.gaps for stat in stats],
                counter_skips=[stat.counter_skips for stat in stats],
                counter_repeats=[stat.counter_repeats for stat in stats],
                counter_offsets={str(list(key)): count for key, count in second.items()},
            )
        log(
            'rx_end',
            lost_sync=lost.is_set(),
            seconds=round(time.monotonic() - started, 3),
            received=[stat.received for stat in stats],
            invalid=[stat.invalid for stat in stats],
            gaps=[stat.gaps for stat in stats],
            counter_skips=[stat.counter_skips for stat in stats],
            counter_repeats=[stat.counter_repeats for stat in stats],
            counter_offsets={str(list(key)): count for key, count in offsets_total.items()},
            drift_ppm_emitter_vs_receiver=[slope_ppm(stat.points, interval) for stat in stats],
            timestamped=[len(stat.points) for stat in stats],
            lc3_errors=[stat.lc3_errors for stat in stats] if args.lc3 else None,
            dominant=[dominant_hz(stat.pcm, 48000) for stat in stats] if args.lc3 else None,
        )
        if not lost.is_set():
            await big_sync.terminate()


if __name__ == '__main__':
    asyncio.run(main())
