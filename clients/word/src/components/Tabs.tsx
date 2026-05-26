export type TabKey = "findings" | "chat";

interface Props {
  active: TabKey;
  onChange: (key: TabKey) => void;
}

const TABS: { key: TabKey; label: string }[] = [
  { key: "findings", label: "Findings" },
  { key: "chat", label: "Chat" },
];

export default function Tabs({ active, onChange }: Props) {
  return (
    <div className="tabs" role="tablist">
      {TABS.map((t) => (
        <button
          key={t.key}
          role="tab"
          aria-selected={active === t.key}
          className={`tab ${active === t.key ? "active" : ""}`}
          onClick={() => onChange(t.key)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
