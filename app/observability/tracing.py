"""Langfuse 요청 추적 (langfuse Python SDK 5.x 공식 인터페이스 기준).

- import 시 네트워크에 연결하지 않는다. SDK도 `Tracer.from_env()` 호출 때 지연 import한다.
- 인증정보는 환경변수(LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL)로만 받고
  로그·기록에 남기지 않는다. 오류는 예외 타입 이름만 저장한다.
- trace ID는 `create_trace_id(seed=request_id)`로 만들어 같은 request_id로 trace를 다시 찾을 수 있다.
- SDK의 flush()는 전송 실패를 예외로 알리지 않는다. 그래서 flush 결과는
  `flushed_unverified`로만 표시하고, 실제 저장 여부는 `verify_trace()`로 API를 조회해 확인한다.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping, Sequence
from typing import Any

ROOT_NAME = "rag-query"


def _error_name(exc: BaseException) -> str:
    return type(exc).__name__


class RequestTrace:
    """요청 한 건의 추적. 기록 실패는 서비스 흐름을 멈추지 않고 `status`/`errors`에 남는다."""

    def __init__(self, client: Any | None, request_id: str, *, question: str,
                 retrieval_mode: str, dataset_id: str, config_hash: str,
                 metadata: Mapping[str, Any] | None = None):
        self.request_id = request_id
        self.errors: list[str] = []
        self.trace_id: str | None = None
        self._client = client
        self._root = None
        self._finished = False
        self.status = "disabled" if client is None else "recording"
        if client is None:
            return
        meta = {"request_id": request_id, "retrieval_mode": retrieval_mode,
                "dataset_id": dataset_id, "config_hash": config_hash, **(metadata or {})}
        try:
            self.trace_id = client.create_trace_id(seed=request_id)
            self._root = client.start_observation(
                trace_context={"trace_id": self.trace_id}, name=ROOT_NAME, as_type="span",
                input={"question": question, "retrieval_mode": retrieval_mode}, metadata=meta)
        except Exception as exc:  # SDK 오류를 성공처럼 숨기지 않는다
            self._fail("start", exc)

    def _fail(self, step: str, exc: BaseException) -> None:
        self.status = "sdk_error"
        self.errors.append(f"{step}:{_error_name(exc)}")

    def _child(self, step: str, **kwargs: Any) -> None:
        if self._root is None:
            return
        try:
            self._root.start_observation(**kwargs).end()
        except Exception as exc:
            self._fail(step, exc)

    def record_retrieval(self, *, retrieved_candidates: Sequence[Mapping[str, Any]],
                         final_context_chunks: Sequence[Mapping[str, Any]],
                         final_context_hash: str, k: int | None = None,
                         latency_ms: int | None = None) -> None:
        """검색 후보와 실제 모델에 전달할 최종 문맥을 각각 기록한다."""
        self._child("retrieval", name="retrieval", as_type="retriever",
                    output={"candidates": list(retrieved_candidates)},
                    metadata={"candidate_count": len(retrieved_candidates), "latency_ms": latency_ms})
        self._child("context", name="final-context", as_type="span",
                    output={"final_context_chunks": list(final_context_chunks),
                            "final_context_hash": final_context_hash},
                    metadata={"k": k, "delivered_chunks": len(final_context_chunks)})

    def record_attempt(self, *, attempt: int, model: str, model_input: Any,
                       model_output: Any = None, input_tokens: int | None = None,
                       output_tokens: int | None = None, error: str | None = None,
                       prompt_variant: str = "base",
                       model_parameters: Mapping[str, Any] | None = None) -> None:
        """실제 모델 입력·출력과 시도별 사용량. 미확인 사용량은 보내지 않는다(0으로 채우지 않음)."""
        usage = {k: v for k, v in (("input", input_tokens), ("output", output_tokens)) if v is not None}
        kwargs: dict[str, Any] = dict(
            name=f"generation-attempt-{attempt}", as_type="generation", model=model,
            input=model_input, output=model_output, usage_details=usage or None,
            model_parameters=dict(model_parameters) if model_parameters else None,
            metadata={"attempt": attempt, "prompt_variant": prompt_variant,
                      "usage_known": {"input": input_tokens is not None,
                                      "output": output_tokens is not None}})
        if error:
            kwargs.update(level="ERROR", status_message=error)
        self._child(f"attempt-{attempt}", **kwargs)

    def finish(self, *, status: str, answer: str | None, reason: str | None,
               citations: Sequence[Mapping[str, Any]], latency_ms: int | None,
               error_type: str | None = None) -> dict[str, Any]:
        """최종 상태·인용·시간·오류를 기록하고 루트 관측을 닫는다. 실행 기록에 넣을 값을 반환한다."""
        if self._root is not None and not self._finished:
            try:
                kwargs: dict[str, Any] = dict(
                    output={"status": status, "answer": answer, "reason": reason,
                            "citations": list(citations)},
                    metadata={"latency_ms": latency_ms, "error_type": error_type})
                if status == "error":
                    kwargs.update(level="ERROR", status_message=error_type or "error")
                self._root.update(**kwargs)
                self._root.end()
            except Exception as exc:
                self._fail("finish", exc)
        self._finished = True
        if self.status == "recording":
            self.status = "recorded_unverified"
        return {"langfuse_trace_id": self.trace_id, "langfuse_status": self.status,
                "langfuse_errors": list(self.errors)}


class Tracer:
    """B가 앱 수명주기에서 하나 만들고 요청마다 `start_request()`를 호출한다."""

    def __init__(self, client: Any | None = None):
        self._client = client

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @classmethod
    def from_env(cls) -> "Tracer":
        """키가 없으면 비활성 Tracer(기록 상태 `disabled`). 키 값은 출력하지 않는다."""
        if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
            return cls(None)
        from langfuse import Langfuse  # 지연 import: 모듈 import 시 SDK 초기화 방지
        return cls(Langfuse())

    def start_request(self, request_id: str, *, question: str, retrieval_mode: str,
                      dataset_id: str, config_hash: str,
                      metadata: Mapping[str, Any] | None = None) -> RequestTrace:
        return RequestTrace(self._client, request_id, question=question,
                            retrieval_mode=retrieval_mode, dataset_id=dataset_id,
                            config_hash=config_hash, metadata=metadata)

    def flush(self) -> dict[str, Any]:
        """대기 중 기록을 전송한다. 성공해도 `flushed_unverified`이며 저장 확인은 verify_trace()."""
        if self._client is None:
            return {"status": "disabled"}
        try:
            self._client.flush()
        except Exception as exc:
            return {"status": "failed", "error": _error_name(exc)}
        return {"status": "flushed_unverified"}

    def verify_trace(self, trace_id: str | None, *, timeout_s: float = 60.0,
                     interval_s: float = 5.0) -> dict[str, Any]:
        """Langfuse API에서 trace의 관측을 조회한다. 수집 지연(15~30초)이 있어 재시도한다."""
        if self._client is None or not trace_id:
            return {"status": "disabled" if self._client is None else "no_trace_id"}
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                resp = self._client.api.observations.get_many(trace_id=trace_id, limit=50)
                names = sorted({o.name for o in resp.data if getattr(o, "name", None)})
                if names:
                    return {"status": "verified", "observation_names": names}
                last = {"status": "not_found"}
            except Exception as exc:
                last = {"status": "failed", "error": _error_name(exc)}
            if time.monotonic() >= deadline:
                return last
            time.sleep(interval_s)

    def shutdown(self) -> dict[str, Any]:
        if self._client is None:
            return {"status": "disabled"}
        try:
            self._client.shutdown()
        except Exception as exc:
            return {"status": "failed", "error": _error_name(exc)}
        return {"status": "flushed_unverified"}
