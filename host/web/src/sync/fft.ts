// An iterative radix-2 FFT, enough for the probe measurement in the browser (measure.ts): numpy's
// `rfft(x, n)` and `irfft(X, n)` for a power-of-two `n`. No DOM: test/measure.test.ts runs it in Node.

export interface Spectrum {
  re: Float64Array;
  im: Float64Array;
}

function transform(re: Float64Array, im: Float64Array, inverse: boolean): void {
  const n = re.length;
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [re[i], re[j]] = [re[j] as number, re[i] as number];
      [im[i], im[j]] = [im[j] as number, im[i] as number];
    }
  }
  for (let size = 2; size <= n; size <<= 1) {
    const angle = ((inverse ? 2 : -2) * Math.PI) / size;
    const wr = Math.cos(angle);
    const wi = Math.sin(angle);
    const half = size >> 1;
    for (let start = 0; start < n; start += size) {
      let cr = 1;
      let ci = 0;
      for (let k = 0; k < half; k++) {
        const a = start + k;
        const b = a + half;
        const br = (re[b] as number) * cr - (im[b] as number) * ci;
        const bi = (re[b] as number) * ci + (im[b] as number) * cr;
        re[b] = (re[a] as number) - br;
        im[b] = (im[a] as number) - bi;
        re[a] = (re[a] as number) + br;
        im[a] = (im[a] as number) + bi;
        const next = cr * wr - ci * wi;
        ci = cr * wi + ci * wr;
        cr = next;
      }
    }
  }
}

/** The first n/2 + 1 bins of the DFT of `x` zero-padded to `n` (numpy.fft.rfft(x, n)). */
export function rfft(x: ArrayLike<number>, n: number): Spectrum {
  const re = new Float64Array(n);
  const im = new Float64Array(n);
  for (let i = 0; i < Math.min(x.length, n); i++) re[i] = x[i] as number;
  transform(re, im, false);
  return { re: re.slice(0, n / 2 + 1), im: im.slice(0, n / 2 + 1) };
}

/** The real signal of length `n` with half spectrum `X` (numpy.fft.irfft(X, n)): the imaginary
 * parts of the DC and Nyquist bins are ignored, as numpy does. */
export function irfft(X: Spectrum, n: number): Float64Array {
  const re = new Float64Array(n);
  const im = new Float64Array(n);
  const half = n / 2;
  for (let k = 0; k <= half; k++) {
    re[k] = X.re[k] as number;
    im[k] = k === 0 || k === half ? 0 : (X.im[k] as number);
  }
  for (let k = 1; k < half; k++) {
    re[n - k] = re[k] as number;
    im[n - k] = -(im[k] as number);
  }
  transform(re, im, true);
  for (let i = 0; i < n; i++) re[i] = (re[i] as number) / n;
  return re;
}
