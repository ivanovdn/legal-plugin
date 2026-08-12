// Read-or-create attorney id. Run with: npx tsx src/attorneyIdentity.test.ts
import { resolveAttorneyId, resolveAttorneyName, setAttorneyName, userHeaders } from "./attorneyIdentity";
import { pass } from "./testAssert";

// in-memory localStorage mock
class MemStore {
  private m = new Map<string, string>();
  getItem(k: string) { return this.m.has(k) ? this.m.get(k)! : null; }
  setItem(k: string, v: string) { this.m.set(k, v); }
}
(globalThis as { localStorage?: unknown }).localStorage = new MemStore();

const first = resolveAttorneyId();
pass(typeof first === "string" && first.length > 0, "mints a non-empty id");

const second = resolveAttorneyId();
pass(first === second, "reuses the stored id on subsequent calls");

// throwing localStorage -> safe fallback
(globalThis as { localStorage?: unknown }).localStorage = {
  getItem() { throw new Error("blocked"); },
  setItem() { throw new Error("blocked"); },
};
pass(resolveAttorneyId() === "word-addin", "falls back to word-addin when localStorage throws");

// name accessors + userHeaders
(globalThis as { localStorage?: unknown }).localStorage = new MemStore();
pass(resolveAttorneyName() === "", "name is empty by default");
setAttorneyName("Dmytro Ivanov");
pass(resolveAttorneyName() === "Dmytro Ivanov", "name round-trips through storage");
const h = userHeaders();
pass(typeof h["X-User-ID"] === "string" && h["X-User-ID"].length > 0, "userHeaders has X-User-ID");
pass(h["X-User-Name"] === "Dmytro Ivanov", "userHeaders has X-User-Name");
