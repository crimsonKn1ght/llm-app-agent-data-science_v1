import { describe, expect, it } from "vitest";
import { NdjsonStreamParser } from "./ndjson";

describe("NdjsonStreamParser", () => {
  it("parses one event per chunk", () => {
    const parser = new NdjsonStreamParser<{ type: string }>();

    const parsed = parser.push('{"type":"progress"}\n');

    expect(parsed.events).toEqual([{ type: "progress" }]);
    expect(parsed.malformedLines).toEqual([]);
  });

  it("parses multiple events per chunk", () => {
    const parser = new NdjsonStreamParser<{ type: string }>();

    const parsed = parser.push('{"type":"progress"}\n{"type":"text"}\n');

    expect(parsed.events).toEqual([{ type: "progress" }, { type: "text" }]);
  });

  it("keeps split events buffered across chunks", () => {
    const parser = new NdjsonStreamParser<{ type: string; text: string }>();

    expect(parser.push('{"type":"text"').events).toEqual([]);
    const parsed = parser.push(',"text":"hello"}\n');

    expect(parsed.events).toEqual([{ type: "text", text: "hello" }]);
  });

  it("ignores blank lines", () => {
    const parser = new NdjsonStreamParser<{ type: string }>();

    const parsed = parser.push('\n{"type":"progress"}\n\n');

    expect(parsed.events).toEqual([{ type: "progress" }]);
    expect(parsed.malformedLines).toEqual([]);
  });

  it("records malformed lines", () => {
    const parser = new NdjsonStreamParser<{ type: string }>();

    const parsed = parser.push('{bad json}\n{"type":"progress"}\n');

    expect(parsed.events).toEqual([{ type: "progress" }]);
    expect(parsed.malformedLines).toEqual(["{bad json}"]);
  });
});
