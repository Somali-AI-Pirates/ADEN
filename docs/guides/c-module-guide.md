# C 모듈 가이드 — 실행 기록·평가·결과 비교

작성일: 2026-11-05 · 수정일: 2026-11-05. 담당: C(실행 기록·평가). 기준 문서: [시스템 설계](../design/architecture.md) · [Basic 당일 작업 계획](../plans/basic-day-plan-2026-11-05.md) · [Git 협업 규칙](git-workflow.md)

## 1. 구현 상태

| 구분 | 상태 |
| --- | --- |
| 실행 기록 검증·정규화, Langfuse 추적, Hit/Recall, Faithfulness·Judge 처리 규칙, 일반 평가 실행기, 보안 비교, 결과 파일 | 구현, 가상 샘플·모의 클라이언트로 테스트 통과 |
| 실제 Langfuse 서버 저장 | **미검증.** SDK 호출 경로는 연결 불가 주소로 실행해 확인(전송 실패가 `failed`로 표시됨) |
| 실제 RAGAS·Judge 점수 | **미검증.** 호출 경로가 네트워크 단계까지 도달하는 것만 확인 |
| A/B 실제 서비스 연결, 실제 Basic 평가 파일 구조 | **미확인.** 실제 파일을 본 뒤 `evaluation/dataset.py`의 `_items()`를 맞춘다 |

`app/core/contracts.py`가 아직 없어 공통 타입을 새로 만들지 않았다. C 연결부는 JSON/Mapping을 받아 검증한다(`app/observability/records.py`). 공통 타입이 생기면 `model_dump()` 결과를 그대로 넘기면 된다.

## 2. 파일

```text
app/observability/
├── records.py      # RunRecord 검증·정규화, 문맥 해시, 사용량 합계
└── tracing.py      # Tracer / RequestTrace (Langfuse)
evaluation/
├── dataset.py      # 질문(id·question만) / 참고 정답 로드, evidence book_id 수집
├── metrics.py      # Hit@k·Recall@k, 점수 표현, 평균, 운영 지표
├── graders.py      # Faithfulness·Judge 적용 규칙, 실제 평가기 생성
├── runner.py       # 질문 × 세 방식 실행 → 채점
├── security.py     # 동일 문맥 기본/방어 비교, 노출·추종 분석
├── report.py       # artifacts/·reports/ 파일 생성
└── samples/        # 가상 샘플 데이터·모의 서비스·모의 평가기
prompts/judge.txt, prompts/security_judge.txt, prompts/defense.txt
scripts/run_benchmark.py, scripts/run_security.py
tests/test_c_*.py
```

## 3. 설치·실행

검증한 버전: Python 3.13, `langfuse==5.1.0`, `ragas==0.4.3`, `openai==3.3.0`, `pytest==9.2.0`. `ragas==0.4.3`은 `langchain-community` 0.4.x에서 import 오류가 나므로 `0.3.31`로 고정한다. B가 의존성 파일을 취합할 때 LangChain 버전과 충돌하지 않는지 확인한다.

```bash
uv venv .venv
```

```bash
uv pip install --python .venv langfuse==5.1.0 ragas==0.4.3 langchain-community==0.3.31 openai==3.3.0 pytest
```

테스트와 가상 샘플 실행(API 키 불필요):

```bash
.venv/Scripts/python -m pytest -q tests
```

```bash
.venv/Scripts/python scripts/run_benchmark.py --sample
```

```bash
.venv/Scripts/python scripts/run_security.py --sample
```

샘플 결과는 `artifacts/experiments/sample-*/`와 `artifacts/sample-reports/sample-*/`에만 생성되며 Git에서 제외된다. 모든 샘플 결과에 "가상 샘플" 표시가 붙고 `data_kind=SAMPLE`이다. 샘플 실험 ID는 `sample-`로 시작해야 하며 실제 결과와 같은 실험 ID에 섞을 수 없다.

환경변수(값은 `.env`에만, 로그·결과 파일에 남기지 않음):

| 변수 | 용도 |
| --- | --- |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | 없으면 Tracer가 `disabled` |
| `LANGFUSE_BASE_URL` | Langfuse 서버 주소(기본 cloud) |
| `OPENAI_API_KEY`, 필요 시 `OPENAI_BASE_URL` | Judge·RAGAS 평가 모델 호출 |

## 4. B에게 전달하는 연결 계약

### 4.1 Langfuse 추적 — 서비스 파이프라인에서 호출

```python
from app.observability import Tracer

tracer = Tracer.from_env()          # lifespan에서 한 번. import 시 네트워크 연결 없음

trace = tracer.start_request(request_id, question=q, retrieval_mode=mode,
                             dataset_id="basic", config_hash=config_hash)
trace.record_retrieval(retrieved_candidates=candidates, final_context_chunks=final_chunks,
                       final_context_hash=context.context_hash, k=5)
for n, attempt in enumerate(attempts, 1):          # 재시도 포함 모든 시도
    trace.record_attempt(attempt=n, model="gpt-6-luna", model_input=messages,
                         model_output=raw_output, input_tokens=in_tok, output_tokens=out_tok,
                         error=error_or_none, prompt_variant="base",
                         model_parameters={...})
langfuse = trace.finish(status=status, answer=answer, reason=reason, citations=citations,
                        latency_ms=latency_ms, error_type=error_type)
# langfuse == {"langfuse_trace_id": ..., "langfuse_status": ..., "langfuse_errors": [...]}

tracer.shutdown()                   # lifespan 종료 시 전송
```

- trace ID는 `Langfuse.create_trace_id(seed=request_id)`로 만든다. 같은 request_id로 trace를 다시 계산할 수 있다.
- trace 구조: `rag-query`(질문·최종 답변) → `retrieval`(검색 후보) → `final-context`(실제 최종 문맥·해시) → `generation-attempt-N`(실제 모델 입력·출력·사용량).
- 기록 메서드는 SDK 오류를 예외로 올리지 않고 `langfuse_status="sdk_error"`와 오류 타입을 남긴다. 서비스 응답은 계속된다.
- `langfuse_status` 값: `disabled`(키 없음) / `sdk_error` / `recorded_unverified`(기록 생성, 저장 미확인). SDK의 `flush()`는 전송 실패를 알려주지 않으므로 `flush()` 결과도 `flushed_unverified`에 그친다. 저장 확인은 `tracer.verify_trace(trace_id)`(API 조회, 수집 지연을 고려해 최대 60초 재시도)로 한다.
- 미확인 토큰은 `None`으로 넘긴다. 0으로 채우지 않는다.

### 4.2 RunRecord — 일반 평가 어댑터가 반환하는 실행 기록

평가 실행기는 다음 형태의 어댑터를 호출한다. 정답·근거·expected_action은 전달하지 않는다.

```python
def adapter(question: str, retrieval_mode: str, request_id: str) -> dict: ...
```

설계의 `answer_query(request, resources, request_id)`를 감싸 실행 기록을 반환하면 된다. HTTP API로 감쌀 경우에도 요청 ID로 서버의 내부 실행 기록(최종 문맥·서버 측 시간)을 읽어 같은 형태로 반환한다. `experiment_id`, `question_id`는 실행기가 채운다. Tracer를 어댑터의 `tracer` 속성으로 두면 실행 후 실행기가 `flush()`한다.

| 필드 | 타입·규칙 |
| --- | --- |
| `request_id`, `dataset_id`, `config_hash`, `question` | 비어 있지 않은 문자열 |
| `retrieval_mode` | `naive` / `hybrid` / `hybrid_rerank` |
| `retrieved_candidates` | 검색 후보 전체, 순서 유지. 청크에 `chunk_id`, `book_id`, `file_name`, `page`(1부터) 필수, 순위·점수 등 추가 필드 보존 |
| `final_context_chunks` | 실제 모델에 전달한 청크, 순서 유지. 위 필드 + `source_id`, `text`(실제 전달 텍스트) 필수 |
| `final_context_hash` | 최종 문맥 해시. 최종 문맥이 있으면 필수 |
| `status` | `answered` / `needs_clarification` / `abstained` / `error` |
| `answer`, `reason` | 정상 응답이면 `answer`는 문자열. `error`이면 `null` 가능 |
| `citations` | `source_id`, `book_id`, `file_name`, `page` 목록 |
| `latency_ms` | 서버 측 처리 시간(재시도 포함), 미확인이면 `null` |
| `attempts` | 시도별 `input_tokens`, `output_tokens`(미확인 `null`), `error` |
| `langfuse_trace_id` | `trace.finish()` 반환값, 비활성이면 `null` |
| `error_type` | `error`일 때 오류 종류(예: `model_timeout`, `invalid_model_output`) |
| `model_input` (선택) | 실제 모델 입력. 로컬 분석·보안 노출 확인에 사용 |

`status=error`이면 `retrieved_candidates`, `final_context_chunks`, `final_context_hash`를 `null`(해당 단계 전에 실패)로 둘 수 있다. `null`은 미확인, `[]`는 실제로 비어 있음이다. 계약을 어긴 기록과 어댑터 예외는 `error_type`이 `invalid_run_record` / `adapter_exception`인 실패 기록으로 저장된다.

```json
{
  "request_id": "7f3c2a9e-0d4b-4c51-9a77-2f1e6b3d8c10",
  "dataset_id": "basic",
  "retrieval_mode": "hybrid",
  "config_hash": "sha256:...",
  "question": "예약 접수번호를 받으면 예약이 확정된 건가요?",
  "retrieved_candidates": [
    {"chunk_id": "booking_guide-p3-c1", "book_id": "booking_guide", "file_name": "booking_guide.pdf",
     "page": 3, "text": "...", "rank": 1, "score": 0.031, "retriever": "hybrid"}
  ],
  "final_context_chunks": [
    {"source_id": "S1", "chunk_id": "booking_guide-p3-c1", "book_id": "booking_guide",
     "file_name": "booking_guide.pdf", "page": 3, "text": "실제 모델에 전달한 텍스트"}
  ],
  "final_context_hash": "sha256:...",
  "status": "answered",
  "answer": "접수번호는 접수 사실만 의미합니다[S1]...",
  "reason": null,
  "citations": [{"source_id": "S1", "book_id": "booking_guide", "file_name": "booking_guide.pdf", "page": 3}],
  "latency_ms": 2140,
  "attempts": [
    {"input_tokens": 2310, "output_tokens": null, "error": "timeout"},
    {"input_tokens": 2310, "output_tokens": 182, "error": null}
  ],
  "error_type": null,
  "langfuse_trace_id": "36fb82e38cd7b4e06145393b0222d9ab",
  "langfuse_status": "recorded_unverified"
}
```

위 값(파일명·문장)은 형식 예시이며 실제 Basic 자료가 아니다.

### 4.3 보안 비교 — 문맥 재사용 함수 두 개

```python
def retrieve_context(question: str, retrieval_mode: str) -> dict:
    # {"retrieved_candidates": [...], "final_context_chunks": [...]}  (청크 형식은 4.2와 같음)

def generate_with_context(question: str, final_context_chunks: list[dict], prompt_variant: str) -> dict:
    # prompt_variant: "base" | "defense". "defense"이면 업무 프롬프트에 prompts/defense.txt를 덧붙인다.
    # 반환: status, answer, reason, citations, attempts, latency_ms,
    #       model_input(실제 모델 입력, 노출 확인에 필수), model, model_parameters, error_type, request_id
```

- C는 질문마다 `retrieve_context`를 한 번만 호출하고 같은 청크(복사본)를 두 변형에 넘긴다. 호출 전후 해시를 비교해 문맥 내용·순서가 같은지, `model`·`model_parameters`가 같은지 확인한다.
- `attack_text`와 공격 문서 ID는 검색·문맥 구성에 쓰지 않고 실행 후 노출 분석에만 쓴다.
- `model_input`이 없으면 노출을 `exposure_unknown`으로 둔다. 결과는 `defended` / `attack_followed` / `not_exposed` / `generation_failed` / `exposure_unknown` / `unjudged`이며 `defended`만 방어 성공으로 센다.

## 5. 평가 규칙 요약

- **검색 지표:** 정답 집합은 `evidence_groups` 전체 `book_id`의 중복 제거, 검색 집합은 최종 문맥 상위 k개 청크의 `book_id` 중복 제거. 검색 후보 전체는 쓰지 않는다. 참고 문서 없음 → 해당 없음, 최종 문맥이 빈 목록 → 0, 서비스 실패로 최종 문맥 미기록 → 평가 불가. `k`와 실제 전달 청크 수를 함께 저장한다.
- **Faithfulness:** 질문, 생성 출력(answer + reason), 실제 최종 문맥 텍스트만 RAGAS에 넘긴다. Judge가 "사실 주장 없음"으로 판단하면 해당 없음. 주장 여부를 모르는 상태에서 RAGAS가 NaN·오류이면 평가 불가. 최종 문맥 없이 사실을 주장하면 0.
- **Judge:** 사실·조건·근거·판단 각각 0/1과 이유. 참고 정답·채점 기준은 이 단계에서만 사용한다. 서비스 실패는 판단 0, 나머지는 평가 불가. 호출·파싱 실패는 전 항목 평가 불가. 형식이 틀린 항목은 평가 불가로 두며 임의 점수로 바꾸지 않는다.
- **평균:** `scored`만 평균하고 `not_applicable`·`unavailable`은 개수로 표시한다. 0점은 평균에 포함한다.
- **운영 지표:** 정상 추가 확인·보류는 오류가 아니다. 오류율 = `error` 요청 수 / 전체 요청 수. 토큰은 재시도 포함 확인값만 더하고 완전/부분/미확인 건수를 표시한다. 평가 호출의 시간·토큰은 `summary.json`의 `grading`에 따로 둔다.

## 6. 실제 실행

```bash
.venv/Scripts/python scripts/run_benchmark.py --questions data/basic/evaluation/<질문 파일>.json --adapter <모듈>:<어댑터> --ids <ID1>,<ID2> --config configs/basic.yaml --judge-model <평가 모델> --ragas-model <평가 모델> --experiment-id basic-<날짜>-<설명> --selection-reason "<선정 이유>"
```

```bash
.venv/Scripts/python scripts/run_security.py --security-file data/basic/evaluation/<보안 파일>.json --retrieve <모듈>:retrieve_context --generate <모듈>:generate_with_context --retrieval-mode <방식> --config configs/basic.yaml --judge-model <평가 모델> --experiment-id basic-<날짜>-<설명>
```

```bash
.venv/Scripts/python scripts/run_benchmark.py --verify-langfuse <trace_id>
```

- 실행 단계는 질문 파일에서 `id`, `question`만 읽는다. 정답 파일(`--references`, 기본은 질문 파일)은 실행 기록을 `artifacts/`에 저장한 뒤 채점 단계에서 읽는다.
- `--judge-model`·`--ragas-model`을 생략하면 해당 평가를 `unavailable`("평가기 미설정")로 기록한다.
- 같은 실험 ID의 결과가 있으면 덮어쓰지 않고 중단한다. 설정·프롬프트가 바뀌면 새 실험 ID를 쓴다.

출력:

| 위치 | 파일 |
| --- | --- |
| `artifacts/experiments/<id>/` (Git 제외) | `manifest.json`, `runs.jsonl`(최종 문맥 텍스트·모델 입력 포함), `scores.jsonl`, `security_runs.jsonl` |
| `reports/<id>/` (제출) | `README.md`, `manifest.json`, `comparison.csv`, `answers.jsonl`, `scores.jsonl`, `summary.json`, `security_comparison.csv`, `security_checks.json` |

## 7. 실제 서비스 연결 후 추가 검증

1. 실제 Basic 질문·정답·보안 파일 구조를 열어 `_items()`·`evidence_groups`·`attack_text` 키가 맞는지 확인한다.
2. B의 실제 요청 한 건으로 Langfuse UI와 `--verify-langfuse`에서 `rag-query` → `retrieval` → `final-context` → `generation-attempt-N`이 보이는지 확인한다.
3. 실제 한 문항으로 Judge·RAGAS를 실행해 JSON 형식 준수율, 호출 시간, 토큰을 확인하고 대표 점수를 PDF 원문과 대조한다.
4. `gpt-6-luna`와 평가 모델이 OpenAI 호환 Chat Completions·`response_format=json_object`를 지원하는지 확인한다. 아니면 `make_openai_judge`만 교체한다.
5. B의 `final_context_hash` 계산 방식과 C의 `compute_context_hash`가 다를 수 있다. 보안 비교는 C가 다시 계산한 해시로 판단한다.
6. 서버 측 `latency_ms`가 재시도를 포함한 API 처리 시간인지, 시도별 토큰이 SDK 응답에서 실제로 채워지는지 확인한다.

[문서 목차로 돌아가기](../README.md)
