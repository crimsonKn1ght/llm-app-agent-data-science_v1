import { Bot, Plus, RefreshCw } from "lucide-react";

export type ReadinessState =
  | { status: "checking" }
  | { status: "ready" }
  | { status: "not_ready"; reason: string }
  | { status: "unreachable"; reason: string };

type ChatHeaderProps = {
  readiness: ReadinessState;
  onRefreshReadiness: () => void;
  onNewChat: () => void;
};

export function ChatHeader({
  readiness,
  onRefreshReadiness,
  onNewChat
}: ChatHeaderProps) {
  const statusLabel = getReadinessLabel(readiness);

  return (
    <header className="chat-header">
      <div className="brand-mark" aria-hidden="true">
        <Bot size={20} />
      </div>
      <div className="brand-copy">
        <h1>RAG Chatbot</h1>
        <span title={statusLabel} className={`status-pill status-${readiness.status}`}>
          {statusLabel}
        </span>
      </div>
      <div className="header-actions">
        <button
          className="icon-button"
          type="button"
          title="Refresh readiness"
          aria-label="Refresh readiness"
          onClick={onRefreshReadiness}
        >
          <RefreshCw size={18} />
        </button>
        <button
          className="text-button"
          type="button"
          title="New chat"
          onClick={onNewChat}
        >
          <Plus size={18} />
          <span>New chat</span>
        </button>
      </div>
    </header>
  );
}

function getReadinessLabel(readiness: ReadinessState): string {
  switch (readiness.status) {
    case "ready":
      return "Ready";
    case "not_ready":
      return "Not ready";
    case "unreachable":
      return "Offline";
    case "checking":
      return "Checking";
    default:
      return "Checking";
  }
}
