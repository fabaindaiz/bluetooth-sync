"""The multichannel source: a WAV with one channel per speaker, read by the engine itself in fixed
blocks (experimentos/17 §1.1: an outside render, through each speaker's alignment, EQ and volume)."""

import wave

import numpy as np
import pytest

from aurasync.multichannel import MultichannelFile

SR = 48000


def _wav(path, channels, rate=SR):
    data = (np.clip(np.column_stack(channels), -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(len(channels))
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())
    return path


def test_each_channel_goes_to_its_speaker_in_order(tmp_path):
    a = np.linspace(0, 0.5, 1000)
    b = -a
    reader = MultichannelFile(_wav(tmp_path / "r.wav", [a, b]), ["s0", "s1"], SR)
    first = reader.read(400)
    assert set(first) == {"s0", "s1"}
    assert np.allclose(first["s0"], a[:400], atol=1e-4)
    assert np.allclose(first["s1"], b[:400], atol=1e-4)


def test_it_loops_and_every_block_has_the_asked_length(tmp_path):
    reader = MultichannelFile(_wav(tmp_path / "r.wav", [np.arange(10) / 100, np.zeros(10)]), ["s0", "s1"], SR)
    blocks = [reader.read(4)["s0"] for _ in range(4)]
    assert all(len(b) == 4 for b in blocks)
    assert np.allclose(np.concatenate(blocks)[:12], (np.arange(12) % 10) / 100, atol=1e-4)


def test_a_file_for_another_installation_is_refused(tmp_path):
    with pytest.raises(ValueError, match="3 channels"):
        MultichannelFile(_wav(tmp_path / "r.wav", [np.zeros(10)] * 3), ["s0", "s1"], SR)
    with pytest.raises(ValueError, match="48000"):
        MultichannelFile(_wav(tmp_path / "s.wav", [np.zeros(10)] * 2, rate=44100), ["s0", "s1"], SR)
    with pytest.raises(ValueError, match="no such file"):
        MultichannelFile(tmp_path / "missing.wav", ["s0"], SR)


def test_the_downmix_for_the_meters(tmp_path):
    reader = MultichannelFile(_wav(tmp_path / "r.wav", [np.full(8, 0.4), np.full(8, 0.2)]), ["s0", "s1"], SR)
    left, right = MultichannelFile.downmix(reader.read(8))
    assert np.allclose(left, 0.3, atol=1e-3)
    assert np.allclose(right, 0.3, atol=1e-3)
