"""Every sync setting explained: recommendation, how it sounds, a simulated figure; and the
whole configuration together (spec 2026-10-03 §5, d-7c8794-0a4586)."""

import dataclasses
import threading

import pytest

from aurasync import sync_docs
from aurasync.knob_docs import Doc, Figure
from aurasync.sync_estimator import SyncEstimator
from aurasync.sync_methods import SyncSettings


def test_every_setting_has_a_doc():
    names = {f.name for f in dataclasses.fields(SyncSettings)}
    assert set(sync_docs.DOCS) == names
    for doc in sync_docs.DOCS.values():
        assert isinstance(doc, Doc)
        assert doc.title
        assert doc.why_recommended
        assert doc.sounds_choices or (doc.sounds_low and doc.sounds_high)


def test_recommended_matches_defaults():
    defaults = SyncSettings()
    for name, doc in sync_docs.DOCS.items():
        assert doc.recommended == getattr(defaults, name), name


@pytest.mark.parametrize("name", sorted(sync_docs.WITH_FIGURE))
def test_figures_are_simulated_and_compare_current_with_recommended(name):
    fig = sync_docs.figure(name, SyncSettings())
    assert isinstance(fig, Figure)
    assert fig.evidence == "SIMULADO"
    styles = {s["style"] for s in fig.series}
    assert "recommended" in styles
    assert fig.caption


def test_settings_not_built_yet_have_no_figure_and_say_when():
    for name in set(sync_docs.DOCS) - sync_docs.WITH_FIGURE:
        assert sync_docs.figure(name, SyncSettings()) is None
        assert "etapa" in sync_docs.DOCS[name].help


@pytest.mark.parametrize("seed", [0, 1])
def test_a_worse_window_shows_more_error(seed):
    def mean_error(window):
        fig = sync_docs.figure("window_min", SyncSettings(window_min=window), seed=seed)
        current = next(s for s in fig.series if s["style"] == "current")
        values = [y for _, y in current["points"] if y is not None]
        return sum(values) / len(values)

    assert mean_error(2) > mean_error(10)


@pytest.mark.parametrize("seed", [0, 1])
def test_the_jump_figure_keeps_the_jump_once_believed(seed):
    """The figures fit as the estimator does: a confirmed jump is carried, not lost after two."""
    fig = sync_docs.figure("jump_repeats", SyncSettings(), seed=seed)
    rec = next(s for s in fig.series if s["style"] == "recommended")
    after = [y for x, y in rec["points"] if x >= 12 and y is not None]
    assert after
    assert max(after) < 0.3, after


def test_together_text_names_the_choices():
    out = sync_docs.together(SyncSettings(jump_repeats=1))
    assert "1 medición" in out["text"]
    assert isinstance(out["figure"], Figure)
    assert {s["style"] for s in out["figure"].series} >= {"recommended", "current"}


def test_explain_is_built_off_the_engine_thread():
    built_on = []

    def explainer(settings):
        built_on.append(threading.current_thread().name)
        return sync_docs.explain(settings)

    est = SyncEstimator(SyncSettings(), dict, "server", clock=lambda: 0.0)
    est.explainer = explainer
    try:
        est.request_explain()
        assert est.wait_idle(timeout=30)
        assert est.explain is not None
        assert set(est.explain["docs"]) == set(sync_docs.DOCS)
        assert "together" in est.explain
        assert built_on == ["aurasync-sync-estimator"]
    finally:
        est.close()
