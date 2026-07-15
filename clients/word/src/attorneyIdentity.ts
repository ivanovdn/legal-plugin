// Per-install attorney identity. Stored in localStorage (per add-in origin, so
// it is the SAME id across all of this attorney's documents — unlike
// document.settings, which is per-document). Confirmed to survive the Word-for-Mac
// task-pane teardown. Sent as the X-User-ID header; O365 SSO (slice 3) will
// overwrite this value at the same seam.
const KEY = "legalTriageAttorneyId";

export function resolveAttorneyId(): string {
  try {
    const existing = localStorage.getItem(KEY);
    if (existing) return existing;
    const id =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `atty-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    localStorage.setItem(KEY, id);
    return id;
  } catch {
    return "word-addin"; // fail-safe: never break a request over identity
  }
}
