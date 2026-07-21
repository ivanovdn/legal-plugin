import { useEffect, useState } from "react";
import { getPreferences, savePreferences } from "../preferences";

interface Props {
  markdown: string;
  setMarkdown: React.Dispatch<React.SetStateAction<string>>;
  loaded: boolean;
  setLoaded: React.Dispatch<React.SetStateAction<boolean>>;
}

export default function PreferencesTab({ markdown, setMarkdown, loaded, setLoaded }: Props) {
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Fetch once (and again whenever `loaded` is reset to false — e.g. after the
  // Chat tab appends a suggested preference, so the cabinet reflects it).
  useEffect(() => {
    if (loaded) return;
    (async () => {
      try {
        setMarkdown(await getPreferences());
        setLoaded(true);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, [loaded, setMarkdown, setLoaded]);

  const save = async () => {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      await savePreferences(markdown);
      setStatus("Saved.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="tab-content preferences">
      <p className="subtitle">
        Your standing preferences (USER.md). The assistant reads these on every review and chat —
        they shape emphasis but never override the firm playbook.
      </p>
      <textarea
        className="preferences-editor"
        rows={16}
        value={markdown}
        onChange={(e) => setMarkdown(e.target.value)}
        placeholder={"# My preferences\n- Always flag uncapped indemnity as Red.\n- Governing-law fallback is Delaware."}
        disabled={busy}
      />
      <div className="preferences-actions">
        <button className="primary" onClick={save} disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </button>
        {status && <span className="status">{status}</span>}
        {error && <span className="status error">Error: {error}</span>}
      </div>
    </div>
  );
}
