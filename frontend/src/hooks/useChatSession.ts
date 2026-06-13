import { useCallback, useEffect, useState } from "react";
import {
  SESSION_STORAGE_KEY,
  type ChatMessage,
  type ChatSession
} from "../types/chat";

export function createClientId(prefix = "id"): string {
  const randomId =
    globalThis.crypto?.randomUUID?.() ??
    `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${randomId}`;
}

export function createUserMessage(content: string): ChatMessage {
  return {
    id: createClientId("user"),
    role: "user",
    content,
    status: "complete",
    createdAt: new Date().toISOString(),
    progress: [],
    streamedText: ""
  };
}

export function createAssistantMessage(): ChatMessage {
  return {
    id: createClientId("assistant"),
    role: "assistant",
    content: "",
    status: "streaming",
    createdAt: new Date().toISOString(),
    progress: [],
    streamedText: ""
  };
}

export function createEmptySession(): ChatSession {
  return {
    conversationId: createClientId("conversation"),
    messages: [],
    webSearchEnabled: false
  };
}

export function useChatSession() {
  const [session, setSession] = useState<ChatSession>(() => loadSession());

  useEffect(() => {
    localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session));
  }, [session]);

  const setWebSearchEnabled = useCallback((enabled: boolean) => {
    setSession((current) => ({ ...current, webSearchEnabled: enabled }));
  }, []);

  const appendMessages = useCallback((messages: ChatMessage[]) => {
    setSession((current) => ({
      ...current,
      messages: [...current.messages, ...messages]
    }));
  }, []);

  const updateMessage = useCallback(
    (messageId: string, updater: (message: ChatMessage) => ChatMessage) => {
      setSession((current) => ({
        ...current,
        messages: current.messages.map((message) =>
          message.id === messageId ? updater(message) : message
        )
      }));
    },
    []
  );

  const newChat = useCallback(() => {
    setSession(createEmptySession());
  }, []);

  return {
    session,
    setWebSearchEnabled,
    appendMessages,
    updateMessage,
    newChat
  };
}

function loadSession(): ChatSession {
  try {
    const raw = localStorage.getItem(SESSION_STORAGE_KEY);
    if (!raw) {
      return createEmptySession();
    }

    const parsed = JSON.parse(raw) as Partial<ChatSession>;
    if (
      typeof parsed.conversationId !== "string" ||
      !Array.isArray(parsed.messages)
    ) {
      return createEmptySession();
    }

    return {
      conversationId: parsed.conversationId,
      messages: parsed.messages,
      webSearchEnabled: Boolean(parsed.webSearchEnabled)
    };
  } catch {
    return createEmptySession();
  }
}
