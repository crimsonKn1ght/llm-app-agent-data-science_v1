import { MessageSquareText } from "lucide-react";

export function EmptyChat() {
  return (
    <div className="empty-chat">
      <MessageSquareText size={34} aria-hidden="true" />
      <h2>Ready for your next question.</h2>
    </div>
  );
}
