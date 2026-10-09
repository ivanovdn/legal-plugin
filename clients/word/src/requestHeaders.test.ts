// X-Office-Platform / X-Office-Version / X-Pane-Build. Run with: npx tsx src/requestHeaders.test.ts
import { clientHeaders, paneBuild, requestHeaders } from "./requestHeaders";
import { pass } from "./testAssert";

type Globals = { Office?: unknown; localStorage?: unknown };
const g = globalThis as Globals;

// tsx applies no Vite `define`, so this is the fallback a real build never sees.
pass(paneBuild() === "dev", "paneBuild() is dev outside a Vite build");

// No Office host at all (unit tests, the eval runner): empty, never a throw.
delete g.Office;
const bare = clientHeaders();
pass(bare["X-Office-Platform"] === "" && bare["X-Office-Version"] === "",
  "no Office global -> empty platform and version");
pass(bare["X-Pane-Build"] === "dev", "X-Pane-Build always carries paneBuild()");

// Word for Mac.
g.Office = { context: { diagnostics: { platform: "Mac", version: "16.89" } } };
const mac = clientHeaders();
pass(mac["X-Office-Platform"] === "Mac" && mac["X-Office-Version"] === "16.89",
  "reads platform and version from Office.context.diagnostics");

// Review Focus #2: fetch throws on a header value outside Latin-1, which would
// fail the attorney's whole request over telemetry.
g.Office = { context: { diagnostics: { platform: "Mac", version: "16.89éж\n" } } };
pass(clientHeaders()["X-Office-Version"] === "16.89",
  "non-ASCII and control characters are stripped before they reach fetch");

// A host whose diagnostics throw.
g.Office = { context: { get diagnostics(): never { throw new Error("not ready"); } } };
const broken = clientHeaders();
pass(broken["X-Office-Platform"] === "" && broken["X-Office-Version"] === "",
  "diagnostics throwing -> empty, never a throw");

// requestHeaders = identity + client facts.
class MemStore {
  private m = new Map<string, string>();
  getItem(k: string) { return this.m.has(k) ? this.m.get(k)! : null; }
  setItem(k: string, v: string) { this.m.set(k, v); }
}
g.localStorage = new MemStore();
g.Office = { context: { diagnostics: { platform: "PC", version: "16.0.18129.20116" } } };
const all = requestHeaders();
pass(typeof all["X-User-ID"] === "string" && all["X-User-ID"].length > 0,
  "requestHeaders keeps the identity headers");
pass(all["X-Office-Platform"] === "PC" && all["X-Pane-Build"] === "dev",
  "requestHeaders adds the client headers");
