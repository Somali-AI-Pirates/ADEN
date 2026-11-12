import type { ChatModelAdapter, ChatModelRunResult } from "@assistant-ui/react";
import {
  ChatError, MAX_CONVERSATION_TEXT_LENGTH, MAX_CONVERSATION_TURNS, streamQuestion,
  type ConversationTurn, type QueryResponse,
} from "./api";

type Message = Parameters<ChatModelAdapter["run"]>[0]["messages"][number];

function messageText(message: Message): string {
  return message.content.filter(part => part.type === "text").map(part => part.text).join("\n").trim();
}

function contextText(message: Message): string {
  return Array.from(messageText(message)).slice(0, MAX_CONVERSATION_TEXT_LENGTH).join("");
}

function priorTurns(messages: readonly Message[], latestUser: number): ConversationTurn[] {
  const turns: ConversationTurn[] = [];
  for (let index = latestUser - 1; index > 0 && turns.length < MAX_CONVERSATION_TURNS; index--) {
    const assistant = messages[index];
    const user = messages[index - 1];
    if (assistant.role !== "assistant" || assistant.status.type !== "complete" || user.role !== "user") continue;
    const question = contextText(user);
    const answer = contextText(assistant);
    if (!question || !answer) continue;
    const response = assistant.metadata?.custom?.response as QueryResponse | undefined;
    const sourceIds = response?.citations
      .map(citation => citation.source_id)
      .filter(id => /^[A-Za-z0-9][A-Za-z0-9_-]{0,79}:p[0-9]{4,9}$/.test(id))
      .slice(0, 8);
    turns.push(sourceIds?.length ? { question, answer, source_ids: sourceIds } : { question, answer });
  }
  return turns.reverse();
}

export const chatAdapter: ChatModelAdapter = {
  async *run({ messages, abortSignal }) {
    const latestUser = messages.findLastIndex(message => message.role === "user");
    const question = latestUser < 0 ? "" : messageText(messages[latestUser]);
    if (!question) throw new Error("질문을 입력해 주세요.");
    let text = "";
    let custom: Record<string, unknown> = {};
    const snapshot = (): ChatModelRunResult => ({
      content: [{ type: "text", text }],
      metadata: { custom: { ...custom } },
    });
    try {
      for await (const event of streamQuestion(question, abortSignal, priorTurns(messages, latestUser))) {
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
