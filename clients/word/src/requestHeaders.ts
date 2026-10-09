// Headers every backend call sends: who is asking (attorneyIdentity.ts) and
// what is asking — the Word host and the pane build — so each turn_log row can
// say which Word and which bundle sent it (spec 2026-10-09). Never a key and
// never a reason to fail a request: an unreadable value is sent empty.
import { userHeaders } from "./attorneyIdentity";

const HEADER_CAP = 64;

/** fetch throws a TypeError on a header value outside Latin-1, and a control
 *  character is never part of a platform or a version — keep printable ASCII. */
function headerSafe(value: string): string {
  return value.replace(/[^\x20-\x7E]/g, "").slice(0, HEADER_CAP);
}

/** The commit this bundle was built from; "dev" outside a Vite build. */
export function paneBuild(): string {
  return typeof __PANE_BUILD__ === "string" ? __PANE_BUILD__ : "dev";
}

/** Office's own diagnostics (Mac / PC / OfficeOnline and the Word build) and the pane build. */
export function clientHeaders(): Record<string, string> {
  let platform = "";
  let version = "";
  try {
    const diagnostics = Office.context.diagnostics;
    platform = String(diagnostics.platform || "");
    version = String(diagnostics.version || "");
  } catch {
    /* no Office host (tests, the eval runner) or no diagnostics yet — send empty */
  }
  return {
    "X-Office-Platform": headerSafe(platform),
    "X-Office-Version": headerSafe(version),
    "X-Pane-Build": headerSafe(paneBuild()),
  };
}

/** Everything a backend call sends besides Content-Type. */
export function requestHeaders(): Record<string, string> {
  return { ...userHeaders(), ...clientHeaders() };
}
