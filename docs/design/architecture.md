# 시스템 설계

## 채택한 설계 패턴

**기능별 모듈 + 얇은 API + 서비스 계층**을 사용한다. API는 HTTP를 처리하고 TableRAG 서비스는 요청 흐름을 조정한다. B/C의 기존 검색 경로는 평가 어댑터와 CLI에 남아 있다.

| 경계 | 책임 |
| --- | --- |
| `app/api/` | TableRAG 앱 조립, 요청 검증, 준비 상태, 응답·오류 변환 |
| `app/rag/` | Advanced TableRAG의 런타임 설정, 스냅샷·검색·읽기 전용 SQL·LangGraph 계획·답변 |
| `app/service/` | B/C 평가의 기존 검색·생성 요청 처리, 자원 준비·정리, 평가 어댑터 |
| `app/ingestion/` · `app/indexing/` | PDF 추출, 페이지 단위 청킹, 임베딩·색인 |
| `app/retrieval/` · `app/generation/` | B/C 평가의 검색·생성 흐름과 TableRAG가 공유하는 문맥·출처 함수 |
| `app/observability/` · `app/evaluation/` | 요청 추적·기록 정규화, 일반·보안·TableRAG 평가 |
| `shared/` | 공통 타입·평가 설정·예외·TableRAG 고정 정책 |

### 가독성과 연결 작성 규칙

서버의 구체적인 검수 기준과 요청 추적 안내는 [서버 코드 설계 기준](server-code-design.md)을 따른다.

- API와 TableRAG 평가 CLI는 `app.rag.service.RagService` 진입점을 공유한다. B/C 평가는 `app.service.bootstrap`의 평가 어댑터를 사용한다.
- `shared/`는 `app/`을 import하지 않는다. 기능 모듈은 API·서비스 조립부·평가기 쪽으로 역참조하지 않는다. 하위 기능 재사용은 순환 의존이 없을 때 허용한다.
- 처리 순서는 함수로 드러내고, SDK 클라이언트나 캐시처럼 상태와 수명이 있는 자원은 객체로 소유한다. 새 파일은 독립된 책임이 생길 때 분리한다.
- 자원은 앱·실행 단위로 준비하고 정리한다. 검색 캐시와 클라이언트를 앱 사이에 전역 공유하지 않는다.

### 준비 검사와 요청 수명

`RagService`가 `ReadinessProbe`와 `RequestAdmission`을 소유한다. 준비 검사는 추론용 작업자와 별도로 실행하고 10초 캐시·단일 실행으로 병합한다. 필수 자원만 확인하며 tracing 장애는 준비 실패에 포함하지 않는다.

요청은 준비 확인 → FIFO 수용 → 기존 그래프·답변·저장 → 슬롯 반환 순서로 처리한다. SSE는 헤더 전에 슬롯을 예약하고 실제 실행이 예약의 소유권을 인계받는다. 응답 정리가 실행 중인 슬롯을 먼저 해제할 수 없다. 실행 시간 초과나 취소 후에도 진행 중인 동기 작업을 기다린다. 종료는 새 수용 차단 → 대기 종료 → 실행 유예 → 취소·작업 정리 → 준비 검사 종료 → 공유 자원 종료 순서다. 기본값·HTTP 의미와 프로세스 단위 한계는 [공통 계약](contracts.md#tablerag-준비수용시간-제한)을 따른다.

## 서버 시작과 요청 흐름

FastAPI CLI는 [패키지 진입점](../../pyproject.toml)의 `app.api.main:app`을 불러온다. [서버 앱](../../app/api/main.py)은 프로세스당 하나이며 수명주기·라우터·오류 처리·요청 기록을 연결한다.

[수명주기](../../app/api/lifespan.py)는 시작할 때 [단일 TableRAG 런타임 설정](../../app/rag/config.py) `load_table_rag_settings()`로 `ADEN_CONFIG`, 환경변수, 루트 `.env`, 선택 YAML, 기본값을 해석한다. YAML·설정 오류는 시작을 중단한다. 설정은 `app.state.settings`에 두고 [초기화 모듈](../../app/rag/bootstrap.py)의 비동기 load_service로 TableRAG 서비스를 한 번 준비한다. 번들·모델 초기화에 실패하면 `/health`와 문서는 열리며, API 질의는 준비 실패 이유와 함께 503을 반환한다. 종료 시 `RagService.aclose()`가 `AsyncExitStack`이 소유한 모델·검색·저장 자원을 정리한다. API는 B/C `Resources`를 준비하거나 선택하지 않는다.

`POST /query` 또는 `POST /query/stream` → `RagService.ainvoke()` → DB 우선 조회·근거 검색 → 계획·도구 호출 → 문맥 검증·답변 → JSON 또는 SSE

라우트는 비동기 RagService.ainvoke()를 await한다. 서비스는 동기 검색·레코드 조회·SQL 실행·최종 근거 조립을 작업 스레드에서 실행하고, 모델의 비동기 API 호출과 요청별 추적을 조정한다. 서버 시작 단계에서 Uvicorn 접근 로그를 끄고 요청 ID가 담긴 앱 로그를 요청당 한 줄 남긴다. 요청 ID와 로그·평가 시간의 의미는 [기록 계약](contracts.md#기록-형식과-측정-범위)을 따른다.

### Advanced TableRAG

기본 Advanced 앱은 [TableRAG 서비스](../../app/rag/service.py)를 시작할 때 한 번 준비한다. 공통 SQLite에서 HG·HA·IS 코드를 먼저 조회하고, 유일한 대상의 원본을 찾으면 그 근거로 계획을 시작한다. 그 외에는 페이지·표 BM25와 벡터 Top 50(기본 NumPy, `table_rag.vector_backend: qdrant` 선택 시 Qdrant exact 검색)을 5:5 RRF로 합쳐 20쪽을 KURE nano로 리랭킹한다. Qdrant Cloud는 dense 검색만 맡는다. 질문 임베딩과 KURE nano 추론, BM25, 원문·SQLite는 로컬에 남고, 원격 실패 시 자동 fallback은 없다. Luna는 `lookup_records`, `search_evidence`, `read_sources`, `query_tables`, `finish`를 사용한다. 그래프 상태와 요청별 `RuntimeContext`는 [app/rag/state.py](../../app/rag/state.py)에, 검색·기록·원문·SQL 도구 처리기는 [app/rag/tool_handlers.py](../../app/rag/tool_handlers.py)에 둔다. 계획 루프는 [LangGraph 그래프](../../app/rag/graph.py)가 조정한다. 정책 상수는 [shared/table_rag_policy.py](../../shared/table_rag_policy.py)에 둔다. 선택 출처를 보존해 최대 8쪽·SQL 포함 8,000토큰을 답변 모델에 제공한다. API와 TableRAG 평가 CLI는 같은 서비스를 호출하고 로컬 실행 기록을 남긴다. [빠른 시작](../guides/quickstart.md)과 [Qdrant Cloud 데이터 경계](../guides/data.md#advanced-tablerag-qdrant-cloud-벡터-검색)

## 검색과 답변

- B/C 평가 어댑터에서 `naive`는 Dense 검색, `hybrid`는 Dense와 BM25를 RRF로 결합, `hybrid_rerank`는 Hybrid 후보를 다시 정렬한다. 이 모드들은 API 서버에서 선택할 수 없다. B/C 검색 구현은 [검색 서비스](../../app/retrieval/service.py), TableRAG 정책은 [고정 정책 모듈](../../shared/table_rag_policy.py)에서 확인한다.
- [생성 정책](../../app/generation/prompts/shipping_qa.txt)은 계약·실제 사건·승인·청구·수납과 예정·실제를 구분한다. 확인된 핵심이 있으면 답하고 확인하지 못한 범위를 밝히며, 적용 조건이 빠지면 되묻고, 근거 부족이나 해결되지 않은 충돌은 보류한다. 비용은 산입 기간·무료/유료일·구간 단가·산식을, 승인·변경은 효력이 미치는 범위를 나눠 설명한다. 출처는 `[S1]` 형식의 자료 ID로만 표시한다(파일명·쪽은 서비스가 출처로 표시).
- 답변은 모델에 실제로 전달한 문맥의 출처만 인용한다. 색인은 원본 PDF로 만들고, 평가 정답과 공격 라벨은 검색·생성 입력에서 분리한다. PDF의 본문과 인용은 검색 근거로 처리한다.
- 계약상 권한자를 실제 서명자로 추정하지 않고, 승인에 직접 연결된 기록에 명시된 주체만 제시한다. 판단 이유는 상태 선택에 필요한 업무 사실을 짧게 요약할 수 있으며 출처 표시나 JSON 작성 지침은 설명하지 않는다.
- 날짜 계산·검산 질문에서 단일 예약·장비의 실제 사건과 기존 파서가 확인한 산입 규칙이 있으면 `app/generation/calendar.py`가 로컬 달력으로 산입 날짜를 열거해 시스템 메시지에 덧붙인다. 원문 청크·출처·문맥 해시는 변경하지 않고, 보조 계산은 `attempts[].calendar_reference`와 실제 모델 입력에 남긴다. 주말 제외는 명시된 월~금 규칙에만 적용한다. 별도 공휴일 제외 자료가 필요하거나 규칙·날짜가 부족/충돌하면 보조 계산을 생략한다. 동일 계약·조회 시각의 명시적인 추가 변경 없음 기록과 유일한 최초 무료일수가 확인되면 무료·유료 날짜를 배정하고 최종 답변의 지정 날짜 행을 검사한다. 그 밖의 승인 효력·요율·금액은 확정하지 않는다. 출력 검증 실패에는 질문·문맥을 유지한 안전한 피드백을 다음 시도에 전달한다.
- B 생성 수정의 적용 범위와 로컬 검사는 [생성 검증 안내](../guides/generation-checks.md)를 따른다.

B/C의 `Resources.evidence_provider`와 기존 검색기는 평가 CLI에서 후보와 최종 문맥을 공급한다. 후보만 반환하면 공통 문맥 구성기로 최종 문맥을 만들며, 제공된 최종 문맥은 평가 생성 전에 다시 선택하지 않는다. 이 실행 경로는 API 모드가 아니다. [로컬 B·C 평가 연결](../guides/local-bc.md)
