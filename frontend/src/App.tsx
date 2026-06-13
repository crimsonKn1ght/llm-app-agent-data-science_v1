import { useCallback, useEffect, useState } from "react";
import { fetchRuntimeStatus } from "./api/healthClient";
import { AppShell } from "./components/AppShell";
import { ChatHeader, type ReadinessState } from "./components/ChatHeader";
import { MessageComposer } from "./components/MessageComposer";
import { MessageList } from "./components/MessageList";
import { useChatSession } from "./hooks/useChatSession";
import { useChatStream } from "./hooks/useChatStream";

export default function App() {
  const {
    session,
    appendMessages,
    updateMessage,
    setWebSearchEnabled,
    newChat
  } = useChatSession();
  const stream = useChatStream({
    conversationId: session.conversationId,
    webSearchEnabled: session.webSearchEnabled,
    appendMessages,
    updateMessage
  });
  const [readiness, setReadiness] = useState<ReadinessState>({
    status: "checking"
  });

  const refreshReadiness = useCallback(async () => {
    setReadiness({ status: "checking" });
    try {
      const status = await fetchRuntimeStatus();
      if (status.status === "not_ready") {
        setReadiness({
          status: "not_ready",
          reason: status.reason
        });
      } else {
        setReadiness({ status: "ready" });
      }
    } catch (error) {
      setReadiness({
        status: "unreachable",
        reason:
          error instanceof Error
            ? error.message
            : "The backend readiness endpoint could not be reached."
      });
    }
  }, []);

  useEffect(() => {
    void refreshReadiness();
  }, [refreshReadiness]);

  const handleNewChat = () => {
    if (stream.isStreaming) {
      stream.stop();
    }
    newChat();
  };

  return (
    <AppShell
      header={
        <ChatHeader
          readiness={readiness}
          onRefreshReadiness={refreshReadiness}
          onNewChat={handleNewChat}
        />
      }
      messages={
        <MessageList
          messages={session.messages}
          isStreaming={stream.isStreaming}
        />
      }
      composer={
        <MessageComposer
          isStreaming={stream.isStreaming}
          webSearchEnabled={session.webSearchEnabled}
          onWebSearchChange={setWebSearchEnabled}
          onSubmit={stream.submit}
          onStop={stream.stop}
        />
      }
    />
  );
}
