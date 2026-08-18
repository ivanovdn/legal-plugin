// Stable per-document id for the backend `document_id`, stored INSIDE the .docx
// via Office.context.document.settings so it travels with the file (local,
// SharePoint, OneDrive) and is immune to content edits/redlines — unlike the
// server-side preamble hash, which drifts when the review workflow fills fields
// in the document's opening block. Validated to survive a task-pane reopen.
// Returns "" on any failure; the backend then falls back to the preamble hash.

const SETTINGS_KEY = "legalTriageDocId";

/** Result of asking Office where this document lives on disk. */
export type FileUrlProbe = { url: string | null; failed: boolean };

interface FilePropsResult {
  value?: { url?: unknown };
}
interface DocumentLike {
  getFilePropertiesAsync?: (cb: (res: FilePropsResult) => void) => void;
  url?: unknown;
}

/** Ask Office for the document's file location.
 *
 * `failed` means we could not find out — NOT that the document is unsaved.
 * The two are deliberately distinct: an unsaved document answers successfully
 * with an empty url, whereas a failure carries no `value` at all. Keying on
 * "did we get a usable url string" rather than on `AsyncResultStatus` keeps
 * this working even where Office's enum object isn't present.
 *
 * Times out rather than awaiting a callback that may never fire — a hung
 * promise here would leave the pane's notice stuck on its initial state.
 */
export async function probeFileUrl(timeoutMs = 3000): Promise<FileUrlProbe> {
  const FAILED: FileUrlProbe = { url: null, failed: true };
  try {
    const doc = (Office as unknown as { context?: { document?: DocumentLike } })
      ?.context?.document;
    if (!doc) return FAILED;

    const probe = doc.getFilePropertiesAsync;
    if (typeof probe === "function") {
      return await new Promise<FileUrlProbe>((resolve) => {
        const timer = setTimeout(() => resolve(FAILED), timeoutMs);
        const settle = (r: FileUrlProbe) => {
          clearTimeout(timer);
          resolve(r);
        };
        try {
          probe.call(doc, (res) => {
            const url = res && res.value ? res.value.url : undefined;
            settle(typeof url === "string" ? { url, failed: false } : FAILED);
          });
        } catch {
          settle(FAILED);
        }
      });
    }

    // Older/simpler surfaces expose only the plain property.
    return typeof doc.url === "string" ? { url: doc.url, failed: false } : FAILED;
  } catch {
    return FAILED;
  }
}

/** Should the pane warn that this document's memory is ephemeral?
 *
 * Warns only on a POSITIVE "no file location". An unusable probe says nothing:
 * `document.url` is documented as null when the URL is merely *unavailable*,
 * which is not exclusively the unsaved case, so guessing could light the notice
 * on every document in some surface — and a notice that is always on is
 * wallpaper. Being wrong here means failing to warn, which is today's
 * behaviour; the alternative fails toward crying wolf.
 */
export function shouldWarnUnsaved(
  url: string | null | undefined,
  probeFailed: boolean,
): boolean {
  if (probeFailed) return false;
  return !(typeof url === "string" && url.trim() !== "");
}

/** True when this document has no file on disk, so nothing keyed to its id
 * (review history, chat, feedback) survives closing it. False when it is saved
 * OR when we could not tell. */
export async function isDocumentUnsaved(): Promise<boolean> {
  const { url, failed } = await probeFileUrl();
  return shouldWarnUnsaved(url, failed);
}

/** Read the document's stable id, creating + persisting one on first use. "" on failure. */
export async function resolveDocumentId(): Promise<string> {
  try {
    const settings = Office.context.document.settings;
    const existing = settings.get(SETTINGS_KEY);
    if (typeof existing === "string" && existing) return existing;
    const id =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `doc-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    settings.set(SETTINGS_KEY, id);
    await new Promise<void>((resolve) => {
      try {
        settings.saveAsync(() => resolve());
      } catch {
        resolve();
      }
    });
    return id;
  } catch {
    return "";
  }
}
