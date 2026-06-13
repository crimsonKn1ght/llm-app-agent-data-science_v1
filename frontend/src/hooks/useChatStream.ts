import { useCallback, useRef, useState } from "react";
import { ChatClientError, streamChat } from "../api/chatClient";
import {
  MAX_USER_QUERY_CHARS,
  type ChatMessage,
  type ChatStreamEvent
} from "../types/chat";
import {
  createAssistantMessage,
  createClientId,
  createUserMessage
} from "./useChatSession";

type UseChatStreamOptions = {
  conversationId: string;
  webSearchEnabled: boolean;
  appendMessages: (messages: ChatMessage[]) => void;
  updateMessage: (
    messageId: string,
    updater: (message: ChatMessage) => ChatMessage
  ) => void;
};

export type SubmitResult =
  | { ok: true }
  | { ok: false; reason: string };

export function useChatStream({
  conversationId,
  webSearchEnabled,
  appendMessages,
  updateMessage
}: UseChatStreamOptions) {
  const [isStreaming, setIsStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const submit = useCallback(
    async (rawQuery: string): Promise<SubmitResult> => {
      const userQuery = rawQuery.trim();
      if (!userQuery) {
        return { ok: false, reason: "Enter a question before sending." };
      }
      if (userQuery.length > MAX_USER_QUERY_CHARS) {
        return {
          ok: false,
          reason: `Keep the request under ${MAX_USER_QUERY_CHARS} characters.`
        };
      }
      if (isStreaming) {
        return { ok: false, reason: "Wait for the current answer to finish." };
      }

      const userMessage = createUserMessage(userQuery);
      const assistantMessage = createAssistantMessage();
      appendMessages([userMessage, assistantMessage]);
      setIsStreaming(true);

      const abortController = new AbortController();
      abortRef.current = abortController;

      try {
        const result = await streamChat(
          {
            user_query: userQuery,
            conversation_id: conversationId,
            web_search: webSearchEnabled
          },
          {
            onEvent: (event) =>
              applyStreamEvent(assistantMessage.id, event, updateMessage),
            onMalformedLine: () => {
              updateMessage(assistantMessage.id, (message) => ({
                ...message,
                errorMessage:
                  message.errorMessage ??
                  "Some streamed data could not be parsed."
              }));
            }
          },
          abortController.signal
        );

        updateMessage(assistantMessage.id, (message) => {
          if (!result.sawFinalResponse) {
            return {
              ...message,
              status: "complete",
              content: message.streamedText,
              errorMessage:
                message.errorMessage ??
                "The stream ended before a final answer arrived."
            };
          }
          return { ...message, status: "complete" };
        });
      } catch (error) {
        updateMessage(assistantMessage.id, (message) => {
          if (isAbortError(error)) {
            return {
              ...message,
              status: "aborted",
              content: message.finalResponse ?? message.streamedText,
              errorMessage: "Response stopped."
            };
          }

          return {
            ...message,
            status: "error",
            errorMessage: errorToMessage(error)
          };
        });
      } finally {
        abortRef.current = null;
        setIsStreaming(false);
      }

      return { ok: true };
    },
    [
      appendMessages,
      conversationId,
      isStreaming,
      updateMessage,
      webSearchEnabled
    ]
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return {
    isStreaming,
    submit,
    stop
  };
}

function applyStreamEvent(
  assistantMessageId: string,
  event: ChatStreamEvent,
  updateMessage: (
    messageId: string,
    updater: (message: ChatMessage) => ChatMessage
  ) => void
): void {
  updateMessage(assistantMessageId, (message) => {
    switch (event.type) {
      case "progress":
        return {
          ...message,
          status: "streaming",
          progress: [
            ...message.progress,
            {
              id: createClientId("progress"),
              message: event.message,
              origin: event.origin,
              createdAt: new Date().toISOString()
            }
          ]
        };
      case "text": {
        const streamedText = `${message.streamedText}${event.text}`;
        return {
          ...message,
          status: "streaming",
          streamedText,
          content: message.finalResponse ?? streamedText
        };
      }
      case "error":
        return {
          ...message,
          status: "streaming",
          errorMessage: event.message
        };
      case "final_response":
        return {
          ...message,
          status: "complete",
          finalResponse: event.content,
          content: event.content
        };
      default:
        return message;
    }
  });
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function errorToMessage(error: unknown): string {
  if (error instanceof ChatClientError) {
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "The request could not be completed.";
}
