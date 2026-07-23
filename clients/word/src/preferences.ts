// Preferences API client — reads/writes the attorney's USER.md via the backend.
// Keyed by the same X-User-ID attorney identity the query client sends.
import { resolveAttorneyId } from "./attorneyIdentity";

export async function getPreferences(): Promise<string> {
  const res = await fetch("/api/preferences", {
    headers: { "X-User-ID": resolveAttorneyId() },
  });
  if (!res.ok) throw new Error(`Backend returned ${res.status} ${res.statusText}`);
  const json = await res.json();
  return (json?.data?.markdown as string) ?? "";
}

export async function savePreferences(markdown: string): Promise<void> {
  const res = await fetch("/api/preferences", {
    method: "PUT",
    headers: { "Content-Type": "application/json", "X-User-ID": resolveAttorneyId() },
    body: JSON.stringify({ markdown }),
  });
  if (!res.ok) throw new Error(`Backend returned ${res.status} ${res.statusText}`);
}

/** Append one preference line to the stored USER.md (GET → append → PUT). */
export async function appendPreference(line: string): Promise<void> {
  const current = (await getPreferences()).replace(/\s+$/, "");
  const next = (current ? current + "\n" : "") + `- ${line}`;
  await savePreferences(next);
}
