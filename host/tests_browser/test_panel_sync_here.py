"""«Medir desde aquí» end to end (spec 2026-10-03 §6.3, step 3): the panel estimates the server
clock, records, fetches the probe each speaker sent, measures in the browser and sends the arrivals.
The microphone is replaced by a recording made of that same probe, delayed by known amounts per
speaker: everything else is the real flow. SIMULADO."""

import time

from playwright.sync_api import Page, expect

from .test_panel import Running, go, start

DELAYS_MS = (20.0, 27.5, 24.0)

FAKE_RECORDER = """(delays) => {
  window.aurasync.recorder = {
    async record(seconds) {
      const api = window.aurasync.api;
      const t0 = performance.now();
      const r = await api.raw({ op: "sync_time" });
      const t1 = performance.now();
      const serverStart = r.result.t;
      const startedAt = (t0 + t1) / 2;
      await new Promise((ok) => setTimeout(ok, seconds * 1000 + 300));
      const ref = await api.raw({ op: "probe_reference", from: serverStart, seconds });
      const sr = 48000, n = Math.round(seconds * sr);
      const mic = new Float32Array(n);
      for (const [name, b64] of Object.entries(ref.result.speakers)) {
        const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
        const pcm = new Int16Array(bytes.buffer);
        const k = Math.round(delays[name] * sr / 1000);
        for (let i = 0; i < pcm.length && i + k < n; i++) mic[i + k] += pcm[i] * ref.result.scale;
      }
      return { samples: mic, sampleRate: sr, startedAt,
        settings: { echo_cancellation: false, noise_suppression: false, auto_gain_control: false, sample_rate: sr } };
    },
  };
}"""


def test_measuring_from_here_sends_the_arrivals_the_room_had(page: Page, svc: Running):
    names = [p.nombre for p in svc.service.installation.parlantes]
    delays = dict(zip(names, DELAYS_MS, strict=False))
    received = []
    estimator = svc.service.sync_estimator
    submit = estimator.submit
    estimator.submit = lambda m: (received.append(m), submit(m))[1]
    start(page)
    svc.command("set", changes={"probe": True})
    time.sleep(3.0)  # the probe ramps up and the ring fills
    page.evaluate(FAKE_RECORDER, delays)
    go(page, "Calibrar")
    page.locator("#est-here").click()
    note = page.locator("#est-here-note")
    expect(note).to_have_attribute("data-state", "done", timeout=30000)
    expect(note).to_contain_text("se oyeron 3 parlantes")
    browser = [m for m in received if m.origin == "browser"]
    assert len(browser) == 1
    m = browser[0]
    assert m.kind == "point"
    assert m.role == "target"
    first = names[0]
    for name in names[1:]:
        measured = m.arrivals_ms[name] - m.arrivals_ms[first]
        assert abs(measured - (delays[name] - delays[first])) < 0.05, (name, measured)


def test_without_the_probe_it_turns_it_on_and_says_so(page: Page, svc: Running):
    """It measures against the probe: rather than sending the person to Ajustes, it switches it on
    (probes/18-usabilidad)."""
    start(page)
    page.evaluate(FAKE_RECORDER, dict.fromkeys((p.nombre for p in svc.service.installation.parlantes), 20.0))
    go(page, "Calibrar")
    page.locator("#est-here").click()
    expect(page.locator("#est-here-note")).to_contain_text("Encendiendo la sonda")
    deadline = time.monotonic() + 5
    while not svc.state()["global"]["probe"] and time.monotonic() < deadline:
        time.sleep(0.1)
    assert svc.state()["global"]["probe"] is True
