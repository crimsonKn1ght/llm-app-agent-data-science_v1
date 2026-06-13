import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type MarkdownMessageProps = {
  content: string;
};

type MarkdownSection = {
  id: string;
  kind: "default" | "notices" | "sources";
  content: string;
};

export function MarkdownMessage({ content }: MarkdownMessageProps) {
  const sections = splitMarkdownSections(content);

  return (
    <div className="markdown-message">
      {sections.map((section) => (
        <section
          className={`markdown-section markdown-section-${section.kind}`}
          key={section.id}
        >
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              a: ({ href, children }) => (
                <a
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {children}
                </a>
              )
            }}
          >
            {section.content}
          </ReactMarkdown>
        </section>
      ))}
    </div>
  );
}

export function splitMarkdownSections(content: string): MarkdownSection[] {
  const lines = content.split(/\r?\n/);
  const sections: MarkdownSection[] = [];
  let current: string[] = [];
  let sectionIndex = 0;

  const pushCurrent = () => {
    const sectionContent = current.join("\n").trim();
    if (!sectionContent) {
      current = [];
      return;
    }

    sections.push({
      id: `section-${sectionIndex}`,
      kind: sectionKind(sectionContent),
      content: sectionContent
    });
    sectionIndex += 1;
    current = [];
  };

  for (const line of lines) {
    if (line.startsWith("## ") && current.length > 0) {
      pushCurrent();
    }
    current.push(line);
  }

  pushCurrent();
  return sections;
}

function sectionKind(content: string): MarkdownSection["kind"] {
  const firstLine = content.split(/\r?\n/, 1)[0]?.toLowerCase() ?? "";
  if (firstLine === "## notices") {
    return "notices";
  }
  if (firstLine === "## sources") {
    return "sources";
  }
  return "default";
}
