import { useState } from "react";
import { appendPreference } from "../preferences";

interface Props {
  text: string;
  onAdded?: () => void;
}

export default function PreferenceSuggestionCard({ text, onAdded }: Props) {
  const [state, setState] = useState<"idle" | "saving" | "added" | "error">("idle");

  const add = async () => {
    setState("saving");
    try {
      await appendPreference(text);
      setState("added");
      onAdded?.();
    } catch {
      setState("error");
    }
  };

  return (
    <div className="preference-suggestion">
      <span className="preference-icon">💡</span>
      <span className="preference-text">Remember this preference? “{text}”</span>
      {state === "added" ? (
        <span className="status">Added ✓</span>
      ) : (
        <button className="secondary" onClick={add} disabled={state === "saving"}>
          {state === "saving" ? "Adding…" : "Add"}
        </button>
      )}
      {state === "error" && <span className="status error">Couldn't save</span>}
    </div>
  );
}
