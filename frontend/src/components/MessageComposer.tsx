import { FormEvent, KeyboardEvent, useMemo, useState } from "react";
import { Send, Square } from "lucide-react";
import { MAX_USER_QUERY_CHARS } from "../types/chat";
import type { SubmitResult } from "../hooks/useChatStream";
import { WebSearchToggle } from "./WebSearchToggle";

type MessageComposerProps = {
  isStreaming: boolean;
  webSearchEnabled: boolean;
  onWebSearchChange: (enabled: boolean) => void;
  onSubmit: (query: string) => Promise<SubmitResult>;
  onStop: () => void;
};

export function MessageComposer({
  isStreaming,
  webSearchEnabled,
  onWebSearchChange,
  onSubmit,
  onStop
}: MessageComposerProps) {
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const trimmedQuery = query.trim();
  const isOverLimit = trimmedQuery.length > MAX_USER_QUERY_CHARS;
  const canSubmit = Boolean(trimmedQuery) && !isOverLimit && !isStreaming;

  const counterLabel = useMemo(
    () => `${trimmedQuery.length}/${MAX_USER_QUERY_CHARS}`,
    [trimmedQuery.length]
  );

  const handleSubmit = async (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault();
    setError("");

    if (!trimmedQuery) {
      setError("Enter a question before sending.");
      return;
    }
    if (isOverLimit) {
      setError(`Keep the request under ${MAX_USER_QUERY_CHARS} characters.`);
      return;
    }

    const result = await onSubmit(trimmedQuery);
    if (result.ok) {
      setQuery("");
      return;
    }
    setError(result.reason);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSubmit();
    }
  };

  return (
    <footer className="composer-shell">
      <form className="composer" onSubmit={handleSubmit}>
        <textarea
          aria-label="Message"
          value={query}
          rows={3}
          placeholder="Ask a question..."
          onChange={(event) => {
            setQuery(event.target.value);
            setError("");
          }}
          onKeyDown={handleKeyDown}
        />
        <div className="composer-footer">
          <div className="composer-controls">
            <WebSearchToggle
              enabled={webSearchEnabled}
              onChange={onWebSearchChange}
              disabled={isStreaming}
            />
            <span className={`char-counter ${isOverLimit ? "char-counter-error" : ""}`}>
              {counterLabel}
            </span>
          </div>
          <div className="composer-actions">
            {isStreaming ? (
              <button
                className="text-button stop-button"
                type="button"
                title="Stop response"
                onClick={onStop}
              >
                <Square size={17} />
                <span>Stop</span>
              </button>
            ) : (
              <button
                className="text-button send-button"
                type="submit"
                title="Send message"
                disabled={!canSubmit}
              >
                <Send size={17} />
                <span>Send</span>
              </button>
            )}
          </div>
        </div>
        {error && (
          <div className="composer-error" role="alert">
            {error}
          </div>
        )}
      </form>
    </footer>
  );
}
