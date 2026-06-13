import type { ReactNode } from "react";

type AppShellProps = {
  header: ReactNode;
  messages: ReactNode;
  composer: ReactNode;
};

export function AppShell({ header, messages, composer }: AppShellProps) {
  return (
    <div className="app-shell">
      {header}
      <main className="chat-main">
        <div className="chat-main-inner">{messages}</div>
      </main>
      {composer}
    </div>
  );
}
