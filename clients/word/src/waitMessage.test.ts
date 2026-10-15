// What the pane says while a turn is out. Run with: npx tsx src/waitMessage.test.ts
import { formatElapsed, waitMessage } from "./waitMessage";
import { pass } from "./testAssert";

pass(formatElapsed(0) === "0:00", "0 s reads 0:00");
pass(formatElapsed(9) === "0:09", "seconds are zero-padded");
pass(formatElapsed(102) === "1:42", "minutes and seconds");
pass(formatElapsed(-3) === "0:00", "a negative clock reads 0:00");

// Chat: most turns answer in 2–25 s, so no clock for the first 15 s.
pass(waitMessage("chat", 0) === "Thinking…", "chat starts without a clock");
pass(waitMessage("chat", 14) === "Thinking…", "chat shows no clock before 15 s");
pass(waitMessage("chat", 23) === "Thinking… 0:23", "chat shows the clock from 15 s");
pass(
  waitMessage("chat", 102) ===
    "Still working — 1:42. The model server is shared with another team, so a long wait usually means it's busy, not broken.",
  "chat past 90 s says it is still working and why",
);

// Review: 78–95 s measured, so the expectation is stated up front.
pass(
  waitMessage("review", 42, 12905) ===
    "Reviewing 12,905 characters… 0:42 — reviews usually take about 1½ minutes.",
  "review shows size, clock and the usual duration",
);
pass(
  waitMessage("review", 179, 12905).startsWith("Reviewing 12,905 characters… 2:59"),
  "review keeps the normal line up to 3 minutes",
);
pass(
  waitMessage("review", 185, 12905) ===
    "Still working — 3:05. The model server is shared with another team, so a long wait usually means it's busy, not broken.",
  "review past 3 minutes says it is still working and why",
);
