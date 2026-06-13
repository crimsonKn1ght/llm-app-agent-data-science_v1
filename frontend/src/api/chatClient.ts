import { NdjsonStreamParser } from "./ndjson";
import type {
  ChatRequest,
  ChatStreamEvent,
  ErrorEvent,
  FinalResponseEvent,
  ProgressEvent,
  TextEvent
} from "../types/chat";

const DEFAULT_API_BASE_URL = "";

export type StreamChatCallbacks = {
  onEvent: (event: ChatStreamEvent) => void;
  onMalformedLine?: (line: string) => void;
};

export type StreamChatResult = {
  sawFinalResponse: boolean;
  streamedText: string;
};

export class ChatClientError extends Error {
  status?: number;
  details?: unknown;

  constructor(message: string, status?: number, details?: unknown) {
    super(message);
    this.name = "ChatClientError";
    this.status = status;
    this.details = details;
  }
}

export async function streamChat(
  request: ChatRequest,
  callbacks: StreamChatCallbacks,
  signal?: AbortSignal
): Promise<StreamChatResult> {
  const response = await fetch(buildApiUrl("/api/chat/generate"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/x-ndjson"
    },
    body: JSON.stringify(request),
    signal
  });

  if (!response.ok) {
    throw await buildResponseError(response);
  }

  if (!response.body) {
    throw new ChatClientError("The server did not provide a response stream.");
  }

  const parser = new NdjsonStreamParser<unknown>();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let sawFinalResponse = false;
  let streamedText = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }

      const chunk = decoder.decode(value, { stream: true });
      const parsed = parser.push(chunk);
      for (const line of parsed.malformedLines) {
        callbacks.onMalformedLine?.(line);
      }
      for (const rawEvent of parsed.events) {
        const event = coerceStreamEvent(rawEvent);
        if (!event) {
          callbacks.onMalformedLine?.(JSON.stringify(rawEvent));
          continue;
        }
        if (event.type === "text") {
          streamedText += event.text;
        }
        if (event.type === "final_response") {
          sawFinalResponse = true;
        }
        callbacks.onEvent(event);
      }
    }

    const tail = decoder.decode();
    const flushed = parser.push(tail);
    const finalFlush = parser.flush();
    for (const line of [...flushed.malformedLines, ...finalFlush.malformedLines]) {
      callbacks.onMalformedLine?.(line);
    }
    for (const rawEvent of [...flushed.events, ...finalFlush.events]) {
      const event = coerceStreamEvent(rawEvent);
      if (!event) {
        callbacks.onMalformedLine?.(JSON.stringify(rawEvent));
        continue;
      }
      if (event.type === "text") {
        streamedText += event.text;
      }
      if (event.type === "final_response") {
        sawFinalResponse = true;
      }
      callbacks.onEvent(event);
    }
  } finally {
    reader.releaseLock();
  }

  return { sawFinalResponse, streamedText };
}

export function buildApiUrl(path: string): string {
  const baseUrl =
    import.meta.env.VITE_API_BASE_URL?.trim() || DEFAULT_API_BASE_URL;
  if (!baseUrl) {
    return path;
  }
  return `${baseUrl.replace(/\/$/, "")}${path}`;
}

function coerceStreamEvent(rawEvent: unknown): ChatStreamEvent | null {
  if (!rawEvent || typeof rawEvent !== "object") {
    return null;
  }

  const event = rawEvent as Partial<ChatStreamEvent>;
  switch (event.type) {
    case "progress":
      if (typeof (event as ProgressEvent).message === "string") {
        return {
          type: "progress",
          message: (event as ProgressEvent).message,
          origin:
            typeof (event as ProgressEvent).origin === "string"
              ? (event as ProgressEvent).origin
              : "system"
        };
      }
      return null;
    case "text":
      if (typeof (event as TextEvent).text === "string") {
        return {
          type: "text",
          text: (event as TextEvent).text,
          origin:
            typeof (event as TextEvent).origin === "string"
              ? (event as TextEvent).origin
              : undefined
        };
      }
      return null;
    case "error":
      if (typeof (event as ErrorEvent).message === "string") {
        return {
          type: "error",
          message: (event as ErrorEvent).message
        };
      }
      return null;
    case "final_response":
      if (typeof (event as FinalResponseEvent).content === "string") {
        return {
          type: "final_response",
          content: (event as FinalResponseEvent).content
        };
      }
      return null;
    default:
      return null;
  }
}

async function buildResponseError(response: Response): Promise<ChatClientError> {
  const fallback = `Request failed with status ${response.status}.`;
  let details: unknown;
  let message = fallback;

  try {
    const text = await response.text();
    details = text;
    if (text) {
      const parsed = JSON.parse(text) as unknown;
      details = parsed;
      message = extractErrorMessage(parsed) || fallback;
    }
  } catch {
    message = fallback;
  }

  return new ChatClientError(message, response.status, details);
}

function extractErrorMessage(value: unknown): string {
  if (!value || typeof value !== "object") {
    return "";
  }

  const payload = value as { detail?: unknown; reason?: unknown; message?: unknown };
  if (typeof payload.reason === "string") {
    return payload.reason;
  }
  if (typeof payload.message === "string") {
    return payload.message;
  }
  if (typeof payload.detail === "string") {
    return payload.detail;
  }
  if (Array.isArray(payload.detail) && payload.detail.length > 0) {
    const first = payload.detail[0] as { msg?: unknown };
    if (typeof first?.msg === "string") {
      return first.msg;
    }
  }
  return "";
}
