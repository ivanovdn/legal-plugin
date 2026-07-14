// Stable per-document id for the backend `document_id`, stored INSIDE the .docx
// via Office.context.document.settings so it travels with the file (local,
// SharePoint, OneDrive) and is immune to content edits/redlines — unlike the
// server-side preamble hash, which drifts when the review workflow fills fields
// in the document's opening block. Validated to survive a task-pane reopen.
// Returns "" on any failure; the backend then falls back to the preamble hash.

const SETTINGS_KEY = "legalTriageDocId";

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
