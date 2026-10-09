//! The complex arithmetic the two STFT stages ([`crate::ambience`], [`crate::spatial`]) share,
//! written as numpy computes it so the two round alike.

use crate::Complex;

/// `s * z` for a real `s`: a componentwise product (numpy's complex product with a zero
/// imaginary part gives the same bits).
pub(crate) fn scaled(z: Complex<f64>, s: f64) -> Complex<f64> {
    Complex::new(z.re * s, z.im * s)
}

/// `z * conj(w)` as numpy multiplies complex numbers.
pub(crate) fn times_conj(z: Complex<f64>, w: Complex<f64>) -> Complex<f64> {
    let (br, bi) = (w.re, -w.im);
    Complex::new(z.re * br - z.im * bi, z.re * bi + z.im * br)
}

/// numpy's `abs` of a complex number: `hypot` (numpy's `npy_cabs`).
pub(crate) fn magnitude(z: Complex<f64>) -> f64 {
    z.re.hypot(z.im)
}

/// The squared norm as `re * re + im * im` (the spatial stage's `z.real * z.real + z.imag *
/// z.imag`). Not the same bits as `magnitude(z) ** 2`, which the ambience extractor squares
/// (numpy's `np.abs(z) ** 2` there): each stage keeps its own.
pub(crate) fn norm2(z: Complex<f64>) -> f64 {
    z.re * z.re + z.im * z.im
}
