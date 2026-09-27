import type { ConversationDetail, ConversationSummary, QuestionResponse } from "./types";

export class ApiError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    if (typeof body.detail === "string" && body.detail) {
      return body.detail;
    }
  } catch {
    // The body is not JSON.
  }
  return `Request failed (${response.status})`;
}

export async function listConversations(signal?: AbortSignal): Promise<ConversationSummary[]> {
  const response = await fetch("/api/conversations", { signal });
  if (!response.ok) {
    throw new ApiError(await readError(response));
  }
  return (await response.json()) as ConversationSummary[];
}

export async function getConversation(
  conversationId: string,
  signal?: AbortSignal,
): Promise<ConversationDetail> {
  const response = await fetch(`/api/conversations/${conversationId}`, { signal });
  if (!response.ok) {
    throw new ApiError(await readError(response));
  }
  return (await response.json()) as ConversationDetail;
}

type SseEvent = {
  event: string;
  data: string;
};

function parseSseBlock(block: string): SseEvent | null {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      data.push(line.slice(5).trim());
    }
  }
  if (data.length === 0) {
    return null;
  }
  return { event, data: data.join("\n") };
}

export async function streamQuestion(
  question: string,
  conversationId: string | null,
  onStatus: (status: string) => void,
  signal?: AbortSignal,
): Promise<QuestionResponse> {
  const response = await fetch("/api/questions/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({
      question,
      conversation_id: conversationId,
    }),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new ApiError(response.ok ? "The stream did not start." : await readError(response));
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: QuestionResponse | null = null;

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      const parsed = parseSseBlock(block.trim());
      if (!parsed) {
        continue;
      }
      if (parsed.event === "status") {
        const payload = JSON.parse(parsed.data) as { status?: string };
        if (payload.status) {
          onStatus(payload.status);
        }
      } else if (parsed.event === "result") {
        result = JSON.parse(parsed.data) as QuestionResponse;
      }
    }
  }

  if (!result) {
    throw new ApiError("The stream ended without an answer.");
  }
  return result;
}
