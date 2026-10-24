import { afterEach, describe, expect, it, vi } from "vitest";
import { markSystemMap, observeLongTasks } from "./system-map-perf";

afterEach(() => {
  vi.unstubAllGlobals();
  performance.clearMarks();
  performance.clearMeasures();
});

describe("system map performance marks", () => {
  it("puts an sm: mark on the timeline", () => {
    markSystemMap("data");
    markSystemMap("paint");
    expect(performance.getEntriesByType("mark").map((m) => m.name)).toEqual(["sm:data", "sm:paint"]);
  });

  it("is a no-op where long tasks cannot be observed", () => {
    vi.stubGlobal("PerformanceObserver", { supportedEntryTypes: ["mark"] });
    const stop = observeLongTasks();
    expect(() => stop()).not.toThrow();
  });

  it("records each long task as an sm:longtask measure until stopped", () => {
    let deliver: (entries: { startTime: number; duration: number }[]) => void = () => {};
    const disconnect = vi.fn();
    class FakeObserver {
      static supportedEntryTypes = ["longtask"];
      constructor(cb: (list: { getEntries: () => unknown[] }) => void) {
        deliver = (entries) => cb({ getEntries: () => entries });
      }
      observe() {}
      disconnect = disconnect;
    }
    vi.stubGlobal("PerformanceObserver", FakeObserver);
    const stop = observeLongTasks();
    deliver([{ startTime: 0, duration: 75 }]);
    const measures = performance.getEntriesByName("sm:longtask");
    expect(measures).toHaveLength(1);
    expect(measures[0]!.duration).toBe(75);
    stop();
    expect(disconnect).toHaveBeenCalled();
  });
});
