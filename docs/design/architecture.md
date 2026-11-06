# Basic 해운 RAG Q&A 서비스 구조와 설계

작성일: 2026-11-05 · 수정일: 2026-11-06.

이 문서는 현재 구현, 채택한 구조, Basic 목표, 과거 검증 기록을 구분한다.

| 구분 | 상태 |
| --- | --- |
| 현재 구현 | 기능별 모듈에 Naive Dense 검색과 답변·API·기록·평가 코드가 있다. `hybrid`·`hybrid_rerank`는 미구현이다. 선택적인 [오프라인 지식 데이터 준비](knowledge-data.md)가 있다. |
| 채택한 구조 | 제품 코드는 `app/` 기능 모듈, 공통 타입·설정은 `shared/`에서 관리한다. 상세 타입·함수·기록 계약은 [공통 계약](contracts.md)을 기준으로 한다. |
| Basic 목표 | 세 검색 방식, 실제 자료 기반 답변·평가, 보안 비교와 운영 지표를 완성한다. 목표 목록은 현재 구현의 증거가 아니다. |
| 검증 상태 | 모의 인프라로 연결을 확인했으나 실제 색인·모델·Langfuse 전체 통합은 미검증이다. 구체적인 당시 수치는 [구조 전환 기록](../plans/functional-layout-2026-11-06.md), 검색 전용 측정은 [데이터 안내](../../data/README.md)에 보존한다. |

관련 문서: [문서 목차](../README.md) · [공통 입출력·호출 계약](contracts.md) · [공통 협업 규칙](../../CONTRIBUTING.md)

## 1. 목표와 확정 범위

물류 담당자가 제공된 Basic 해운 PDF를 근거로 예약, 서류 제출, 처리 상태와 비용 관련 질문에 답을 얻는 서비스다. 답변에는 문서명과 PDF 페이지를 표시하고, 필요한 조건이 빠지면 되묻고, 자료가 부족하거나 문서 차이를 해결하지 못하면 이유를 설명하고 보류한다.

사용자 결정: **우선 Basic부터 구현한다.** 이후 Intermediate·Advanced로 확장할 수 있도록 데이터 경로와 색인, 설정을 분리한다.

| 구분 | 설계 기준 |
| --- | --- |
| 고정 자료 | 발제문에 제공된 Basic 해운 ZIP 2개. PDF 15개, 각 24쪽, 총 360쪽 |
| 업무 범위 | 부산→LA 일반 건화물의 예약, 서류 제출, 적용 조건과 처리 상태 |
| 비용 질의 | PDF에서 확인 가능한 설명을 답한다. 요율·계약 조건 등이 없으면 금액 계산을 보류한다 |
| 주요 판단 | 접수와 확정, 요청과 실제 완료, 예정과 실제 완료를 구분한다 |
| 목표 기술 | FastAPI, Pydantic, LangChain, Langfuse, 생성 모델 `gpt-6-luna` |
| 목표 비교 | Naive RAG / Hybrid RAG / Hybrid + Reranking |
| 시연 | FastAPI `/docs`에서 질문·검색 방식 선택과 응답 확인 |

Basic 안내는 복합 운임 계산을 요구하지 않는다. 안내에 나온 `S-DEV-30`도 요율과 계약 조건이 없어 계산할 수 없다고 답하는 문항이다. 따라서 Basic에는 범용 비용 계산기를 넣지 않는다. 비용 질문을 일괄 거절하지 않고, 확인 가능한 내용과 계산할 수 없는 부분을 구분한다.

실제 예약 접수, 선사 API 연동, 외부 자료 수집, 프런트엔드, 대화 메모리, 자동 문서 갱신은 현재 구현 범위에 포함하지 않는다. 사용자는 추가 조건을 포함한 완성된 질문을 다시 제출할 수 있다.

설계의 원안은 [2026-11-05 계획](../plans/basic-day-plan-2026-11-05.md)에, PDF 처리와 검색 전용 측정은 [데이터 안내](../../data/README.md)에 남아 있다. 과거 확인 범위와 현재 구현의 검증 상태를 구분한다.

## 2. 구조의 핵심

**제품 코드는 기능별 `app/` 모듈로 구성하고, 공통 타입·설정은 기존 `shared/`에서 유지한다.** `main.py`는 앱 생성 함수를 호출하고 질문 한 건의 실행 순서는 서비스 계층이 연결한다.

- `main.py`: FastAPI 앱 생성 함수 호출.
- `app/api/app.py`: 설정과 수명주기, 라우터, 오류 처리, 요청 추적 연결.
- `app/service/query.py`: 검색 → 문맥 구성 → 생성 → 출처 검증 순서 연결.
- 각 기능 모듈은 기능과 `shared/` 계약만 사용한다.
- 제품 모듈은 `experimental/<GitHubID>/`를 import하거나 참조하지 않는다.

기능별 연락 담당자와 공동 변경 절차는 [CONTRIBUTING](../../CONTRIBUTING.md#1-작업담당기록)에서 관리한다.

예약·서류·비용은 사용자 업무 범주다. Basic에서는 세 범주를 별도 RAG 서비스나 검색 인덱스로 나누지 않는다. 하나의 질문이 예약 안내, 서류 조건과 정정 공지를 함께 필요로 할 수 있기 때문이다. 업무 범주는 프롬프트와 평가 사례에서 관리한다.

```mermaid
flowchart LR
    PDF[Basic 해운 PDF 15개] --> ING[페이지 추출·청킹]
    ING --> IDX[현재: Dense 색인 / 후속: BM25 색인]
    Q[사용자 질문] --> API[FastAPI /query]
    API --> SVC[service.answer_query]
    SVC --> RET[검색 방식 선택]
    IDX --> RET
    RET --> CTX[최종 문맥·출처 ID 구성]
    CTX --> GEN[LangChain + gpt-6-luna]
    GEN --> VAL[출력·출처 검증]
    VAL --> OUT[답변·상태·문서명·페이지]
    SVC -. 요청별 실행 기록 .-> LF[Langfuse]
    OUT --> SAVE[실행 결과 저장]
    SAVE --> EVAL[검색 지표·RAGAS·Judge]
    REF[평가 정답·채점 기준] --> EVAL
```

색인 생성은 사전 작업이고 API 요청 처리와 분리한다. 서버가 시작할 때 준비된 색인과 모델을 한 번 로드하며, 요청마다 PDF를 읽거나 임베딩을 다시 만들지 않는다.

## 3. 폴더 구조

아래는 현재 기능별 제품 구조다. Python 패키지에는 필요한 `__init__.py`가 있다. Git 추적 범위와 협업 규칙은 [CONTRIBUTING](../../CONTRIBUTING.md)을 따른다. 이전 경로 대응은 [구조 전환 기록](../plans/functional-layout-2026-11-06.md)에 보존한다.

```text
ADEN/
├── main.py                         # app.api.app.create_app() 호출
├── app/
│   ├── ingestion/                  # PDF 페이지 추출·청킹
│   ├── indexing/                   # 임베딩·Dense 색인·색인 CLI
│   │   └── scripts/build_index.py
│   ├── retrieval/                  # retrieve()와 검색 방식
│   ├── generation/                 # 문맥·답변·출처
│   │   └── prompts/                # shipping_qa.txt, defense.txt
│   ├── service/                    # query.py, resources.py, adapters.py
│   ├── api/                        # FastAPI create_app()과 라우트
│   ├── observability/              # 실행 기록과 Langfuse 추적
│   └── evaluation/                 # 평가·보안 비교·샘플·실행 CLI
│       ├── prompts/                # judge.txt, security_judge.txt
│       ├── samples/                # 공용 가상 샘플
│       └── scripts/                # eval_retrieval, run_benchmark, run_security
├── shared/                         # 공통 타입·설정·예외
├── tests/
│   ├── api/
│   ├── generation/
│   ├── service/
│   ├── evaluation/
│   ├── integration/
│   └── test_package_layout.py
├── experimental/<GitHubID>/        # 개인 실험 공간, 내부 구조·기록 형식은 자율
├── configs/basic.yaml              # 서비스 모델·검색 설정
├── data/basic/                     # documents/와 evaluation/; 원본은 Git 제외
├── artifacts/                      # 로컬 생성물, Git 제외
├── reports/<experiment_id>/        # 공식 비교·제출 결과
└── docs/                           # 설계·계약·가이드·계획
```

`app/evaluation/`은 평가 코드, `data/basic/evaluation/`은 제공 질문·정답 데이터다. 서비스 런타임은 정답 데이터를 읽지 않는다. 색인 생성기는 `data/basic/documents/*.pdf`만 읽고 데이터 루트 전체를 재귀 색인하지 않는다.

원본 데이터와 `artifacts/`는 Git에서 제외하고, 공식 비교표·답변·점수·재현 설정은 검토 후 `reports/<experiment_id>/`에 보존한다. 제품 코드는 `experimental/`에 의존하지 않는다. 개인 실험의 사용·채택 기준은 [개인 실험 안내](../../experimental/README.md)와 CONTRIBUTING을 따른다.

별도 데이터 준비 경로인 `app.ingestion.scripts.curate`는 세 난이도의 규칙·행별 기록·사례 참조·원본 출처를 `data/curated/<dataset>/`에 생성해 Git으로 관리한다. 검색 기록·규칙은 `artifacts/knowledge/search/`에 분리한다. 기존 Basic v1 추출 CLI는 호환·과거 비교용으로 보존한다. 모델 행동 지침은 기존 프롬프트가 기준이며, 데이터 준비만으로 런타임 색인이나 서비스 난이도를 전환하지 않는다. 스키마·실행·정규화 한계는 [지식 데이터 구성](knowledge-data.md)을 따른다.

`app/service/query.py`가 검색 함수, 답변 생성, 추적을 연결한다. 제품 모듈은 `shared/contracts.py`를 공통 참조하고 `shared/`는 `app/`을 역으로 import하지 않는다. 평가 실행기는 서비스 어댑터나 API 클라이언트를 주입받아 순환 참조 없이 동작한다. 색인·모델 초기화 연결은 `app/service/resources.py`가 맡는다. 제품 모듈 import 시 모델 로딩이나 네트워크 호출을 실행하지 않는다.

## 4. 모듈별 책임

아래 표는 기능 경계와 현재 상태만 요약한다. 타입 필드, JSON 예시, 함수 시그니처, 오류와 `Resources`의 기준은 [공통 계약](contracts.md)이며, 같은 세부 정의를 여기서 반복하지 않는다.

| 모듈 | 책임과 현재 상태 |
| --- | --- |
| `app/ingestion/` | PDF 페이지 추출과 페이지 안 청킹 |
| `app/indexing/` | 임베딩, Dense 색인 생성과 CLI |
| `app/retrieval/` | `retrieve()` 진입점. 현재 `naive` Dense 검색만 구현 |
| `app/generation/` | 문맥 구성, 답변 생성, 출처 ID 해석 |
| `app/service/` | 자원 준비, 요청 흐름과 평가 어댑터 연결 |
| `app/api/` | FastAPI 앱 수명주기, 라우트와 오류 변환 |
| `app/observability/` | 실행 기록 검증·정규화와 Langfuse 추적 |
| `app/evaluation/` | 검색 측정, 답변 채점, 보안 비교와 실행 CLI |

공통 타입은 `shared/contracts.py`에 한 번만 정의한다. 구조화 검색 결과와 평탄화 실행 기록의 변환, 출처 및 두 문맥 해시의 의미도 [공통 계약](contracts.md)을 따른다.

## 5. 메인 파일과 요청 실행 흐름

현재 `main.py`는 다음 앱 생성 함수를 호출한다.

```python
from app.api.app import create_app

app = create_app()
```

`create_app()`은 설정 로드, FastAPI 생성, 라우터·오류 핸들러·요청 추적 등록을 맡는다. FastAPI lifespan에서 `load_resources()`를 호출하고 종료 시 자원을 해제한다.

```python
# app/service/query.py의 핵심 흐름을 단순화한 예시
from app.retrieval.service import retrieve
from app.generation.context import build_context
from app.generation.chain import generate_answer
from app.generation.citations import resolve_citations
from shared.contracts import QueryResponse

def answer_query(request, resources, request_id, settings):
    mode = request.retrieval_mode or settings.retrieval.default_mode
    candidates = retrieve(
        request.question, mode, resources,
        top_n=settings.retrieval.candidate_count,
    )
    context = build_context(
        candidates, settings.retrieval.k,
        settings.retrieval.context_token_budget, resources.count_tokens,
    )
    generated = generate_answer(
        request.question, context, resources.model,
    )
    answer = generated.model_answer
    citations = resolve_citations(
        answer.source_ids, context.source_map,
    )
    return QueryResponse(
        request_id=request_id, status=answer.status,
        answer=answer.answer, reason=answer.reason, citations=citations,
    )
```

위 코드는 성공 경로의 연결 설명이며 생략한 추적·오류·빈 문맥 처리를 포함해야 실제 서비스가 된다. 실제 구현에서는 C의 단계별 기록 함수에 평탄 로그와 실제 시도별 입출력을 넘긴다. API 응답과 내부 RunRecord는 요청 ID로 연결한다. 기능 함수는 동기 호출로 통일하고 비동기 API 래퍼가 요청 파이프라인을 제한된 작업 스레드에서 처리한다. 하나의 공통 재시도 정책을 두고 SDK와 LangChain의 재시도가 중복되지 않게 설정한다.

CLI는 저장소 루트에서 모듈 방식으로 실행한다. 설치·검사는 [개발 가이드](../guides/development.md), 색인·검색 실행은 [데이터 안내](../../data/README.md), 일반·보안 평가는 [평가 가이드](../guides/c-module-guide.md)를 따른다.

빈 검색 결과는 근거를 확인하지 못했다는 정상 보류로 처리할 수 있다. 반면 색인 연결 실패, 모델 timeout, 잘못된 출력은 시스템 오류로 전달한다. 서비스 내부에서 오류를 보류로 바꾸지 않는다.

## 6. 데이터 처리와 세 검색 방식

두 Basic 해운 ZIP을 `data/basic/`에 풀어 `documents/`와 `evaluation/`을 보존한다. PDF 15개와 총 페이지 수를 확인하고 파일별 추출 성공·실패, 빈 페이지와 실제 청크 수를 기록한다. 누락이 있으면 그대로 명시하고 재처리한다.

권장 기술은 pypdf, LangChain Document, 다국어 E5 임베딩, Qdrant, BM25다. E5와 Qdrant는 발제문의 권장 도구이며 의무는 아니다. 기존 강의 환경을 우선 사용한다.

| 설정 | 초기 제안 | 비고 |
| --- | --- | --- |
| 임베딩 | 강의의 다국어 E5 계열, 모델 미정이면 `intfloat/multilingual-e5-small` 검토 | 실제 사용 ID·버전 고정, query/passage 접두어 일관 적용 |
| 청킹 | 페이지 안에서 문단·목록 기준, 약 400 임베딩 토큰, overlap 약 60 | 모델 최대 입력 길이 내로 유지, 대표 PDF로 확인 |
| Dense 후보 | 20개 | Naive 및 Hybrid에서 같은 설정 |
| BM25 후보 | 20개 | 같은 청크, 동일한 한국어·영문 토큰화 규칙 |
| 결합 | RRF로 합치고 `chunk_id` 중복 제거 | 서로 다른 점수를 그대로 더하지 않음 |
| 리랭킹 | Hybrid 상위 후보 20개 재정렬 | 한국어 지원 모델을 실행 환경에 맞춰 확정 |
| 최종 k | 5개 | 세 방식 공통 |
| 문맥 예산 | 생성 모델 토크나이저 기준 4,000토큰으로 시작 | 시스템·질문·출력 여유 포함 전체 한도 확인 |
| 생성 | `gpt-6-luna`, 공통 프롬프트·지원되는 동일 생성 설정 | 모델의 실제 지원 파라미터 확인 후 고정 |
| 호출 제어 | `model_timeout_seconds` 60초, `max_output_tokens` 2048, `generation_max_attempts` 2 | `configs/basic.yaml`에서 제한. 첫 연결에서 환경에 맞게 정하고 비교 전에 동결 |

숫자는 출발점이며 발제문 필수값이나 검증된 최적값이 아니다. 최종 비교 전에 한 번 확정하고 세 방식에 동일하게 적용한다.

| `retrieval_mode` | 검색 흐름 |
| --- | --- |
| `naive` | Dense 검색 → 공통 문맥 구성 |
| `hybrid` | Dense + BM25 → RRF → 공통 문맥 구성 |
| `hybrid_rerank` | 동일 Hybrid 후보 → 리랭커 → 공통 문맥 구성 |

문맥 구성기는 순서대로 최대 k개를 선택하되 토큰 한도를 지킨다. 문장 중간을 무조건 잘라 조건이나 출처를 없애지 않는다. 필요한 청크가 예산에 들어가지 않으면 제외 사실과 실제 최종 개수를 기록한다. 세 방식에서 같은 정책을 적용한다.

최종 문맥을 확정한 뒤 `S1`, `S2` 등의 요청 내부 출처 ID를 부여한다. **검색 후보와 실제 모델에 전달한 최종 문맥을 각각 저장한다.** 검색은 성공했지만 문맥 구성에서 근거가 빠진 경우를 구분하기 위해서다.

## 7. 답변 정책과 API

모델은 다음 순서로 판단한다.

1. 질문에서 답변에 필요한 대상·항로·화물·기준일·예약 또는 서류 조건을 확인한다.
2. 검색 자료가 그 대상과 조건에 적용되는지 확인한다.
3. 접수·검토·확정, 요청·완료, 예정·실제 상태를 구분한다.
4. 내용이 다르면 정정 문서가 무엇을 언제부터 바꾸는지 확인한다. 최신 날짜나 검색 순위만으로 선택하지 않는다.
5. 근거가 충분하면 답하고, 사용자가 보완할 조건이 빠졌으면 되묻고, 자료 부족이나 해결되지 않은 충돌은 설명하고 보류한다.
6. 중요한 문서 기반 주장에 `[S1]` 같은 출처 표시를 연결하고 `source_ids`에는 사용한 ID만 반환한다.

일반적인 예약 절차를 묻는 질문에 예약번호나 기준일을 무조건 요구하지 않는다. 현재 질문에 실제 필요한 정보만 확인한다. 문서 적용 여부와 답변 가능 여부는 LLM과 프롬프트로 판단하며 Basic에서 별도 규칙 엔진은 만들지 않는다.

| 상태 | 뜻 | HTTP 처리 |
| --- | --- | --- |
| `answered` | 근거에 따른 답변 | 200 |
| `needs_clarification` | 답변에 필요한 사용자 조건 확인 | 200 |
| `abstained` | 자료 부족·해결되지 않은 충돌에 따른 보류 | 200 |
| `error` | 코드가 감지한 입력·의존성·모델·출력 오류 | 원인에 맞는 4xx/5xx |

`error`는 LLM의 업무 판단 값에 포함하지 않고 코드가 만든다. 요청 검증 실패는 422, 의존성 사용 불가는 503, 모델 timeout은 504, 잘못된 모델 출력은 502, 예상하지 못한 내부 오류는 500으로 매핑하는 안을 사용한다. 오류 응답에 API 키나 원문 스택을 노출하지 않는다.

`POST /query` 요청 예시:

```json
{
  "question": "예약 접수번호를 받으면 예약이 확정된 건가요?",
  "retrieval_mode": "hybrid"
}
```

`retrieval_mode`는 세 값 중 하나이며 미지정 시 개발 초기에는 `naive`를 기본값으로 둔다. 최종 기본값은 평가 결과를 보고 바꾼다. 데이터셋은 서버의 `basic.yaml`로 고정하여 요청마다 다른 자료 집합이 섞이지 않게 한다.

성공 응답에는 `request_id`, `status`, `answer`, `reason`, `citations`를 항상 반환한다. `citations`는 실제 파일에서 얻은 `book_id`, `file_name`, 1부터 시작하는 `page`를 포함한다. 출처가 없는 확인 질문이나 검색 결과가 없는 보류는 빈 목록이 가능하다.

모델이 문서명과 페이지를 새로 작성하게 하지 않는다. 모델의 출처 ID를 코드의 `source_map`으로 변환한다. 존재하지 않는 ID, 최종 문맥에 없는 ID, 형식 불일치는 제한된 출력 재시도 후에도 해결되지 않으면 오류다. 이는 출처 ID와 문서·페이지 대응을 확인하는 코드 검증이다. 답변의 주장이 실제 PDF 페이지에 근거하는지는 보장하지 않으며, 실제 Basic 자료와 답변의 대조는 아직 완료되지 않은 검증 항목이다.

`GET /health`는 앱과 필수 색인의 준비 상태를 확인한다. 요청 ID는 입력 검증 이전 미들웨어에서 생성해 오류 요청에도 남긴다.

## 8. Langfuse와 내부 실행 기록

한 요청 ID로 다음 정보를 연결한다.

- 질문, 검색 방식, 데이터셋·설정 버전.
- 검색 후보와 순위, 리랭킹 결과, 실제 최종 문맥과 순서.
- 실제 모델 입력과 출력, 모델명, 시도별 상태·사용량.
- 답변 상태·사유·인용, 시스템 오류 종류.
- 처리 시간과 그 측정 범위(`latency_scope`), 단계별 시간.
- 입력·출력 토큰, 확인되지 않은 사용량 여부.

`RunRecord`의 필수·선택 필드, null 조건, 실제 문맥과 로그 청크 해시의 정의는 [공통 계약](contracts.md)을 기준으로 한다. 이 문서에서는 요청 추적에 필요한 질문·검색·최종 문맥·모델 시도·응답·처리 시간의 범위만 요약한다.

로컬 JSONL은 평가 입력과 장애 분석용 보조 기록이다. Langfuse 기록을 대체하지 않는다. 전송 실패는 별도 표시하고 기록을 재전송한 후 확인한다. Langfuse의 실제 요청 한 건에서 질문→검색→모델 입력→출력을 추적할 수 있어야 연동 완료로 본다.

비밀키는 환경변수로 받고 `.env`를 저장소에 올리지 않는다. 로그와 발표 화면에도 남기지 않는다. 별도 운영 대시보드는 만들지 않는다.

## 9. 평가와 비교

Basic은 일반 질문 72개(개발 30, 추가 30, 확장 검증 12)와 보안 질문 2개를 제공한다. 일반 72개 전체 실행은 필수가 아니다. 초기 제안은 12~18문항을 골라 예약·서류·정정·조건 확인·근거 부족 사례를 포함하는 것이다. 실제 문항을 검토한 뒤 선택 ID와 이유를 고정한다. 발제문의 필수 문항 수로 해석하지 않는다.

선택한 일반 질문 N개를 세 방식에서 각각 실행해 **3N개 모든 결과**를 평가한다. PDF, 추출, 청크, 임베딩, 저장소, 생성 모델, 프롬프트, 생성 설정, k, 문맥 토큰 한도를 고정한다. 질문 파일에서는 `id`, `question`만 읽어 서비스 adapter에 전달하고, 답변과 문맥을 저장한 뒤 정답 파일을 평가 모듈에서 연결한다. 현재 공식 bootstrap이 없어 CLI에 준비된 callable을 제공하는 연결은 후속 작업이다. CLI 연결 조건은 [공통 계약](contracts.md)을 따른다.

| 평가 | 구현 기준 |
| --- | --- |
| Hit@k | 최종 문맥의 상위 k개 청크가 참고 근거 문서를 하나라도 포함하면 1 |
| Recall@k | 찾은 참고 문서 수 / 참고 문서 전체 수 |
| RAGAS Faithfulness | 질문·생성 답변·실제 최종 문맥으로 평가 |
| LLM-as-a-Judge | 사실·조건·근거·판단 각각 0/1, 이유와 근거 저장 |
| 운영 지표 | 응답 시간, 오류율, 입력·출력 토큰; 단가 확인 시 비용 |

검색 지표의 정답 집합, Faithfulness·Judge의 적용 조건, 실패·평가 불가 처리와 평균 계산은 [평가 모듈 가이드](../guides/c-module-guide.md)의 기준을 따른다. 평가 질문과 설정은 실행 전에 고정하고, 세 검색 방식의 결과를 같은 조건에서 비교한다. 평가 호출의 시간·토큰은 서비스 측 지표와 분리한다.

**응답 시간 목표와 현재 한계:** 비교 결과에는 실제 HTTP 요청 전체의 처리 시간을 기록해야 한다. 현재 평가 실행기의 `make_query_adapter()`는 HTTP 요청을 보내지 않고 `answer_query()`를 프로세스 안에서 직접 호출하며 `latency_scope="in_process_adapter"`로 표시한다. 반면 `app/api/app.py`의 미들웨어는 실제 API 요청에서 `latency_scope="http"` 시간을 기록한다. 현재 `operational_summary()`는 `latency_scope`별로 나누지 않고 전달된 `latency_ms`를 집계하므로, 평가 보고서의 현재 시간 집계는 HTTP 전체 응답 시간으로 해석할 수 없다. 실제 HTTP 경로의 시간은 후속 통합 검증에서 같은 `request_id`로 내부 기록을 연결하고, 보고서도 측정 범위별로 분리한 뒤 확정해야 한다.

현재 일반 평가 실행기는 어댑터 기록과 응답 문맥을 기준으로 동작한다. 이를 실제 HTTP 평가 경로로 연결하거나 HTTP 시간 측정이 검증된 것으로 간주하지 않는다. 실제 API 응답에 정답·내부 전체 문맥을 노출할 필요는 없다.

보안 비교는 한 RAG 구성에서 제공된 질문 2개를 실행한다. 질문별 검색·문맥 구성을 한 번만 수행하고 같은 최종 문맥으로 기본 프롬프트와 방어 문장 추가 프롬프트를 각각 실행한다. 총 4개 답변이며 `final_context_hash`와 문서 순서가 동일해야 한다. 모델·출력 형식·호출/토큰 한도·출력 검사 등은 같게 유지한다.

공격 라벨·문서 ID로 검색을 유도하거나 `attack_text`를 문맥에 따로 넣지 않는다. 실행 후 실제 모델 입력을 확인해 공격 노출, 공격 추종, 업무 답변 정확성, 불필요한 거절을 나눠 기록한다. 공격 미노출이나 생성 실패를 방어 성공으로 세지 않는다.

보안 비교는 일반 평가와 별도 결과로 보존하며 일반 요청의 운영 지표에 합산하지 않는다. 결과 파일과 세부 평가 규칙은 [평가 모듈 가이드](../guides/c-module-guide.md)를 따른다.

## 10. 협업 단위와 구현 순서

아래는 기능의 연결 상태와 기술적인 통합 순서 제안이다. 실제 작업의 담당·일정·진행 상태는 Notion WBS에서, 협업 절차는 [CONTRIBUTING](../../CONTRIBUTING.md)에서 관리한다.

| 기능 영역 | 경로 | 현재 상태와 남은 확인 |
| --- | --- | --- |
| 데이터 처리·색인 | `app/ingestion/`, `app/indexing/` | 페이지별 추출·청킹과 Dense 색인 코드가 있다. 공용 색인 재생성은 별도 운영 절차를 따른다. |
| 검색 | `app/retrieval/` | `naive` Dense 검색이 있다. Hybrid·리랭킹은 미구현이다. |
| 답변 생성 | `app/generation/`, `app/generation/prompts/` | 생성·출처 검증 모듈이 있다. 실제 모델 연결은 실행 검증되지 않았다. |
| API·서비스·기록 | `app/api/`, `app/service/`, `app/observability/` | 가상 인프라 통합 테스트는 통과했다. 실제 색인·모델 통합은 미실행이다. |
| 평가·비교 | `app/evaluation/`, `app/evaluation/scripts/` | 평가·보안 비교 모듈이 있다. 실제 점수·보안 비교 결과는 없다. |

`shared/contracts.py`와 설정 키는 팀 공통 약속으로 먼저 확정한다. 함수 시그니처 변경은 담당자끼리 공유하고 호출부를 함께 바꾼다. 각 작업은 작은 샘플 입출력으로 연결을 확인한 뒤 실제 자료로 전환한다. 정답 자료에서 가져온 샘플은 런타임 프롬프트에 넣지 않는다.

아래는 남은 구현·검증을 위한 순서 제안이며, 완료 결과의 증거를 대신하지 않는다.

1. 공통 타입·설정·폴더 구조를 확정하고 PDF 한 개와 질문 하나로 Naive 검색→생성→API→Langfuse를 먼저 연결한다.
2. PDF 15개 전체를 처리하고 출처·누락·청킹을 확인한다. 답변·추가 확인·보류 동작을 점검한다.
3. Hybrid와 리랭킹을 동일한 `retrieve()` 계약에 추가한다.
4. 평가 질문과 공통 설정을 고정하고 일반 3N개 결과, 두 자동 평가와 운영 지표를 기록한다.
5. 보안 답변 4개를 비교하고 대표 결과를 원문과 대조한다.
6. 결과를 보고 기본 검색 방식을 정하고 README·비교표·발표자료를 완성한다. 제출용 결과를 `reports/<experiment_id>/`에 정리하고 README에서 연결한다.

구현 검증은 핵심 계약에 집중한다. 페이지 번호와 출처 매핑, 문맥 밖 출처 거부, 빈 입력·모델 오류·근거 부족 구분, 정답 파일 색인 방지, 알려진 예제의 문서 단위 Hit/Recall 계산, 동일 문맥 보안 비교를 확인한다. 이 설계 문서만으로 성능이나 구현 완료를 주장하지 않는다.

## 11. 이후 난이도 확장

Basic이 완료된 뒤 `configs/intermediate.yaml`, `data/intermediate/`, 별도 색인을 추가한다. Advanced도 같은 방식으로 분리한다. 난이도별 자료를 한 색인에 섞지 않는다.

- Intermediate: 실제 PDF 표의 행·열·단위·각주 보존과 운송 날짜·요율에 따른 계산을 추가한다.
- Advanced: 여러 기록의 계약 조건·변경·승인 범위와 비용별 감면·면제, 청구와 수납 구분을 확장한다.
- 계산 코드가 필요해지면 `app/generation/calculation/` 같은 작은 모듈을 추가하되, PDF에서 확인한 입력값과 출처를 함께 전달한다.
- API 응답 계약, 검색 세 방식, 평가·Langfuse 구조는 유지한다. 각 난이도 안에서 공통 설정을 맞춰 다시 비교한다.

## 12. 요구사항과 산출물 대응

| 발제문 요구 | 설계 위치 | 완료 증빙 |
| --- | --- | --- |
| 고정 PDF 기반 답변·출처·확인·보류 | `app/ingestion/`, `app/generation/`, `app/service/` | 실제 응답과 원문 대조 |
| 세 RAG 방식 비교 | `app/retrieval/`, `app/evaluation/` | 세 방식 구현 후 같은 질문별 비교표·설정 manifest |
| FastAPI·LangChain·Langfuse | `app/api/`, `app/generation/`, `app/observability/` | 실제 API 시연·요청별 trace |
| 검색·답변 품질 평가 | `app/evaluation/` | Hit/Recall·Faithfulness·Judge 점수와 이유 |
| 보안 비교 | `app/evaluation/security.py` | 같은 문맥의 답변 4개와 노출 분석 |
| 운영 지표 | `app/observability/`, `app/evaluation/` | 시간·오류·토큰·확인 가능 시 비용 |
| 재실행·최종 선택·발표 | README / comparison.csv / 발표자료 | 실행 절차·선택 이유·한계·팀 기여 |

참고 원문:

- [프로젝트 발제문](https://app.notion.com/p/40fb4b2b8e0b801f9cbff8b5d61d9369): 필수 요건, STEP 3~7, 산출물·완료 기준.
- [Basic 데이터셋 안내](https://app.notion.com/p/fd3b4b2b8e0b82068e02017c8fb62434): 해운 자료 범위, 파일 구성, 비용 질의의 한계.
- [Intermediate 데이터셋 안내](https://app.notion.com/p/214b4b2b8e0b8220962881089f1a561e): 이후 확장 참고.
- [Advanced 데이터셋 안내](https://app.notion.com/p/100b4b2b8e0b823a9aab01d45b66f2a2): 이후 확장 참고.
