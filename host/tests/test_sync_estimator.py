"""The sync estimator's thread, queue and suggestion (spec 2026-10-03 §4.1, d-7c8794-589dec)."""

import threading
import time

from aurasync import sync_estimator, sync_levels, sync_sim
from aurasync.sync_estimator import SyncEstimator
from aurasync.sync_measurement import Measurement
from aurasync.sync_methods import SyncSettings


def _estimator(sc, clock):
    delays = dict.fromkeys(sc.speakers, 0.0)
    return SyncEstimator(SyncSettings(), lambda: dict(delays), "server", clock=clock)


def test_submit_never_blocks(monkeypatch):
    real = sync_estimator.sync_methods.fit

    def slow(*args, **kwargs):
        time.sleep(0.5)
        return real(*args, **kwargs)

    monkeypatch.setattr(sync_estimator.sync_methods, "fit", slow)
    sc = sync_sim.standard("drift")
    est = _estimator(sc, lambda: 3600.0)
    try:
        ms = sync_sim.measurements(sc, 0)[:200]
        start = time.perf_counter()
        for m in ms:
            est.submit(m)
        assert time.perf_counter() - start < 0.05
        assert est.dropped > 0
    finally:
        est.close()


def test_suggestion_appears_off_the_engine_thread(monkeypatch):
    built_on = []
    real = sync_estimator.sync_methods.fit

    def spy(*args, **kwargs):
        built_on.append(threading.current_thread().name)
        return real(*args, **kwargs)

    monkeypatch.setattr(sync_estimator.sync_methods, "fit", spy)
    sc = sync_sim.standard("drift")
    now = {"t": 0.0}
    est = _estimator(sc, lambda: now["t"])
    try:
        for m in sync_sim.measurements(sc, 0):
            if m.t > 900:
                break
            now["t"] = m.t
            est.submit(m)
        assert est.wait_idle()
        s = est.suggestion
        assert s is not None
        assert s.anchor == "server"
        assert sync_sim.alignment_error(sc, s.delays_ms, now["t"], "server") < 0.25
        assert set(built_on) == {"aurasync-sync-estimator"}
        assert s.spread_now_ms is not None
        assert s.spread_now_ms > s.spread_after_ms
    finally:
        est.close()


def test_suggestion_ids_increase():
    sc = sync_sim.standard("drift")
    now = {"t": 0.0}
    est = _estimator(sc, lambda: now["t"])
    try:
        ids = []
        for m in sync_sim.measurements(sc, 0)[:6]:
            now["t"] = m.t
            est.submit(m)
            assert est.wait_idle()
            if est.suggestion is not None:
                ids.append(est.suggestion.id)
        assert ids == sorted(ids)
        assert len(set(ids)) == len(ids)
    finally:
        est.close()


def test_levels_are_statistics_only():
    ms = [
        Measurement("p", "p", "continuous", None, 1.0, float(t), {"a": 1.0, "b": 2.0}, {}, {"a": -20.0 - t, "b": -26.0})
        for t in range(5)
    ]
    out = sync_levels.summarise(ms)
    assert out["by_position"]["p"]["a"]["n"] == 5
    assert out["by_position"]["p"]["a"]["median_db"] == -22.0
    assert out["by_position"]["p"]["b"]["spread_db"] == 0.0
    assert "spread_across_positions_db" in out["by_speaker"]["a"]
    assert not any("gain" in f for f in sync_estimator.Suggestion.__dataclass_fields__)


def test_the_thread_survives_a_failing_fit(monkeypatch):
    """Review 2026-10-03: an exception in the fit killed the thread silently."""

    def broken(*_args, **_kwargs):
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(sync_estimator.sync_methods, "fit", broken)
    sc = sync_sim.standard("drift")
    est = _estimator(sc, lambda: 100.0)
    try:
        est.submit(sync_sim.measurements(sc, 0)[0])
        assert est.wait_idle()
        assert est.thread.is_alive()
        assert "boom" in (est.error or "")
        assert est.suggestion is not None
        assert est.suggestion.delays_ms == {}
        assert "boom" in est.suggestion.reason
    finally:
        est.close()


def test_a_jump_is_carried_from_fit_to_fit():
    sc = sync_sim.standard("jump")
    sc.jumps = [(300.0, "s1", 6.52)]
    now = {"t": 0.0}
    est = _estimator(sc, lambda: now["t"])
    try:
        for m in sync_sim.measurements(sc, 0):
            if m.t > 900:
                break
            now["t"] = m.t
            est.submit(m)
            assert est.wait_idle()
            if m.t >= 320:
                err = sync_sim.alignment_error(sc, est.suggestion.delays_ms, m.t, "server")
                assert err < 0.3, (m.t, err)
    finally:
        est.close()


def test_reset_forgets_history_and_suggestion():
    sc = sync_sim.standard("drift")
    est = _estimator(sc, lambda: 600.0)
    try:
        for m in sync_sim.measurements(sc, 0)[:40]:
            est.submit(m)
        assert est.wait_idle()
        assert est.suggestion is not None
        est.reset()
        assert est.wait_idle()
        assert est.suggestion is None
        assert est.levels()["by_position"] == {}
        assert est.sources() == []
    finally:
        est.close()


def test_a_rejected_measurement_says_why_and_keeps_the_suggestion():
    sc = sync_sim.standard("drift")
    est = _estimator(sc, lambda: 600.0)
    try:
        for m in sync_sim.measurements(sc, 0)[:30]:
            est.submit(m)
        assert est.wait_idle()
        before = est.suggestion.id
        est.submit(Measurement("phone", "phone@sofa", "continuous", None, 1.0, 590.0, {"s0": 500.0}))
        assert est.wait_idle()
        assert est.suggestion.id == before
        phone = next(s for s in est.sources() if s["source_id"] == "phone")
        assert "speaker" in phone["last_rejected"]
        assert phone["rejected"] == 1
    finally:
        est.close()


def test_close_stops_the_thread():
    sc = sync_sim.standard("drift")
    est = _estimator(sc, lambda: 0.0)
    est.close()
    assert not est.thread.is_alive()
