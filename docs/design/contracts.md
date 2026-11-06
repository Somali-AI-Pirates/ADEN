# 공통 입출력·호출 계약

작성일: 2026-11-06 · 수정일: 2026-11-06.

관련 문서: [문서 목차](../README.md) · [시스템 설계](architecture.md) · [Git 협업 규칙](../guides/git-workflow.md)

이 문서는 Basic의 모듈 연결 기준이다. Python 공통 데이터 타입은 구현 시 `shared/contracts.py`에서 정의하고 이 문서의 예시와 맞춘다. SDK 클라이언트를 포함하는 실행 자원은 B가 `member_b/service/resources.py`에서 관리한다. C의 로그 검증·직렬화 코드는 `member_c/observability/records.py`가 맡는다. 현재 main에는 문서만 있으며, 아래 계약을 실제 코드에 적용한 것은 아니다.

## 1. 패키지명과 의존 방향

Basic의 import 경로는 `member_a`, `member_b`, `member_c`, `shared`로 고정한다. 실제 담당자 이름은 문서의 담당표로 연결한다. 실제 이름을 패키지명에 사용하기로 변경하면 팀 전체가 경로·import·패키지 설정·실행 명령을 같은 변경에서 맞춘다. 각자가 독립적으로 이름을 바꾸지 않는다.

- A: `member_a/ingestion`, `indexing`, `retrieval`.
- B: `member_b/api`, `generation`, `service`; 공통 파일 변경 취합.
- C: `member_c/observability`, `evaluation`; 평가·보안 실행 스크립트.
- `shared/`는 사람별 패키지를 import하지 않는다. 공통 타입 정의 시 모델·SDK 초기화를 수행하지 않는다.
- B가 A의 검색과 C의 추적 함수를 호출한다. C 평가기는 서비스 호출 함수를 주입받으며 B의 앱을 직접 초기화하지 않는다.

## 2. A·B가 전달하는 데이터 객체

아래 필드는 별도 표시가 없으면 필수다. Python에서 dataclass 또는 Pydantic을 사용하더라도 JSON으로 변환했을 때의 구조는 동일해야 한다. `page`·`rank`는 1부터 시작하고, 확인되지 않은 문서 속성을 만들어 넣지 않는다.

| 타입 | 필드 | 제약 |
| --- | --- | --- |
| `PageDocument` | `book_id`, `file_name`, `page`, `text` | 원본 PDF 이름·페이지 보존 |
| `Chunk` | `chunk_id`, `book_id`, `file_name`, `page`, `text`; 선택 `heading=""` | 청크 본문은 `text`; heading은 원문에서 확인된 제목만 사용 |
| `RetrievedChunk` | `chunk: Chunk`, `rank: int`, `score: float`, `retriever: str` | **중첩 구조**. 본문은 `item.chunk.text`로 접근 |
| `Citation` | `source_id`, `book_id`, `file_name`, `page` | `source_id`는 이번 요청 안의 `S1`, `S2` 등 |
| `ContextBundle` | `text`, `final_chunks: list[RetrievedChunk]`, `source_map: dict[str, Citation]`, `context_hash`, `token_count: int` | 실제 모델에 전달할 최종 문맥과 출처 매핑 |
| `ModelAnswer` | `status`, `answer`, `reason`, `source_ids: list[str]` | 모델 상태는 `answered`, `needs_clarification`, `abstained` |
| `GenerationResult` | `model_answer: ModelAnswer`, `attempts: list[AttemptRecord]`, `usage` | 성공한 호출도 실제 시도·사용량 기록 보존 |
| `QueryRequest` | `question: str`, 선택 `retrieval_mode` | 질문 공백 제거 후 빈 문자열 거부; mode 생략 시 서버 기본값 |
| `QueryResponse` | `request_id`, `status`, `answer`, `reason`, `citations: list[Citation]` | 시스템 실패에는 코드가 `error` 상태를 사용 |

`retrieval_mode`는 `naive`, `hybrid`, `hybrid_rerank` 중 하나다. `retriever`는 점수를 낸 단계의 이름으로 `dense`, `hybrid`, `reranker` 등을 기록할 수 있다. 검색 방식이 다른 점수의 크기를 직접 비교하지 않는다.

`RetrievedChunk` 예시다. 모든 DEMO 문서·수치·답변은 인터페이스 설명용 가상 자료다.

```json
{
  "chunk": {
    "chunk_id": "DEMO_BOOK-p1-c1",
    "book_id": "DEMO_BOOK",
    "file_name": "DEMO_BOOK.pdf",
    "page": 1,
    "text": "예약 접수는 예약 확정이 아닙니다.",
    "heading": "예약 안내"
  },
  "rank": 1,
  "score": 0.8,
  "retriever": "dense"
}
```

`ContextBundle.final_chunks`에도 같은 중첩 구조를 사용한다. 최종 선택 순서의 첫 청크에는 `S1`, 두 번째에는 `S2`를 부여한다. `source_map["S1"]`의 문서·페이지는 첫 청크와 일치해야 한다. 검색 순위와 최종 출처 번호가 항상 같다고 가정하지 않는다.

`ContextBundle.context_hash`는 **실제 문맥 문자열 `text`의 UTF-8 바이트에 대한 SHA-256 hex**다. 제목·문서명·페이지 헤더·줄바꿈까지 포함하고 `sha256:` 접두어는 붙이지 않는다. `token_count`는 이 문자열 전체를 `Resources.count_tokens`로 계산한 값이다. 토크나이저가 근사값이면 설정에 표시하고 실제 사용 토큰과 구분한다.

## 3. C가 받는 직렬화된 실행 기록

**공통 객체와 로그 청크는 서로 다른 형식이다.** A·B는 `RetrievedChunk.chunk`를 사용하고, C의 `RunRecord`는 JSON 분석을 위해 청크 필드를 평탄하게 저장한다. B의 연결부에서 객체를 로그 형식으로 변환한 뒤 C의 검증 함수에 넘긴다. C는 들어온 중첩 객체를 평탄한 로그로 간주하지 않는다.

| 로그 청크 | 필수 필드 | 추가 필드 |
| --- | --- | --- |
| `retrieved_candidates[]` | `chunk_id`, `book_id`, `file_name`, `page`, `text` | `heading`, `rank`, `score`, `retriever` 보존 가능 |
| `final_context_chunks[]` | 위 필드 + `source_id` | 실제 전달한 순서와 본문 유지 |

변환 규칙은 다음과 같다.

1. 후보는 `RetrievedChunk.chunk`를 펼치고 `rank`, `score`, `retriever`를 덧붙인다.
2. 최종 청크도 `chunk`를 펼치고 최종 선택 순서에 대응하는 `source_id`를 덧붙인다.
3. `text`에는 실제 전달한 청크 내용을 보존한다. 검색용 가공 문자열을 대신 넣지 않는다.
4. `model_context_text`에는 헤더·구분자를 포함한 `ContextBundle.text` 원문을 별도로 기록한다.
5. 최종 문맥에 없는 ID를 인용하면 출력 검증 오류다. 페이지·출처를 추측해 보충하지 않는다.

정규화 후 로그의 예시다. 실험·질문 ID도 가상 값이다. 문맥 해시는 아래 알고리즘으로 예시 텍스트에서 계산한 값이다.

```json
{
  "experiment_id": "demo-basic",
  "question_id": "DEMO-Q1",
  "request_id": "demo-request-001",
  "dataset_id": "basic_shipping",
  "retrieval_mode": "naive",
  "config_hash": "demo-config-hash",
  "question": "예약 접수만으로 확정되나요?",
  "retrieved_candidates": [
    {
      "chunk_id": "DEMO_BOOK-p1-c1",
      "book_id": "DEMO_BOOK",
      "file_name": "DEMO_BOOK.pdf",
      "page": 1,
      "text": "예약 접수는 예약 확정이 아닙니다.",
      "rank": 1,
      "score": 0.8,
      "retriever": "dense",
      "heading": "예약 안내"
    }
  ],
  "final_context_chunks": [
    {
      "chunk_id": "DEMO_BOOK-p1-c1",
      "book_id": "DEMO_BOOK",
      "file_name": "DEMO_BOOK.pdf",
      "page": 1,
      "source_id": "S1",
      "text": "예약 접수는 예약 확정이 아닙니다.",
      "heading": "예약 안내"
    }
  ],
  "final_context_hash": "sha256:c441bab8d652890ff51af84c77cf90a0a8b67b036fefb68cdd5aa42aedc43bf1",
  "model_context_text": "[S1]\n문서 ID: DEMO_BOOK\n문서명: DEMO_BOOK.pdf\nPDF 페이지: 1\n제목: 예약 안내\n내용: 예약 접수는 예약 확정이 아닙니다.",
  "model_context_hash": "84b0cedaa7745a1f633848a3960e770c131163b5b4a586248f0cd4a3b0f68cff",
  "status": "answered",
  "answer": "접수만으로 확정되지는 않습니다. [S1]",
  "reason": "안내에서 접수와 확정을 구분합니다.",
  "citations": [
    {
      "source_id": "S1",
      "book_id": "DEMO_BOOK",
      "file_name": "DEMO_BOOK.pdf",
      "page": 1
    }
  ],
  "latency_ms": null,
  "attempts": [],
  "usage": {
    "input_tokens": null,
    "output_tokens": null,
    "status": "no_attempts"
  },
  "langfuse_trace_id": null,
  "error_type": null
}
```

이 예시의 `latency_ms`, 사용량, trace ID는 미측정이며 실제 실행의 증거가 아니다.

### 필수 키와 null의 처리

| 필드 | 타입·규칙 |
| --- | --- |
| `experiment_id`, `question_id`, `request_id`, `dataset_id`, `config_hash`, `question` | 비어 있지 않은 문자열. 일반 서비스 요청의 평가용 ID가 없으면 연결부가 `live`/요청 ID 같은 이름표를 사용하며 모델 입력에는 넣지 않는다 |
| `retrieval_mode` | 위 세 값 중 하나; 기본값을 실제 적용한 뒤 기록 |
| `retrieved_candidates`, `final_context_chunks` | 정상 처리에서는 배열. 오류로 해당 단계의 결과를 확인하지 못했으면 `null`; 실제 검색 결과가 없으면 `[]` |
| `final_context_hash` | 최종 청크를 알면 아래 로그 해시 알고리즘 값. 단계 미확인 오류이면 `null` |
| `status`, `answer`, `reason`, `citations` | status는 API 네 상태. 정상 answer/reason은 문자열; 시스템 오류 기록은 answer/reason의 null을 허용. citations는 항상 배열 |
| `latency_ms` | 0 이상의 정수 또는 미측정 `null`. API가 요청을 받은 시점부터 응답 직전까지 재시도 포함; B의 API 연결부가 최종 확정 |
| `attempts` | `AttemptRecord` 배열. 미기록 시 `[]`로 두되 모델을 호출하지 않았다는 증명으로 해석하지 않음 |
| `langfuse_trace_id` | 실제 ID 또는 `null`; ID가 있다는 사실만으로 서버 저장 완료를 보장하지 않음 |

위 표의 키는 모두 존재해야 한다. `usage`는 `attempts`에서 C가 계산한다. 오류에는 `error_type`과 확인 가능한 진단 이유를 보존한다. `model_context_text`·`model_context_hash`와 실제 모델 입출력은 생성 단계에 도달한 실행에서 필수이며, 그 전에 실패하면 null 또는 해당 단계 미도달 사유를 남긴다. 실제 입출력은 시도별 `attempts[].model_input/model_output`에 보존하고, 보안 분석용 루트 `model_input/model_output`에는 마지막 실제 호출의 값을 연결한다. 평가 결과가 없는 단계의 데이터를 만들어 채우지 않는다.

### 문맥 해시 두 종류

- `ContextBundle.context_hash` → `RunRecord.model_context_hash`: 헤더를 포함한 실제 문맥 문자열의 해시, 접두어 없는 hex.
- `RunRecord.final_context_hash`: C의 평탄 로그 청크 배열에서 순서대로 `[chunk_id, source_id, text]`를 뽑아 `json.dumps(payload, ensure_ascii=False, separators=(",", ":"))`로 직렬화한 UTF-8의 SHA-256에 `sha256:`를 붙인다. C의 `compute_context_hash()`가 계산한다.

두 값은 목적·계산식이 다르므로 서로 복사하거나 같은 값이어야 한다고 비교하지 않는다. 보안 비교에서는 각 종류의 값이 두 프롬프트 실행 사이에 각각 같아야 하고, 실제 모델 입력에서도 질문에 전달한 검색 문맥이 동일한지 확인한다. 검색 문맥을 재구성하지 않고 B가 준비한 동일 문맥을 재사용한다.

### AttemptRecord와 사용량

호출한 모델 시도마다 다음 정보를 기록한다. 모델 재시도와 출력 검증 후 재생성을 모두 별도 시도로 남긴다.

| 필드 | 규칙 |
| --- | --- |
| `attempt` | 요청 안에서 1부터 증가하는 시도 번호 |
| `model`, `model_input` | 실제 모델명과 실제 전달한 메시지 또는 직렬화 가능한 입력 |
| `model_output` | 실제 출력; 출력 전에 실패했으면 null |
| `input_tokens`, `output_tokens` | 시도별 확인된 0 이상 정수 또는 null |
| `error` | 오류 이름 또는 null |
| `prompt_variant`, `model_parameters` | `base`/`defense`와 실제 적용한 설정; 정상 일반 비교에서는 공통 설정 유지 |

SDK가 반환한 실제 사용량만 기록한다. 합계를 모든 시도에 복제하지 않는다. B의 기존 `input_messages`/`output_message`가 있으면 연결부가 `model_input`/`model_output`으로 이름을 맞춘다. 시도별 사용량이 없으면 근거 없이 나누어 채우지 않는다.

`usage`는 `input_tokens`, `output_tokens`, `status`를 가진다. 각 토큰 합계는 확인분 합산이며 확인값이 하나도 없으면 null이다. status는 전부 확인한 `complete`, 일부만 확인한 `partial`, 확인값이 없는 `unknown`, 시도 기록이 없는 `no_attempts`다. 평가 모델 호출은 서비스 attempts/usage에 넣지 않는다.

## 4. Settings와 Resources

공통 설정은 `shared/settings.py`에서 읽는다. 설정 JSON/YAML과 실행 자원 객체를 혼용하지 않는다.

| Settings 필드 | 용도 |
| --- | --- |
| `dataset`, `documents_dir` | 데이터셋 식별자와 PDF 입력 경로 |
| `retrieval.default_mode`, `retrieval.k` | 기본 검색 방식과 최종 최대 청크 수 |
| `retrieval.context_token_budget`, `retrieval.candidate_count` | 문맥 한도와 후보 수 |
| `generation_model`, `model_timeout_seconds`, `max_output_tokens` | 생성 모델·호출 한도 |
| `qdrant_collection`, `embedding_model`, `embedding_device` | 색인·임베딩 연결 설정 |

Hybrid 결합과 리랭커 설정은 A가 제안하고 B가 위 설정과 함께 한 번 반영한다. 최종 비교 전에 값을 동결해 manifest에 남긴다. 특정 환경 변수·원격 저장소를 필수로 가정하지 않고 실제 사용하는 연결 방식을 문서화한다.

| Resources 필드·메서드 | 제공 계약 |
| --- | --- |
| `embedder`, `qdrant`, `collection` | A의 검색 함수가 사용할 임베딩·벡터 클라이언트·컬렉션 이름 |
| `model` | B의 생성 함수가 호출할 LangChain 모델 또는 같은 기능의 가짜 클라이언트 |
| `count_tokens(text) -> int` | 문맥 전체 토큰 계산 함수 |
| `tracer` | C의 `Tracer`와 동일한 요청 추적 인터페이스 |
| `cleanup`, `close()` | B가 앱 종료 시 색인·모델·추적 자원을 정리 |

Hybrid·리랭킹용 추가 자원은 A·B가 공통 변경으로 정한다. A·C는 B의 Resources 구체 클래스를 역으로 import하지 않고 필요한 속성·호출 기능에 의존한다. Resources와 SDK 객체는 RunRecord로 직렬화하지 않는다.

## 5. 함수 호출 계약

기존 작업 브랜치의 호출 방식을 기준으로 **아래 기능 함수는 모두 동기 함수**로 통일한다. 반환값에 임의로 `await`하지 않는다. FastAPI의 비동기 라우터가 호출할 때 B의 래퍼가 `run_in_threadpool` 또는 `asyncio.to_thread`로 요청 파이프라인을 실행한다. 동기 모델·검색 연산으로 이벤트 루프를 막지 않는다.

| 담당·함수 | 확정 호출 형태 → 반환 |
| --- | --- |
| A `retrieve` | `retrieve(question, mode, resources, top_n=20) -> list[RetrievedChunk]` |
| B `load_resources` | `load_resources(settings) -> Resources` |
| B `build_context` | `build_context(candidates, k, token_budget, count_tokens) -> ContextBundle` |
| B `generate_answer` | `generate_answer(question, context, model, *, prompt_path=..., defense="", trace=None) -> GenerationResult` |
| B `resolve_citations` | `resolve_citations(source_ids, source_map) -> list[Citation]` |
| B `answer_query` | `answer_query(request, resources, request_id, settings) -> QueryResponse` |
| C `normalize_run_record` | `normalize_run_record(raw: Mapping) -> dict`. 실행 기록 검증·usage 계산 |
| C `run_comparison` | `run_comparison(questions, adapter, *, experiment_id, dataset_id, config_hash, modes=...) -> list[dict]` |
| C `grade_all` | `grade_all(records, references, *, k, judge_fn, faithfulness_fn) -> list[dict]` |
| C `run_security_comparison` | `run_security_comparison(questions, retrieve_fn, generate_fn, *, experiment_id, retrieval_mode) -> list[dict]` |

일반 평가 어댑터는 `adapter(question, retrieval_mode, request_id) -> Mapping`이다. B가 API 응답과 같은 request_id의 내부 실행 기록을 연결해 RunRecord를 반환한다. C가 `experiment_id`·`question_id`를 붙인 뒤 정규화한다. API 응답 JSON만으로는 최종 문맥과 시도별 기록을 확보할 수 없으므로 어댑터가 이를 합쳐야 한다. 정답·기대 행동·근거 목록은 어댑터에 전달하지 않는다.

보안 비교 어댑터는 다음과 같다.

- `retrieve_fn(question, mode) -> {retrieved_candidates, final_context_chunks, ...}`: 실제 검색과 문맥 구성을 한 번 수행한다.
- `generate_fn(question, final_context_chunks, prompt_variant) -> Mapping`: 미리 준비한 B의 ContextBundle을 어댑터가 유지하고, 같은 문맥을 B의 생성 함수에 전달한다. 재검색하지 않는다.
- `base`이면 defense는 빈 문자열, `defense`이면 C의 방어 문장을 덧붙인다. 입력 chunks가 준비된 문맥과 같은지 확인한다. 반환 기록에 실제 문맥 문자열·해시·모델 입력·출력·시도·요청 ID를 포함한다.

Tracer의 동기 호출은 `start_request()` → `record_retrieval()` → 시도별 `record_attempt()` → `finish()`이며, 시작은 C의 Tracer, 나머지는 반환된 RequestTrace에서 호출한다. 평탄화한 후보·최종 청크와 실제 시도별 입출력을 전달한다. B의 임시 추적 구현은 통합 시 C의 구현·인터페이스로 교체하고 같은 역할의 두 구현을 병렬 유지하지 않는다.

Langfuse의 flush 호출이나 trace ID 생성만으로 저장 확인을 완료 처리하지 않는다. C가 전송 상태·오류를 남기고 실제 trace의 관측을 조회한다. 기록 실패를 모델의 업무 판단 `abstained`로 바꾸지 않는다.

## 6. 오류와 평가값

| 상황 | 처리 기준 |
| --- | --- |
| 빈 질문·잘못된 mode | 입력 검증 오류, API 422 |
| 자료 부족·실제 검색 결과 없음 | 정상 `abstained`; 시스템 오류율에 넣지 않음 |
| 선택한 검색 방식 미구현 | 개발 중에는 명시적인 미지원 오류. 세 방식 구현 완료로 표시하지 않음 |
| 색인·검색 의존성 실패 | 시스템 오류, 보통 API 503 |
| 모델 timeout | 시스템 오류, API 504 |
| 모델 호출·JSON·출처 검증 실패 | 시스템 오류, API 502 |
| RunRecord 필수 필드·타입 불일치 | C의 기록 검증 오류와 사유 보존; 점수로 임의 치환하지 않음 |
| RAGAS·Judge 호출 실패 | 해당 평가는 `unavailable`; 생성된 답변을 다시 만들지 않음 |

서비스 예외는 B가 공통 오류 응답으로 변환한다. C는 각 요청·평가 실패를 기록하고 다음 문항을 계속 처리한다. Judge·Faithfulness 값은 `{value, status, reason}` 형식이며 `status`는 `scored`, `not_applicable`, `unavailable`다. scored만 숫자를 가지며 나머지는 null이다. Judge는 네 항목 각각 0/1, Faithfulness는 0~1 범위다.

## 7. 실행 위치와 패키지 설정

Basic의 공통 실행 위치는 저장소 루트다. 코드가 병합되고 의존성이 설치된 뒤의 목표 명령은 다음과 같다. 현재 main에서 아래 명령이 실행된다는 뜻은 아니다.

```bash
python -m uvicorn main:app --reload
python -m member_a.scripts.build_index --help
python -m member_c.scripts.run_benchmark --help
python -m member_c.scripts.run_security --help
```

각 패키지·하위 Python 패키지에는 `__init__.py`를 둔다. 직접 파일 실행이나 개인별 sys.path 수정보다 위 모듈 실행 방식을 사용한다. 프롬프트 경로는 모듈 위치 또는 명시 설정에서 구하고, 데이터·산출물 경로는 프로젝트 기준 경로로 정규화한다.

B 브랜치처럼 setuptools를 사용하면 pyproject의 패키지 탐색을 기존 `app*`에서 아래처럼 바꾼다. main.py는 실행 위치를 고정해 저장소 루트에서 사용한다. 휠 배포가 필요해지면 main 모듈과 프롬프트 package-data 포함도 따로 확인한다.

```toml
[tool.setuptools.packages.find]
where = ["."]
include = ["shared", "shared.*", "member_a", "member_a.*", "member_b", "member_b.*", "member_c", "member_c.*"]
exclude = ["*.tests", "*.tests.*"]
```

이 설정은 향후 코드의 배치·설치 기준이며 현재 pyproject를 생성하거나 수정한 것이 아니다. 다른 빌드 도구를 택하면 같은 패키지·리소스 포함 조건을 만족하도록 명시한다.

## 8. 기존 브랜치 코드에서 맞출 항목

2026-11-06 검토한 기준은 A `8d666f3`, B `99c9d3f`, C `5a55f7e`다. 아래는 해당 시점 코드의 연동 조정 목록이며 브랜치가 갱신되면 실제 차이를 다시 확인한다. 이번 문서 변경으로 각 브랜치 소스를 직접 변경하지 않았다.

1. 각자의 `app/`·평가·프롬프트·scripts·tests를 담당 패키지로 옮기고 import·설정 경로를 맞춘다. 공통 Chunk·RetrievedChunk는 A와 B 모두 중첩 구조이므로 shared의 단일 정의로 합친다. 선택 필드 `Chunk.heading`도 보존한다.
2. B의 동기 retrieve/build_context/generate/answer_query 호출을 유지한다. 예전 설계의 async 예시를 그대로 섞지 않는다. 비동기 API 래퍼에서 작업 스레드를 사용한다.
3. B의 ContextBundle.final_chunks를 C 로그로 평탄화하는 연결을 유지하고 문서의 변환 규칙을 따른다.
4. B의 문맥 문자열 해시를 C의 final_context_hash로 그대로 복사하지 않는다. C의 compute_context_hash로 로그 해시를 계산하고 B의 값은 model_context_hash로 함께 보존한다.
5. B GenerationResult의 기존 input_messages/output_message를 AttemptRecord 키로 연결하고 실제 시도별 토큰·모델 정보를 보존한다. 모델 응답 aggregate usage만으로 다중 시도 사용량을 추정하지 않는다.
6. B의 answer_query는 QueryResponse를 반환한다. 실행 기록은 C 추적·저장 연결을 통해 별도로 수집하고 일반 평가 어댑터가 합쳐 반환한다. API 공개 응답에 내부 전체 로그를 추가할 필요는 없다.
7. 기존 C 로그 검증은 평탄 청크를 받으므로 중첩 객체를 직접 넘기지 않는다. 실제 문맥 문자열과 그 해시·입출력도 로그·추적에 보존하고 보안 비교 시 검증한다.
8. B의 실제 API 수신 시각과 최종 응답 시각으로 latency_ms를 확정한다. 서비스 함수 내부 시간이나 클라이언트 왕복 시간만으로 대체하지 않는다.

## 9. 첫 연결 완료 기준

- [ ] B가 shared 타입·설정과 Resources를 반영하고 A·C가 같은 정의를 사용한다.
- [ ] 중첩 RetrievedChunk → ContextBundle → 평탄 RunRecord 변환을 가상 한 건으로 확인한다.
- [ ] 실제 질문 한 건의 API 응답, 최종 문맥, 모델 입출력, Langfuse 기록을 같은 ID로 연결한다.
- [ ] 오류 기록에서 `null`과 실제 빈 배열을 구분하고 시도별 사용량을 보존한다.
- [ ] 동일 문맥의 두 해시를 각각 검증하고 기본/방어 프롬프트가 문맥을 바꾸지 않는지 확인한다.
- [ ] 설치·모듈 import와 위 실행 명령을 공통 환경에서 확인한다.

샘플 인터페이스 확인은 실제 Basic 성능 평가와 구분한다. 선택한 일반 질문 전체의 세 방식 비교, 두 자동 평가와 보안 4개 실행은 기존 시스템 설계의 완료 기준을 따른다.
