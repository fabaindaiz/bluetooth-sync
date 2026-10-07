"""Feed a 4-BIS BIG at the PC's pace, as real audio would arrive (experiment 21, §7).

tx_big.py lets the controller pace the host (a new SDU when one completes). Real audio arrives at the
PC's clock, so the queue in the controller drifts by the clock difference. Two modes:

  open  prefill P frames, then one frame every sdu_interval of time.monotonic (no correction):
        the queue's trend measures the drift and how long a cushion lasts.
  dll   the same, with a loop that estimates the controller/PC clock ratio from VS ISO Read TX
        Timestamp (0xfd17) and adjusts the frame period so the queue stays at P. In the product the
        same ratio would drive a resampler instead of the frame period (research/08 §4).

Every second it logs the SDUs pending per BIS (sent - completed), the controller timestamp of the last
SDU on BIS 1, and the loop's state.

Usage: python feed_pc.py serial:/dev/ttyACM1 --mode open --seconds 600 --prefill 6
"""

from __future__ import annotations

import argparse
import asyncio
import time

from bumble import hci
from bumble.device import (
    AdvertisingEventProperties,
    AdvertisingParameters,
    BigParameters,
    PeriodicAdvertisingParameters,
)

import tx_big
from common import EMITTER_ADDRESS, EMITTER_SID, log, open_device, payload


class ClockLoop:
    """Second-order loop on the controller/PC rate ratio, from (host time, controller time) pairs."""

    def __init__(self, interval_us: int, target: int, rate_gain: float, phase_gain: float) -> None:
        self.interval_us = interval_us
        self.target = target
        self.rate_gain = rate_gain
        self.phase_gain = phase_gain
        self.ratio_ppm = 0.0  # controller runs this many ppm fast vs the PC (estimate)
        self.correction_ppm = 0.0  # what is applied to the period, ratio + phase term
        self._last: tuple[float, int] | None = None

    def update(self, host_t: float, ctrl_ts: int, pending: float) -> None:
        if self._last is not None:
            host_dt = (host_t - self._last[0]) * 1e6
            ctrl_dt = (ctrl_ts - self._last[1]) % 2**32
            if host_dt > 0.5e6:
                measured = (ctrl_dt / host_dt - 1.0) * 1e6
                self.ratio_ppm += self.rate_gain * (measured - self.ratio_ppm)
        self._last = (host_t, ctrl_ts)
        # Fewer SDUs pending than the target: send faster (positive correction shortens the period).
        phase_error_frames = self.target - pending
        self.correction_ppm = self.ratio_ppm + self.phase_gain * phase_error_frames

    def period_s(self) -> float:
        return self.interval_us / 1e6 / (1.0 + self.correction_ppm / 1e6)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('transport')
    parser.add_argument('--mode', choices=('open', 'dll'), default='open')
    parser.add_argument('--seconds', type=float, default=600)
    parser.add_argument('--prefill', type=int, default=6)
    parser.add_argument('--max-sdu', type=int, default=120)
    parser.add_argument('--rate-gain', type=float, default=0.1)
    parser.add_argument('--phase-gain', type=float, default=20.0, help='ppm per frame of queue error')
    args = parser.parse_args()
    interval = 10000

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
                num_bis=4, sdu_interval=interval, max_sdu=args.max_sdu, max_transport_latency=60, rtn=4
            ),
        )
        log('big_created', nse=big.nse, irc=big.irc, pto=big.pto, mode=args.mode, prefill=args.prefill)
        feeders = []
        for link in big.bis_links:
            await link.setup_data_path(direction=link.Direction.HOST_TO_CONTROLLER)
            feeder = tx_big.BisFeeder(link, depth=64)  # no back-pressure: the PC sets the pace
            tx_big.FEEDERS[link.handle] = feeder
            feeders.append(feeder)

        loop = ClockLoop(interval, args.prefill, args.rate_gain, args.phase_gain)
        frame = 0

        async def send_frame() -> None:
            nonlocal frame
            for index, feeder in enumerate(feeders):
                await feeder.send(payload(frame, index + 1, args.max_sdu), frame, None)
            frame += 1

        for _ in range(args.prefill):
            await send_frame()
        started = time.monotonic()
        next_send = started
        next_log = started + 1.0
        underruns = 0
        while time.monotonic() - started < args.seconds:
            now = time.monotonic()
            if now >= next_send:
                if min(f.in_controller() for f in feeders) == 0:
                    underruns += 1
                await send_frame()
                next_send += loop.period_s() if args.mode == 'dll' else interval / 1e6
                if next_send < now - 0.5:  # the host stalled for half a second: do not burst
                    next_send = now
            if now >= next_log:
                pending = [f.in_controller() for f in feeders]
                before = time.monotonic()
                reading = await tx_big.read_vs_tx_timestamp(device, feeders[0].link.handle)
                host_t = (before + time.monotonic()) / 2
                if reading is not None and args.mode == 'dll':
                    loop.update(host_t, reading[1], sum(pending) / len(pending))
                log(
                    'feed_pc',
                    s=round(now - started, 3),
                    frame=frame,
                    pending=pending,
                    underruns=underruns,
                    host_t=host_t,
                    psn=None if reading is None else reading[0],
                    tx_ts=None if reading is None else reading[1],
                    ratio_ppm=round(loop.ratio_ppm, 3),
                    correction_ppm=round(loop.correction_ppm, 3),
                )
                next_log += 1.0
            await asyncio.sleep(max(0.0, min(next_send, next_log) - time.monotonic()))
        log('feed_end', frames=frame, underruns=underruns, seconds=round(time.monotonic() - started, 3))
        await big.terminate()


if __name__ == '__main__':
    asyncio.run(main())
