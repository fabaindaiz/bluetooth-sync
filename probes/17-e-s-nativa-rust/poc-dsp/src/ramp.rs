//! A linear ramp advanced one sample at a time, the building block of every live change.

/// Moves linearly from its current value to a target over a given number of samples.
///
/// `advance()` returns the value for the next sample; after the last step it is exactly the
/// target (no accumulated rounding is left behind).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct LinearRamp {
    current: f32,
    target: f32,
    step: f32,
    remaining: u32,
}

impl LinearRamp {
    /// A ramp already settled at `value`.
    pub fn new(value: f32) -> Self {
        Self {
            current: value,
            target: value,
            step: 0.0,
            remaining: 0,
        }
    }

    /// Starts a ramp to `target` that ends after `samples` calls to `advance()`.
    /// With `samples == 0` the value jumps.
    pub fn set(&mut self, target: f32, samples: u32) {
        if samples == 0 || target == self.current {
            self.jump(target);
            return;
        }
        self.target = target;
        self.step = (target - self.current) / samples as f32;
        self.remaining = samples;
    }

    /// Starts a ramp to `target` that moves at most `per_sample` per sample.
    pub fn set_rate_limited(&mut self, target: f32, per_sample: f32) {
        let distance = (target - self.current).abs();
        let samples = if per_sample > 0.0 {
            (distance / per_sample).ceil() as u32
        } else {
            0
        };
        self.set(target, samples);
    }

    /// Sets the value with no ramp.
    pub fn jump(&mut self, value: f32) {
        *self = Self::new(value);
    }

    /// The value for the next sample.
    #[inline]
    pub fn advance(&mut self) -> f32 {
        if self.remaining > 0 {
            self.remaining -= 1;
            self.current = if self.remaining == 0 {
                self.target
            } else {
                self.current + self.step
            };
        }
        self.current
    }

    pub fn value(&self) -> f32 {
        self.current
    }

    pub fn target(&self) -> f32 {
        self.target
    }

    /// True when the ramp has reached its target.
    pub fn is_settled(&self) -> bool {
        self.remaining == 0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reaches_the_target_exactly_after_n_samples() {
        let mut r = LinearRamp::new(1.0);
        r.set(0.0, 4);
        let v: Vec<f32> = (0..6).map(|_| r.advance()).collect();
        assert_eq!(v, vec![0.75, 0.5, 0.25, 0.0, 0.0, 0.0]);
        assert!(r.is_settled());
    }

    #[test]
    fn rate_limited_takes_distance_over_speed_samples() {
        let mut r = LinearRamp::new(-1.0);
        r.set_rate_limited(1.0, 0.5);
        let v: Vec<f32> = (0..5).map(|_| r.advance()).collect();
        assert_eq!(v, vec![-0.5, 0.0, 0.5, 1.0, 1.0]);
    }

    #[test]
    fn zero_samples_jumps() {
        let mut r = LinearRamp::new(0.0);
        r.set(2.0, 0);
        assert_eq!(r.advance(), 2.0);
        assert!(r.is_settled());
    }
}
