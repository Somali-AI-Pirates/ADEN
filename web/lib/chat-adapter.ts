import type { ChatModelAdapter, ChatModelRunResult } from "@assistant-ui/react";
import { ChatError, streamQuestion } from "./api";

export const chatAdapter: ChatModelAdapter = {
  async *run({ messages, abortSignal }) {
    // The service currently handles independent questions, not conversation memory.
    const latest = [...messages].reverse().find(message => message.role === "user");
    const question = latest?.content.filter(part => part.type === "text").map(part => part.text).join("\n").trim();
    if (!question) throw new Error("질문을 입력해 주세요.");
    let text = "";
    let custom: Record<string, unknown> = {};
    const snapshot = (): ChatModelRunResult => ({
      content: [{ type: "text", text }],
      metadata: { custom: { ...custom } },
    });
    try {
      for await (const event of streamQuestion(question, abortSignal)) {
        custom.requestId = event.request_id;
        if (event.type === "progress") custom.progress = event.message;
        if (event.type === "answer_delta") text += event.delta;
        if (event.type === "error") throw new ChatError(event.message, event.request_id);
        if (event.type === "final") {
          text = event.response.answer;
          custom = {
            requestId: event.request_id,
            response: event.response,
            traceUrl: event.trace_url,
          };
          yield { ...snapshot(), status: { type: "complete", reason: "stop" } };
          return;
        }
        yield snapshot();
      }
    } catch (error) {
      if (abortSignal.aborted) throw error;
      const message = error instanceof Error ? error.message : "질문을 처리하지 못했습니다.";
      custom.error = message;
      if (error instanceof ChatError && error.requestId) custom.requestId = error.requestId;
      yield { ...snapshot(), status: { type: "incomplete", reason: "error", error: message } };
    }
  },
};
