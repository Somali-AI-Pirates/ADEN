# 공통 입출력·호출 계약

작성일: 2026-11-06 · 수정일: 2026-11-06.

관련 문서: [문서 목차](../README.md) · [시스템 설계](architecture.md) · [Git 협업 규칙](../guides/git-workflow.md)

이 문서는 Basic의 필드·함수·로그 계약의 기준이다. Python 공통 데이터 타입은 `shared/contracts.py`에서 정의하고 이 문서의 예시와 맞춘다. 시스템 구조 문서는 책임과 흐름을 요약하며, 세부 계약은 여기서 관리한다. SDK 클라이언트를 포함하는 실행 자원은 `app/service/resources.py`에서 관리한다. 실행 기록 검증·직렬화 코드는 `app/observability/records.py`가 맡는다.

선택적인 오프라인 지식 산출물은 `app/ingestion/knowledge_schema.py`에서 정의하며 [지식 데이터 구성](knowledge-data.md)을 기준으로 한다. `prepare_knowledge(data_dir, output_root, *, catalog_path, max_chars=800)`는 검증한 빌드 경로와 manifest를 반환한다. 기존 `PageDocument`·`Chunk`·API·로그 계약은 바꾸지 않는다. `index_entries.jsonl`의 메타데이터를 기존 `Chunk` 생성자나 Qdrant payload에 그대로 전달하지 않는다.

현재 기능 모듈과 공통 계약은 가짜 외부 자원으로 연결 검증했다. 실제 색인·생성 모델·Langfuse 서버를 사용한 전체 실행은 이번 구조 전환에서 확인하지 않았다. 구조와 검증 상태의 요약은 [시스템 설계](architecture.md)를 따른다.

## 1. 패키지명과 의존 방향

제품의 import 경로는 기능별 `app.*`와 공통 `shared.*`다. A/B/C 표기는 초기
구현과 연락 지점의 설명이며 폴더의 독점 소유권을 뜻하지 않는다.

- 데이터·검색: `app.ingestion`, `app.indexing`, `app.retrieval`.
- 답변·서비스: `app.generation`, `app.service`, `app.api`.
- 기록·평가: `app.observability`, `app.evaluation`.
- `shared/`는 `app/`을 역으로 import하지 않고 공통 타입·설정·예외를 제공한다.
- 서비스가 검색·생성·추적을 연결한다. 검색·추적 모듈은 서비스·API를 역으로 import하지 않는다.
- 평가기는 초기화된 서비스 호출 함수를 주입받는다. 평가 모듈 import만으로 앱·모델을 초기화하지 않는다.
- 제품 코드와 기본 검사·설치는 `experimental/<GitHubID>/`에 의존하지 않는다.

개인 실험 내부의 파일·폴더·작업일지 양식은 자율이다. 채택할 기능과 결과를
공통 코드에 반영하는 절차는 [Git 협업 가이드](../guides/git-workflow.md)를 따른다.

## 2. A·B가 전달하는 데이터 객체

아래 필드는 별도 표시가 없으면 필수다. Python에서 dataclass 또는 Pydantic을 사용하더라도 JSON으로 변환했을 때의 구조는 동일해야 한다. `page`·`rank`는 1부터 시작하고, 확인되지 않은 문서 속성을 만들어 넣지 않는다.

| 타입 | 필드 | 제약 |
| --- | --- | --- |
| `PageDocument` | `book_id`, `file_name`, `page`, `text` | 원본 PDF 이름·페이지 보존 |
| `Chunk` | `chunk_id`, `book_id`, `file_name`, `page`, `text`; 선택 `heading=""` | 청크 본문은 `text`; heading은 원문에서 확인된 제목만 사용 |
| `RetrievedChunk` | `chunk: Chunk`, `rank: int`, `score: float`, `retriever: str` | **중첩 구조**. 본문은 `item.chunk.text`로 접근 |
| `Citation` | `source_id`, `book_id`, `file_name`, `page` | `source_id`는 이번 요청 안의 `S1`, `S2` 등 |
| `ContextBundle` | `text`, `final_chunks: list[RetrievedChunk]`, `source_map: dict[str, Citation]`, `context_hash`, `token_count: int`; 선택 `excluded_chunk_ids: list[str] = []` | 실제 모델에 전달할 최종 문맥과 출처 매핑. `excluded_chunk_ids`는 토큰 예산 때문에 뺀 청크 ID |
| `ModelAnswer` | `status`, `answer`, `reason`, `source_ids: list[str]` | 모델 상태는 `answered`, `needs_clarification`, `abstained` |
| `GenerationResult` | `model_answer: ModelAnswer`, `attempts: list[AttemptRecord]`, `usage` | 성공한 호출도 실제 시도·사용량 기록 보존 |
| `QueryRequest` | `question: str`, 선택 `retrieval_mode` | 질문 공백 제거 후 빈 문자열 거부; mode 생략 시 서버 기본값 |
| `QueryResponse` | `request_id`, `status`, `answer`, `reason`, `citations: list[Citation]` | 시스템 실패에는 코드가 `error` 상태를 사용 |

`book_id`는 원본 PDF 파일명에서 `.pdf`만 제거한 값이다. Basic의 현재 청커는 청크가 PDF 페이지 경계를 넘지 않게 하며, `page`는 1부터 센다. 날짜·승인 상태·항로처럼 원문에서 확인할 수 없는 속성을 추측해 추가하지 않는다.

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

`k`와 토큰 예산을 모두 만족하지 못해 뺀 청크는 `excluded_chunk_ids`에 남긴다. 순위 k 밖이라 애초에 보지 않은 후보나 `chunk_id`가 중복이라 건너뛴 후보는 제외 기록 대상이 아니다. 연결부는 이 목록과 실제 최종 청크 수를 실행 기록에 함께 보존한다.

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
| `latency_ms` | 0 이상의 정수 또는 미측정 `null`. `latency_scope`가 나타내는 경로의 경과 시간이다. 범위가 없으면 HTTP 전체 시간으로 간주하지 않는다 |
| `attempts` | `AttemptRecord` 배열. 미기록 시 `[]`로 두되 모델을 호출하지 않았다는 증명으로 해석하지 않음 |
| `langfuse_trace_id` | 실제 ID 또는 `null`; ID가 있다는 사실만으로 서버 저장 완료를 보장하지 않음 |

위 표의 키는 모두 존재해야 한다. `usage`는 `attempts`에서 C가 계산하므로 B가 조립한 기록에는 없을 수 있다. B는 위 키 외에 문맥 구성 진단용으로 `model_context_token_count`, `final_context_chunk_count`, `budget_excluded_chunk_ids`와 처리 시간 범위를 뜻하는 선택 필드 `latency_scope`를 덧붙인다. 현재 기록하는 범위 이름은 `http`, `service`, `in_process_adapter`, `fixed_context_generation`이다. 이는 필수 계약 필드가 아니며, 측정 범위가 다른 시간은 구분해서 해석한다. C는 이 선택 키의 유무로 기록 검증을 실패 처리하지 않는다. 오류에는 `error_type`과 확인 가능한 진단 이유를 보존한다. `model_context_text`·`model_context_hash`와 실제 모델 입출력은 생성 단계에 도달한 실행에서 필수이며, 그 전에 실패하면 null 또는 해당 단계 미도달 사유를 남긴다. 실제 입출력은 시도별 `attempts[].model_input/model_output`에 보존하고, 보안 분석용 루트 `model_input/model_output`에는 마지막 실제 호출의 값을 연결한다. 평가 결과가 없는 단계의 데이터를 만들어 채우지 않는다.

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

재시도 정책은 한 곳에서만 둔다. B의 모델 클라이언트는 `max_retries=0`으로 만들고, 출력 형식·출처 검증에 실패했을 때만 같은 문맥으로 `generation_max_attempts`까지 다시 생성한다. 시도마다 `attempt`를 1부터 올려 모두 남기고, 마지막 시도까지 검증을 통과하지 못하면 출력 검증 오류다. 모델 호출 자체의 timeout·API 오류는 재시도하지 않고 그대로 올려 504·502 구분을 유지한다.

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
| `generation_max_attempts` | 출력 검증 실패 시 재생성을 포함한 한 요청의 최대 모델 호출 수 |
| `context_tokenizer` | 문맥 예산용 로컬 토크나이저. 실제 사용량은 모델 usage로 확인 |
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
| B `generate_answer` | `generate_answer(question, context, model, *, prompt_path=..., defense="", trace=None, attempt_log=None, max_attempts=1) -> GenerationResult` |
| B `resolve_citations` | `resolve_citations(source_ids, source_map) -> list[Citation]` |
| B `answer_query` | `answer_query(request, resources, request_id, settings, *, run_record=None) -> QueryResponse` |
| C `normalize_run_record` | `normalize_run_record(raw: Mapping) -> dict`. 실행 기록 검증·usage 계산 |
| C `run_comparison` | `run_comparison(questions, adapter, *, experiment_id, dataset_id, config_hash, modes=...) -> list[dict]` |
| C `grade_all` | `grade_all(records, references, *, k, judge_fn, faithfulness_fn) -> list[dict]` |
| C `run_security_comparison` | `run_security_comparison(questions, retrieve_fn, generate_fn, *, experiment_id, retrieval_mode) -> list[dict]` |

일반 평가 어댑터의 계약은 `adapter(question, retrieval_mode, request_id) -> Mapping`이다. 서비스 응답과 같은 `request_id`의 내부 기록을 연결해 RunRecord를 반환하고, C가 `experiment_id`·`question_id`를 붙인 뒤 정규화한다. 응답 JSON만으로는 최종 문맥과 시도별 기록을 확보할 수 없으므로 어댑터가 이를 합쳐야 한다. 정답·기대 행동·근거 목록은 어댑터에 전달하지 않는다. 현재 B 어댑터의 직접 서비스 호출과 HTTP 미사용은 [시스템 설계의 평가 절](architecture.md#9-평가와-비교)에 정리한다.

현재 평가 CLI는 `--adapter`, `--retrieve`, `--generate`로 지정한 `모듈:이름`을 import한 뒤 그 callable을 직접 호출한다. CLI가 팩터리를 실행하거나 `Resources`·`Settings`를 초기화하지 않는다. `make_query_adapter(resources, settings)`와 `make_security_adapters(resources, settings, *, defense)`는 준비된 자원이 필요한 팩터리이므로 CLI 인자로 팩터리 자체를 넘길 수 없다. 초기화부가 자원을 준비하고 팩터리 반환 callable을 노출해야 한다. 이는 현재 CLI 연결 조건이며, 별도의 factory 지원 CLI 계약을 뜻하지 않는다.

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

공통 실행 위치는 저장소 루트다. 가상환경에서 `python -m pip install -e '.[test]'`로
앱과 검사 의존성을 설치한다. `main:app` 진입점과 설정·데이터 기준 위치는 유지한다.
API 실행과 CLI 도움말 명령은 다음과 같다. `--help`는 실제 모델·색인에 연결하지 않는다.

```bash
python -m uvicorn main:app --reload
python -m app.indexing.scripts.build_index --help
python -m app.evaluation.scripts.eval_retrieval --help
python -m app.evaluation.scripts.run_benchmark --help
python -m app.evaluation.scripts.run_security --help
```

앱 실행은 실제 자원 준비를 시도하며 준비 상태를 `/health`로 확인한다.
도움말·가상 샘플 검증과 실제 질의 성공을 구분한다. 자세한 설치·실행·검사는
[README](../../README.md), [Git 협업 가이드](../guides/git-workflow.md),
[평가 가이드](../guides/c-module-guide.md)를 따른다.

패키지·하위 Python 패키지에는 `__init__.py`를 둔다. 직접 파일 실행이나 개인별
`sys.path` 수정보다 모듈 실행을 사용한다. `shared.settings.PROJECT_ROOT`와
모듈 파일 위치를 기준으로 설정·프롬프트·샘플 위치를 구한다. A의 색인 생성·검색
평가 CLI의 상대 데이터·산출물 인자는 기존처럼 저장소 루트에서 실행한다.

설치 설정의 기준은 [pyproject.toml](../../pyproject.toml)이다. `app`·`shared`만
제품 패키지에 포함하고 `tests`·`experimental`은 제외한다. 생성 프롬프트,
평가 프롬프트와 공용 샘플 JSON도 package-data로 포함한다. 현재 실행 지원은
저장소 체크아웃과 editable 설치 기준이며, 독립적인 wheel 서비스 배포는
이번 범위에서 검증하지 않았다. wheel 검사는 필수 코드·리소스의 포함을 확인한다.

A 기능 모듈이 없어도 API·답변 모듈 import는 가능하다. 검색 모듈 누락은 준비
실패로 안내하고, 선택적 추적 모듈 부재는 추적 비활성으로 처리한다. 타사 의존성
누락은 해당 기능 자체의 부재로 숨기지 않는다.

## 8. 기능별 구조로 전환할 때 보존한 경계

2026-11-06의 사람별 패키지를 기능별 공통 구조로 재배치했다. 과거 브랜치별
조정 내역은 Git 이력에서 확인하고, 현재 경로 대응표와 작업 동기화 방법은
[Git 협업 가이드](../guides/git-workflow.md)의 전환 절차를 따른다.

1. `shared` 타입과 동기 함수 시그니처, HTTP 입출력·오류를 유지한다.
2. 중첩 검색 객체를 평탄 실행 기록으로 변환하는 연결과 두 해시의 정의를 유지한다.
3. 최종 문맥·출처 대응표와 생성 시도별 실제 입력·출력·사용량을 보존한다.
4. 요청별 기록은 서비스 응답과 별도로 수집하며 공개 응답에 전체 내부 로그를 추가하지 않는다.
5. 일반·보안 평가기는 기존 어댑터 계약을 유지한다. 보안 비교는 같은 문맥을 재사용한다.
6. 설정과 프롬프트 내용, 기존 데이터·색인·산출물 위치를 바꾸지 않는다.
7. import·동적 로딩·프롬프트 경로·테스트 mock·패키지 설정·CLI·문서를 함께 갱신한다.

## 9. 연결 검증 상태 — 2026-11-06

가짜 외부 자원과 가상 샘플로 다음 항목을 확인했다. 실제 Basic 성능의 증빙은 아니다.

- [x] 기능 모듈이 같은 shared 타입·설정을 사용한다.
- [x] 검색 → 문맥 → 생성 → 평탄 RunRecord → 기록 정규화가 연결된다.
- [x] 오류의 null과 실제 빈 배열, 시도별 사용량을 구분한다.
- [x] 보안 비교의 두 문맥 해시·실제 입력 문맥·생성 조건이 유지된다.
- [x] 새 모듈 도움말·샘플 CLI와 설치·패키지 리소스를 확인했다.
- [ ] 실제 질문의 API 응답·최종 문맥·모델 입출력·Langfuse 저장을 같은 ID로 확인한다.
- [ ] 실제 Basic 질문·참고 JSON 및 보안 JSON의 구조를 `_items()`, `evidence_groups`, `attack_text` 기준으로 확인한다. 현재 참조 파일의 필수 평가 필드 검증은 완료되지 않았다.
- [ ] 준비된 평가 callable과 실제 Basic 질문·정답 데이터의 어댑터 연결을 확인한다.
- [ ] RAGAS 설치 조합·평가 모델 호환성과 실제 한 문항 채점을 확인한다. 현재 호환성은 미검증이다.
- [ ] 실제 Basic 답변의 출처 문서·페이지가 PDF 원문 주장을 뒷받침하는지 확인한다.
- [ ] 실제 HTTP 전체 응답 시간과 시간 범위별 보고를 확인한다.
- [ ] 실제 선택 질문의 세 검색 방식·자동 평가·보안 비교를 완료한다.

이 체크리스트는 가상 자원 연결 검증과 실제 자료·서비스 검증을 구분한다. 실제 모델이나 외부 서비스를 호출한 결과가 추가되면 수행 날짜와 결과 경로를 함께 갱신한다.

## 10. B 구현 연결 현황 — 2026-11-06

서비스 연결은 `app/service/`에서 관리하고 검색·추적 구현은 해당 공통 기능 모듈을 사용한다.
검색·추적 모듈은 자원 준비 시 연결한다. 검색 모듈이 없으면 `/docs`·`/openapi.json`·`/health`는
사용할 수 있고 `query_ready=false`, `/query`는 명시적인 503이다. 추적 모듈이 없으면
추적은 비활성 상태다. 실제 업무 응답을 가짜 검색으로 대체하지 않는다.

Resources의 기존 필드는 유지하며 B 연결용 `retriever` 호출 함수와
선택적 `context_hasher`를 추가한다. A 연결은 `app.indexing.embeddings`,
`app.retrieval.service`이며 C 연결은 `app.observability`다.

`run_record`와 `attempt_log`는 기존 호출을 유지하는 선택 출력 수집 인자다.
요청마다 별도 객체를 사용한다. 실패한 모델 시도도 입력·오류 종류·확인되지 않은
사용량 null을 남긴다. B는 RunRecord를 조립하며 C의 검증·usage 집계를 대체하지 않는다.

B의 `app.service.adapters`에서 다음 연결 함수를 제공한다.

- `make_query_adapter(resources, settings)` → `adapter(question, retrieval_mode, request_id)`.
  반환값은 평탄 실행 기록이다. 오류도 기록으로 반환하여 C가 다음 문항을 처리할 수 있다.
- `make_security_adapters(resources, settings, *, defense)` → `retrieve_fn`, `generate_fn`.
  검색·문맥을 한 번 준비해 base/defense에 재사용한다. 질문이나 문맥을 바꾸면 거부한다.

정식 평가 어댑터에는 C가 제공한 `compute_context_hash` 콜백이 필요하다.
C가 연결되지 않았으면 평가 어댑터는 준비 오류를 알린다. 검색·생성·정답 채점 코드를
B에서 새로 복제하지 않는다.

`create_app(run_record_sink=callback)`은 응답 JSON을 바꾸지 않고 요청별 실행 기록을
동기 callback에 전달한다. API 미들웨어가 수신부터 응답 생성 직전까지 잰 값을
`latency_ms`, `latency_scope="http"`로 확정한다. 기록 저장 실패는 업무 답변을
바꾸지 않고 경고로 남긴다. 서비스 직접 호출과 고정 문맥 생성은 각각
`service`/`in_process_adapter`, `fixed_context_generation` 범위로 표시한다.
현재 일반 평가 adapter는 `answer_query()`를 직접 호출해 `in_process_adapter`를
기록한다. HTTP 요청 전체 시간은 실제 API 경로의 `http` 범위에서 측정한다.
각 `latency_ms`는 그 기록의 scope와 함께 해석하며, 클라이언트 왕복 시간이나
다른 범위의 시간을 HTTP 처리 시간과 섞어 비교하지 않는다. 범위별 보고의 현행 한계는
[시스템 설계의 평가 절](architecture.md#9-평가와-비교)에 정리한다.
Langfuse 루트의 서비스 시간과 자식 관측 타임라인 보완은 C 연결 때 별도로 검증한다.

문맥 토큰은 `context_tokenizer` 설정의 로컬 추정값이며 실제 모델 usage와 구분한다.
최종 문자열도 예산을 재확인하고 초과하면 마지막 청크 전체를 제외한다.
