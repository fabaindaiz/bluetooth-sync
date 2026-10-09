//! Prueba aislada del producto de núcleos del limitador de pico real (experimentos/20 §9), fuera
//! del limitador: los tres puntos entre muestras de cada ventana de 24 coeficientes, sobre las
//! 5079 ventanas de un bloque de 4096 con los valores por defecto, 2000 repeticiones por forma, dos
//! rondas. Las formas:
//!
//! - `vorig`: la de la primera versión, una ventana a la vez con sus tres filas (la de
//!   `limitador-producto-por-ventana.patch`);
//! - `v0`: lo mismo, con el máximo de las filas acumulado a mano;
//! - `v1`: la elegida, coeficiente por coeficiente sobre todas las ventanas (`limiter.rs`);
//! - `v2`: ocho ventanas con sus tres filas en registros (la "forma en registros" de §9);
//! - `v3`: `v1` por bloques de 256 ventanas; `v4<L>`: grupos de L ventanas por fila.
//!
//! Imprime el tiempo por bloque de cada una y si dan los mismos bits que `v0`. Desde la raíz del
//! repositorio:
//!
//!     cargo run --release --manifest-path probes/20-costo-sinc-rust/producto_aislado/Cargo.toml

use std::hint::black_box;
use std::time::Instant;
const LANES: usize = 8;
fn maximum(a: f64, b: f64) -> f64 { if a.is_nan() || b.is_nan() { f64::NAN } else if a >= b { a } else { b } }

// v1: taps outer over all windows, scratch per row
fn v1(x: &[f64], k: &[f64], taps: usize, point: &mut [f64], between: &mut [f64]) {
    let windows = between.len();
    for (p, row) in k.chunks_exact(taps).enumerate() {
        point.fill(0.0);
        for (m, &kk) in row.iter().enumerate() {
            for (s, &v) in point.iter_mut().zip(&x[m..m + windows]) { *s += kk * v; }
        }
        for (b, &s) in between.iter_mut().zip(point.iter()) { *b = if p == 0 { s.abs() } else { maximum(*b, s.abs()) }; }
    }
}
// v2: 8 windows x 3 rows in registers
fn v2(x: &[f64], k: &[f64], taps: usize, between: &mut [f64]) {
    let rows: [&[f64]; 3] = [&k[..taps], &k[taps..2 * taps], &k[2 * taps..]];
    let (groups, _rest) = between.as_chunks_mut::<LANES>();
    for (g, group) in groups.iter_mut().enumerate() {
        let start = g * LANES;
        let mut sums = [[0.0; LANES]; 3];
        for m in 0..taps {
            let w: &[f64; LANES] = x[start + m..start + m + LANES].try_into().unwrap();
            for (sum, row) in sums.iter_mut().zip(rows) {
                let kk = row[m];
                for (s, &v) in sum.iter_mut().zip(w) { *s += kk * v; }
            }
        }
        for (j, slot) in group.iter_mut().enumerate() {
            *slot = maximum(maximum(sums[0][j].abs(), sums[1][j].abs()), sums[2][j].abs());
        }
    }
}
// v3: taps outer, but in tiles of 256 windows (scratch stays in L1), 3 rows in one pass
fn v3(x: &[f64], k: &[f64], taps: usize, between: &mut [f64]) {
    const T: usize = 256;
    let rows: [&[f64]; 3] = [&k[..taps], &k[taps..2 * taps], &k[2 * taps..]];
    let windows = between.len();
    let mut start = 0;
    while start < windows {
        let len = T.min(windows - start);
        let mut acc = [[0.0f64; T]; 3];
        for m in 0..taps {
            let xs = &x[start + m..start + m + len];
            for (a, row) in acc.iter_mut().zip(rows) {
                let kk = row[m];
                for (s, &v) in a[..len].iter_mut().zip(xs) { *s += kk * v; }
            }
        }
        for j in 0..len {
            between[start + j] = maximum(maximum(acc[0][j].abs(), acc[1][j].abs()), acc[2][j].abs());
        }
        start += len;
    }
}
// v0: plain per window
fn v0(x: &[f64], k: &[f64], taps: usize, between: &mut [f64]) {
    for (i, b) in between.iter_mut().enumerate() {
        let w = &x[i..i + taps];
        let mut best = 0.0; 
        for (p, row) in k.chunks_exact(taps).enumerate() {
            let mut s = 0.0; for (a, v) in row.iter().zip(w) { s += a * v; }
            best = if p == 0 { f64::abs(s) } else { maximum(best, s.abs()) };
        }
        *b = best;
    }
}
fn minimum(a: f64, b: f64) -> f64 { if a.is_nan() || b.is_nan() { f64::NAN } else if a <= b { a } else { b } }
fn vorig(x: &[f64], kernels: &[f64], taps: usize, between: &mut [f64]) {
    for (i, b) in between.iter_mut().enumerate() {
        let window = &x[i..i + taps];
        let mut points = kernels.chunks_exact(taps).map(|row| {
            let mut sum = 0.0;
            for (k, v) in row.iter().zip(window) { sum += k * v; }
            sum.abs()
        });
        let first = points.next().unwrap_or(0.0);
        *b = points.fold(first, maximum);
    }
}
fn needed_loop(x: &[f64], w: usize, target: f64, between: &[f64], needed: &mut [f64]) {
    for (i, slot) in needed.iter_mut().enumerate() {
        let peak = maximum(x[w + i].abs(), maximum(between[i], between[i + 1]));
        *slot = minimum(1.0, target / maximum(peak, 1e-12));
    }
}
fn v4<const L: usize>(x: &[f64], k: &[f64], taps: usize, between: &mut [f64]) {
    let windows = between.len();
    let groups = windows / L;
    for (p, row) in k.chunks_exact(taps).enumerate() {
        for g in 0..groups {
            let start = g * L;
            let span = &x[start..start + taps + L - 1];
            let mut acc = [0.0f64; L];
            for (m, &kk) in row.iter().enumerate() {
                let w: &[f64; L] = span[m..m + L].try_into().unwrap();
                for j in 0..L { acc[j] += kk * w[j]; }
            }
            let out: &mut [f64; L] = (&mut between[start..start + L]).try_into().unwrap();
            for j in 0..L { out[j] = if p == 0 { acc[j].abs() } else { maximum(out[j], acc[j].abs()) }; }
        }
    }
}
fn main() {
    let taps = 24; let windows = 4096 + 2*131 + 720 + 1; let n = windows + taps - 1;
    let mut seed = 1u64; let mut r = || { seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1); ((seed >> 11) as f64 / (1u64<<53) as f64) - 0.5 };
    let x: Vec<f64> = (0..n).map(|_| r()).collect();
    let k: Vec<f64> = (0..3*taps).map(|_| r()).collect();
    let mut point = vec![0.0; windows];
    let mut outs = vec![vec![0.0; windows]; 4];
    let reps = 2000;
    for round in 0..2 {
        let t = Instant::now(); for _ in 0..reps { v0(black_box(&x), &k, taps, &mut outs[0]); } let a = t.elapsed().as_secs_f64()*1e6/reps as f64;
        let t = Instant::now(); for _ in 0..reps { v1(black_box(&x), &k, taps, &mut point, &mut outs[1]); } let b = t.elapsed().as_secs_f64()*1e6/reps as f64;
        let t = Instant::now(); for _ in 0..reps { v2(black_box(&x), &k, taps, &mut outs[2]); } let c = t.elapsed().as_secs_f64()*1e6/reps as f64;
        let t = Instant::now(); for _ in 0..reps { v3(black_box(&x), &k, taps, &mut outs[3]); } let d = t.elapsed().as_secs_f64()*1e6/reps as f64;
        let t = Instant::now(); for _ in 0..reps { vorig(black_box(&x), &k, taps, &mut outs[3]); } let e = t.elapsed().as_secs_f64()*1e6/reps as f64;
        let mut needed = vec![0.0; windows - 1];
        let t = Instant::now(); for _ in 0..reps { needed_loop(black_box(&x), 12, 0.89, &outs[0], &mut needed); } let f = t.elapsed().as_secs_f64()*1e6/reps as f64;
        let mut o4 = vec![0.0; windows]; let mut o5 = vec![0.0; windows];
        let t = Instant::now(); for _ in 0..reps { v4::<4>(black_box(&x), &k, taps, &mut o4); } let g4 = t.elapsed().as_secs_f64()*1e6/reps as f64;
        let t = Instant::now(); for _ in 0..reps { v4::<8>(black_box(&x), &k, taps, &mut o5); } let g8 = t.elapsed().as_secs_f64()*1e6/reps as f64;
        let full = windows / 8 * 8;
        println!("v4<4> {g4:.1} us, v4<8> {g8:.1} us, identical {} {}", o4[..full] == outs[0][..full], o5[..full] == outs[0][..full]);
        println!("vorig {e:.1} us, needed loop {f:.1} us");
        println!("round {round}: v0 plain {a:.1} us, v1 taps-outer {b:.1} us, v2 regblock {c:.1} us, v3 tiles {d:.1} us");
    }
    let full = windows / 8 * 8;
    println!("identical: v1 {} v2 {} v3 {}", outs[1] == outs[0], outs[2][..full] == outs[0][..full], outs[3] == outs[0]);
}
