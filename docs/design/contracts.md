# 공통 계약

## HTTP

`POST /query`는 `{"question": "예약이 확정됐나요?"}` 형식의 JSON 본문을 받는다. 질문은 양끝 공백을 제거한다. 비어 있는 질문이나 `retrieval_mode`를 비롯한 추가 필드가 있으면 `422`를 반환한다. 검색 방식은 서버 설정의 `retrieval.default_mode`를 사용한다.

응답은 `request_id`, `status`, `answer`, `reason`, `citations`를 반환한다. `X-Request-ID`와 본문의 요청 ID로 로그를 연결한다.

| HTTP | 상태·조건 |
| --- | --- |
| 200 | `answered` 답변, `needs_clarification` 추가 확인, `abstained` 근거 부족·충돌로 보류 |
| 422 | 질의 요청의 입력값 또는 JSON 형식 검증 실패 |
| 501 | 서버에 설정된 검색 방식을 사용할 수 없음 |
| 502 | 모델 호출·출력 형식·출처 검증 실패 |
| 503 | 앱 자원 미준비 또는 Qdrant 요청 실패 |
| 504 | 모델 호출 시간 초과 |
| 500 | 예상 밖 처리 오류 |

`GET /health`는 `status="ok"`, `dataset`, `query_ready`를 반환한다. 자원 초기화가 실패해도 `/health`, `/docs`, `/openapi.json`은 열리며, 유효한 질의에는 `503`을 반환한다.

### 서버 오류 처리

`POST /query`의 오류 응답은 `QueryResponse`를 사용한다. `status="error"`, 빈 답변과 출처 목록, 공개 안내 문구를 반환한다.

- `/query` 라우트는 질의 서비스의 `ServiceError`에 담긴 상태 코드로 응답하고 공개 메시지를 `reason`에 넣는다. 서버 시작 중의 자원 준비 오류는 수명주기에서 준비 상태로 기록하며, 자원 미준비 시 `/query`가 `503`을 반환한다.
- FastAPI는 `query()`를 호출하기 전에 요청을 검증한다. 질의 라우터는 이 단계의 `RequestValidationError`를 받아 본문에 비어 있지 않은 질문만 입력하라는 안내를 `QueryResponse.reason`에 담아 `422`로 반환한다. 다른 라우트의 검증 오류는 FastAPI 기본 응답을 사용한다.
- 예상 밖 오류는 전역 핸들러가 `500`과 공통 서버 오류 안내로 응답한다. 요청 ID가 있는 `POST /query`는 안내를 `QueryResponse.reason`에, 다른 요청과 ID 생성 전 실패는 `detail`에 담는다. 요청 ID가 있으면 `X-Request-ID` 헤더에도 넣는다. 앱의 오류 로그에는 예외 타입과 요청 ID를 기록한다.

## 데이터 객체

타입은 [shared/contracts.py](../../shared/contracts.py)에 정의한다.

| 타입 | 필드·의미 |
| --- | --- |
| `PageDocument` | `book_id`, `file_name`, `page`, `text` |
| `Chunk` | `chunk_id`와 페이지 필드, 선택적인 원문 제목 `heading`. Basic 청크는 한 PDF 페이지 안에 있다 |
| `RetrievedChunk` | `chunk: Chunk`, `rank`, `score`, `retriever`. 본문은 `item.chunk.text` |
| `Citation` | `source_id`, `book_id`, `file_name`, `page` |
| `ContextBundle` | `text`, `final_chunks`, `source_map`, `context_hash`, `token_count`, 선택 `excluded_chunk_ids` |
| `ModelAnswer` | `status`, `answer`, `reason`, `source_ids`. `error`는 서비스가 정한다 |
| `GenerationResult` | `model_answer`, 검증한 `citations`, `attempts`, `usage` |

`book_id`는 PDF 확장자를 뺀 파일명이다. 페이지와 순위는 1부터 센다. 검색 단계별 점수는 서로 직접 비교하지 않는다. 최종 문맥 순서대로 `S1`, `S2`를 부여하고 `source_map`에서 실제 문서·페이지를 찾는다. `excluded_chunk_ids`는 토큰 예산 때문에 제외한 청크만 기록한다. `token_count`는 제목과 구분자를 포함한 최종 문자열의 로컬 추정값이다.

## 설정과 함수

[Basic 설정](../../configs/basic.yaml)은 [Settings](../../shared/settings.py)로 검증한다. 검색 기본값·후보 수·최종 `k`·문맥 예산, 모델·호출 한도, 색인·임베딩 설정을 관리한다. `Resources`는 클라이언트, 토큰 계산기, 추적기, 검색 상태와 정리 콜백을 소유한다.

| 진입점 | 호출과 반환 |
| --- | --- |
| `load_resources` | `(settings) → Resources`. `close()`로 정리 |
| `retrieve` | `(question, mode, resources, top_n=20) → list[RetrievedChunk]` |
| `build_context` | `(candidates, k, token_budget, count_tokens) → ContextBundle` |
| `generate_answer` | `(question, context, model, *, prompt_path=..., defense="", trace=None, attempt_log=None, max_attempts=1, capture_attempts=True) → GenerationResult` |
| `resolve_citations` | `(source_ids, source_map) → list[Citation]` |
| `answer_query` | `(question, resources, request_id, settings) → QueryResponse`. 서버 설정의 검색 방식 사용 |
| `record_query` | `(question, resources, request_id, settings, *, retrieval_mode=None) → dict`. 평가용 실행 기록 반환 |
| `normalize_run_record` | `(raw: Mapping) → dict` |
| `run_comparison` | `(questions, adapter, *, experiment_id, dataset_id, config_hash, modes=...) → list[dict]` |
| `grade_all` | `(records, references, *, k, judge_fn, faithfulness_fn) → list[dict]` |
| `run_security_comparison` | `(questions, retrieve_fn, generate_fn, *, experiment_id, retrieval_mode) → list[dict]` |

기능 함수는 동기 호출이다. 모델 클라이언트의 자동 재시도는 끈다. 서비스는 출력 형식·출처 검증에 실패했을 때만 같은 문맥으로 `generation_max_attempts`까지 다시 생성한다. 모델 호출 자체의 API 오류와 시간 초과는 바로 반환한다.

HTTP 질의는 추적이 켜져 있을 때만 검색·모델 시도 기록을 준비한다. 평가 경로는 추적과 별도로 실행 기록을 남긴다. 검색에 성공하고 문맥 구성에 실패하면 평가 기록에 검색 후보와 실패 상태를 남긴다.

### 평가 어댑터

- `make_query_adapter(resources, settings)`는 `adapter(question, retrieval_mode, request_id)`를 반환한다. 일반 평가는 `naive`, `hybrid`, `hybrid_rerank`를 이 어댑터로 비교한다. 어댑터는 검증한 질문과 검색 방식으로 `record_query()`를 호출한다. 서비스는 실행이 실패해도 완료한 단계의 기록을 반환한다.
- `make_security_adapters(resources, settings, *, defense)`는 `retrieve_fn(question, mode)`과 `generate_fn(question, final_context_chunks, prompt_variant)`를 반환한다. 검색과 문맥 구성은 한 번 하고 `base`와 `defense`에 같은 문맥을 전달한다. 반환 기록에는 실제 문맥·모델 입출력·해시·시도·요청 ID를 담는다.
- 평가 CLI의 `모듈:이름`은 초기화된 callable이다. 실제 실행 준비 절차는 [평가 가이드](../guides/c-module-guide.md#실제-연결)를 따른다.

## 기록 형식과 측정 범위

| 기록 | 소비자와 범위 |
| --- | --- |
| HTTP `http_request` | 운영 로그. 요청 ID, 메서드, 라우트, 상태 코드, `latency_ms`와 `latency_scope="http"`를 기록한다. 경로가 없으면 `<unmatched>`를 쓴다. 질문·헤더·쿼리 문자열·모델 원문은 제외한다 |
| `RunRecord` | 평가 입력과 결과 재현. 서비스 직접 호출은 `service`, 일반 평가 어댑터는 `in_process_adapter`, 고정 문맥 생성은 `fixed_context_generation` |
| Langfuse | 요청 ID로 질문·검색·최종 문맥·모델 시도·응답을 연결한다. 저장 확인은 [평가 가이드](../guides/c-module-guide.md#langfuse)를 따른다 |

HTTP 시간은 응답 생성까지이며 본문 전송·백그라운드 작업·클라이언트 왕복은 제외한다. HTTP 로그는 RunRecord나 평가 보고서에 자동 합쳐지지 않는다. 비교할 때는 같은 시간 범위를 사용한다.

### RunRecord

[정규화 코드](../../app/observability/records.py)는 아래 키를 요구한다. 중첩 `RetrievedChunk.chunk`를 연결부에서 평탄화하고, `final_context_chunks`는 실제 전달 순서·본문을 유지한다.

| 필수 키 | 규칙 |
| --- | --- |
| `experiment_id`, `question_id`, `request_id`, `dataset_id`, `config_hash`, `question` | 비어 있지 않은 문자열 |
| `retrieval_mode` | 기본값을 적용한 실제 검색 방식 |
| `retrieved_candidates`, `final_context_chunks` | `chunk_id`, `book_id`, `file_name`, `page`, `text`를 가진 배열. 최종 청크에 `source_id` 추가. 결과 없음은 `[]`, 단계 미도달은 `null` |
| `final_context_hash` | 최종 문맥 로그 해시. 최종 청크가 미확인이면 `null` |
| `status`, `answer`, `reason`, `citations` | API 상태와 출처 배열. 시스템 오류의 답변·이유는 `null` 허용 |
| `latency_ms` | 0 이상의 정수 또는 미측정 `null` |
| `attempts`, `langfuse_trace_id` | 시도 배열과 trace ID 또는 `null` |

후보의 `rank`·`score`·`retriever`와 제목은 보존할 수 있다. 오류에는 `error_type`과 확인 가능한 이유를 남긴다. `latency_scope`, 실제 최종 청크 수, 문맥 토큰 수, 예산으로 제외한 ID는 선택 필드다. 모델 시도가 있으면 실제 `model_context_text`와 `model_context_hash`가 필수다. 보안 분석의 루트 모델 입출력은 마지막 시도의 값을 연결한다.

### 문맥 해시

- `model_context_hash`는 `ContextBundle.context_hash`와 같다. 제목·구분자를 포함한 실제 문맥 문자열의 UTF-8 SHA-256 hex다.
- `final_context_hash`는 최종 청크에서 순서대로 `[chunk_id, source_id, text]`를 뽑아 압축 JSON(`ensure_ascii=False`, `separators=(",", ":")`)으로 직렬화한 UTF-8 SHA-256에 `sha256:`를 붙인다.
- 보안 비교에서는 각 해시가 두 프롬프트 실행 사이에서 각각 같아야 한다. 실제 모델 입력에서도 같은 문맥인지 확인한다.

### 모델 시도와 사용량

`attempts`에 각 모델 시도의 `attempt`(1부터 증가), `model`, `model_input`, `model_output`, `input_tokens`, `output_tokens`, `error`를 남긴다. 출력 전 실패나 확인되지 않은 사용량은 `null`로 기록한다. 프롬프트 변형·모델 설정은 실제 적용값을 보존한다.

`usage`는 시도별 확인 토큰만 합산한다. 상태는 `complete`, `partial`, `unknown`, `no_attempts`다. 확인값이 하나도 없으면 합계는 `null`이다. 평가기 호출은 이 서비스 사용량에 합산하지 않는다.
