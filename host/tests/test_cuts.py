from aurasync.cuts import CutLog, likely_cause


class Clock:
    t = 10.0

    def __call__(self):
        return self.t


def test_events_carry_the_context_and_age_out():
    clock = Clock()
    log = CutLog(clock)
    log.context = {"loop_measuring": True, "slow_order": None}
    log.add("underrun", "salida", "0 ms en la tubería")
    log.add("fade", None, "preset")
    s = log.summary()
    assert s["faults_10min"] == 1
    assert s["events"][0]["context"] == {"loop_measuring": True}
    clock.t += 700
    assert log.summary()["faults_10min"] == 0


def test_the_reading_points_at_what_the_cuts_share():
    def event(kind, where="x", **context):
        return {"kind": kind, "where": where, "context": context, "fault": True}

    assert "Bluetooth buscaba" in likely_cause([event("xrun", bt_discovering=True)] * 3)
    assert "lazo medía" in likely_cause([event("underrun", loop_measuring=True)] * 3)
    assert "Solo Red" in likely_cause([event("xrun", "Red")] * 4)
    assert "fuente" in likely_cause([event("input_gap")] * 2)
    assert likely_cause([]) is None
