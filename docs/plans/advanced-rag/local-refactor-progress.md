# 로컬 TableRAG 구조 정리 진행 기록

2026-11-12. 현재 사용자 승인 범위는 **기존 동작을 유지하는 로컬 리팩터링**이다.
Qdrant Cloud·Langfuse 연결은 유지하며, 운영 배포·Docker 이전·새 상태 API·요청 제한 정책은 후속 범위다.

## 재개할 때

1. 이 문서의 단계 상태와 `git status`를 함께 확인한다.
2. 진행 중 단계의 변경과 검사 증거를 대조한다. 검사를 하지 않은 단계는 완료로 올리지 않는다.
3. 미완료 작업부터 이어가며 기존 원본·번들·Cloud 컬렉션과 다른 작업 파일을 보존한다.

기준 커밋: `7af54bfd7c94bf417826615f5f92233c73005575`.
원본 소스·기존 작업 지문: `artifacts/verification/local-refactor/baseline.json`와 `before/`.
데이터 manifest SHA-256: `d6b746e5e64ed5d0877593d060b0a6bd397ccb97d77dbcdffec1f945bc865828`.
이전 Qdrant 적용은 완료된 별도 작업이며 이번 리팩터링 완료로 계산하지 않는다.

## 단계 상태

| 단계 | 상태 | 완료 조건 및 현재 증거 | 다음 작업 |
| --- | --- | --- | --- |
| 0. 기준 고정 | 완료 | 설정 소비자·자원 소유자·도구 오류 흐름 조사, 소스/데이터 지문과 변경 전 소스 보존. graph 계약은 baseline-graph-contract.json, 기존 synthetic graph 12개 통과·10개 실행 결과는 graph-baseline.json | 단계 1 |
| 1. 설정 통합 | 완료 | 공통 설정·명시 인증 주입, 집중 65개 + 레거시/API 45개·4 subtests 통과 | 단계 2 |
| 2. 초기화·종료 통합 | 완료 | async 공통 loader·종료 스택, 부분 실패/반복 취소/종료 검사 97개·4 subtests 통과 | 단계 3 |
| 2a. 레거시 실행 정리 | 완료 | 서버 단일 TableRAG 경로. API·공통 평가 81개·6 subtests, SSE/PDF 7개 통과 | 최종 회귀·문서 링크 검사 |
| 3. LangGraph 분리 | 완료 | 22개 통과, 고정 10개 결과 동일·도구 span 경계 보존 | 최종 통합 검사 |
| 4. 검색·데이터 정리 | 완료 | 집중 42개 통과, 작은 구축 전후 내용·출처·벡터 지문 동일·재개 검사 | 독립 리뷰 |
| 5. 통합 검증·문서 | 완료 | 독립 리뷰 2회·553 tests/11 subtests·Ruff·lock·CLI·샘플·build·Cloud 23/23·번들 검사 통과 | 후속 운영 작업은 별도 범위 |

## 고정 동작과 책임

- DB 우선, KURE-v3·KURE nano·Luna medium, BM25/벡터 5:5·RRF 60·exact, 후보 20쪽·최종 8쪽·8,000토큰을 유지한다.
- 현재 TableRAG의 HTTP·SSE·SQL·도구·저장 계약을 보존한다. 요청별 근거 확인 기능은 추가하지 않는다. 후속 사용자 지시에 따라 레거시 호환만을 위한 분기·중복 코드는 사용처 확인 후 제거한다.
- 실행 환경 해석은 진입점에서 한 번, 공유 자원은 서비스 수명, planner·사용량·trace는 요청 수명으로 둔다.
- 그래프가 흐름과 State 갱신을 소유한다. 도구 처리 함수는 결과를 반환하며 State와 공유 자원의 종료를 소유하지 않는다.
- 동작 비교에서는 timing·코드 지문과 검색/SQL/최종 근거를 구분한다. 유료 생성·Judge 호출은 하지 않는다.

## 설정 소비자와 소유자

| 경계 | 현행 호출자 | 통합 후 책임 |
| --- | --- | --- |
| 실행 설정 | API lifespan, RAG CLI, 평가 run, 준비 CLI | 공통 resolver, 명시 CLI 우선·동일 루트 기준 |
| 제품 정책 | retrieval/model/graph/builder의 고정 상수 | 한 정책 모듈과 manifest 대조 |
| 공유 자원 | load_service, RagService.aclose | 단계별 획득·역순 정리, 성공 시 소유권 인계 |
| 요청 상태 | RagService.ainvoke, TableRagGraph.run | 요청별 context/trace·usage, 공유 객체에 요청 데이터 보관 금지 |
| 데이터 구축 | prepare_table_rag와 builder | 기존 명령·완성 번들 보존, 지문이 바뀐 중간 캐시는 명확히 거절 |

## 검증 기록

단계별 명령·종료 코드·출력은 `artifacts/verification/local-refactor/`에 남긴다.
비밀값·원문·실제 질문은 공유 문서에 복사하지 않는다. 실제 실행 전인 검사를 통과로 표시하지 않는다.

## 후속 범위와 제외

운영·배포 계획, 새 readiness API·요청 대기 제한, 로컬 Docker 전환, 신규 인프라, 재임베딩, 커밋·푸시·병합은 이번 작업의 완료 조건이 아니다.
다른 작성자의 `infra/`, 채팅·운영 문서·개인 실험 변경은 보존한다.

### 단계 0 — 고정 입력 확인

기존 graph 테스트 12개를 가짜 모델·작은 저장소로 실행해 통과했다. 그래프 실행 결과가 있는 10건의 상태·출처·SQL·문맥·호출 횟수를 `graph-baseline.json`에 고정했다. 순수 스키마/근거 검사 2건에는 graph 반환값이 없다. 질문과 문맥은 지문으로 기록하며 시간·코드 지문은 결과 동등성에서 제외한다. 실제 모델·외부 저장소를 호출하지 않았다.

### 단계 1 — 완료

`app/rag/config.py`의 불변 설정과 `shared/table_rag_policy.py`의 고정 정책을 분리했다. `table_rag` YAML 절만 런타임 기본값으로 읽으며 기존 Advanced YAML의 모델·검색 키는 레거시 전용이다. `ADEN_TABLE_RAG_RUN_DIR`은 저장 경로 환경값이며 명시 CLI 경로가 환경값보다 우선한다. 환경 읽기는 프로세스 환경을 수정하지 않는다. 설정 manifest와 repr에는 인증값을 넣지 않는다.

집중 검사: `.venv/bin/python -m pytest -q tests/test_table_rag_config.py` — 14개 통과. 호출부 포함 65개, 레거시·API·추적 45개와 4 subtests도 통과했다. 빈 ADEN_TABLE_RAG_DIR은 기존처럼 미지정으로 처리한다. API의 테스트용 loader(path) 주입 계약은 유지했다. 증거는 stage1-summary.json, stage1-pytest.log, stage1-legacy.log, stage1-ruff.log다.

### 단계 2 — 완료

API·CLI·평가가 같은 async load_service를 사용하고, 초기화·종료를 같은 AsyncExitStack으로 연결했다. 모델 생성은 비동기 함수로 바꿨고, HTTP transport·모델 래퍼 생성의 부분 실패와 반복 취소 중 종료를 검사했다. 모델·스트리밍 집중 11개가 통과했다. 서비스 전체를 포함한 97개·4 subtests가 통과했다. worker 생성 중 반복 취소는 완료를 기다리고 자원 등록 후 전달한다. 초기화 실패와 종료 오류에도 역순 정리를 진행하며, 서비스 생성 성공 후에만 종료 책임을 인계한다. corpus mmap 검증 실패와 모델 HTTP transport 부분 생성도 정리한다. 증거: stage2-summary.json, stage2-focused.log, stage2-ruff.log.

### 범위 갱신 — 레거시 정리 허용

사용자가 작업 중 “레거시는 그냥 정리해도 돼”라고 지시했다. 기존 단계 1의 레거시 검사 결과는 그 시점의 증거로 보존한다. 이후에는 호환 유지 자체를 이유로 중복 구현을 남기지 않는다. 현재 TableRAG·평가에 필요한 공통 함수와 실제 소비처를 확인한 뒤 제거 범위를 정하고, 원본 데이터·기존 실험 산출물은 삭제하지 않는다. 사용처 감사 결과 서버의 두 번째 검색 경로(resources_loader·state.resources·answer_query fallback)와 미사용 CLI alias만 제거하기로 했다. app/service·retrieval·generation의 공유 함수와 일반·보안 평가 CLI는 실제 소비처가 있어 유지한다. HTTP·설정·로그 검사는 TableRAG fake 서비스로 옮겨 보존한다.

단계 2 후속 점검: 요청 취소가 동기 검색보다 먼저 끝나서 mmap 종료와 겹치지 않도록 서비스 worker도 완료를 기다린 뒤 취소를 전달한다. 실제 ainvoke 취소 후 검색 완료·자원 종료 순서 검사 포함 service 18개가 통과했다. Langfuse 기본 주소도 설정 이후 바뀐 환경을 다시 읽지 않도록 명시하며, 서비스·기존 추적 집중 30개가 통과했다. 최종 소스 지문은 독립 리뷰 직전에 다시 고정한다.

레거시 정리의 설정 경계: API의 별도 legacy Settings/환경 로드를 제거한다. 공통 TableRagSettings가 기존 documents_dir와 고정 dataset=advanced를 제공하므로 최소 table_rag YAML도 CLI와 API에서 동일하게 읽는다. 명시 Basic YAML·잘못된 설정은 시작 단계에서 거절하고, 유효한 설정 뒤 번들·모델·저장소 준비 실패는 기존 query_ready=false/503 경로를 유지한다. 설정 단위 검사는 16개 통과했다.

### 단계 3 — 완료

`state.py`는 요청별 데이터와 Runtime context를 정의하고, `tool_handlers.py`의 네 처리 함수는 검증된 인자와 저장소를 받아 ToolDelta를 반환한다. 그래프가 메시지·관측·검색·SQL·호출 횟수를 한 번 갱신한다. 모델·trace·planner는 공유 State에 두지 않는다. 기존 12개와 추가 오류/런타임 5개가 통과했다. 원본 스냅샷과 현재 코드의 10개 재생에서 상태·도구 오류·검색/SQL·최종 출처·문맥·호출 횟수가 같았다. timing과 코드 지문은 별도 메타데이터다. 증거: `stage3-summary.json`, `graph-stage3-comparison.json`.

### 단계 2a — 완료

서버의 resources_loader/state.resources/answer_query fallback과 rag_backend 선택을 제거했다. API는 공통 TableRagSettings를 한 번 읽는다. 미사용 run_table_rag_a 명령 alias와 dense_retriever 모듈도 제거했다. B/C 평가 bootstrap과 공통 검색·생성 함수는 실제 평가 소비처가 있어 유지한다. API 계약 검사는 TableRAG 가짜 서비스 기준으로 이관했고 81개·6 subtests, SSE/PDF 7개가 통과했다. 증거: legacy-cleanup-*.log. 설정·원본 경로·요청 ID·오류·동시 요청·반복 수명주기를 확인했다.

단계 3 추가 점검: 기존 10개 재생은 반환 결과 동등성을 확인했고 stage 콜백은 비교하지 않았다. 별도 코드 대조에서 grounding·미관측 표·출처 거절이 span 안으로 옮겨져 새 ERROR 관측이 생기는 차이를 발견했다. 기존처럼 상태 기반 검사를 span 전에 한 번 수행하도록 보완 중이며 단계 3을 검증 대기로 되돌렸다. 검색 정책은 변경하지 않는다.

단계 3 보완 완료: 상태 기반 거절은 span 전에 한 번 수행하고 read_sources의 정상 관측도 기존 span 경계를 유지한다. 도구 거절 4종과 정상 네 도구의 span 입출력을 포함해 22개가 통과했다. 원본 synthetic 실행 10건은 여전히 동일하다. 증거: stage3-trace.log, 갱신된 stage3-summary.json.

### 단계 4 — 완료

페이지·표 파일 읽기를 corpus로 옮기고 공유 토큰·정규화 함수와 고정 정책을 분리했다. builder는 원문·저장·벡터·지문 단계와 조립기로 나눴다. 구축 배치 4·CPU 스레드 8, 추론 CPU 스레드 2 등 기존 실제 값은 유지한다. prepare CLI도 공통 설정과 저장소 루트 경로 해석을 사용한다. 완료 번들은 계속 읽으며, 지문이 다른 중간 작업은 보존하고 새 --work-dir 또는 이전 코드로 재개하도록 오류를 낸다.

작은 구축의 변경 전후 페이지·표·기록·코드 관계·원본 출처·벡터 순서가 동일했다. 내용 SHA-256은 dcadcc0689fb66977352d5d8724902adc4697d5d92d9b2c1aa862b030ca21b44이며 반복 실행도 같았다. 집중 42개와 Ruff가 통과했다. 증거: before/table-rag-baseline/comparison.json, focused-pytest-after.log, snapshot-repeat.log. 실제 PDF 재구축·모델 호출은 하지 않았다. 공통 설정·서비스·corpus·검색·토큰 추가 집중 44개도 통과했다(pre-review-runtime.log).

### 단계 5 — 검증 진행 이력

최종 변경 지문과 전체 diff를 고정한 뒤 자원/설정/취소 및 그래프/데이터/호환 경계의 두 독립 리뷰를 진행한다. 이후 전체 검사·Cloud exact 대조·패키징 결과를 기록한다. 현행 문서 10개의 로컬 링크·heading 100개는 존재를 확인했다(docs-links.json).

### 단계 5 — 완료

두 독립 에이전트가 고정 diff와 주변 코드를 검토했다. 설정·비밀값·자원 수명·취소/동시성 검토에서 수정 사항은 없었다. 그래프·데이터·재개 검토는 코드 회귀를 찾지 않았으며, architecture.md의 오래된 동기 라우트 설명을 지적했다. 실제 비동기 호출과 작업 스레드 대상 작업으로 수정하고 독립 재확인을 받았다. 구현 코드는 리뷰 이후 바뀌지 않았다. 기록은 review-request.md, review.diff, review-scope.json, reviews.json에 있다.

| 최종 검사 | 실제 결과 | 증거 |
| --- | --- | --- |
| `python -m ruff check .` | 통과 | final-ruff.log |
| `python -m pytest -q tests` | **553 passed, 11 subtests passed** | final-pytest.log |
| `uv lock --check` | 통과 | final-lock.log |
| 9개 CLI `--help` | 모두 통과 | final-cli-*.log |
| 일반·보안 평가 `--sample` | 가상 실행 두 건 통과 | final-sample-*.log |
| `python -m build`·휠 코드/리소스/제외 파일 검사 | sdist·wheel 통과 | final-build.log, final-checks.json |
| `prepare_table_rag check --directory artifacts/advanced-table-rag` | 기존 완성 번들 읽기·DB·정렬 검사 통과 | final-existing-bundle-check.log |
| 저장 질문 벡터 기반 실제 Cloud exact | **23/23 동등** | cloud-replay.json, cloud-replay-verified.log |
| 기존 번들 파일 지문 | SQLite·벡터 포함 9개 파일 유지 | preservation.json |
| 문서 링크·절 제목, `git diff --check` | 통과 | docs-links.json, final-content.json |

pytest 경고 1개는 메모리 Qdrant의 exact 검색에 search_params가 별도 영향을 주지 않는다는 SDK 안내다. 실패·미실행 검사가 아니다. 실제 Cloud 검사는 별도로 수행했다.

Cloud 대조는 기존 23개의 서로 다른 질문 벡터와 저장한 KURE 입력/점수를 사용했다. BM25 원점수·dense Top 50 순서·RRF 원점수/후보·KURE에 전달하는 원문 순서·최종 출처/문맥 지문·토큰 수가 같았다. 실제 KURE 모델 호출과 질문 재임베딩은 각각 0회다. 최초 검증 스크립트는 원점수와 이미 반올림된 진단값을 직접 비교해 실패했다. 원본 코드도 진단값을 소수점 8자리로 저장함을 확인했고, 원점수의 정확한 일치와 진단 포맷 일치를 별도로 검증하도록 스크립트만 수정했다. 검색 코드는 수정하지 않았다. 최초 실패 로그와 차이도 보존했다.

검사한 번들은 원문 18,000쪽, 검색 단위 21,750개, 2,048차원 벡터, 물리 표 21,126개다. 임베딩은 KURE-v3 `73d5e710bc0b1f324a60746922cbad6d38f480d4`, 리랭커는 KURE nano `8514b9e113f3b27c441ed9c2b50f81d80388b2fd`다. 입력 벡터·문항·번들 지문은 baseline.json과 cloud-replay.json에 연결돼 있다. 전체 명령·종료 코드는 final-checks.json에 저장했다.

### 완료 상태와 남은 범위

이번 로컬 구조 정리의 단계 0~5와 추가 레거시 정리는 모두 완료했다. 다음 작업이 필요하면 이 문서와 final-content.json의 소스 지문을 실제 파일과 비교한 뒤 시작한다. 운영 계획의 Docker 이전·readiness·요청 수용/대기 정책·CI/CD·클라우드 배포는 이번 작업에 포함하지 않았으므로 별도 구현 상태를 확인해야 한다.

- 검증 플랫폼은 WSL Ubuntu-26.04·Python 3.13이다. Windows 네이티브·운영 ARM 환경은 검증하지 않았다.
- 실제 LLM 답변·Judge·Ragas 재채점 및 Langfuse 원격 전송/점수 재조회는 이번 구조 검증에서 실행하지 않았다. 추적 설정·도구 span·사용량·평가 연결은 가짜 외부 경계로 검사했다.
- 원본 전체 재파싱·재임베딩은 하지 않았다. 기존 완성 번들 검사와 작은 합성 데이터의 구축/재개 동등성을 확인했다.
- 기존 Cloud 컬렉션·SQLite·벡터를 변경하지 않았다. 다른 작성자의 infra·채팅·운영 문서·실험 기록도 편집 대상에서 제외했다. 작업 도중 변경된 외부 작업 파일은 덮어쓰지 않았다.
- 위 로컬 리팩터링 완료 시점에는 커밋·푸시·PR·병합을 수행하지 않았다. 이후 사용자가 현재 변경 전체의 커밋·푸시와 P4 로컬 구현을 승인했다. 후속 실행 결과는 P4를 시작할 때 이 문서에 별도 기록한다.

## P4 — 준비 상태·요청 수용·시간 제한 (2026-11-12)

시작 기준 커밋은 6acc5aa8f5ca067c17ed382706d4ff6df9cdbb70다. 이전 변경 전체를 리팩터링(480a752), 운영 준비·채팅 헤더(9fcbb6c), CPU·ONNX 실험 기록(6acc5aa)으로 나눠 feat/advanced-hybrid-baseline에 푸시했고 원격 SHA를 확인했다. main 병합은 하지 않았다. 추가 운영 파일의 독립 검토와 비밀값/원문·셸 구문/상태표 점검은 artifacts/verification/p4/precommit-review.json에 남겼다.

### 요구 동작과 지원 경계

기존 검색·모델·HTTP/SSE 정상 응답·필수 저장 계약을 유지한다. /health는 기존 초기화 표시를 유지하고 /health/live와 /health/ready를 추가한다. readiness는 로컬 필수 자원·읽기 전용 SQLite·선택 벡터 저장소·결과 저장 가능성을 10초 캐시/단일 실행으로 확인하며 Qdrant 요청은 2초로 제한한다. Langfuse·유료 LLM은 판정에서 제외한다.

서비스에 실행 1개·FIFO 대기 2개·대기 10초·대기를 포함한 처리 예산 300초·종료 유예 30초를 둔다. 수용 실패는 429/Retry-After 10, 미준비·종료는 503, 실행 예산 초과는 504다. SSE는 헤더 전 수용 검사, 시작 후 오류 시 error만 반환한다. 동기 작업은 강제 종료하지 않으며 완료까지 슬롯/자원을 유지하므로 취소 이후 정리 시간이 추가될 수 있다. 기존 CLI·평가도 같은 서비스 정책을 적용한다.

이 P4 변경은 로컬 구현·검증만 한다. 새 P4 커밋·푸시·배포·Docker·CI/CD·모델/검색 정책 변경·유료 모델 평가는 수행하지 않는다. 지원 대안은 기존 Cloud/NumPy와 uv 실행이며, 분산 대기열·멀티프로세스 전역 제한은 제공하지 않는다.

| 단계 | 상태 | 완료 조건 |
| --- | --- | --- |
| P4-1 설정·준비 상태 | 완료 | 우선순위/값 검증, probe 캐시·장애·회복·종료, live/ready 계약 |
| P4-2 수용·예산·종료 | 완료 | FIFO/대기 초과·취소·soft deadline·shutdown·단일 해제 |
| P4-3 HTTP·SSE 통합 | 완료 | 헤더 전/후 오류·Retry-After·요청 ID·저장 후 final |
| P4-4 리뷰·최종 검증 | 완료 | 두 독립 리뷰, 고정 재생, 전체 검사·문서·검증 증거 |

검증 증거는 artifacts/verification/p4/에 저장한다. 단계별 검사가 끝난 뒤 상태를 갱신한다.

### P4 집중 검사 — 독립 리뷰 전

- 서비스·수용·설정·준비·Qdrant 집중 검사 97개 통과. 캐시/동시 검사, SQLite/저장 경로 장애, FIFO/초과/대기 취소, 동기 작업 중 예산 초과와 종료 정리를 포함한다. 증거: `artifacts/verification/p4/focused-service.log`.
- 고정 그래프 검사 12개 통과, 저장한 10개 실행의 후보·점수·SQL·최종 출처·문맥이 이전 고정 재생과 일치했다. 시각·실행 지문은 비교에서 제외했다. 증거: `graph-comparison.json`, `graph-replay.json`.
- 기존 번들로 NumPy 및 Qdrant Cloud readiness가 모두 준비 상태를 반환했다. Cloud는 컬렉션 메타데이터를 읽기 전용으로 한 번 조회했다. 검사 후 임시 파일 0개, 캐시 재사용 결과 동일. 모델 로딩·추론·유료 호출은 하지 않았다. 증거: `actual-readiness.json`.
- 테스트 지연은 가짜 작업으로 확인한 취소/대기 경계다. 실제 모델 처리량이나 배포 서버 부하 시험으로 해석하지 않는다.
- 리뷰 전 확인 순서: API/SSE 집중 검사 결과 고정 → 두 독립 리뷰 → 지적 사항 수정/재검토 → 최종 공통 검사 및 완료 기록. 아래에 최종 결과를 이어 기록했다.

API 집중 검사 40개를 통과했다. 실제 RagService로 헤더 전 거절, 대기 중 연결 종료, 스트림 도중 서비스 종료와 전송 실패, 동기 작업 정리까지 확인했다. 증거: `artifacts/verification/p4/focused-api.log`.

### P4 독립 리뷰와 수정 — 완료

새 문맥의 독립 에이전트 두 명이 전체 26개 변경 파일을 검토했다. 한 명은 FIFO·취소·동기 작업·자원 소유권을, 다른 한 명은 HTTP/SSE·준비 검사·설정·문서 경계를 중점 확인했다. 두 리뷰 모두 준비 검사 대기가 전체 요청 예산을 적용받지 않는 문제를 지적했다.

`reserve_request`의 준비 검사 대기에 도착 시각 기준 deadline을 적용했다. 요청이 만료되어도 공유 probe는 계속 실행되고, 다른 health 요청이 같은 결과를 받을 수 있다. 차단한 가짜 probe가 끝나기 전에 요청만 만료되며 검색·planner·슬롯 사용이 없음을 회귀 검사로 확인했다. 수정 후 관련 80개 검사와 Ruff가 통과했고 두 리뷰어가 각각 수정과 경계를 재검토했다. 미해결 지적은 없다. 원래 검토본과 수정본은 `review-v1.diff`, `review.diff` 및 각각의 scope 지문으로 남겼다. 증거: `reviews.json`, `review-fix.md`, `review-fix-tests.log`.

### P4 최종 검사 — 완료

아래 증거 경로는 모두 `artifacts/verification/p4/` 기준이다.

| 검사 | 실제 결과 | 증거 |
| --- | --- | --- |
| `python -m ruff check .` | 통과 | final-ruff.log |
| `python -m pytest -q tests` | **616 passed, 11 subtests passed** | final-pytest.log |
| `uv lock --check` | 통과 | final-lock.log |
| 9개 CLI `--help` | 모두 통과 | final-cli-*.log |
| 일반·보안 평가 `--sample` | 가상 실행 2건 통과 | final-sample-*.log |
| `python -m build`·wheel 리소스/제외 파일 | sdist·wheel 및 새 모듈 포함 검사 통과 | final-build.log, final-checks.json |
| 고정 그래프·검색 결과 재생 | 12개 검사, 저장된 10개 실행 동등 | graph-comparison.json |
| 실제 로컬/Cloud 준비 검사 | NumPy·Qdrant 모두 준비, 임시 파일 0개 | actual-readiness.json |
| 기존 SQLite·벡터 등 8개 번들 파일 | 변경 전후 SHA-256 동일 | bundle-preservation.json |
| 변경 문서 7개의 링크·앵커 65개 | 모두 존재 | docs-links.json |
| `git diff --check` | 통과 | diff-check.log |

pytest 경고 1개는 메모리 Qdrant exact 검색에 search_params가 별도 영향을 주지 않는다는 SDK 안내다. 실제 Cloud readiness는 별도 읽기 전용으로 확인했다. 이전 리팩터링의 Cloud 23문항 exact 동등성 기록은 재실행하지 않았으며, 이번에는 변경된 준비 검사와 고정 검색 결과 재생을 확인했다.

### P4 완료 상태와 재개 지점

P4-1~4의 로컬 구현·검증을 완료했다. 런타임 코드는 수정 후 독립 재리뷰에서 고정한 지문과 일치한다. 마지막 변경은 완료 기록과 기준 계획의 상태 표시뿐이며, 링크/구조와 실제 증거를 다시 대조했다. 최종 파일 지문·명령·결과는 `final-content.json`과 `final-checks.json`에 있다.

- 현재 HEAD와 푸시한 원격 브랜치는 `6acc5aa8f5ca067c17ed382706d4ff6df9cdbb70`다. **P4 변경은 로컬 미커밋 상태**다. main 병합·P4 추가 푸시는 하지 않았다.
- CPU 스레드·ONNX 결과는 앞선 실험 기록 커밋에만 포함했다. 제품 모델·스레드 정책에 채택하지 않았다.
- 검증 환경은 WSL Ubuntu-26.04·Python 3.13이다. 실제 모델 처리량/운영 부하·Windows 네이티브·ARM 환경은 검증하지 않았다.
- 유료 LLM 답변·Judge·Ragas와 Langfuse 원격 전송/점수 재조회는 실행하지 않았다. 기존 연결을 유지하고 가짜 외부 경계로 정상 실행·저장·추적 계약을 검사했다.
- Docker·CI/CD·프록시·운영 서버 설정은 적용하지 않았다. 기존 Cloud 컬렉션·SQLite·벡터를 다시 만들거나 변경하지 않았다.
- 재개 시 먼저 `git status`와 `final-content.json`을 대조한다. 변경이 없으면 이번 검증 증거를 사용하고, 달라진 코드가 있으면 해당 경계 검사를 다시 수행한다. 추가 커밋/푸시나 운영 후속 작업은 새로 요청된 범위에서 진행한다.
