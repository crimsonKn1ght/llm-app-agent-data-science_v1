import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarkdownMessage, splitMarkdownSections } from "./MarkdownMessage";

describe("MarkdownMessage", () => {
  it("splits notices and sources into styled sections", () => {
    const sections = splitMarkdownSections(
      "## Summary\n\nDone\n\n## Notices\n\n- Partial issue\n\n## Sources\n\n1. [Example](https://example.com)"
    );

    expect(sections.map((section) => section.kind)).toEqual([
      "default",
      "notices",
      "sources"
    ]);
  });

  it("renders external links safely", () => {
    render(
      <MarkdownMessage content="## Sources\n\n1. [Example](https://example.com)" />
    );

    const link = screen.getByRole("link", { name: "Example" });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("renders notices and sources from final markdown", () => {
    render(
      <MarkdownMessage
        content={
          "## Notices\n\n- Summary generation failed.\n\n## Sources\n\n1. [Source](https://example.com)"
        }
      />
    );

    expect(screen.getByText("Notices")).toBeInTheDocument();
    expect(screen.getByText("Summary generation failed.")).toBeInTheDocument();
    expect(screen.getByText("Sources")).toBeInTheDocument();
  });
});
