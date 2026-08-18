// Ephemeral-document detection. Run with: npx tsx src/docIdentity.test.ts
//
// A document id minted into Office settings only becomes durable once the FILE
// is saved. Close an unsaved draft and the id dies with it, so that contract's
// review history, chat turns and feedback rows are orphaned — permanently
// unjoinable from any later work on the same contract.
//
// This does NOT fix that. It makes it visible, which means the detection has
// one rule that matters: warn on a POSITIVE "no file location", stay silent
// whenever the probe cannot tell. `Office.context.document.url` is documented
// as "null if the URL is unavailable", which is not exclusively the unsaved
// case — so a guess could fire on every document in some surface, and a banner
// that is always on is wallpaper. Banner blindness would cost us the real
// warning, so the unknown case says nothing.

declare const process: { exitCode?: number };

import { isDocumentUnsaved, probeFileUrl, resolveDocumentId, shouldWarnUnsaved } from "./docIdentity";
import { pass } from "./testAssert";

// --- the decision, as a pure table ------------------------------------------

pass(shouldWarnUnsaved("/Users/d/contracts/nda.docx", false) === false,
  "saved: local path -> no warning");
pass(shouldWarnUnsaved("https://trinetix.sharepoint.com/sites/x/nda.docx", false) === false,
  "saved: SharePoint url -> no warning");
pass(shouldWarnUnsaved("", false) === true,
  "unsaved: empty url -> warn");
pass(shouldWarnUnsaved(null, false) === true,
  "unsaved: null url -> warn");
pass(shouldWarnUnsaved(undefined, false) === true,
  "unsaved: undefined url -> warn");
pass(shouldWarnUnsaved("   ", false) === true,
  "unsaved: whitespace-only url -> warn");

// The unknown case is silent regardless of what came back with it.
pass(shouldWarnUnsaved(null, true) === false,
  "probe failed -> silent, never a guess");
pass(shouldWarnUnsaved("", true) === false,
  "probe failed alongside an empty url -> still silent");
pass(shouldWarnUnsaved("/Users/d/nda.docx", true) === false,
  "probe failed alongside a url -> still silent");

// --- the probe --------------------------------------------------------------

type Cb = (res: unknown) => void;
const setDocument = (document: unknown): void => {
  (globalThis as { Office?: unknown }).Office =
    document === undefined ? undefined : { context: { document } };
};

async function main(): Promise<void> {
  setDocument({ getFilePropertiesAsync: (cb: Cb) => cb({ value: { url: "/Users/d/nda.docx" } }) });
  pass((await probeFileUrl()).url === "/Users/d/nda.docx",
    "probe: reads the url out of getFilePropertiesAsync");

  // An unsaved document reports an empty url — a SUCCESSFUL probe with a real
  // answer, which is the whole signal. It must not be confused with a failure.
  setDocument({ getFilePropertiesAsync: (cb: Cb) => cb({ value: { url: "" } }) });
  const empty = await probeFileUrl();
  pass(empty.url === "" && empty.failed === false,
    "probe: an empty url is an answer, not a failure");

  // A failed AsyncResult carries no `value`. Checking for a usable url string
  // rather than an enum keeps this independent of Office's enum being present.
  setDocument({ getFilePropertiesAsync: (cb: Cb) => cb({ status: "failed" }) });
  pass((await probeFileUrl()).failed === true,
    "probe: a result with no value counts as failed");

  setDocument({ getFilePropertiesAsync: () => { throw new Error("office blew up"); } });
  pass((await probeFileUrl()).failed === true,
    "probe: a throwing getFilePropertiesAsync counts as failed");

  setDocument({ url: "/Users/d/legacy.docx" });
  pass((await probeFileUrl()).url === "/Users/d/legacy.docx",
    "probe: falls back to document.url when getFilePropertiesAsync is absent");

  setDocument(undefined);
  pass((await probeFileUrl()).failed === true,
    "probe: no Office at all counts as failed");

  // A callback that never fires would otherwise hang the await and freeze the
  // notice on its initial state for the rest of the session.
  setDocument({ getFilePropertiesAsync: () => { /* never calls back */ } });
  pass((await probeFileUrl(10)).failed === true,
    "probe: a callback that never fires times out instead of hanging");

  // --- end to end -----------------------------------------------------------

  setDocument({ getFilePropertiesAsync: (cb: Cb) => cb({ value: { url: "" } }) });
  pass((await isDocumentUnsaved()) === true, "e2e: unsaved document warns");

  setDocument({ getFilePropertiesAsync: (cb: Cb) => cb({ value: { url: "/Users/d/nda.docx" } }) });
  pass((await isDocumentUnsaved()) === false, "e2e: saved document stays quiet");

  setDocument(undefined);
  pass((await isDocumentUnsaved()) === false, "e2e: an unusable probe stays quiet");

  // --- minting is deliberately untouched by this branch ----------------------
  //
  // The fix for the orphaning itself is still open; this branch only observes.
  // If these two ever change, the id semantics changed and the wiki row is stale.

  const store = new Map<string, string>();
  setDocument({
    settings: {
      get: (k: string) => store.get(k),
      set: (k: string, v: string) => { store.set(k, v); },
      saveAsync: (cb: () => void) => cb(),
    },
  });
  const minted = await resolveDocumentId();
  pass(typeof minted === "string" && minted.length > 0, "mint: creates an id on first use");
  pass((await resolveDocumentId()) === minted, "mint: reuses the stored id afterwards");

  setDocument({ settings: { get: () => { throw new Error("no settings"); } } });
  pass((await resolveDocumentId()) === "", "mint: returns '' on failure so the backend hash takes over");
}

main().catch((e) => {
  process.exitCode = 1;
  console.log(`FAIL: unexpected throw — ${e}`);
});
