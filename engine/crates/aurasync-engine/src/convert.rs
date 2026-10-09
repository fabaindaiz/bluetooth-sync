//! Python values to Rust ones and back: the vectors, rows, matrices and state dicts the classes
//! share. Nothing is converted silently: a wrong dtype, rank or byte order is a `TypeError`.

use std::borrow::Cow;

use aurasync_dsp::Complex;
use numpy::{PyArray1, PyArray2, PyArrayMethods, PyReadonlyArray1};
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::error::EngineError;

/// `value` as a 1-D float64 numpy array, or a `TypeError` that says what it is instead (the
/// automatic one reads "'ndarray' object is not an instance of 'ndarray'"). Nothing is converted:
/// float32, integers, another byte order or a list are refused.
pub fn float64_vector<'py>(
    name: &str,
    value: &Bound<'py, PyAny>,
) -> Result<PyReadonlyArray1<'py, f64>, EngineError> {
    let Ok(array) = value.cast::<PyArray1<f64>>() else {
        let what = match (value.getattr("dtype"), value.getattr("ndim")) {
            (Ok(dtype), Ok(ndim)) => {
                format!("an array of {} with {ndim} dimension(s)", dtype.str()?)
            }
            _ => format!("{}", value.get_type().name()?),
        };
        return Err(EngineError::Type(format!(
            "{name} must be a 1-D float64 numpy array in native byte order, not {what}"
        )));
    };
    Ok(array.try_readonly()?)
}

/// The array's samples in order: borrowed when contiguous, copied from a strided view.
pub fn samples<'a>(array: &'a PyReadonlyArray1<'_, f64>) -> Cow<'a, [f64]> {
    match array.as_slice() {
        Ok(slice) => Cow::Borrowed(slice),
        Err(_) => Cow::Owned(array.as_array().iter().copied().collect()),
    }
}

/// `taps` as a 1-D float64 numpy array's samples, owned.
pub fn taps_of(taps: &Bound<'_, PyAny>) -> Result<Vec<f64>, EngineError> {
    let taps = float64_vector("taps", taps)?;
    Ok(samples(&taps).into_owned())
}

/// `value` as a 2-D float64 numpy array's rows, or a `TypeError` (as [`float64_vector`]).
pub fn float64_rows(name: &str, value: &Bound<'_, PyAny>) -> Result<Vec<Vec<f64>>, EngineError> {
    let Ok(array) = value.cast::<PyArray2<f64>>() else {
        return Err(EngineError::Type(format!(
            "{name} must be a 2-D float64 numpy array in native byte order"
        )));
    };
    let array = array.try_readonly()?;
    Ok(array
        .as_array()
        .rows()
        .into_iter()
        .map(|row| row.to_vec())
        .collect())
}

/// `state[name]`, or a `ValueError` naming the missing key.
pub fn item<'py>(state: &Bound<'py, PyDict>, name: &str) -> Result<Bound<'py, PyAny>, EngineError> {
    state
        .get_item(name)?
        .ok_or_else(|| EngineError::Invalid(format!("state: {name} is missing")))
}

/// `state[name]` as a 1-D float64 vector, owned.
pub fn vector(state: &Bound<'_, PyDict>, name: &str) -> Result<Vec<f64>, EngineError> {
    let array = float64_vector(name, &item(state, name)?)?;
    Ok(samples(&array).into_owned())
}

/// `rows` as a new `(rows, columns)` float64 array (`columns` is needed when there are none).
pub fn matrix<'py>(
    py: Python<'py>,
    rows: &[Vec<f64>],
    columns: usize,
) -> Result<Bound<'py, PyArray2<f64>>, EngineError> {
    let flat: Vec<f64> = rows.iter().flatten().copied().collect();
    Ok(PyArray1::from_vec(py, flat).reshape([rows.len(), columns])?)
}

/// A complex accumulator's two float64 arrays under `<name>_re` and `<name>_im` in `out`.
pub fn set_complex(
    py: Python<'_>,
    out: &Bound<'_, PyDict>,
    name: &str,
    values: &[Complex<f64>],
) -> Result<(), EngineError> {
    let re: Vec<f64> = values.iter().map(|z| z.re).collect();
    let im: Vec<f64> = values.iter().map(|z| z.im).collect();
    out.set_item(format!("{name}_re"), PyArray1::from_vec(py, re))?;
    out.set_item(format!("{name}_im"), PyArray1::from_vec(py, im))?;
    Ok(())
}

/// The inverse of [`set_complex`]; a `ValueError` when the two parts differ in length.
pub fn get_complex(
    state: &Bound<'_, PyDict>,
    name: &str,
) -> Result<Vec<Complex<f64>>, EngineError> {
    let re = vector(state, &format!("{name}_re"))?;
    let im = vector(state, &format!("{name}_im"))?;
    if re.len() != im.len() {
        return Err(EngineError::Invalid(format!(
            "state: {name}_re has length {} and {name}_im {}",
            re.len(),
            im.len()
        )));
    }
    Ok(re
        .iter()
        .zip(&im)
        .map(|(&re, &im)| Complex::new(re, im))
        .collect())
}
