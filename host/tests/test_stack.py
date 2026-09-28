"""Humo del stack: las dependencias en las que se apoya el diseño existen y se
comportan como dicen docs/research/05, 07 y 08. No prueba lógica de producto."""

from importlib.metadata import version

import lc3
import pytest
from bumble.device import Device, IsoPacketStream
from bumble.profiles import bap

FRAME_US = 10_000
RATE_HZ = 48_000
SAMPLES_PER_FRAME = 480
BYTES_PER_CHANNEL = 100  # 80 kbps con tramas de 10 ms, el valor por defecto de Bumble
BYTES_PER_SAMPLE = 2  # s16


def test_bumble_is_at_least_the_reviewed_version():
    major, minor, patch = (int(part) for part in version("bumble").split(".")[:3])
    assert (major, minor, patch) >= (0, 0, 235)


def test_bumble_exposes_the_api_the_emitter_needs():
    assert hasattr(Device, "create_big")
    assert hasattr(Device, "create_advertising_set")
    assert hasattr(bap, "BasicAudioAnnouncement")
    assert IsoPacketStream is not None


def test_lc3_frame_and_algorithmic_delay_match_the_research():
    encoder = lc3.Encoder(FRAME_US, RATE_HZ)
    assert encoder.get_frame_samples() == SAMPLES_PER_FRAME
    # 2,5 ms de retardo algorítmico (docs/research/07 §7.1).
    assert encoder.get_delay_samples() == RATE_HZ * 25 // 10_000


@pytest.mark.parametrize("channels", [1, 4])
def test_lc3_round_trip_keeps_the_frame_size(channels):
    encoder = lc3.Encoder(FRAME_US, RATE_HZ, num_channels=channels)
    decoder = lc3.Decoder(FRAME_US, RATE_HZ, num_channels=channels)
    silence = bytes(SAMPLES_PER_FRAME * BYTES_PER_SAMPLE * channels)

    encoded = encoder.encode(silence, num_bytes=BYTES_PER_CHANNEL * channels, bit_depth=16)
    assert len(encoded) == BYTES_PER_CHANNEL * channels

    decoded = decoder.decode(encoded, bit_depth=16)
    assert len(decoded) == len(silence)
