import assert from "node:assert/strict";
import { test } from "node:test";
import { readEvents, parseResponse, documentUrl } from "../lib/api";
import { chatAdapter } from "../lib/chat-adapter";

const id = "a".repeat(32);
const response = { request_id: id, status: "answered", answer: "예약 확인 😀", reason: "근거", citations: [{ source_id: "S1", book_id: "AS001", file_name: "AS001.pdf", page: 2 }] };
const sse = (event: object) => `data: ${JSON.stringify(event)}\r\n\r\n`;
function body(text: string, size = 1) {
  const bytes = new TextEncoder().encode(text);
  return new ReadableStream<Uint8Array>({ start(controller) {
    for (let i = 0; i < bytes.length; i += size) controller.enqueue(bytes.slice(i, i + size));
    controller.close();
  } });
}
const start = { type: "start", request_id: id };

test("UTF-8 and SSE boundaries preserve Korean, emoji, heartbeat and authoritative citations", async () => {
  const data = ": keep-alive\r\n\r\n" + sse(start) + sse({ type: "answer_delta", request_id: id, delta: "예약 😀" }) + sse({ type: "final", request_id: id, response });
  const events = [];
  for await (const event of readEvents(body(data), id)) events.push(event);
  assert.equal(events.length, 3);
  assert.deepEqual(events[2], { type: "final", request_id: id, response });
  assert.equal(documentUrl(response.citations[0]), "/api/documents/AS001");
});

test("optional trace links preserve valid targets and discard unsafe ones", async () => {
  const traceUrl = "https://traces.example.test/project/team/traces/" + "b".repeat(32);
  for (const supplied of [traceUrl, "javascript:alert(1)", traceUrl + "?token=private"]) {
    const data = sse(start) + sse({ type: "final", request_id: id, response, trace_url: supplied });
    const events = [];
    for await (const event of readEvents(body(data), id)) events.push(event);
    assert.deepEqual(events.at(-1), {
      type: "final", request_id: id, response,
      ...(supplied === traceUrl ? { trace_url: traceUrl } : {}),
    });
  }
});

test("EOF, mismatched identity and invalid document sources do not become success", async () => {
  for (const data of [
    sse(start) + sse({ type: "answer_delta", request_id: id, delta: "임시" }),
    sse({ type: "start", request_id: "b".repeat(32) }),
    sse(start) + sse({ type: "final", request_id: id, response: { ...response, request_id: "b".repeat(32) } }),
  ]) {
    await assert.rejects(async () => { for await (const _ of readEvents(body(data), id)) { /* consume */ } });
  }
  assert.throws(() => parseResponse({ ...response, citations: [{ ...response.citations[0], book_id: "../secret" }] }));
  assert.throws(() => parseResponse({ ...response, citations: [{ ...response.citations[0], page: 0 }] }));
});

test("adapter skips unfinished turns and keeps the authoritative final response", async () => {
  const original = globalThis.fetch;
  const posted: unknown[] = [];
  globalThis.fetch = async (_url, init) => {
    posted.push(JSON.parse(String(init?.body)));
    return new Response(body(sse(start) + sse({ type: "answer_delta", request_id: id, delta: "임시" }) + sse({ type: "final", request_id: id, response })), { headers: { "content-type": "text/event-stream", "X-Request-ID": id } });
  };
  try {
    const args = {
      messages: [
        { role: "user", content: [{ type: "text", text: "과거 질문" }] },
        {
          role: "assistant",
          content: [{ type: "text", text: "저장 전 임시 답변" }],
          metadata: { custom: {} },
          status: { type: "incomplete", reason: "error" },
        },
        { role: "user", content: [{ type: "text", text: "예약 확인" }] },
      ],
      abortSignal: new AbortController().signal,
    } as unknown as Parameters<typeof chatAdapter.run>[0];
    const result = chatAdapter.run(args);
    assert.ok(Symbol.asyncIterator in result);
    const snapshots = [];
    for await (const update of result) snapshots.push(update);
    assert.deepEqual(posted, [{ question: "예약 확인" }]);
    assert.deepEqual(snapshots.at(-1)?.metadata?.custom?.response, response);
    assert.equal(snapshots.at(-1)?.status?.type, "complete");
  } finally { globalThis.fetch = original; }
});

test("storage error after partial output leaves an incomplete answer without citations", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(body(sse(start) + sse({ type: "answer_delta", request_id: id, delta: "임시 답변" }) + sse({ type: "error", request_id: id, message: "저장 실패" })), { headers: { "content-type": "text/event-stream", "X-Request-ID": id } });
  try {
    const args = { messages: [{ role: "user", content: [{ type: "text", text: "질문" }] }], abortSignal: new AbortController().signal } as unknown as Parameters<typeof chatAdapter.run>[0];
    const result = chatAdapter.run(args);
    assert.ok(Symbol.asyncIterator in result);
    const snapshots = [];
    for await (const update of result) snapshots.push(update);
    assert.equal(snapshots.at(-1)?.status?.type, "incomplete");
    assert.equal(snapshots.at(-1)?.metadata?.custom?.response, undefined);
    assert.equal(snapshots.at(-1)?.metadata?.custom?.error, "저장 실패");
  } finally { globalThis.fetch = original; }
});
