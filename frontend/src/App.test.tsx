import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { streamChat } from "./api/chatClient";
import { fetchRuntimeStatus } from "./api/healthClient";
import type { StreamChatCallbacks } from "./api/chatClient";
import type { ChatRequest } from "./types/chat";

vi.mock("./api/chatClient", () => ({
  ChatClientError: class ChatClientError extends Error {},
  buildApiUrl: (path: string) => path,
  streamChat: vi.fn()
}));

vi.mock("./api/healthClient", () => ({
  fetchRuntimeStatus: vi.fn()
}));

describe("App", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    vi.mocked(fetchRuntimeStatus).mockResolvedValue({ status: "ready" });
  });

  it("sends web_search=false by default and replaces draft text with final response", async () => {
    const user = userEvent.setup();
    let capturedRequest: ChatRequest | undefined;
    let releaseFinalResponse: () => void = () => {};
    const finalResponseGate = new Promise<void>((resolve) => {
      releaseFinalResponse = resolve;
    });

    vi.mocked(streamChat).mockImplementation(
      async (request: ChatRequest, callbacks: StreamChatCallbacks) => {
        capturedRequest = request;
        callbacks.onEvent({ type: "text", text: "Draft answer" });
        await finalResponseGate;
        await Promise.resolve();
        callbacks.onEvent({
          type: "final_response",
          content: "## Summary\n\nFinal answer"
        });
        return { sawFinalResponse: true, streamedText: "Draft answer" };
      }
    );

    render(<App />);

    await screen.findByText("Ready");
    await user.type(screen.getByRole("textbox", { name: /message/i }), "Explain AI");
    await user.click(screen.getByRole("button", { name: /send/i }));

    expect(await screen.findByText("Draft answer")).toBeInTheDocument();
    releaseFinalResponse();
    await waitFor(() => expect(screen.getByText("Final answer")).toBeInTheDocument());
    expect(screen.queryByText("Draft answer")).not.toBeInTheDocument();
    expect(capturedRequest?.web_search).toBe(false);
    expect(capturedRequest?.conversation_id).toMatch(/^conversation-/);
  });

  it("sends web_search=true when the toggle is enabled", async () => {
    const user = userEvent.setup();
    let capturedRequest: ChatRequest | undefined;

    vi.mocked(streamChat).mockImplementation(
      async (request: ChatRequest, callbacks: StreamChatCallbacks) => {
        capturedRequest = request;
        callbacks.onEvent({
          type: "final_response",
          content: "## Summary\n\nDone"
        });
        return { sawFinalResponse: true, streamedText: "" };
      }
    );

    render(<App />);

    await screen.findByText("Ready");
    await user.click(screen.getByRole("switch", { name: /web search/i }));
    await user.type(screen.getByRole("textbox", { name: /message/i }), "Latest AI news");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => expect(streamChat).toHaveBeenCalled());
    expect(capturedRequest?.web_search).toBe(true);
  });
});
