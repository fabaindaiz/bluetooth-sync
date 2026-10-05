"""The multichannel source in a session (the simulated room) and through the contract. SIMULADO."""

import threading
import time
import wave

import numpy as np

from aurasync.config import Instalacion, Parlante
from aurasync.logbuffer import LogBuffer
from aurasync.service import Service
from aurasync.session import SessionOptions
from aurasync.simulated import SimulatedObserver, SimulatedSession

SR = 48000


def _wav(path, channels):
    data = (np.clip(np.column_stack(channels), -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(len(channels))
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())
    return path


def test_a_multichannel_source_reaches_each_speaker_through_the_contract(tmp_path):
    inst = Instalacion(
        parlantes=[Parlante(f"s{i}", f"sink{i}", pan=p, ambiente=0.2) for i, p in enumerate((-0.7, 0.7))]
    )
    inst.guardar(tmp_path / "i.json")
    svc = Service(
        tmp_path / "i.json",
        tmp_path / "p.json",
        options=SessionOptions(microphone="sim", block=1024),
        session_factory=SimulatedSession,
        observer=SimulatedObserver(inst),
        simulated=True,
        log=lambda _: None,
        logs=LogBuffer(),
    )
    thread = threading.Thread(target=svc.run, daemon=True)
    thread.start()
    try:
        noise = np.random.default_rng(2).standard_normal(SR) * 0.1
        path = _wav(tmp_path / "render.wav", [noise, np.zeros(SR)])
        assert svc.handle({"v": 1, "op": "start"})["ok"]
        played: list[dict] = []
        room = svc.session.room
        original = room.play
        room.play = lambda blocks: (played.append({k: v.copy() for k, v in blocks.items()}), original(blocks))[1]
        reply = svc.handle({"v": 1, "op": "source", "kind": "multichannel", "name": str(path)})
        assert reply["ok"], reply
        deadline = time.monotonic() + 5
        while (svc.session.source.kind != "multichannel" or len(played) < 30) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert svc.session.source.kind == "multichannel"
        tail = played[-10:]
        assert sum(float(b["s0"] @ b["s0"]) for b in tail) > 0
        assert max(float(np.max(np.abs(b["s1"]))) for b in tail) < 1e-6
        # A file for another installation says why, and the source does not change.
        bad = _wav(tmp_path / "three.wav", [noise[:100]] * 3)
        assert svc.handle({"v": 1, "op": "source", "kind": "multichannel", "name": str(bad)})["ok"]
        deadline = time.monotonic() + 5
        while "source" not in svc.errors and time.monotonic() < deadline:
            time.sleep(0.05)
        assert "3 channels" in svc.errors["source"]
        assert svc.session.source.kind == "multichannel"
    finally:
        svc.handle({"v": 1, "op": "shutdown"})
        thread.join(timeout=5)
        svc.close()
