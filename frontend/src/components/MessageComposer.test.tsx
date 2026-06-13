import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MAX_USER_QUERY_CHARS } from "../types/chat";
import { MessageComposer } from "./MessageComposer";

function renderComposer(overrides = {}) {
  const props = {
    isStreaming: false,
    webSearchEnabled: false,
    onWebSearchChange: vi.fn(),
    onSubmit: vi.fn().mockResolvedValue({ ok: true }),
    onStop: vi.fn(),
    ...overrides
  };

  render(<MessageComposer {...props} />);
  return props;
}

describe("MessageComposer", () => {
  it("prevents empty input from being submitted", async () => {
    const props = renderComposer();

    expect(screen.getByRole("button", { name: /send/i })).toBeDisabled();
    fireEvent.submit(screen.getByRole("textbox").closest("form") as HTMLFormElement);

    expect(props.onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a question");
  });

  it("trims whitespace before submitting", async () => {
    const user = userEvent.setup();
    const props = renderComposer();

    await user.type(screen.getByRole("textbox"), "  Explain AI  ");
    await user.click(screen.getByRole("button", { name: /send/i }));

    expect(props.onSubmit).toHaveBeenCalledWith("Explain AI");
  });

  it("blocks input above the backend character limit", () => {
    const props = renderComposer();

    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "x".repeat(MAX_USER_QUERY_CHARS + 1) }
    });

    expect(screen.getByRole("button", { name: /send/i })).toBeDisabled();
    fireEvent.submit(screen.getByRole("textbox").closest("form") as HTMLFormElement);
    expect(props.onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("under 8000 characters");
  });

  it("toggles the web search request flag", async () => {
    const user = userEvent.setup();
    const props = renderComposer();
    const toggle = screen.getByRole("switch", { name: /web search/i });

    expect(toggle).toHaveAttribute("aria-checked", "false");
    await user.click(toggle);

    expect(props.onWebSearchChange).toHaveBeenCalledWith(true);
  });
});
