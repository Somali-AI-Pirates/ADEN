export type Citation = {
  source_id: string;
  book_id: string;
  file_name: string;
  page: number;
};

export type QueryResponse = {
  request_id: string;
  status: "answered" | "needs_clarification" | "abstained" | "error";
  answer: string;
  reason: string;
  citations: Citation[];
};

export type ConversationTurn = { question: string; answer: string };

export type StreamEvent =
  | { type: "start"; request_id: string }
  | { type: "progress"; request_id: string; stage: string; message: string }
  | { type: "answer_delta"; request_id: string; delta: string }
  | { type: "final"; request_id: string; response: QueryResponse; trace_url?: string }
  | { type: "error"; request_id: string; message: string };

const record = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);
const requestId = (v: unknown): v is string =>
  typeof v === "string" && /^[a-f0-9]{32}$/.test(v);
const bookId = (v: unknown): v is string =>
  typeof v === "string" && /^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/.test(v);

export class ChatError extends Error {
  constructor(message: string, readonly requestId?: string) { super(message); }
}

export function parseResponse(value: unknown): QueryResponse {
  if (!record(value) || !requestId(value.request_id) ||
      !["answered", "needs_clarification", "abstained", "error"].includes(String(value.status)) ||
      typeof value.answer !== "string" || typeof value.reason !== "string" ||
      !Array.isArray(value.citations) ||
      !value.citations.every(c => record(c) && typeof c.source_id === "string" &&
        bookId(c.book_id) && typeof c.file_name === "string" &&
        Number.isInteger(c.page) && Number(c.page) >= 1)) {
    throw new ChatError("서버 응답 형식을 확인하지 못했습니다.");
  }
  return value as QueryResponse;
}

function traceUrl(value: unknown): string | undefined {
  if (typeof value !== "string") return;
  try {
    const url = new URL(value);
    if ((url.protocol === "http:" || url.protocol === "https:") &&
        !url.username && !url.password && !url.search && !url.hash &&
        /\/project\/[A-Za-z0-9_-]+\/traces\/[a-f0-9]{32}$/.test(url.pathname)) {
      return url.href;
    }
  } catch {
    // Optional trace metadata should not prevent a completed answer.
  }
}

function parseEvent(data: string): StreamEvent {
  let value: unknown;
  try { value = JSON.parse(data); }
  catch { throw new ChatError("스트림 데이터를 읽지 못했습니다."); }
  if (!record(value) || !requestId(value.request_id)) {
    throw new ChatError("요청 식별 정보를 확인하지 못했습니다.");
  }
  const id = value.request_id;
  switch (value.type) {
    case "start": return { type: "start", request_id: id };
    case "progress":
      if (typeof value.stage === "string" && typeof value.message === "string")
        return { type: "progress", request_id: id, stage: value.stage, message: value.message };
      break;
    case "answer_delta":
      if (typeof value.delta === "string")
        return { type: "answer_delta", request_id: id, delta: value.delta };
      break;
    case "final": {
      const response = parseResponse(value.response);
      if (response.request_id !== id) throw new ChatError("응답의 요청 ID가 다릅니다.", id);
      const trace_url = traceUrl(value.trace_url);
      return { type: "final", request_id: id, response, ...(trace_url ? { trace_url } : {}) };
    }
    case "error":
      if (typeof value.message === "string")
        return { type: "error", request_id: id, message: value.message };
      break;
  }
  throw new ChatError("지원하지 않는 스트림 응답입니다.", id);
}

/** Decode SSE across arbitrary UTF-8/network chunk boundaries. */
export async function* readEvents(body: ReadableStream<Uint8Array>, id: string): AsyncGenerator<StreamEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let data: string[] = [];
  let started = false;
  try {
    while (true) {
      const chunk = await reader.read();
      buffer += decoder.decode(chunk.value, { stream: !chunk.done });
      if (buffer.length > 1_000_000) throw new ChatError("스트림 응답이 너무 큽니다.", id);
      let newline: number;
      while ((newline = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, newline).replace(/\r$/, "");
        buffer = buffer.slice(newline + 1);
        if (line === "" && data.length) {
          const event = parseEvent(data.join("\n"));
          data = [];
          if (event.request_id !== id) throw new ChatError("응답의 요청 ID가 다릅니다.", id);
          if (!started && event.type !== "start") throw new ChatError("스트림 시작 정보가 없습니다.", id);
          if (started && event.type === "start") throw new ChatError("스트림이 중복 시작되었습니다.", id);
          started = true;
          yield event;
          if (event.type === "final" || event.type === "error") return;
        } else if (line.startsWith("data:")) {
          data.push(line.slice(5).replace(/^ /, ""));
        }
      }
      if (chunk.done) throw new ChatError("답변 완료 전에 연결이 끊겼습니다. 다시 시도해 주세요.", id);
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export async function* streamQuestion(
  question: string,
  signal: AbortSignal,
  history: ConversationTurn[] = [],
): AsyncGenerator<StreamEvent> {
  const controller = new AbortController();
  const cancel = () => controller.abort(signal.reason);
  signal.addEventListener("abort", cancel, { once: true });
  if (signal.aborted) cancel();
  const timeout = setTimeout(() => controller.abort(new Error("timeout")), 300_000);
  let id: string | undefined;
  try {
    const response = await fetch("/api/query/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(history.length ? { question, history } : { question }),
      signal: controller.signal,
      cache: "no-store",
    });
    id = response.headers.get("X-Request-ID") || undefined;
    if (!response.ok) {
      let message = "질문을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.";
      try {
        const error: unknown = await response.json();
        if (record(error) && typeof error.reason === "string") message = error.reason;
      } catch { /* A proxy error may be HTML. Do not render it. */ }
      throw new ChatError(message, id);
    }
    if (!requestId(id) || !response.body ||
        !response.headers.get("content-type")?.startsWith("text/event-stream")) {
      throw new ChatError("스트리밍 응답을 확인하지 못했습니다.", id);
    }
    yield* readEvents(response.body, id);
  } catch (error) {
    if (signal.aborted) throw error;
    if (controller.signal.aborted) throw new ChatError("답변 대기 시간이 초과되었습니다.", id);
    if (error instanceof ChatError) throw error;
    throw new ChatError("서버 연결을 확인해 주세요.", id);
  } finally {
    clearTimeout(timeout);
    signal.removeEventListener("abort", cancel);
  }
}

export function documentUrl(citation: Citation): string {
  if (!bookId(citation.book_id)) throw new ChatError("문서 ID가 올바르지 않습니다.");
  return `/api/documents/${encodeURIComponent(citation.book_id)}`;
}
