// A slider sends while it is dragged, at most every 80 ms, and once more when it is let go: the
// same rhythm as app.js's `liveRange` (the stream brings the answer in the next frame).
export const SLIDER_EVERY_MS = 80;

export interface Clock {
  now(): number;
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
}

const realClock: Clock = {
  now: () => Date.now(),
  setTimeout: (fn, ms) => globalThis.setTimeout(fn, ms),
  clearTimeout: (handle) => globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>),
};

export interface Throttled<T> {
  /** While dragging: sends now if the last send is older than `every`, else at the end of it. */
  input(value: T): void;
  /** Let go: sends now, and drops what was waiting. */
  commit(value: T): void;
  cancel(): void;
}

export function throttle<T>(send: (value: T) => void, every = SLIDER_EVERY_MS, clock: Clock = realClock): Throttled<T> {
  let last = Number.NEGATIVE_INFINITY;
  let timer: unknown = null;
  const clear = (): void => {
    if (timer !== null) clock.clearTimeout(timer);
    timer = null;
  };
  return {
    input(value) {
      const now = clock.now();
      clear();
      if (now - last > every) {
        last = now;
        send(value);
      } else {
        timer = clock.setTimeout(() => {
          timer = null;
          last = clock.now();
          send(value);
        }, every);
      }
    },
    commit(value) {
      clear();
      last = clock.now();
      send(value);
    },
    cancel: clear,
  };
}
