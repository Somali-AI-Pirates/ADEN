# ADEN — Basic 해운 RAG Q&A

Basic 해운 PDF에서 근거를 찾아 한국어 답변·판단 이유·PDF 페이지를 반환한다.
현재 Naive 검색, FastAPI, 답변 생성과 Langfuse 추적을 연결했다.
Hybrid·리랭킹·정식 평가·Advanced 확장은 후속 작업이다.

## 패키지와 담당

| 위치 | 역할 |
| --- | --- |
| `shared/` | B가 취합하는 공통 타입·설정·오류. A/B의 타입을 단일 정의로 유지 |
| `member_a/` | PDF 추출·청킹·색인·검색·데이터 준비 실행기 |
| `member_b/` | API·문맥·생성·출처·자원 관리·프롬프트·연결 테스트 |
| `member_c/observability/` | C의 추적·기록 검증·문맥 해시. 이번 로컬 구성에 가져온 C 범위 |
| `main.py` | `member_b.api.app.create_app()` 호출 |

C의 평가·보안 실행기 전체는 이번 로컬 변경에 포함하지 않았다.

- [공통 입출력·호출 계약](docs/design/contracts.md)
- [시스템 설계](docs/design/architecture.md)
- [Git 협업 규칙](docs/guides/git-workflow.md)
- [데이터 배치·A의 측정 기록](data/README.md)

## 로컬 실행

Python 3.11 이상을 사용하며 저장소 루트에서 실행한다.

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -e .
.venv/Scripts/python -m uvicorn main:app --reload
```

루트 `.env`에 `OPENAI_API_KEY`, `QDRANT_URL`, `QDRANT_API_KEY`를 설정한다.
Langfuse 기록에는 `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`,
`LANGFUSE_BASE_URL`도 설정한다. 빈 양식은 `.env.example`에 있다.
이미 실행 환경에 있는 값은 `.env`보다 우선한다.

- `/health`: 앱 상태·데이터셋·`query_ready` 확인.
- `http://127.0.0.1:8000/docs`: 한국어 `POST /query` 실행.
- `naive`는 실행되며 `hybrid`, `hybrid_rerank`는 현재 501을 반환한다.
- 정상 상태는 `answered`, `needs_clarification`, `abstained`이며 오류는 `error`다.
- 입력 422, 검색 의존성 503, 모델·출처 502, 모델 시간 초과 504로 구분한다.
- 본문 `request_id`와 `X-Request-ID` 헤더로 실행 기록을 연결한다.

서버 시작 시 모델·색인을 준비하고 종료 때 정리한다. 시작 로그뿐 아니라
`query_ready`와 실제 응답도 확인한다.

## A 색인과 B Resources

B의 `member_b/service/resources.py`는 A의 `open_vector_index()`로 색인을 연다.
`Resources`에는 `embedder`, `qdrant`, `collection`, `model`, `count_tokens`,
`tracer`, `cleanup`, `close()`가 있다.

`retrieve()`는 동기 함수다. B API는 `asyncio.to_thread()`로 전체 요청
파이프라인을 실행한다. 서버를 켤 때 PDF 처리나 색인 생성을 자동 실행하지 않는다.

A가 생성한 `artifacts/basic/index_manifest.json`의 `vector_store.collection`을
`configs/basic.yaml`의 `qdrant_collection`에 맞춘다. `embedding.model`도
`embedding_model`과 맞춰야 한다. 차원만 같다고 다른 모델을 섞으면 안 된다.
현재 API는 YAML 값을 사용하며 manifest를 자동으로 덮어 읽지 않는다.

문맥 예산은 `tiktoken`의 `o200k_base` 로컬 추정값으로 계산한다.
실제 생성 토큰은 모델 응답의 usage로 따로 기록한다.

## 데이터 준비·평가 실행기

Basic PDF 15개는 `data/basic/documents/`, 평가 JSON 8개는
`data/basic/evaluation/`에 둔다. 원본·`.env`·`artifacts/`는 Git에서 제외한다.

```powershell
.venv/Scripts/python -m member_a.scripts.build_index --help
.venv/Scripts/python -m member_a.scripts.eval_retrieval --help
```

새 색인을 만들 때는 새 컬렉션 이름을 지정한다.

```powershell
.venv/Scripts/python -m member_a.scripts.build_index --collection basic_multilingual-e5-small_p800_localcheck
```

`--recreate`는 기존 컬렉션을 삭제하므로 팀 공용 컬렉션에 사용하지 않는다.
검색 보조 평가는 A의 manifest가 필요하다. 후보 기준 결과와 B 최종 문맥 기준
정식 평가는 구분한다.

## Langfuse와 C 연결

요청 ID 아래 `rag-query`, `retrieval`, `final-context`,
`generation-attempt-1`을 기록한다. 후보·최종 청크는 평탄 로그로 전달한다.

- `model_context_hash`: 실제 문맥 문자열의 SHA-256 hex.
- `final_context_hash`: C의 `compute_context_hash()`로 계산한 평탄 청크 해시.
- `GenerationResult.attempts`: `model_input`, `model_output`, 모델·시도·실제 토큰 정보.

두 해시의 계산식은 다르다. flush나 trace ID만으로 저장을 확정하지 않고 관측을
조회한다. 단계별 관측 시간 개선과 전체 평가 어댑터는 후속 작업이다.

## 테스트

```powershell
.venv/Scripts/python -m unittest discover -s member_b/tests -v
```

가상 모델·메모리 Qdrant로 계약·오류·출처·A/B 흐름을 확인한다.
실제 모델 품질·클라우드 부하 시험을 대신하지 않는다.

이 변경은 로컬 파일 구조와 호출 경로를 맞추는 작업이다.
Git 머지·커밋·푸시와 공유 Qdrant 색인 재생성은 수행하지 않는다.
