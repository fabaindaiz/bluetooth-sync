"""Emitter of real LC3 audio: a different tone on each BIS (experiment 21, §8).

BIS 1..4 carry 500, 1000, 1500 and 2000 Hz at 48 kHz, 10 ms, 120 B per BIS (48_4). The receiver
(rx_big.py --lc3) decodes each BIS and finds its dominant frequency: it checks that each channel
arrives in its own BIS, end to end through the encoder, the BIG and the decoder.

Usage: python tx_lc3.py serial:/dev/ttyACM1 --seconds 30
"""

from __future__ import annotations

import argparse
import asyncio
import math
import time

import lc3
import numpy as np
from bumble import hci
from bumble.device import (
    AdvertisingEventProperties,
    AdvertisingParameters,
    BigParameters,
    PeriodicAdvertisingParameters,
)

import tx_big
from common import EMITTER_ADDRESS, EMITTER_SID, log, open_device

RATE, FRAME_US, FRAME_BYTES = 48000, 10000, 120
SAMPLES = RATE * FRAME_US // 1_000_000
TONES = [500.0, 1000.0, 1500.0, 2000.0]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('transport')
    parser.add_argument('--seconds', type=float, default=30)
    args = parser.parse_args()

    transport, device = await open_device(args.transport, EMITTER_ADDRESS)
    async with transport:
        tx_big.install_completion_counter(device)
        await tx_big.vendor_command(device, tx_big.VS_BIG_RESERVED_TIME_SET, (1600).to_bytes(4, 'little'))
        advertising_set = await device.create_advertising_set(
            advertising_parameters=AdvertisingParameters(
                advertising_event_properties=AdvertisingEventProperties(is_connectable=False),
                advertising_sid=EMITTER_SID,
                own_address_type=hci.OwnAddressType.RANDOM,
            ),
            random_address=hci.Address(EMITTER_ADDRESS),
            periodic_advertising_parameters=PeriodicAdvertisingParameters(
                periodic_advertising_interval_min=80, periodic_advertising_interval_max=100
            ),
            periodic_advertising_data=bytes([3, 0xFF, 0xFF, 0xFF]),
        )
        await advertising_set.start_periodic()
        big = await device.create_big(
            advertising_set,
            parameters=BigParameters(
                num_bis=4, sdu_interval=FRAME_US, max_sdu=FRAME_BYTES, max_transport_latency=60, rtn=4
            ),
        )
        feeders = []
        for link in big.bis_links:
            await link.setup_data_path(direction=link.Direction.HOST_TO_CONTROLLER)
            feeder = tx_big.BisFeeder(link, depth=4)
            tx_big.FEEDERS[link.handle] = feeder
            feeders.append(feeder)
        encoder = lc3.Encoder(FRAME_US, RATE, num_channels=len(TONES))
        log('lc3_tx', tones=TONES, nse=big.nse, irc=big.irc)
        frame, encode_s = 0, []
        started = time.monotonic()
        while time.monotonic() - started < args.seconds:
            t = (frame * SAMPLES + np.arange(SAMPLES)) / RATE
            pcm = np.stack([0.3 * np.sin(2 * math.pi * f * t) for f in TONES], axis=1).reshape(-1)
            before = time.perf_counter()
            data = encoder.encode(pcm.tolist(), num_bytes=len(TONES) * FRAME_BYTES)
            encode_s.append(time.perf_counter() - before)
            for index, feeder in enumerate(feeders):
                await feeder.send(data[index * FRAME_BYTES : (index + 1) * FRAME_BYTES], frame, None)
            frame += 1
        encode_s.sort()
        log(
            'lc3_tx_end',
            frames=frame,
            encode_median_ms=round(encode_s[len(encode_s) // 2] * 1000, 3),
            encode_max_ms=round(encode_s[-1] * 1000, 3),
        )
        await big.terminate()


if __name__ == '__main__':
    asyncio.run(main())
