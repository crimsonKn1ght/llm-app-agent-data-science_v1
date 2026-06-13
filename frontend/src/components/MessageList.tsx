import { useEffect, useRef } from "react";
import type { ChatMessage } from "../types/chat";
import { ChatMessageBubble } from "./ChatMessageBubble";
import { EmptyChat } from "./EmptyChat";

type MessageListProps = {
  messages: ChatMessage[];
  isStreaming: boolean;
};

export function MessageList({ messages, isStreaming }: MessageListProps) {
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (typeof endRef.current?.scrollIntoView === "function") {
      endRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [messages]);

  if (messages.length === 0) {
    return <EmptyChat />;
  }

  return (
    <div
      className="message-list"
      role="log"
      aria-live={isStreaming ? "polite" : "off"}
      aria-relevant="additions text"
    >
      {messages.map((message) => (
        <ChatMessageBubble key={message.id} message={message} />
      ))}
      <div ref={endRef} />
    </div>
  );
}
