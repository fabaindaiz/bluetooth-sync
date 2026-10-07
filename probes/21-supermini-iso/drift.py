"""Drift between the PC's clock and the controller's ISO clock, from tx_big.py's tx_sync records.

Each record pairs host_t (time.monotonic, read around the VS command) with tx_ts (µs, controller
clock) for the last SDU sent. tx_ts advances in whole SDU intervals, so it is reduced to the
event grid first: what is fitted is tx_ts against host_t, both in µs.

Usage: python drift.py <tx_big jsonl>
"""

from __future__ import annotations

import json
import statistics
import sys


def unwrap32(values: list[int]) -> list[int]:
    out, base, prev = [], 0, None
    for value in values:
        if prev is not None and value < prev and prev - value > 2**31:
            base += 2**32
        prev = value
        out.append(value + base)
    return out


def fit(xs: list[float], ys: list[float]) -> tuple[float, float]:
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / sxx
    return slope, mean_y - slope * mean_x


def main(path: str) -> None:
    host, ctrl = [], []
    for line in open(path):
        record = json.loads(line)
        if record['kind'] != 'tx_sync' or not record['bis'] or record['bis'][0] is None:
            continue
        first = record['bis'][0]
        host.append(first['host_t'] * 1e6)
        ctrl.append(first['tx_ts'])
    ctrl = unwrap32(ctrl)
    if len(host) < 20:
        print(json.dumps({'error': 'too few points', 'points': len(host)}))
        return
    slope, intercept = fit(host, ctrl)
    residuals = [c - (slope * h + intercept) for h, c in zip(host, ctrl)]
    half = len(host) // 2
    slope_a, _ = fit(host[:half], ctrl[:half])
    slope_b, _ = fit(host[half:], ctrl[half:])
    print(
        json.dumps(
            {
                'points': len(host),
                'span_s': round((host[-1] - host[0]) / 1e6, 1),
                'controller_vs_pc_ppm': round((slope - 1) * 1e6, 2),
                'first_half_ppm': round((slope_a - 1) * 1e6, 2),
                'second_half_ppm': round((slope_b - 1) * 1e6, 2),
                'residual_std_us': round(statistics.pstdev(residuals), 1),
                'residual_max_abs_us': round(max(abs(r) for r in residuals), 1),
            }
        )
    )


if __name__ == '__main__':
    main(sys.argv[1])
