import { Activity } from "lucide-react";
import type { ProgressItem } from "../types/chat";

type ProgressRailProps = {
  progress: ProgressItem[];
  isStreaming: boolean;
};

export function ProgressRail({ progress, isStreaming }: ProgressRailProps) {
  const visibleItems = progress.slice(-5);

  return (
    <div className="progress-rail" aria-label="Progress updates">
      <div className="progress-rail-header">
        <Activity size={15} />
        <span>{isStreaming ? "Running" : "Progress"}</span>
      </div>
      <ol>
        {visibleItems.map((item) => (
          <li key={item.id}>
            <span className="progress-origin">{formatOrigin(item.origin)}</span>
            <span>{item.message}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function formatOrigin(origin: string): string {
  return origin.replace(/_/g, " ");
}
