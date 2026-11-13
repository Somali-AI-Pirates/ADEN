"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import {
  ActionBarPrimitive, AssistantRuntimeProvider, ComposerPrimitive,
  MessagePrimitive, ThreadPrimitive, useAui, useAuiState, useLocalRuntime,
} from "@assistant-ui/react";
import { chatAdapter } from "../lib/chat-adapter";
import { chatReadyEvent } from "../lib/chat-startup";
import type { Citation, QueryResponse } from "../lib/api";
import { MarkdownContent } from "./markdown";
import { ThemeSwitch } from "./theme-switch";

const PdfPanel = dynamic(() => import("./pdf-panel"), {
  ssr: false,
  loading: () => <aside className="pdf-panel"><p className="pdf-loading" role="status">문서 뷰어를 여는 중…</p></aside>,
});
const SourceContext = createContext<{
  source: Citation | null;
  openSource: (citation: Citation, trigger: HTMLButtonElement) => void;
}>({ source: null, openSource: () => {} });

export function Chat() {
  const runtime = useLocalRuntime(chatAdapter);
  const [source, setSource] = useState<Citation | null>(null);
  const sourceTrigger = useRef<HTMLButtonElement | null>(null);
  const openSource = (citation: Citation, trigger: HTMLButtonElement) => {
    sourceTrigger.current = trigger;
    setSource(citation);
  };
  const closeSource = () => setSource(null);
  useEffect(() => { if (!source) sourceTrigger.current?.focus(); }, [source]);
  return <AssistantRuntimeProvider runtime={runtime}>
    <SourceContext.Provider value={{ source, openSource }}>
      <div className={`app-shell${source ? " with-document" : ""}`}>
        <ThreadPrimitive.Root className="chat-shell">
          <Header onReset={() => { runtime.thread.cancelRun(); runtime.thread.reset(); setSource(null); }} />
          <ThreadPrimitive.Viewport
            className="chat-viewport"
            turnAnchor="top"
            scrollToBottomOnInitialize={false}
          >
            <div className="conversation">
              <ThreadPrimitive.Empty><Welcome /></ThreadPrimitive.Empty>
              <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
            </div>
            <div className="latest-control">
              <ThreadPrimitive.ScrollToBottom className="latest-button" aria-label="최신 답변으로 이동">
                ↓ 최신 답변
              </ThreadPrimitive.ScrollToBottom>
            </div>
          </ThreadPrimitive.Viewport>
          <Composer />
        </ThreadPrimitive.Root>
        {source && <PdfPanel key={source.book_id} citation={source} onClose={closeSource} />}
      </div>
    </SourceContext.Provider>
  </AssistantRuntimeProvider>;
}

function Header({ onReset }: { onReset: () => void }) {
  const resetDisabled = useAuiState(s => s.thread.isLoading || s.thread.isRunning);
  return <header className="topbar">
    <div className="brand"><span className="brand-mark" aria-hidden="true">A</span><div><strong>ADEN</strong><span>해운 문서 도우미</span></div></div>
    <div className="header-actions">
      <ThemeSwitch />
      <button className="quiet-button" onClick={onReset} disabled={resetDisabled}>＋ 새 대화</button>
    </div>
  </header>;
}

function Welcome() {
  const loading = useAuiState(s => s.thread.isLoading);
  const examples = [
    "예약 접수번호를 받으면 예약이 확정된 건가요?",
    "선적 전에 제출할 서류와 확인할 사항을 알려주세요.",
  ];
  return <section className="welcome">
    <h1>어떤 업무를 확인할까요?</h1>
    <p>해운 문서에서 근거를 찾고, 출처를 누르면 해당 페이지를 볼 수 있어요.</p>
    <div className="suggestions">
      {examples.map(question => (
        <ThreadPrimitive.Suggestion key={question} prompt={question} send={false} disabled={loading} className="suggestion">
          <span>{question}</span>
          <span aria-hidden="true">→</span>
        </ThreadPrimitive.Suggestion>
      ))}
    </div>
    <p className="scope-note">같은 대화에서 앞서 확인한 기록을 이어서 물을 수 있습니다.</p>
  </section>;
}

function UserMessage() {
  return <MessagePrimitive.Root className="user-message"><div className="user-bubble"><MessagePrimitive.Parts /></div></MessagePrimitive.Root>;
}

function AssistantMessage() {
  const message = useAuiState(s => s.message);
  const { source, openSource } = useContext(SourceContext);
  const custom = message.metadata.custom;
  const response = custom.response as QueryResponse | undefined;
  const traceUrl = typeof custom.traceUrl === "string" ? custom.traceUrl : undefined;
  const running = message.status?.type === "running";
  const incomplete = message.status?.type === "incomplete";
  const error = typeof custom.error === "string" ? custom.error : undefined;
  const text = message.content.filter(part => part.type === "text").map(part => part.text).join("");
  const labels = { answered: "답변 완료", needs_clarification: "추가 확인이 필요해요", abstained: "근거가 부족해요", error: "처리 오류" };
  return <MessagePrimitive.Root className="assistant-message">
    <div className="assistant-label"><span className="avatar" aria-hidden="true">A</span><strong>ADEN</strong>
      {response && <span className={`answer-status ${response.status}`}>{labels[response.status]}</span>}
    </div>
    {running && <p className="progress" role="status"><span className="progress-dot" />{String(custom.progress || "질문을 전달하고 있습니다.")}</p>}
    {text && (
      <MarkdownContent text={text} citations={response?.citations} activeSource={source} onOpenSource={openSource} />
    )}
    {running && text && <p className="provisional">작성 중인 답변입니다. 출처 확인 후 완료됩니다.</p>}
    {incomplete && <div className="answer-error" role={error ? "alert" : "status"}>{error || "답변 생성을 중단했습니다."}{text && <span> 위 내용은 완료되지 않은 임시 답변입니다.</span>}</div>}
    {response && response.citations.length > 0 && <div className="sources" aria-label="답변 출처">
      <p>참고한 원문</p>
      <div className="source-list">{response.citations.map((citation, index) => (
        <button
          className="source-card"
          key={`${citation.source_id}-${index}`}
          onClick={event => openSource(citation, event.currentTarget)}
          aria-current={(source?.book_id === citation.book_id && source.page === citation.page) || undefined}
          aria-label={`${citation.file_name} ${citation.page}쪽 원문 열기`}
        >
          <span className="source-index" aria-hidden="true">{index + 1}</span>
          <strong>{citation.file_name}</strong>
          <span className="source-page">{citation.page}쪽</span>
          <span className="source-arrow" aria-hidden="true">↗</span>
        </button>
      ))}</div>
    </div>}
    {!running && <div className="message-footer">
      <ActionBarPrimitive.Root className="message-actions"><ActionBarPrimitive.Copy className="quiet-button">복사</ActionBarPrimitive.Copy><ActionBarPrimitive.Reload className="quiet-button">다시 답변</ActionBarPrimitive.Reload></ActionBarPrimitive.Root>
      {typeof custom.requestId === "string" && (
        <details className="request-details">
          <summary>요청 정보</summary>
          <code>{custom.requestId}</code>
          {traceUrl && (
            <a className="request-trace-link" href={traceUrl} target="_blank" rel="noopener noreferrer">
              Langfuse에서 보기 <span aria-hidden="true">↗</span>
            </a>
          )}
        </details>
      )}
    </div>}
  </MessagePrimitive.Root>;
}

function Composer() {
  const loading = useAuiState(s => s.thread.isLoading);
  const running = useAuiState(s => s.thread.isRunning);
  const aui = useAui();
  const [touch, setTouch] = useState(false);
  const composing = useRef(false);
  useEffect(() => {
    if (!loading) window.dispatchEvent(new Event(chatReadyEvent));
  }, [loading]);
  useEffect(() => {
    const media = matchMedia("(pointer: coarse) and (not (any-pointer: fine))");
    const update = () => setTouch(media.matches);
    update(); media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return <footer className="composer-wrap">
    <div className="composer-inner">
      <ComposerPrimitive.Root className="composer">
        <ComposerPrimitive.Input
          aria-label="질문"
          placeholder={loading ? "채팅을 준비하고 있습니다" : "확인할 내용을 질문하세요"}
          disabled={loading}
          minRows={2}
          maxRows={5}
          submitMode="none"
          addAttachmentOnPaste={false}
          onCompositionStart={() => { composing.current = true; }}
          onCompositionEnd={() => { composing.current = false; }}
          onKeyDown={event => {
            if (composing.current || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
            if (event.key === "Enter" && !event.shiftKey && !event.ctrlKey && !event.altKey && !event.metaKey && !touch) {
              event.preventDefault();
              if (!loading && !running && !event.repeat) aui.composer().send();
            }
          }}
        />
        {running ? (
          <ComposerPrimitive.Cancel className="send-button stop-button">중단</ComposerPrimitive.Cancel>
        ) : (
          <ComposerPrimitive.Send className="send-button" aria-label="질문 전송" disabled={loading}>전송 ↑</ComposerPrimitive.Send>
        )}
      </ComposerPrimitive.Root>
      <p className="chat-startup-retry" role="status">
        채팅 준비가 지연되고 있습니다. <a href="/">다시 불러오기</a>
      </p>
      <div className="composer-hint">
        <span className="composer-keyboard-hint">Enter 전송 · Shift+Enter 줄바꿈</span>
        <span>새로고침하면 대화가 지워집니다.</span>
      </div>
    </div>
  </footer>;
}
