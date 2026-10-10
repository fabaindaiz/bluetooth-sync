//! `capabilities()`: the constants each stage was built with, for the host to check.

use aurasync_dsp::{ambience, interpolation, limiter, loudness, spatial};
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::error::guard;
use crate::gil;

/// The constants each stage was built with: `{"interpolation": {"half": 16, "beta": 8.0,
/// "steps": 2048}, "spatial": {...}, "ambience": {"floor": 1e-8}, "fir": {"version": 2},
/// "virtual_bass": {"version": 1}, "limiter": {"version": 1, "margin_db": 0.01,
/// "near_ceiling": 0.25}, "loudness": {"version": 1, "near_peak": 0.5}, "api": {"version": 2}, "gil": {"detach_min_samples": 65536}}`. The FIR filters, the virtual bass and the
/// binding's own API share no constant with numpy; their `version` says this build has them, so
/// the host refuses an older build at load instead of failing on the first filter. The limiter
/// gets its design values from numpy per call; its two own constants (`MARGIN_DB`,
/// `NEAR_CEILING`) are numpy's and are listed so a build with others is refused at load; the
/// loudness meter likewise, with its one constant (`NEAR_PEAK`). `gil` is the current threshold
/// from which a call lets go of the interpreter (`set_detach_min_samples`), for the probes to note.
#[pyfunction]
pub fn capabilities(py: Python<'_>) -> PyResult<Bound<'_, PyDict>> {
    guard(|| {
        let read = PyDict::new(py);
        read.set_item("half", interpolation::HALF)?;
        read.set_item("beta", interpolation::BETA)?;
        read.set_item("steps", interpolation::STEPS)?;
        let upmix = PyDict::new(py);
        upmix.set_item("max_haas_ms", spatial::MAX_HAAS_MS)?;
        upmix.set_item("fade_in", spatial::FADE_IN)?;
        upmix.set_item("floor", spatial::FLOOR)?;
        upmix.set_item("silent", spatial::SILENT)?;
        upmix.set_item("front_boost_db", spatial::FRONT_BOOST_DB)?;
        upmix.set_item("min_energy_ratio", spatial::MIN_ENERGY_RATIO)?;
        upmix.set_item("mu0", spatial::MU0)?;
        upmix.set_item("mu1", spatial::MU1)?;
        upmix.set_item("sigma", spatial::SIGMA)?;
        let extractor = PyDict::new(py);
        extractor.set_item("floor", ambience::FLOOR)?;
        let all = PyDict::new(py);
        all.set_item("interpolation", read)?;
        all.set_item("spatial", upmix)?;
        all.set_item("ambience", extractor)?;
        // No constant shared with numpy: `version` is bumped (here and in backend.py) whenever
        // the Rust behaviour of the stage changes, so a stale build is refused.
        //
        // `fir` is at 2 although its Rust behaviour did not change: a host from before `api`
        // (`main` without a rebuild) checks no `api` key, takes this build, and fails every block
        // on `read`, which `Reader` replaced. At 2, such a host refuses the build with its hint.
        let fir = PyDict::new(py);
        fir.set_item("version", 2)?;
        let bass = PyDict::new(py);
        bass.set_item("version", 1)?;
        let peak = PyDict::new(py);
        peak.set_item("version", 1)?;
        peak.set_item("margin_db", limiter::MARGIN_DB)?;
        peak.set_item("near_ceiling", limiter::NEAR_CEILING)?;
        let meter = PyDict::new(py);
        meter.set_item("version", 1)?;
        meter.set_item("near_peak", loudness::NEAR_PEAK)?;
        let api = PyDict::new(py);
        api.set_item("version", 2)?;
        all.set_item("fir", fir)?;
        all.set_item("virtual_bass", bass)?;
        all.set_item("limiter", peak)?;
        all.set_item("loudness", meter)?;
        all.set_item("api", api)?;
        let threshold = PyDict::new(py);
        threshold.set_item("detach_min_samples", gil::min_samples())?;
        all.set_item("gil", threshold)?;
        Ok(all)
    })
}
