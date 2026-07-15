// Read-or-create attorney id. Run with: npx tsx src/attorneyIdentity.test.ts
import { resolveAttorneyId } from "./attorneyIdentity";

const pass = (cond: boolean, label: string) =>
  console.log(cond ? `PASS: ${label}` : `FAIL: ${label}`);

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
