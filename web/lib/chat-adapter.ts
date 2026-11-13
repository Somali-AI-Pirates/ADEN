import type { ChatModelAdapter, ChatModelRunResult, ThreadMessage } from "@assistant-ui/react";
import { ChatError, streamQuestion } from "./api";
import type { ConversationTurn, QueryResponse } from "./api";

const maxHistoryTurns = 8;
const textOf = (message: ThreadMessage) =>
  message.content.filter(part => part.type === "text").map(part => part.text).join("\n").trim();
const prefix = (text: string, limit: number) => Array.from(text).slice(0, limit).join("");

function completedTurns(messages: readonly ThreadMessage[]): ConversationTurn[] {
  const turns: ConversationTurn[] = [];
  let question = "";
  for (const message of messages) {
    if (message.role === "user") {
      question = textOf(message);
    } else if (message.role === "assistant" && question) {
      const response = message.metadata.custom.response as QueryResponse | undefined;
      if (response && response.status !== "error" && response.answer.trim()) {
        turns.push({ question: prefix(question, 800), answer: prefix(response.answer, 1000) });
      }
      question = "";
    }
  }
  return turns.slice(-maxHistoryTurns);
}

export const chatAdapter: ChatModelAdapter = {
  async *run({ messages, abortSignal }) {
    const currentIndex = messages.findLastIndex(message => message.role === "user");
    const question = currentIndex >= 0 ? textOf(messages[currentIndex]) : "";
    if (!question) throw new Error("질문을 입력해 주세요.");
    const history = completedTurns(messages.slice(0, currentIndex));
    let text = "";
    let custom: Record<string, unknown> = {};
    const snapshot = (): ChatModelRunResult => ({
      content: [{ type: "text", text }],
      metadata: { custom: { ...custom } },
    });
    try {
      for await (const event of streamQuestion(question, abortSignal, history)) {
        custom.requestId = event.request_id;
        if (event.type === "progress") custom.progress = event.message;
        if (event.type === "answer_delta") text += event.delta;
        if (event.type === "error") throw new ChatError(event.message, event.request_id);
        if (event.type === "final") {
          text = event.response.answer;
          custom = { requestId: event.request_id, response: event.response };
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
