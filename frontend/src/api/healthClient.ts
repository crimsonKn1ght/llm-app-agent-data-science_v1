import { buildApiUrl, ChatClientError } from "./chatClient";
import type { RuntimeStatus } from "../types/chat";

export async function fetchRuntimeStatus(): Promise<RuntimeStatus> {
  const response = await fetch(buildApiUrl("/api/ready"));
  const payload = (await response.json()) as RuntimeStatus;

  if (!response.ok && payload.status !== "not_ready") {
    throw new ChatClientError("Could not read backend readiness.", response.status, payload);
  }

  return payload;
}
