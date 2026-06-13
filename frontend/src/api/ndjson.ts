export type NdjsonParseResult<T> = {
  events: T[];
  malformedLines: string[];
};

export class NdjsonStreamParser<T> {
  private buffer = "";

  push(chunk: string): NdjsonParseResult<T> {
    this.buffer += chunk;
    const lines = this.buffer.split("\n");
    this.buffer = lines.pop() ?? "";
    return parseCompleteLines<T>(lines);
  }

  flush(): NdjsonParseResult<T> {
    const pending = this.buffer;
    this.buffer = "";
    if (!pending.trim()) {
      return { events: [], malformedLines: [] };
    }
    return parseCompleteLines<T>(pending.split("\n"));
  }
}

function parseCompleteLines<T>(lines: string[]): NdjsonParseResult<T> {
  const events: T[] = [];
  const malformedLines: string[] = [];

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      continue;
    }

    try {
      events.push(JSON.parse(trimmed) as T);
    } catch {
      malformedLines.push(line);
    }
  }

  return { events, malformedLines };
}
