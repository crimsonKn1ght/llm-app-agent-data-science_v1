export const MAX_USER_QUERY_CHARS = 8000;
export const SESSION_STORAGE_KEY = "rag-chatbot.session.v1";

export type ChatRequest = {
  user_query: string;
  conversation_id?: string;
  web_search?: boolean;
};

export type ProgressEvent = {
  type: "progress";
  message: string;
  origin: string;
};

export type TextEvent = {
  type: "text";
  text: string;
  origin?: string;
};

export type ErrorEvent = {
  type: "error";
  message: string;
};

export type FinalResponseEvent = {
  type: "final_response";
  content: string;
};

export type ChatStreamEvent =
  | ProgressEvent
  | TextEvent
  | ErrorEvent
  | FinalResponseEvent;

export type ChatMessageStatus =
  | "pending"
  | "streaming"
  | "complete"
  | "error"
  | "aborted";

export type ProgressItem = {
  id: string;
  message: string;
  origin: string;
  createdAt: string;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  status: ChatMessageStatus;
  createdAt: string;
  progress: ProgressItem[];
  streamedText: string;
  finalResponse?: string;
  errorMessage?: string;
};

export type ChatSession = {
  conversationId: string;
  messages: ChatMessage[];
  webSearchEnabled: boolean;
};

export type RuntimeStatus =
  | {
      status: "ready" | "ok";
      reason?: string;
    }
  | {
      status: "not_ready";
      reason: string;
    };
