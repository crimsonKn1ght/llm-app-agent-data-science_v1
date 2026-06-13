import { AlertTriangle, CheckCircle2, CircleStop, Loader2 } from "lucide-react";
import type { ChatMessage } from "../types/chat";
import { MarkdownMessage } from "./MarkdownMessage";
import { ProgressRail } from "./ProgressRail";

type ChatMessageBubbleProps = {
  message: ChatMessage;
};

export function ChatMessageBubble({ message }: ChatMessageBubbleProps) {
  const isAssistant = message.role === "assistant";
  const displayContent =
    message.finalResponse || message.streamedText || message.content;

  return (
    <article className={`message-row message-row-${message.role}`}>
      <div className={`message-bubble message-bubble-${message.role}`}>
        <div className="message-meta">
          <span>{isAssistant ? "Assistant" : "You"}</span>
          <MessageStatus status={message.status} />
        </div>

        {isAssistant ? (
          <>
            {message.progress.length > 0 && (
              <ProgressRail
                progress={message.progress}
                isStreaming={message.status === "streaming"}
              />
            )}
            {displayContent ? (
              <MarkdownMessage content={displayContent} />
            ) : (
              <div className="assistant-waiting">Working...</div>
            )}
          </>
        ) : (
          <p className="user-message-text">{message.content}</p>
        )}

        {message.errorMessage && (
          <div className="message-warning" role="status">
            <AlertTriangle size={16} />
            <span>{message.errorMessage}</span>
          </div>
        )}
      </div>
    </article>
  );
}

function MessageStatus({ status }: { status: ChatMessage["status"] }) {
  if (status === "streaming" || status === "pending") {
    return (
      <span className="message-status">
        <Loader2 size={14} className="spin" />
        Streaming
      </span>
    );
  }
  if (status === "aborted") {
    return (
      <span className="message-status">
        <CircleStop size={14} />
        Stopped
      </span>
    );
  }
  if (status === "error") {
    return (
      <span className="message-status status-error">
        <AlertTriangle size={14} />
        Error
      </span>
    );
  }
  return (
    <span className="message-status">
      <CheckCircle2 size={14} />
      Complete
    </span>
  );
}
