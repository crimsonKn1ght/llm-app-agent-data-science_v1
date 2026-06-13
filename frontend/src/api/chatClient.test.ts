import { afterEach, describe, expect, it, vi } from "vitest";
import { streamChat } from "./chatClient";
import type { ChatStreamEvent } from "../types/chat";

function streamFromText(text: string): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(text));
      controller.close();
    }
  });
}

describe("streamChat", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("emits progress, text, and final response events", async () => {
    const events: ChatStreamEvent[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          streamFromText(
            [
              '{"type":"progress","message":"Analyzing","origin":"system"}',
              '{"type":"text","text":"Draft","origin":"insights"}',
              '{"type":"final_response","content":"Final"}'
            ].join("\n") + "\n"
          ),
          { status: 200 }
        )
      )
    );

    const result = await streamChat(
      { user_query: "Explain AI", conversation_id: "abc", web_search: false },
      { onEvent: (event) => events.push(event) }
    );

    expect(result.sawFinalResponse).toBe(true);
    expect(result.streamedText).toBe("Draft");
    expect(events).toEqual([
      { type: "progress", message: "Analyzing", origin: "system" },
      { type: "text", text: "Draft", origin: "insights" },
      { type: "final_response", content: "Final" }
    ]);
  });

  it("emits backend error events without ending the stream", async () => {
    const events: ChatStreamEvent[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          streamFromText(
            '{"type":"error","message":"Partial failure"}\n{"type":"final_response","content":"Recovered"}\n'
          ),
          { status: 200 }
        )
      )
    );

    const result = await streamChat(
      { user_query: "Explain AI" },
      { onEvent: (event) => events.push(event) }
    );

    expect(result.sawFinalResponse).toBe(true);
    expect(events).toEqual([
      { type: "error", message: "Partial failure" },
      { type: "final_response", content: "Recovered" }
    ]);
  });

  it("reports streams that end without final response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(streamFromText('{"type":"text","text":"Only draft"}\n'), {
          status: 200
        })
      )
    );

    const result = await streamChat(
      { user_query: "Explain AI" },
      { onEvent: vi.fn() }
    );

    expect(result.sawFinalResponse).toBe(false);
    expect(result.streamedText).toBe("Only draft");
  });

  it("sends the web_search flag from the request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(streamFromText('{"type":"final_response","content":"Done"}\n'), {
        status: 200
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    await streamChat(
      { user_query: "Latest AI news", conversation_id: "abc", web_search: true },
      { onEvent: vi.fn() }
    );

    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body).toEqual({
      user_query: "Latest AI news",
      conversation_id: "abc",
      web_search: true
    });
  });

  it("supports abort controller cancellation", async () => {
    const controller = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async () => {
        controller.abort();
        throw new DOMException("Aborted", "AbortError");
      })
    );

    await expect(
      streamChat({ user_query: "Explain AI" }, { onEvent: vi.fn() }, controller.signal)
    ).rejects.toThrow("Aborted");
  });
});
