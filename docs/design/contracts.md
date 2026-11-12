# 공통 계약

## HTTP

`POST /query`는 `question`과 선택 필드 `retrieval_mode`를 받는다. 질문은 양끝 공백을 제거한 뒤 빈 값이면 거부한다. 방식은 `naive`, `hybrid`, `hybrid_rerank`이며 생략하거나 `null`이면 서비스 설정을 사용한다.

응답은 `request_id`, `status`, `answer`, `reason`, `citations`를 반환한다. `X-Request-ID`와 본문의 요청 ID로 로그를 연결한다.

| HTTP | 상태·조건 |
| --- | --- |
| 200 | `answered` 답변, `needs_clarification` 추가 확인, `abstained` 근거 부족·충돌로 보류 |
| 422 | 질문이나 검색 방식 검증 실패 |
| 501 | 선택한 검색 방식의 구현을 사용할 수 없음 |
| 502 | 모델 호출·출력 형식·출처 검증 실패 |
| 503 | 앱 자원 미준비 또는 색인·검색 의존성 실패 |
| 504 | 모델 호출 시간 초과 |
| 500 | 예상 밖 처리 오류 |

오류 응답은 `status="error"`, 빈 답변과 출처 목록, 안내 문구를 반환한다. `GET /health`는 `status="ok"`, `dataset`, `query_ready`를 반환한다. 자원 초기화가 실패해도 `/health`, `/docs`, `/openapi.json`은 열리며, 유효한 질의에는 `503`을 반환한다.

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
| `GenerationResult` | `model_answer`, `attempts`, `usage` |

`book_id`는 PDF 확장자를 뺀 파일명이다. 페이지와 순위는 1부터 센다. 검색 단계별 점수는 서로 직접 비교하지 않는다. 최종 문맥 순서대로 `S1`, `S2`를 부여하고 `source_map`에서 실제 문서·페이지를 찾는다. `excluded_chunk_ids`는 토큰 예산 때문에 제외한 청크만 기록한다. `token_count`는 제목과 구분자를 포함한 최종 문자열의 로컬 추정값이다.

## 설정과 함수

[Advanced 설정](../../configs/advanced.yaml)은 [Settings](../../shared/settings.py)로 검증한다. 검색 기본값·후보 수·최종 `k`·문맥 예산, 모델·호출 한도, 색인·임베딩 설정을 관리한다. `Resources`는 클라이언트, 토큰 계산기, 추적기, 검색 상태와 정리 콜백을 소유한다.

| 진입점 | 호출과 반환 |
| --- | --- |
| `load_resources` | `(settings, *, qdrant_client=None) → Resources`. `close()`로 정리 |
| `retrieve` | `(question, mode, resources, top_n=20) → list[RetrievedChunk]` |
| `build_context` | `(candidates, k, token_budget, count_tokens) → ContextBundle` |
| `generate_answer` | `(question, context, model, *, prompt_path=..., defense="", trace=None, attempt_log=None, max_attempts=1) → GenerationResult` |
| `resolve_citations` | `(source_ids, source_map) → list[Citation]` |
| `answer_query` | `(request, resources, request_id, settings, *, run_record=None) → QueryResponse` |
| `normalize_run_record` | `(raw: Mapping) → dict` |
| `run_comparison` | `(questions, adapter, *, experiment_id, dataset_id, config_hash, modes=...) → list[dict]` |
| `grade_all` | `(records, references, *, k, judge_fn, faithfulness_fn) → list[dict]` |
| `run_security_comparison` | `(questions, retrieve_fn, generate_fn, *, experiment_id, retrieval_mode) → list[dict]` |

기능 함수는 동기 호출이다. 모델 클라이언트의 자동 재시도는 끈다. 서비스는 출력 형식·출처·지원 날짜 검증에 실패했을 때만 같은 문맥으로 `generation_max_attempts`까지 다시 생성한다. 재시도 시스템 메시지에는 허용 자료 ID와 안전한 검증 피드백을 추가하며, 모델 출력이나 오류 원문을 지시문으로 복사하지 않는다. 질문·원문 문맥은 유지하고 각 시도의 실제 입력을 기록한다. 모델 호출 자체의 API 오류와 시간 초과는 바로 반환한다.

출처 검증은 `answer`와 `reason`의 `[S#]` 표시를 모두 확인한다. `[S1, S2]`, `[S1-S2]`, `[s1]`, 닫히지 않은 `[S1`처럼 자료 ID로 보이는 잘못된 대괄호 표기는 검증 실패다. `answered`는 `source_ids`나 `reason`의 인용만으로 통과할 수 없으며 `answer` 본문에 유효한 `[S#]` 인용이 하나 이상 있어야 한다. 추가 확인·보류 응답에는 근거 없는 인용을 강제하지 않는다. 최종 문맥에 없는 ID가 있으면 검증 실패이며, 문맥에 있는 ID가 `source_ids`에서 빠졌으면 중복 없이 보충해 응답의 파일명·쪽수와 연결한다. 이 검사는 출처의 존재 여부를 확인하며, 해당 문서가 주장을 뒷받침하는지 채점하는 평가를 대신하지 않는다.

### 평가 어댑터

- `make_query_adapter(resources, settings)`는 `adapter(question, retrieval_mode, request_id)`를 반환한다. 어댑터는 `answer_query()`를 직접 호출하고 실패도 평탄한 실행 기록으로 반환한다.
- `make_security_adapters(resources, settings, *, defense)`는 `retrieve_fn(question, mode)`과 `generate_fn(question, final_context_chunks, prompt_variant)`를 반환한다. 검색과 문맥 구성은 한 번 하고 `base`와 `defense`에 같은 문맥을 전달한다. 반환 기록에는 실제 문맥·모델 입출력·해시·시도·요청 ID를 담는다.
- 평가 모듈이 `configure_evaluation(config_path: Path)`를 제공하면 CLI가 선택한 설정 경로(`--config` → `ADEN_CONFIG` → `configs/advanced.yaml`)를 자원 준비 전에 전달한다. 표준 bootstrap은 지정 설정을 일반·보안 어댑터가 공유하고, 활성 자원의 다른 설정 경로나 같은 설정 파일의 내용 변경을 거부한다. 훅이 없는 사용자 정의 모듈은 자체 설정을 사용한다.
- 평가 CLI의 `모듈:이름`은 초기화된 callable이다. 실제 실행 준비 절차는 [평가 가이드](../guides/c-module-guide.md#실제-연결)를 따른다.
- `app.service.bootstrap`이 그 callable을 제공한다. `query_adapter`(`--adapter`), `retrieve_context`(`--retrieve`), `generate_with_context`(`--generate`)이며, import 때가 아니라 첫 사용 때 프로젝트 루트 `.env`(기존 환경변수 우선)·선택 설정 파일(미지정 시 `configs/advanced.yaml`)·`load_resources()`를 한 번 준비하고 프로세스 종료 때 `Resources.close()`로 해제한다. `query_adapter`는 실행기의 flush·저장 확인용 `tracer` 속성을 노출하고, 보안 두 callable은 같은 어댑터 쌍과 `app/generation/prompts/defense.txt`를 사용한다.

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

HTTP 요청을 서버 trace로 복원한 기록(`app.evaluation.scripts.http_records`)은 `latency_ms`에 서버 처리 시간(`service`)을 두고 `http_experiment_id`, `http_status`, `http_outcome`, `http_latency_ms`(`http_client`), `prompt_hash`, `k`를 더한다. 후보의 `rank`·`score`·`retriever`와 제목은 보존할 수 있다. 오류에는 `error_type`과 확인 가능한 이유를 남긴다. `latency_scope`, 실제 최종 청크 수, 문맥 토큰 수, 예산으로 제외한 ID는 선택 필드다. 모델 시도가 있으면 실제 `model_context_text`와 `model_context_hash`가 필수다. 보안 분석의 루트 모델 입출력은 마지막 시도의 값을 연결한다.

### 문맥 해시

- `model_context_hash`는 `ContextBundle.context_hash`와 같다. 제목·구분자를 포함한 실제 문맥 문자열의 UTF-8 SHA-256 hex다.
- `final_context_hash`는 최종 청크에서 순서대로 `[chunk_id, source_id, text]`를 뽑아 압축 JSON(`ensure_ascii=False`, `separators=(",", ":")`)으로 직렬화한 UTF-8 SHA-256에 `sha256:`를 붙인다.
- 보안 비교에서는 각 해시가 두 프롬프트 실행 사이에서 각각 같아야 한다. 실제 모델 입력에서도 같은 문맥인지 확인한다.

### 모델 시도와 사용량

`attempts`에 각 모델 시도의 `attempt`(1부터 증가), `model`, `model_input`, `model_output`, `input_tokens`, `output_tokens`, `error`를 남긴다. 출력 전 실패나 확인되지 않은 사용량은 `null`로 기록한다. 프롬프트 변형·모델 설정은 실제 적용값을 보존한다.

`calendar_reference`는 원문으로 계산한 로컬 산입 날짜 보조표이며 적용하지 않으면 빈 문자열이다. 시스템 메시지에만 추가하고, `model_context_text`·`model_context_hash`는 사용자 메시지로 전달한 원문 문맥을 계속 나타낸다. 조회 시각(KST 분 단위)과 대상 계약이 하나로 일치하고, 해당 시각의 승인 대조에 `추가 변경 없음; 최초 계약 적용`이 명시되며 최초 계약표의 기본 무료일수가 비용별로 유일할 때는 무료·유료 날짜도 계산한다. 이 경우 answered 본문의 `비용명 무료 날짜: ...`·`비용명 유료 날짜: ...` 행을 필수로 검사한다. 명시적인 비용 한정·제외·전체 범위 정정은 마지막 지정을 날짜 보조표와 검사에도 적용한다. 짧은 목록과 양끝을 포함한 YYYY-MM-DD~YYYY-MM-DD 구간을 허용하며, 구간에 `(월~금)`이 있으면 계약의 월~금 규칙을 확인한 뒤 주말을 제외하고 펼친다. 달력일 계약에는 `(월~금)`을 허용하지 않는다. 긴 목록은 구간으로 표시해 출력량을 줄이고, 원래 계산된 전체 날짜와 펼친 결과의 순서·중복·배정을 대조한다. 날짜·요일·순서·중복·누락·무료/유료 배정이 다르면 재시도하고 한도 소진 시 502로 중단한다. 확인 질문·보류는 없는 날짜 행을 강제하지 않지만 표시한 행은 검사한다. 그 밖의 승인 변경·금액 검산 결과나 별도의 PDF 출처를 뜻하지 않는다. 이 검사는 정해진 날짜 행을 확인하며 다른 자유 서술 전체의 의미를 판정하지 않는다. 보조표 생성 자체에는 외부 호출이 없다.

`usage`는 시도별 확인 토큰만 합산한다. 상태는 `complete`, `partial`, `unknown`, `no_attempts`다. 확인값이 하나도 없으면 합계는 `null`이다. 평가기 호출은 이 서비스 사용량에 합산하지 않는다.

### 로컬 B·C 연결

기본 설정은 `configs/advanced.yaml`이다. `Resources.evidence_provider(question, mode)`가 있으면 `(후보 목록, ContextBundle)`을 받아 최종 문맥을 보존하고, `(후보 목록, None)`이면 공통 문맥 구성기로 최종 문맥을 만든다. provider가 없으면 기존 검색과 공통 문맥 구성기를 호출한다. 제공된 문맥도 설정의 k·토큰 한도를 넘을 수 없으며, 넘으면 `503`이다. 최종 문맥은 `build_context`의 자료 블록 형식과 같은 본문을 사용한다. 모델 호출 전에 최종 청크 순서의 자료 ID·문서·페이지와 `source_map`, 청크로 구성한 본문, 본문의 UTF-8 SHA-256, 현재 토큰 계산기로 센 `token_count`를 대조한다. 하나라도 다르면 문맥을 고치거나 재선택하지 않고 `503`으로 중단한다. 제공자는 같은 토큰 계산기를 사용해야 한다. 이 경로는 내부 실행 코드용이며 HTTP 입력 계약은 바뀌지 않는다.

`load_generation_resources(settings, *, evidence_provider)`는 검색·임베더 없이 생성·추적 자원을 준비한다. `create_app(resources_loader=...)`와 기존 `ServiceBootstrap(resources_loader=...)`로 같은 로더를 사용할 수 있다. 메모리 Qdrant는 같은 실행에서 적재한 클라이언트를 `load_resources(..., qdrant_client=...)`로 넘기며 반환 자원이 종료 책임을 맡는다. API와 표준 bootstrap의 기본 로더 `load_resources(settings)`는 환경변수 `ADEN_QDRANT_CLIENT`·`ADEN_EVIDENCE_PROVIDER`(`모듈:이름`, `settings`를 받는 팩터리)로 같은 연결을 받고, 설정 파일은 `ADEN_CONFIG`로 고른다. 상세 실행 범위는 [로컬 B·C 안내](../guides/local-bc.md)를 따른다.

모델 시도에는 실제 체인 호출의 `latency_ms`를 추가한다. `provided_context`는 provider(주입한 후보 또는 후보·문맥) 사용 여부다. 최종 문맥의 토큰 수와 예산 제외 ID는 Langfuse에도 기록한다. 평가 진단과 점수 게시는 별도 채점 단계에서만 수행한다.

채점 기록에는 `evidence_diagnostics`와 Judge의 검산 진단 `calculation_checks`(`status`, `reason`, 대상별 `items`)를, 점수를 게시한 경우 `langfuse_score_status_counts`와 보낸 점수의 `langfuse_score_ids`(`<평가 세트>.<지표>` 이름 → ID)를 남긴다. manifest의 `langfuse_scores`에는 `evaluation_set`, 게시한 지표군 `groups`, 채점 조건 해시 `evaluation_hash`와 게시 상태를 남긴다. 실행 기록의 `langfuse_verification`은 `verified`, `partial`, `not_found`, `failed` 또는 추적 비활성의 `null`이다. 뜻과 게시 규칙은 [평가 가이드](../guides/c-module-guide.md#langfuse)를 따른다.
