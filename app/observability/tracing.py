"""Langfuse 요청 추적 (langfuse Python SDK 5.x 공식 인터페이스 기준).

- import 시 네트워크에 연결하지 않는다. SDK도 `Tracer.from_env()` 호출 때 지연 import한다.
- 인증정보는 환경변수(LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL)로만 받고
  로그·기록에 남기지 않는다. 오류는 예외 타입 이름만 저장한다.
- trace ID는 `create_trace_id(seed=request_id)`로 만들어 같은 request_id로 trace를 다시 찾을 수 있다.
- SDK의 flush()는 전송 실패를 예외로 알리지 않는다. 그래서 flush 결과는
  `flushed_unverified`로만 표시하고, 실제 저장 여부는 `verify_trace()`로 API를 조회해 확인한다.
"""

from __future__ import annotations

from contextlib import contextmanager
import math
import os
import time
from collections.abc import Mapping, Sequence
from typing import Any, cast

ROOT_NAME = "rag-query"


def _error_name(exc: BaseException) -> str:
    return type(exc).__name__


def _safe_error_details(exc: BaseException) -> dict[str, Any]:
    """Return only the exception type and HTTP status; never persist response bodies or headers."""
    details = {"error": _error_name(exc)}
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and not isinstance(status_code, bool):
        details["status_code"] = status_code
    return details


class RequestTrace:
    """요청 한 건의 추적. 기록 실패는 서비스 흐름을 멈추지 않고 `status`/`errors`에 남는다."""

    def __init__(
        self,
        client: Any | None,
        request_id: str,
        *,
        question: str,
        retrieval_mode: str,
        dataset_id: str,
        config_hash: str,
        metadata: Mapping[str, Any] | None = None,
    ):
        self.request_id = request_id
        self.errors: list[str] = []
        self.trace_id: str | None = None
        self._client = client
        self._root = None
        self._finished = False
        self._query = {"question": question, "retrieval_mode": retrieval_mode}
        self._active: dict[str, tuple[Any, dict]] = {}
        self._observed: set[str] = set()
        if client is None:
            self.status = "disabled"
        else:
            self.status = "recording"
        if client is None:
            return
        meta = {
            "request_id": request_id,
            "retrieval_mode": retrieval_mode,
            "dataset_id": dataset_id,
            "config_hash": config_hash,
            **(metadata or {}),
        }
        try:
            self.trace_id = client.create_trace_id(seed=request_id)
            self._root = client.start_observation(
                trace_context={"trace_id": self.trace_id},
                name=ROOT_NAME,
                as_type="span",
                input=self._query,
                metadata=meta,
            )
        except Exception as exc:  # SDK 오류를 성공처럼 숨기지 않는다
            self._fail("start", exc)

    def _fail(self, step: str, exc: BaseException) -> None:
        self.status = "sdk_error"
        self.errors.append(f"{step}:{_error_name(exc)}")

    @contextmanager
    def stage(self, name: str, *, as_type: str = "span", **kwargs: Any):
        """실제 실행 구간을 기록하며 추적 오류는 서비스 오류를 가리지 않는다."""
        observation, updates = None, {}
        if self._root is not None:
            try:
                parents = [obs for obs, _ in self._active.values() if obs is not None]
                parent = parents[-1] if parents else self._root
                observation = parent.start_observation(name=name, as_type=as_type, **kwargs)
            except Exception as exc:
                self._fail(name, exc)
        self._active[name] = observation, updates
        try:
            yield updates
        except BaseException as exc:
            updates.update(level="ERROR", status_message=_error_name(exc))
            raise
        finally:
            self._active.pop(name, None)
            self._observed.add(name)
            if observation is not None:
                try:
                    if updates:
                        observation.update(**updates)
                except Exception as exc:
                    self._fail(name, exc)
                finally:
                    try:
                        observation.end()
                    except Exception as exc:
                        self._fail(name, exc)

    def _child(self, step: str, **kwargs: Any) -> None:
        name = kwargs.get("name", step)
        if name in self._active:
            self._active[name][1].update({k: v for k, v in kwargs.items() if k not in ("name", "as_type")})
            return
        if self._root is None or name in self._observed:
            return
        try:
            self._root.start_observation(**kwargs).end()
            self._observed.add(name)
        except Exception as exc:
            self._fail(step, exc)

    def record_retrieval(
        self,
        *,
        retrieved_candidates: Sequence[Mapping[str, Any]],
        final_context_chunks: Sequence[Mapping[str, Any]],
        final_context_hash: str,
        k: int | None = None,
        latency_ms: int | None = None,
        model_context_text: str | None = None,
        model_context_hash: str | None = None,
        model_context_token_count: int | None = None,
        budget_excluded_chunk_ids: Sequence[str] = (),
    ) -> None:
        """평탄화한 검색 후보와 최종 문맥, 실제 모델에 넣을 문맥 문자열을 기록한다."""
        self._child(
            "retrieval",
            name="retrieval",
            as_type="retriever",
            input=self._query,
            output={"candidates": list(retrieved_candidates)},
            metadata={
                "candidate_count": len(retrieved_candidates),
                "latency_ms": latency_ms,
            },
        )
        self._child(
            "context",
            name="final-context",
            as_type="span",
            input=self._query,
            output={
                "final_context_chunks": list(final_context_chunks),
                "final_context_hash": final_context_hash,
                "model_context_text": model_context_text,
                "model_context_hash": model_context_hash,
                "model_context_token_count": model_context_token_count,
                "budget_excluded_chunk_ids": list(budget_excluded_chunk_ids),
            },
            metadata={"k": k, "delivered_chunks": len(final_context_chunks)},
        )

    def record_attempt(
        self,
        *,
        attempt: int,
        model: str,
        model_input: Any,
        model_output: Any = None,
        role: str | None = None,
        tool_schema: Any = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        error: str | None = None,
        prompt_variant: str | None = None,
        model_parameters: Mapping[str, Any] | None = None,
        latency_ms: int | None = None,
        calendar_reference: str = "",
    ) -> None:
        """Record visible model inputs/outputs and measured usage for one call."""
        usage = {}
        for usage_key, token_count in (("input", input_tokens), ("output", output_tokens)):
            if token_count is not None:
                usage[usage_key] = token_count
        recorded_parameters = dict(model_parameters) if model_parameters else None
        if role in {"planner", "answer"}:
            name = f"{role}-{attempt}"
        else:
            name = f"generation-attempt-{attempt}"
        call_input = {"request": model_input}
        if tool_schema is not None:
            call_input["tool_schema"] = tool_schema
        kwargs: dict[str, Any] = dict(
            name=name,
            as_type="generation",
            model=model,
            input=call_input,
            output=model_output,
            usage_details=usage or None,
            model_parameters=recorded_parameters,
            metadata={
                "attempt": attempt,
                "role": role,
                "latency_ms": latency_ms,
                "prompt_variant": prompt_variant,
                "calendar_reference": calendar_reference or None,
                "usage_known": {
                    "input": input_tokens is not None,
                    "output": output_tokens is not None,
                },
            },
        )
        if error:
            kwargs.update(level="ERROR", status_message=error)
        self._child(name, **kwargs)

    def finish(
        self,
        *,
        status: str,
        answer: str | None,
        reason: str | None,
        citations: Sequence[Mapping[str, Any]],
        latency_ms: int | None,
        error_type: str | None = None,
    ) -> dict[str, Any]:
        """최종 상태·인용·시간·오류를 기록하고 루트 관측을 닫는다. 실행 기록에 넣을 값을 반환한다."""
        if self._root is not None and not self._finished:
            try:
                kwargs: dict[str, Any] = dict(
                    output={
                        "status": status,
                        "answer": answer,
                        "reason": reason,
                        "citations": list(citations),
                    },
                    metadata={"latency_ms": latency_ms, "error_type": error_type},
                )
                if status == "error":
                    kwargs.update(level="ERROR", status_message=error_type or "error")
                self._root.update(**kwargs)
            except Exception as exc:
                self._fail("finish", exc)
            finally:
                try:
                    self._root.end()
                except Exception as exc:
                    self._fail("finish", exc)
        self._finished = True
        if self.status == "recording":
            self.status = "recorded_unverified"
        return {
            "langfuse_trace_id": self.trace_id,
            "langfuse_status": self.status,
            "langfuse_errors": list(self.errors),
        }


class Tracer:
    """B가 앱 수명주기에서 하나 만들고 요청마다 `start_request()`를 호출한다."""

    def __init__(self, client: Any | None = None):
        self._client = client

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @classmethod
    def from_credentials(
        cls,
        *,
        public_key: str | None,
        secret_key: str | None,
        base_url: str | None = None,
    ) -> "Tracer":
        """Create a client from explicit credentials; missing keys disable tracing."""
        if not (public_key and secret_key):
            return cls(None)
        from langfuse import Langfuse  # 지연 import: 모듈 import 시 SDK 초기화 방지

        return cls(
            Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                # Use the SDK's existing default explicitly; do not re-read an
                # environment that may have changed after settings resolution.
                base_url=base_url or "https://cloud.langfuse.com",
            )
        )

    @classmethod
    def from_env(cls) -> "Tracer":
        """Legacy factory for tools that still load their credentials from the environment."""
        return cls.from_credentials(
            public_key=os.environ.get("LANGFUSE_PUBLIC_KEY"),
            secret_key=os.environ.get("LANGFUSE_SECRET_KEY"),
            base_url=os.environ.get("LANGFUSE_BASE_URL")
            or os.environ.get("LANGFUSE_HOST"),
        )

    def start_request(
        self,
        request_id: str,
        *,
        question: str,
        retrieval_mode: str,
        dataset_id: str,
        config_hash: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> RequestTrace:
        return RequestTrace(
            self._client,
            request_id,
            question=question,
            retrieval_mode=retrieval_mode,
            dataset_id=dataset_id,
            config_hash=config_hash,
            metadata=metadata,
        )

    def record_score(self, trace_id, name, value, *, comment=None, score_id=None, metadata=None, timestamp=None):
        """명시적 평가값을 전송하며 수신 확인은 별도로 남긴다."""
        if self._client is None:
            return {"status": "disabled"}
        if not trace_id:
            return {"status": "no_trace_id"}
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return {"status": "invalid"}
        try:
            options = {"timestamp": timestamp} if timestamp is not None else {}
            self._client.create_score(trace_id=trace_id, name=name, value=float(value),
                                      data_type="NUMERIC", comment=comment, score_id=score_id,
                                      metadata=metadata, **options)
        except Exception as exc:
            return {"status": "sdk_error", "error": _error_name(exc)}
        return {"status": "recorded_unverified"}

    def verify_score(
        self,
        score_id,
        *,
        name,
        value,
        timestamp,
        timeout_s: float = 5.0,
        interval_s: float = 0.5,
    ) -> dict[str, Any]:
        """Poll Langfuse briefly for a score, then verify its ID, value and date."""
        if self._client is None:
            return {"status": "disabled"}
        if (
            not math.isfinite(timeout_s)
            or not math.isfinite(interval_s)
            or timeout_s < 0
            or interval_s < 0
        ):
            raise ValueError("Score readback timeout and interval must be finite and non-negative")
        deadline = time.monotonic() + timeout_s
        first_attempt = True
        while True:
            remaining = deadline - time.monotonic()
            if not first_attempt and remaining <= 0:
                return {"status": "not_found"}
            # Langfuse 5.x request_options forwards timeout_in_seconds to HTTPX.
            # HTTPX accepts sub-second floats; retries are disabled so one fetch
            # cannot silently extend the readback deadline through SDK retries.
            request_timeout = max(0.001, remaining)
            try:
                response = self._client.api.scores.get_many(
                    id=score_id,
                    limit=2,
                    request_options=cast(
                        Any,
                        {
                            "timeout_in_seconds": request_timeout,
                            "max_retries": 0,
                        },
                    ),
                )
                first_attempt = False
                matches = [row for row in response.data if row.id == score_id]
                completed_at = time.monotonic()
                if timeout_s > 0 and completed_at > deadline:
                    return {"status": "not_found"}
                if len(matches) > 1:
                    return {"status": "duplicate"}
                if matches:
                    row = matches[0]
                    if (row.name, row.value, row.timestamp.date()) != (
                        name,
                        value,
                        timestamp.date(),
                    ):
                        return {"status": "mismatch"}
                    return {"status": "verified"}
            except Exception as exc:
                return {"status": "failed", **_safe_error_details(exc)}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"status": "not_found"}
            time.sleep(min(interval_s, remaining))

    def verify_scores(
        self,
        trace_id: str | None,
        expected_scores: Sequence[Mapping[str, Any]],
        *,
        timeout_s: float = 5.0,
        interval_s: float = 0.5,
    ) -> dict[str, dict[str, Any]]:
        """Read a trace's score pages once and match the expected score IDs locally."""
        expected = {item["score_id"]: item for item in expected_scores}
        if len(expected) != len(expected_scores):
            raise ValueError("Expected score IDs must be unique")
        if not expected:
            return {}
        if self._client is None:
            return {score_id: {"status": "disabled"} for score_id in expected}
        if not trace_id:
            return {score_id: {"status": "no_trace_id"} for score_id in expected}
        if (
            not math.isfinite(timeout_s)
            or not math.isfinite(interval_s)
            or timeout_s < 0
            or interval_s < 0
        ):
            raise ValueError("Score readback timeout and interval must be finite and non-negative")

        deadline = time.monotonic() + timeout_s
        found: dict[str, list[Any]] = {score_id: [] for score_id in expected}
        error_details = None
        first_fetch = True
        while True:
            scan_found: dict[str, list[Any]] = {score_id: [] for score_id in expected}
            cursor = None
            while True:
                remaining = deadline - time.monotonic()
                if not first_fetch and remaining <= 0:
                    break
                request_timeout = max(0.001, remaining)
                try:
                    response = self._client.api.scores.get_many(
                        trace_id=trace_id,
                        limit=100,
                        cursor=cursor,
                        request_options=cast(
                            Any,
                            {
                                "timeout_in_seconds": request_timeout,
                                "max_retries": 0,
                            },
                        ),
                    )
                except Exception as exc:
                    error_details = _safe_error_details(exc)
                    break
                first_fetch = False
                completed_at = time.monotonic()
                if timeout_s > 0 and completed_at > deadline:
                    break
                for row in response.data:
                    score_id = getattr(row, "id", None)
                    if score_id in scan_found:
                        scan_found[score_id].append(row)
                if all(scan_found.values()):
                    break
                metadata = getattr(response, "meta", None)
                cursor = getattr(metadata, "cursor", None)
                if not cursor:
                    break
            if error_details:
                break
            found = scan_found
            if all(found.values()):
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(interval_s, remaining))

        results = {}
        for score_id, expected_score in expected.items():
            matches = found[score_id]
            if error_details:
                result = {"status": "failed", **error_details}
            elif not matches:
                result = {"status": "not_found"}
            elif len(matches) > 1:
                result = {"status": "duplicate"}
            else:
                row = matches[0]
                if (row.name, row.value, row.timestamp.date()) != (
                    expected_score["name"],
                    expected_score["value"],
                    expected_score["timestamp"].date(),
                ):
                    result = {"status": "mismatch"}
                else:
                    result = {"status": "verified"}
            results[score_id] = result
        return results

    def flush(self) -> dict[str, Any]:
        """대기 중 기록을 전송한다. 성공해도 `flushed_unverified`이며 저장 확인은 verify_trace()."""
        if self._client is None:
            return {"status": "disabled"}
        try:
            self._client.flush()
        except Exception as exc:
            return {"status": "failed", "error": _error_name(exc)}
        return {"status": "flushed_unverified"}

    def verify_trace(
        self,
        trace_id: str | None,
        *,
        timeout_s: float = 60.0,
        interval_s: float = 5.0,
        expected_observations: Sequence[str] | None = None,
        require_payload_names: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Langfuse API에서 trace의 관측을 조회한다. 수집 지연(15~30초)이 있어 재시도한다."""
        if self._client is None or not trace_id:
            if self._client is None:
                return {"status": "disabled"}
            else:
                return {"status": "no_trace_id"}
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                resp = self._client.api.observations.get_many(
                    trace_id=trace_id, limit=50,
                    fields="core,basic,time,io,metadata,model,usage",
                )
                observation_names = set()
                missing_payload = []
                required_payloads = set(require_payload_names)
                for observation in resp.data:
                    name = getattr(observation, "name", None)
                    if name:
                        observation_names.add(name)
                    if name in required_payloads and (
                        getattr(observation, "input", None) is None
                        or getattr(observation, "output", None) is None
                    ):
                        missing_payload.append(name)
                names = sorted(observation_names)
                missing = sorted(set(expected_observations or [ROOT_NAME]) - set(names))
                if names and not missing and not missing_payload:
                    return {
                        "status": "verified",
                        "observation_names": names,
                        "payload_observations": sorted(required_payloads),
                    }
                last = (
                    {
                        "status": "partial",
                        "observation_names": names,
                        "missing_observations": missing,
                        "missing_payload": sorted(set(missing_payload)),
                    }
                    if names
                    else {"status": "not_found"}
                )
            except Exception as exc:
                last = {"status": "failed", "error": _error_name(exc)}
            if time.monotonic() >= deadline:
                return last
            time.sleep(interval_s)

    def observations(self, trace_id: str) -> list[Any]:
        """trace의 관측을 입출력·메타데이터·사용량까지 조회한다(HTTP 평가 기록 연결용). 비활성이면 []."""
        if self._client is None or not trace_id:
            return []
        return list(self._client.api.observations.get_many(
            trace_id=trace_id, limit=50, fields="core,basic,time,io,metadata,model,usage").data)

    def shutdown(self) -> dict[str, Any]:
        if self._client is None:
            return {"status": "disabled"}
        try:
            self._client.shutdown()
        except Exception as exc:
            return {"status": "failed", "error": _error_name(exc)}
        return {"status": "flushed_unverified"}
