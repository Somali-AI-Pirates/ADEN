# C 모듈 가이드 — 실행 기록·평가·결과 비교

작성일: 2026-11-05 · 수정일: 2026-11-06. 담당: C(실행 기록·평가). 기준 문서: [공통 입출력·호출 계약](../design/contracts.md) · [시스템 설계](../design/architecture.md) · [Basic 당일 작업 계획](../plans/basic-day-plan-2026-11-05.md) · [Git 협업 규칙](git-workflow.md)

필드·함수 시그니처의 기준은 [공통 계약](../design/contracts.md)이다. 이 문서는 C 패키지의 사용법과, 계약을 C 코드가 어떻게 검증하는지를 설명한다.

## 1. 구현 상태

| 구분 | 상태 |
| --- | --- |
| 실행 기록 검증·정규화, Langfuse 추적, Hit/Recall, Faithfulness·Judge 처리 규칙, 일반 평가 실행기, 보안 비교, 결과 파일 | 구현, 가상 샘플·모의 클라이언트로 테스트 통과 |
| 실제 Langfuse 서버 저장 | **미검증.** SDK 호출 경로는 연결 불가 주소로 실행해 확인(전송 실패가 `failed`로 표시됨) |
| 실제 RAGAS·Judge 점수 | **미검증.** 호출 경로가 네트워크 단계까지 도달하는 것만 확인 |
| A/B 실제 서비스 연결, 실제 Basic 평가 파일 구조 | **미확인.** 실제 파일을 본 뒤 `member_Damsebi/evaluation/dataset.py`의 `_items()`를 맞춘다 |

`shared/contracts.py`는 아직 main에 없다. C는 공통 타입을 import하지 않고, B 연결부가 평탄화한 JSON/Mapping 로그를 받아 검증한다.

## 2. 파일

```text
member_Damsebi/
├── observability/
│   ├── records.py      # RunRecord 검증·정규화, final_context_hash 계산, 사용량 합계
│   └── tracing.py      # Tracer / RequestTrace (Langfuse)
├── evaluation/
│   ├── dataset.py      # 질문(id·question만) / 참고 정답 로드, evidence book_id 수집
│   ├── metrics.py      # Hit@k·Recall@k, 점수 표현, 평균, 운영 지표
│   ├── graders.py      # Faithfulness·Judge 적용 규칙, 실제 평가기 생성
│   ├── runner.py       # 질문 × 세 방식 실행 → 채점
│   ├── security.py     # 동일 문맥 기본/방어 비교, 노출·추종 분석
│   ├── report.py       # artifacts/·reports/ 파일 생성
│   └── samples/        # 가상 샘플 데이터·모의 서비스·모의 평가기
├── prompts/            # judge.txt, defense.txt, security_judge.txt(보안 답변 판정용)
├── scripts/            # run_benchmark.py, run_security.py
└── tests/              # test_c_*.py
```

## 3. 설치·실행

검증한 버전: Python 3.13, `langfuse==5.1.0`, `ragas==0.4.3`, `openai==3.3.0`, `pytest==9.2.0`. `ragas==0.4.3`은 `langchain-community` 0.4.x에서 import 오류가 나므로 `0.3.31`로 고정한다. B가 의존성 파일을 취합할 때 LangChain 버전과 충돌하지 않는지 확인한다.

```bash
uv venv .venv
```

```bash
uv pip install --python .venv langfuse==5.1.0 ragas==0.4.3 langchain-community==0.3.31 openai==3.3.0 pytest
```

모든 명령은 저장소 루트에서 모듈 방식으로 실행한다. 테스트와 가상 샘플 실행에는 API 키가 필요 없다.

```bash
.venv/Scripts/python -m pytest -q member_Damsebi/tests
```

```bash
.venv/Scripts/python -m member_Damsebi.scripts.run_benchmark --sample
```

```bash
.venv/Scripts/python -m member_Damsebi.scripts.run_security --sample
```

샘플 결과는 `artifacts/experiments/sample-*/`와 `artifacts/sample-reports/sample-*/`에만 생성되며 Git에서 제외된다. 모든 샘플 결과에 "가상 샘플" 표시가 붙고 `data_kind=SAMPLE`이다. 샘플 실험 ID는 `sample-`로 시작해야 하며 실제 결과와 같은 실험 ID에 섞을 수 없다.

환경변수(값은 `.env`에만, 로그·결과 파일에 남기지 않음):

| 변수 | 용도 |
| --- | --- |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | 없으면 Tracer가 `disabled` |
| `LANGFUSE_BASE_URL` | Langfuse 서버 주소(기본 cloud) |
| `OPENAI_API_KEY`, 필요 시 `OPENAI_BASE_URL` | Judge·RAGAS 평가 모델 호출 |

## 4. B 연결 방법

### 4.1 Langfuse 추적

B의 `Resources.tracer`로 하나를 두고 요청마다 동기 호출한다. B의 임시 추적 구현은 통합 시 이 모듈로 교체한다.

```python
from member_Damsebi.observability import Tracer, compute_context_hash

tracer = Tracer.from_env()          # load_resources에서 한 번. import 시 네트워크 연결 없음

trace = tracer.start_request(request_id, question=q, retrieval_mode=mode,
                             dataset_id=settings.dataset, config_hash=config_hash)
trace.record_retrieval(retrieved_candidates=flat_candidates,         # 평탄 로그 청크
                       final_context_chunks=flat_final,
                       final_context_hash=compute_context_hash(flat_final),
                       k=settings.retrieval.k,
                       model_context_text=context.text,             # ContextBundle.text
                       model_context_hash=context.context_hash)
for attempt in attempt_records:                                     # 재시도·재생성 포함 모든 시도
    trace.record_attempt(**attempt)                                 # AttemptRecord 그대로
langfuse = trace.finish(status=status, answer=answer, reason=reason, citations=citations,
                        latency_ms=latency_ms, error_type=error_type)
# langfuse == {"langfuse_trace_id": ..., "langfuse_status": ..., "langfuse_errors": [...]}

tracer.shutdown()                   # Resources.close()에서 전송
```

- trace ID는 `Langfuse.create_trace_id(seed=request_id)`로 만든다. 같은 request_id로 trace를 다시 계산할 수 있다.
- trace 구조: `rag-query`(질문·최종 답변) → `retrieval`(검색 후보) → `final-context`(최종 청크·문맥 문자열·두 해시) → `generation-attempt-N`(실제 모델 입력·출력·사용량).
- 기록 메서드는 SDK 오류를 예외로 올리지 않고 `langfuse_status="sdk_error"`와 오류 타입을 남긴다. 기록 실패를 `abstained`로 바꾸지 않으며 서비스 응답은 계속된다.
- `langfuse_status` 값: `disabled`(키 없음) / `sdk_error` / `recorded_unverified`(기록 생성, 저장 미확인). SDK의 `flush()`는 전송 실패를 알려주지 않으므로 저장 확인은 `tracer.verify_trace(trace_id)`가 API에서 관측을 조회해 판단한다.

### 4.2 일반 평가 어댑터와 RunRecord

```python
def adapter(question: str, retrieval_mode: str, request_id: str) -> dict: ...
```

B의 `answer_query(request, resources, request_id, settings)` 결과와 같은 request_id의 내부 기록을 합쳐 [공통 계약 3절](../design/contracts.md)의 RunRecord를 반환한다. `experiment_id`, `question_id`는 실행기가 붙인다. 어댑터에 `tracer` 속성(C의 `Tracer`)이 있으면 실행기가 실행 후 `flush()`하고 각 trace의 저장 여부를 조회한다. 정답·근거·expected_action은 어댑터에 전달하지 않는다.

`normalize_run_record()`가 거부하는 경우(실패 기록 `invalid_run_record`와 사유로 저장, 점수로 바꾸지 않음):

| 검사 | 거부 조건 |
| --- | --- |
| 청크 형식 | `RetrievedChunk`처럼 `chunk` 아래에 중첩된 객체. 후보는 `chunk_id`·`book_id`·`file_name`·`page`·`text`, 최종 청크는 여기에 `source_id`까지 필수, `page`는 1 이상 |
| 로그 문맥 해시 | `final_context_hash`가 `compute_context_hash(final_context_chunks)`와 다름. ContextBundle의 `context_hash`를 복사한 경우가 여기에 걸린다 |
| 모델 문맥 | 모델을 호출한 기록(attempts가 있음)에 `model_context_text`·`model_context_hash`가 없음, 또는 해시가 문자열의 SHA-256 hex가 아님 |
| AttemptRecord | `attempt`가 1부터 차례로 증가하지 않음, `model`·`model_input` 누락, 토큰이 음수 |
| 상태 | 정상 응답(`answered`/`needs_clarification`/`abstained`)의 `answer`·`reason`이 문자열이 아님 |
| null | 오류가 아닌데 후보·최종 청크가 `null`, 최종 청크가 `null`인데 해시가 있음 |

정규화 결과에는 `usage`(attempts 합계, 미확인은 null)를 C가 계산해 넣는다. 루트 `model_input`/`model_output`이 없으면 마지막 시도의 값을 연결한다. 생성 단계 전에 끝난 기록(attempts 없음)은 모델 문맥 필드가 `null`이어도 된다. 어댑터 예외는 `adapter_exception` 실패 기록으로 남는다.

### 4.3 보안 비교 어댑터

```python
def retrieve_context(question: str, retrieval_mode: str) -> dict:
    # {"retrieved_candidates": [...평탄 청크], "final_context_chunks": [...평탄 청크]}
    # 어댑터가 이때 만든 ContextBundle을 보관한다.

def generate_with_context(question: str, final_context_chunks: list[dict], prompt_variant: str) -> dict:
    # 보관한 ContextBundle로 generate_answer(..., defense="" 또는 member_Damsebi/prompts/defense.txt 내용) 호출.
    # 반환: status, answer, reason, citations, attempts(AttemptRecord), latency_ms,
    #       model_context_text, model_context_hash, request_id, error_type
```

- C는 질문마다 `retrieve_context`를 한 번만 호출하고 같은 청크(복사본)를 두 변형에 넘긴다.
- 질문별 확인(`security_checks.json`): 로그 청크 해시·순서(`final_context_identical`), 문맥 문자열과 그 해시(`model_context_identical`), 실제 모델 입력에 그 문맥이 그대로 들어갔는지(`context_in_model_input`), `model`·`model_parameters`(`generation_settings_identical`). 값은 True/False/None(확인 불가)이며 False가 있으면 스크립트가 실패로 끝난다.
- 루트 `model_input`이 없으면 마지막 시도의 값을 쓴다. 모델 입력이 없으면 노출은 `exposure_unknown`이다.
- `attack_text`와 공격 문서 ID는 검색·문맥 구성에 쓰지 않고 실행 후 노출 분석에만 쓴다. 결과는 `defended` / `attack_followed` / `not_exposed` / `generation_failed` / `exposure_unknown` / `unjudged`이며 `defended`만 방어 성공으로 센다.

## 5. 평가 규칙 요약

- **검색 지표:** 정답 집합은 `evidence_groups` 전체 `book_id`의 중복 제거, 검색 집합은 최종 문맥 상위 k개 청크의 `book_id` 중복 제거. 검색 후보 전체는 쓰지 않는다. 참고 문서 없음 → 해당 없음, 최종 문맥이 빈 목록 → 0, 서비스 실패로 최종 문맥 미기록 → 평가 불가. `k`와 실제 전달 청크 수를 함께 저장한다.
- **Faithfulness:** 질문, 생성 출력(answer + reason), 실제 최종 청크 텍스트만 RAGAS에 넘긴다. Judge가 "사실 주장 없음"으로 판단하면 해당 없음. 주장 여부를 모르는 상태에서 RAGAS가 NaN·오류이면 평가 불가. 최종 문맥 없이 사실을 주장하면 0.
- **Judge:** 사실·조건·근거·판단 각각 0/1과 이유. 참고 정답·채점 기준은 이 단계에서만 사용한다. 서비스 실패는 판단 0, 나머지는 평가 불가. 호출·파싱 실패는 전 항목 평가 불가. 형식이 틀린 항목은 평가 불가로 두며 임의 점수로 바꾸지 않는다.
- **평균:** `scored`만 평균하고 `not_applicable`·`unavailable`은 개수로 표시한다. 0점은 평균에 포함한다.
- **운영 지표:** 정상 추가 확인·보류는 오류가 아니다. 오류율 = `error` 요청 수 / 전체 요청 수. 토큰은 재시도 포함 확인값만 더하고 완전/부분/미확인 건수를 표시한다. 평가 호출의 시간·토큰은 `summary.json`의 `grading`에 따로 둔다.

## 6. 실제 실행

```bash
.venv/Scripts/python -m member_Damsebi.scripts.run_benchmark --questions data/basic/evaluation/<질문 파일>.json --adapter <모듈>:<어댑터> --ids <ID1>,<ID2> --config configs/basic.yaml --judge-model <평가 모델> --ragas-model <평가 모델> --experiment-id basic-<날짜>-<설명> --selection-reason "<선정 이유>"
```

```bash
.venv/Scripts/python -m member_Damsebi.scripts.run_security --security-file data/basic/evaluation/<보안 파일>.json --retrieve <모듈>:retrieve_context --generate <모듈>:generate_with_context --retrieval-mode <방식> --config configs/basic.yaml --judge-model <평가 모델> --experiment-id basic-<날짜>-<설명>
```

```bash
.venv/Scripts/python -m member_Damsebi.scripts.run_benchmark --verify-langfuse <trace_id>
```

- 실행 단계는 질문 파일에서 `id`, `question`만 읽는다. 정답 파일(`--references`, 기본은 질문 파일)은 실행 기록을 `artifacts/`에 저장한 뒤 채점 단계에서 읽는다.
- 일반 평가는 실행 후 각 trace를 Langfuse API로 조회해 `langfuse_verification`(`verified` / `not_found` / `failed`)을 기록한다. 전체 대기 한도는 `--langfuse-verify-timeout`(기본 90초)이다.
- `--judge-model`·`--ragas-model`을 생략하면 해당 평가를 `unavailable`("평가기 미설정")로 기록한다.
- 같은 실험 ID의 결과가 있으면 덮어쓰지 않고 중단한다. 설정·프롬프트가 바뀌면 새 실험 ID를 쓴다.

출력:

| 위치 | 파일 |
| --- | --- |
| `artifacts/experiments/<id>/` (Git 제외) | `manifest.json`, `runs.jsonl`(최종 문맥 텍스트·모델 입력 포함), `scores.jsonl`, `security_runs.jsonl` |
| `reports/<id>/` (제출) | `README.md`, `manifest.json`, `comparison.csv`, `answers.jsonl`, `scores.jsonl`, `summary.json`, `security_comparison.csv`, `security_checks.json` |

## 7. 실제 서비스 연결 후 추가 검증

1. 실제 Basic 질문·정답·보안 파일 구조를 열어 `_items()`·`evidence_groups`·`attack_text` 키가 맞는지 확인한다.
2. B의 연결부로 가상 한 건의 `RetrievedChunk → ContextBundle → RunRecord` 변환이 `normalize_run_record()`를 통과하는지 확인한다.
3. B의 실제 요청 한 건으로 Langfuse UI와 `--verify-langfuse`에서 `rag-query` → `retrieval` → `final-context` → `generation-attempt-N`이 보이는지 확인한다.
4. 실제 한 문항으로 Judge·RAGAS를 실행해 JSON 형식 준수율, 호출 시간, 토큰을 확인하고 대표 점수를 PDF 원문과 대조한다.
5. `gpt-6-luna`와 평가 모델이 OpenAI 호환 Chat Completions·`response_format=json_object`를 지원하는지 확인한다. 아니면 `make_openai_judge`만 교체한다.
6. 서버 측 `latency_ms`가 API 수신부터 응답 직전까지(재시도 포함)인지, 시도별 토큰이 SDK 응답에서 실제로 채워지는지 확인한다.

[문서 목차로 돌아가기](../README.md)
