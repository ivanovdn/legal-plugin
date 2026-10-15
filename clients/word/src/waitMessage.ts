// What the pane says while a turn is out — the real elapsed time instead of a
// fixed estimate. Thresholds come from measured turns (2026-10-14/15 pilot):
// chat answers in 2–25 s, a review in 78–95 s. Spark's Ollama is shared with
// another team, so a wait well past those usually means it is queued, not dead.
import { useEffect, useState } from "react";

export type WaitKind = "chat" | "review";

const CHAT_CLOCK_FROM_S = 15;
const CHAT_STILL_WORKING_S = 90;
const REVIEW_STILL_WORKING_S = 180;
const STILL_WORKING_WHY =
  "The model server is shared with another team, so a long wait usually means it's busy, not broken.";

/** m:ss, never negative. */
export function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function waitMessage(kind: WaitKind, seconds: number, charCount = 0): string {
  const clock = formatElapsed(seconds);
  const longWait = kind === "chat" ? CHAT_STILL_WORKING_S : REVIEW_STILL_WORKING_S;
  if (seconds >= longWait) return `Still working — ${clock}. ${STILL_WORKING_WHY}`;
  if (kind === "review") {
    return `Reviewing ${charCount.toLocaleString("en-US")} characters… ${clock} — reviews usually take about 1½ minutes.`;
  }
  return seconds < CHAT_CLOCK_FROM_S ? "Thinking…" : `Thinking… ${clock}`;
}

/** Whole seconds since `active` last became true; 0 while inactive. */
export function useElapsedSeconds(active: boolean): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    setSeconds(0);
    if (!active) return;
    const started = Date.now();
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return seconds;
}
