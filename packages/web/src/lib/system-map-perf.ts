/**
 * Performance marks for the System Map page: `sm:data` when the graph arrives,
 * then `sm:aggregate`, `sm:layout` and `sm:paint` from the map's phase
 * callback, plus one `sm:longtask` measure per main-thread task over 50 ms
 * while the page is open. Everything stays on the browser's own performance
 * timeline (the Performance panel, or `performance.getEntriesByName`); nothing
 * is sent anywhere.
 */

import type { SystemMapPhase } from "@repowise-dev/ui/workspace/system-map";

export type SystemMapMark = "data" | SystemMapPhase;

export function markSystemMap(mark: SystemMapMark): void {
  if (typeof performance === "undefined" || typeof performance.mark !== "function") return;
  performance.mark(`sm:${mark}`);
}

/** Starts recording long tasks; returns the stop function. A no-op where unsupported. */
export function observeLongTasks(): () => void {
  if (
    typeof PerformanceObserver === "undefined" ||
    !PerformanceObserver.supportedEntryTypes?.includes("longtask")
  ) {
    return () => {};
  }
  const observer = new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
      performance.measure("sm:longtask", { start: entry.startTime, duration: entry.duration });
    }
  });
  observer.observe({ type: "longtask" });
  return () => observer.disconnect();
}
