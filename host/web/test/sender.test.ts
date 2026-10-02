import { describe, expect, it } from "vitest";
import { type Clock, throttle } from "../src/sender.ts";

function fakeClock(): Clock & { advance(ms: number): void } {
  let now = 1000;
  let timers: { at: number; fn: () => void; id: number }[] = [];
  let next = 0;
  return {
    now: () => now,
    setTimeout: (fn, ms) => {
      timers.push({ at: now + ms, fn, id: ++next });
      return next;
    },
    clearTimeout: (id) => {
      timers = timers.filter((t) => t.id !== id);
    },
    advance(ms) {
      now += ms;
      const due = timers.filter((t) => t.at <= now);
      timers = timers.filter((t) => t.at > now);
      for (const t of due) t.fn();
    },
  };
}

describe("a slider's sender", () => {
  it("sends at most every 80 ms while dragged, and the last value when let go", () => {
    const clock = fakeClock();
    const sent: number[] = [];
    const s = throttle<number>((v) => sent.push(v), 80, clock);
    s.input(1); // sends at once
    clock.advance(20);
    s.input(2); // waits
    clock.advance(20);
    s.input(3); // replaces the waiting one
    expect(sent).toEqual([1]);
    clock.advance(80);
    expect(sent).toEqual([1, 3]);
    s.input(4);
    s.commit(5); // let go: now, and 4 is dropped
    clock.advance(200);
    expect(sent).toEqual([1, 3, 5]);
  });
});
