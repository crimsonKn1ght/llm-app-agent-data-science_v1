import { Search } from "lucide-react";

type WebSearchToggleProps = {
  enabled: boolean;
  onChange: (enabled: boolean) => void;
  disabled?: boolean;
};

export function WebSearchToggle({
  enabled,
  onChange,
  disabled = false
}: WebSearchToggleProps) {
  return (
    <button
      type="button"
      className={`web-toggle ${enabled ? "web-toggle-on" : ""}`}
      role="switch"
      aria-checked={enabled}
      disabled={disabled}
      title="Web search"
      onClick={() => onChange(!enabled)}
    >
      <Search size={16} />
      <span>Web search</span>
    </button>
  );
}
