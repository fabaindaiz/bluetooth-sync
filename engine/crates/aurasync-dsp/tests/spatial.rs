//! The spatial / front upmix: the port of `host/src/aurasync/dsp/spatial.py`.
//!
//! The golden against numpy (within 1e-9) is in `host/tests/test_spatial_rust.py`; these tests
//! check what can be said without numpy: the front render gives its input back, silence stays
//! silence, the block size does not change the stream, the state moves exactly, errors change
//! nothing, and `process` allocates nothing within the reserved block.
//!
//! The counting allocator below is the only `unsafe` here: `GlobalAlloc` is an unsafe trait. The
//! library itself is `#![forbid(unsafe_code)]`.

use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;
use std::hint::black_box;

use aurasync_dsp::spatial::{N_FFT, Params, SpatialError, SpatialUpmix};

// --- An allocation counter, per thread so that tests running in parallel do not interfere.

struct Counting;

thread_local! {
    static COUNTING: Cell<bool> = const { Cell::new(false) };
    static ALLOCATIONS: Cell<usize> = const { Cell::new(0) };
}

fn note_allocation() {
    if COUNTING.try_with(Cell::get).unwrap_or(false) {
        let _ = ALLOCATIONS.try_with(|n| n.set(n.get() + 1));
    }
}

// SAFETY: every call is forwarded unchanged to the system allocator; the counter only touches
// const-initialised thread-locals, which never allocate.
unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        note_allocation();
        // SAFETY: same contract as the caller's.
        unsafe { System.alloc(layout) }
    }

    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        note_allocation();
        // SAFETY: same contract as the caller's.
        unsafe { System.alloc_zeroed(layout) }
    }

    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        note_allocation();
        // SAFETY: same contract as the caller's.
        unsafe { System.realloc(ptr, layout, new_size) }
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        // SAFETY: same contract as the caller's.
        unsafe { System.dealloc(ptr, layout) }
    }
}

#[global_allocator]
static GLOBAL: Counting = Counting;

/// How many allocations `f` made on this thread.
fn allocations_in(f: impl FnOnce()) -> usize {
    ALLOCATIONS.with(|n| n.set(0));
    COUNTING.with(|c| c.set(true));
    f();
    COUNTING.with(|c| c.set(false));
    ALLOCATIONS.with(Cell::get)
}

// --- Helpers.

const SR: u32 = 48_000;
const BLOCK: usize = 4096;

/// SplitMix64: a small deterministic generator, so the tests need no dependency.
struct Rng(u64);

impl Rng {
    fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    /// Full scale: uniform in [-1, 1).
    fn signal(&mut self, n: usize) -> Vec<f64> {
        (0..n)
            .map(|_| 2.0 * ((self.next_u64() >> 11) as f64 / (1u64 << 53) as f64) - 1.0)
            .collect()
    }
}

/// Partly correlated stereo: a centre plus independent room on each side.
fn music(n: usize, seed: u64) -> (Vec<f64>, Vec<f64>) {
    let mut rng = Rng(seed);
    let centre = rng.signal(n);
    let (a, b) = (rng.signal(n), rng.signal(n));
    let left = centre
        .iter()
        .zip(&a)
        .map(|(c, x)| 0.6 * c + 0.4 * x)
        .collect();
    let right = centre
        .iter()
        .zip(&b)
        .map(|(c, x)| 0.6 * c + 0.4 * x)
        .collect();
    (left, right)
}

/// Three principals at -60, 60 and 180 degrees and one ambient speaker.
fn ring(params: Params) -> SpatialUpmix {
    let mut up = SpatialUpmix::new(4, SR, N_FFT, N_FFT / 4, BLOCK);
    up.set_params(params);
    up.set_layout(
        &[Some(-60.0), Some(60.0), Some(180.0), None],
        &[false, false, false, true],
        None,
    )
    .unwrap();
    up
}

/// The whole stream through `up` in blocks of `sizes` (cycled): (direct, ambience) per speaker.
fn stream(
    up: &mut SpatialUpmix,
    left: &[f64],
    right: &[f64],
    sizes: &[usize],
) -> Vec<(Vec<f64>, Vec<f64>)> {
    let k = up.speakers();
    let mut out = vec![(Vec::new(), Vec::new()); k];
    let (mut at, mut i) = (0, 0);
    while at < left.len() {
        let n = sizes[i % sizes.len()].min(left.len() - at);
        let (mut direct, mut ambience) = (vec![0.0; k * n], vec![0.0; k * n]);
        up.process(
            &left[at..at + n],
            &right[at..at + n],
            &mut direct,
            &mut ambience,
        )
        .unwrap();
        for (s, (d, a)) in out.iter_mut().enumerate() {
            d.extend_from_slice(&direct[s * n..(s + 1) * n]);
            a.extend_from_slice(&ambience[s * n..(s + 1) * n]);
        }
        at += n;
        i += 1;
    }
    out
}

// --- The tests.

#[test]
fn the_front_render_gives_its_input_back_to_the_front_pair() {
    let (left, right) = music(48_000, 1);
    let mut up = ring(Params {
        front_intact: true,
        ..Params::default()
    });
    let out = stream(&mut up, &left, &right, &[BLOCK]);
    // Past the latency (N_FFT) and the entry fade, the front pair plays L and R as they came.
    let settle = 24_000;
    for (speaker, input) in [(0, &left), (1, &right)] {
        let worst = (settle..left.len())
            .map(|i| (out[speaker].0[i] - input[i - N_FFT]).abs())
            .fold(0.0, f64::max);
        assert!(worst < 1e-12, "speaker {speaker}: {worst}");
        // Nothing of the ambience path on the front.
        assert!(out[speaker].1.iter().all(|&x| x == 0.0));
    }
    // The rear and the ambient speaker get only ambience.
    for (direct, ambience) in &out[2..] {
        assert!(direct.iter().all(|&x| x == 0.0));
        assert!(ambience[settle..].iter().any(|&x| x != 0.0));
    }
}

#[test]
fn silence_stays_silence_and_a_signal_comes_out_after_the_latency() {
    let zeros = vec![0.0; 20_000];
    let mut up = ring(Params::default());
    let out = stream(&mut up, &zeros, &zeros, &[BLOCK]);
    assert!(
        out.iter()
            .all(|(d, a)| d.iter().chain(a).all(|&x| x == 0.0))
    );

    let (left, right) = music(20_000, 2);
    let mut up = ring(Params::default());
    let out = stream(&mut up, &left, &right, &[BLOCK]);
    for (d, a) in &out {
        // The first N_FFT samples are the latency: exact zeros.
        assert!(d[..N_FFT].iter().chain(&a[..N_FFT]).all(|&x| x == 0.0));
        assert!(d.iter().chain(a).all(|x| x.is_finite()));
    }
    assert!(
        out.iter()
            .any(|(d, _)| d[N_FFT..].iter().any(|&x| x != 0.0))
    );
}

#[test]
fn the_block_size_does_not_change_the_stream() {
    // Without a live change, the output is a function of the input stream only: frames fall on
    // fixed hops and the fade counts samples. Bit for bit, whatever the blocks.
    let (left, right) = music(30_000, 3);
    let reference = stream(&mut ring(Params::default()), &left, &right, &[BLOCK]);
    for sizes in [
        &[1usize, 777, 10_000][..],
        &[512],
        &[2047, 2049],
        &[0, 5000],
    ] {
        let got = stream(&mut ring(Params::default()), &left, &right, sizes);
        assert_eq!(got, reference, "blocks {sizes:?}");
    }
}

#[test]
fn the_state_moves_exactly() {
    let (left, right) = music(30_000, 4);
    let mut a = ring(Params::default());
    let _ = stream(&mut a, &left[..10_001], &right[..10_001], &[BLOCK]);
    let mut b = ring(Params::default());
    b.set_state(&a.state()).unwrap();
    assert_eq!(a.state(), b.state());
    let rest_a = stream(&mut a, &left[10_001..], &right[10_001..], &[3000]);
    let rest_b = stream(&mut b, &left[10_001..], &right[10_001..], &[3000]);
    assert_eq!(rest_a, rest_b);
}

#[test]
fn a_refused_call_changes_nothing() {
    let (left, right) = music(10_000, 5);
    let mut up = ring(Params::default());
    let _ = stream(&mut up, &left, &right, &[BLOCK]);
    let before = up.state();

    let mut out = vec![0.0; 4 * 10];
    assert_eq!(
        up.process(&left[..10], &right[..11], &mut out.clone(), &mut out),
        Err(SpatialError::LengthMismatch {
            left: 10,
            right: 11
        })
    );
    let mut short = vec![0.0; 4 * 10 - 1];
    assert_eq!(
        up.process(&left[..10], &right[..10], &mut short, &mut out),
        Err(SpatialError::OutputMismatch)
    );
    assert!(matches!(
        up.set_layout(&[Some(0.0)], &[false], None),
        Err(SpatialError::BadShape(_))
    ));
    let mut bad = up.state();
    bad.norm.pop();
    assert!(matches!(up.set_state(&bad), Err(SpatialError::BadShape(m)) if m.contains("norm")));
    let mut bad = up.state();
    bad.haas_read[0] = up.haas_len() + 1;
    assert!(
        matches!(up.set_state(&bad), Err(SpatialError::BadShape(m)) if m.contains("haas_read"))
    );
    let mut bad = up.state();
    bad.ready_ambience[1].push(0.0);
    assert!(matches!(up.set_state(&bad), Err(SpatialError::BadShape(_))));
    assert_eq!(up.state(), before);
}

#[test]
fn the_haas_delay_is_clamped_and_rounded_like_python() {
    let mut up = ring(Params::default());
    up.set_params(Params {
        haas_ms: 45.0,
        ..Params::default()
    });
    assert_eq!(up.params().haas_ms, 30.0);
    up.set_params(Params {
        haas_ms: -1.0,
        ..Params::default()
    });
    assert_eq!(up.params().haas_ms, 0.0);
    // 30 ms at 48 kHz: a line of 1440 samples.
    assert_eq!(up.haas_len(), 1440);
}

#[test]
fn no_allocation_in_process() {
    // The control: the counter does see an allocation.
    assert!(allocations_in(|| drop(black_box(Vec::<f64>::with_capacity(1)))) >= 1);

    let (left, right) = music(BLOCK, 6);
    let k = 4;
    let (mut direct, mut ambience) = (vec![0.0; k * BLOCK], vec![0.0; k * BLOCK]);
    for front_intact in [false, true] {
        let mut up = ring(Params {
            front_intact,
            ..Params::default()
        });
        // Past the latency and the fade, then a live Haas change (the crossfade path).
        for _ in 0..4 {
            up.process(&left, &right, &mut direct, &mut ambience)
                .unwrap();
        }
        up.set_params(Params {
            front_intact,
            haas_ms: 25.0,
            ..Params::default()
        });
        for n in [BLOCK, 1, 511, 2049, BLOCK - 1, BLOCK, BLOCK] {
            let allocations = allocations_in(|| {
                up.process(
                    black_box(&left[..n]),
                    black_box(&right[..n]),
                    &mut direct[..k * n],
                    &mut ambience[..k * n],
                )
                .unwrap();
            });
            assert_eq!(allocations, 0, "front {front_intact}, block of {n}");
        }
    }
}
