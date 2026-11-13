# ADEN 문서 채팅 클라이언트

## 범위와 호환 경계

사내비의 Next.js 화면·한국어 입력·오류/취소 UX를 참고해 `web/`에 독립 클라이언트를 만든다. assistant-ui가 대화 상태를 소유하고, 기존 Python TableRAG 서비스를 그대로 호출한다. 기존 `POST /query`와 CLI·평가 계약은 보존한다. 새 `POST /query/stream`은 진행 상태, 임시 답변 텍스트, 저장·검증 완료 후 최종 QueryResponse를 SSE로 보낸다. PDF는 설정된 원본 디렉터리의 문서 ID로만 제공한다. 사내비 저장소와 기존 데이터는 변경하지 않는다.

초기 구현 범위는 스트리밍, 출처 카드, 출처 페이지 PDF 열람, 취소·오류·추가 확인 상태, 실행 방법과 검사였다. 이때는 화면 메모리에 대화를 보관하고 서버에 현재 질문만 전달했다. 후속 질문 지원과 검증 범위는 [멀티턴 구현과 검증](#멀티턴-구현과-검증--2026-11-13)에 기록한다. 서버의 기존 인증 배포 경계는 API와 PDF에 동일하게 적용해야 한다.

## 순서

1. 현재 JSON 응답의 출처와 PDF 페이지 열람을 연결한다.
2. 실제 최종 모델 답변의 스트리밍과 진행 상태를 연결한다. 최종 검증·저장 전 내용은 임시이며 성공으로 확정하지 않는다.
3. 취소·동시 요청 격리·저장 실패·출처 오류, 정적 프런트 배포를 검사한다.
4. 허용된 기존 키로 실제 질문 1~2건을 실행하고 결과·출처·저장 기록을 대조한다.

## 참고와 재사용

- 사내비 `web/features/question`: 한국어 IME, 모바일 입력, 취소·오류 시 입력 보존, 독서 중 자동 스크롤 방지. 별도 ChatSession은 이식하지 않는다.
- [assistant-ui LocalRuntime](https://www.assistant-ui.com/docs/runtimes/custom/local-runtime)
- [assistant-ui Sources](https://www.assistant-ui.com/elements/sources)
- [React-PDF](https://github.com/wojtekmaj/react-pdf)
- [Microsoft 원문 열람 패널](https://github.com/Azure-Samples/azure-search-openai-demo/blob/main/app/frontend/src/components/AnalysisPanel/AnalysisPanel.tsx)
- [nginx 응답 버퍼링](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_buffering)

## 검증 기록

구현 후 명령·결과·남은 범위를 기록한다. 이 대화에서는 독립 에이전트 사용이 금지되어 자체 검토를 수행하며 독립 리뷰 완료로 표시하지 않는다.

## 구현과 검증 결과 — 2026-11-12

### 반영한 동작

- `web/`: Next.js 정적 화면, assistant-ui LocalRuntime, 한국어 IME와 모바일 입력, 취소·재시도, Markdown 답변, 최종 출처 카드, React-PDF 페이지 열람.
- `app/api/chat.py`: 질문 전용 SSE와 원본 PDF API. PDF는 설정된 원본 디렉터리 바로 아래 문서만 제공하며 경로 이동·파일 심볼릭 링크를 거절한다. Range 요청을 지원한다.
- `app/rag/streaming.py`: 같은 최종 모델 호출의 구조화 JSON에서 답변 텍스트만 점진적으로 전달한다. 추론·도구 인자·원시 JSON은 화면으로 보내지 않는다.
- 기존 서비스의 검증·실행 기록 저장 후에만 final을 전송한다. 저장 실패·모델 실패·중도 연결 종료는 성공으로 확정하지 않는다. 요청별 큐와 취소 범위를 분리한다.
- 기존 JSON `/query`, 검색·도구 정책, 모델·데이터, 평가 CLI는 유지한다. 스트림에서도 같은 서비스와 저장 계약을 사용한다.
- Next.js는 사내비의 16.4.0을 그대로 이식하지 않고 npm audit에서 확인한 수정 버전 16.4.6으로 고정했다. 사내비 저장소에는 변경하지 않았다.
- 서버 빠른 시작과 계약 문서에서 [클라이언트 안내](../../web/README.md)로 연결했다.

### 검사

| 검사 | 결과 |
|---|---|
| Ruff 전체 `python -m ruff check .` | 통과 |
| 스트림·PDF·기존 TableRAG API/모델/서비스 집중 pytest | 22개 통과 |
| `web/npm test` | 4개 통과: UTF-8/SSE 경계, 요청 ID, 불완전 연결, 저장 실패, 최종 응답 |
| `web/npm run test:e2e` | Chromium 5개 통과: PC·모바일 PDF와 포커스 복귀, 부분 실패, 한글 조합, 취소와 초안 보존, PDF 404 |
| `web/npm run typecheck` 및 `npm run build` | 통과, 정적 out 생성 |
| `web/npm audit --omit=dev` | 취약점 0건. 잠금 파일 재확인에서도 전체 취약점 0건 |
| Python 패키지 빌드 | wheel·sdist 생성. 새 API·스트림 모듈과 프롬프트 포함 확인. 기존 루트 README 부재 경고 존재 |
| 문서 상대 링크와 `git diff --check` | 통과 |
| 전체 `python -m pytest -q tests` | **469 통과, 9 실패**. 아래 기존 충돌 참조 |

전체 pytest 실행 뒤 추가한 동시 스트림 격리 검사는 집중 검사 22개에 포함한다. 전체 통과 수에 이를 임의로 합산하지 않는다.

### 실제 연결

승인된 기존 키로 질문 두 건만 실행했다. 정적 `out/` → 별도 로컬 nginx → FastAPI → 현재 TableRAG를 사용했다. 벡터 재생성이나 추가 답변·Judge 호출은 하지 않았다.

| 표본 | 요청 ID | 결과 | 기록 |
|---|---|---|---|
| 명시 사건 ID 질문 | `c3f45e67c05f4e67a256b8f28a5b2d31` | answered, 출처 1개, 서비스 약 10.2초 | 모델 호출 3회, 실행 기록·trace ID 저장 |
| 일반 규정 질문 | `d73b7c27bbac41daae66fd969209a1ae` | answered, 출처 1개 | 모델 호출 3회, 답변 조각 99개, 화면 텍스트 갱신 63회 |

두 번째 표본은 첫 답변 텍스트가 약 25.0초, 마지막 텍스트가 약 26.1초에 보였다. 조회·계획에 걸린 대기 시간은 스트리밍만으로 없어지지 않는다. 진행 상태를 먼저 표시한다. 요청 헤더 ID, 최종 응답 ID, 저장 기록이 일치했고 답변·출처 내용도 같았다. 출처를 클릭해 실제 PDF의 해당 페이지가 렌더링되는 것을 확인했다.

첫 표본은 브라우저 개발자 프로토콜의 response.text()가 SSE 본문을 회수하지 못해 조각 수 계측은 남기지 못했다. 저장된 실제 성공 기록으로 결과를 확인했고 같은 질문을 재호출하지 않았다. 두 번째 표본은 fetch 스트림 사본과 DOM 변화를 관측해 검증했다.

상세 수치와 화면은 로컬 `artifacts/client-verification/`, 원래 요청은 `artifacts/rag-runs/<request_id>/`에 있다. 원문·답변·PDF 화면은 공유 문서에 복사하지 않았다. Langfuse의 기록 상태는 두 건 모두 `recorded_unverified`이며, 이 작업에서 외부 대시보드·점수 재조회까지 완료했다고 주장하지 않는다.

### 기존 실패와 검토 범위

전체 pytest의 실패는 기존 레거시 자원 테스트가 기본 Advanced 설정을 복사하면서 새 `rag_backend: table_rag`를 함께 가져오는 데서 발생한다. 해당 테스트는 레거시 로더·자원 오류를 기대하지만 현재 lifespan은 TableRAG 경로를 선택한다.

- `tests/service/test_prepared_context.py::test_api_and_evaluation_share_configured_settings_and_resources`: 매개변수 2건.
- `tests/service/test_resources.py::ResourceTests::test_misconfigured_connection_names_the_setting_without_private_details`: 하위 검사 6건.
- `tests/service/test_resources.py::ResourceTests::test_startup_runs_real_pipeline_and_closes_shared_clients`: 1건.

작업 시작 시 보관한 main/model/service 코드로 해당 테스트를 다시 실행해 동일한 9개 실패를 확인했다. 파일의 실제 `__file__`과 자원 경로를 유지한 별도 프로세스에서 재생했으며 작업 트리 파일을 되돌리지 않았다. 이 충돌을 해결하려고 진행 중인 서버 초기화나 레거시 계약을 변경하지 않았다.

`implementation-final-review` 기준으로 데이터 접근·취소·동시성·완료/오류 계약과 화면 접근성을 자체 검토하고, `code-change-verification`으로 위 검사를 수행했다. 이 사이드 대화의 위임 금지에 따라 **독립 리뷰는 미실시**다. `pr-draft-summary` 기준으로 문제·변경·검증을 이 문서와 최종 인계에 요약하며, 커밋·푸시·외부 배포는 하지 않았다.

### 남은 지원 범위

- 대화 기억·저장, 사용자 계정·문서별 권한, 문장별 인용·PDF 문장 강조는 이번 범위가 아니다.
- 운영 인증을 적용할 때 채팅·API·PDF를 같은 접근 경계 안에 둔다.
- Chrome/Chromium에서 검사했다. 다른 브라우저의 별도 실기기 검증은 하지 않았다.

## 멀티턴 구현과 검증 — 2026-11-13

구현은 로컬 커밋 `f1e8a2f`에 있다. 화면에 대화가 남아 있어도 최신 질문만 전송하던 경로를 바꿨다. 이제 [브라우저](../../web/lib/chat-adapter.ts)가 같은 채팅에서 완료된 질문·답변을 최대 8턴까지 보낸다. 새 채팅과 새로고침에서 이력은 초기화된다. [스트리밍 계약](../design/contracts.md#브라우저-스트리밍과-원본-문서)이 입력 형식과 길이 제한의 기준이다.

- [TableRAG 그래프](../../app/rag/graph.py)는 이력이 있을 때 `initial_search` 앞에서 대화 맥락을 해석한다. 최근 2턴을 먼저 확인하고, 모델이 더 필요하다고 판단하면 요청에 포함된 이전 턴까지 한 번 더 본다. 현재 질문에 명시한 코드는 유지하며, 현재 질문이나 선택한 대화에 근거가 없는 코드는 추가할 수 없다.
- 대상을 확정하면 풀어 쓴 질문으로 이번 요청의 근거를 찾는다. 대상을 확정하기 어렵다면 조회를 건너뛰고 되묻는다. 사용자가 식별자만 답해도 직전의 미완료 질문과 대상이 연결되면 그 질문을 이어서 처리한다.
- [답변 모델](../../app/rag/model.py)은 시스템 메시지, 선택한 이전 사용자·어시스턴트 메시지, 이번 질문과 근거를 순서대로 받는다. 검색 질문을 재구성한 경우 이번 입력에 함께 담는다. 과거 대화는 지시 대상과 조건을 이해하는 데 쓰이며, 답변의 인용은 이번에 확보한 출처로 검증한다.

### 실제 모델과 합성 자료

기존 키로 GPT-6 Luna `medium`을 호출하고 `/query/stream`으로 3개 표본을 실행했다. 검색과 기록 조회에는 합성 자료를 사용했다.

| 표본 | 결과 |
| --- | --- |
| 최근 대화에 대상이 있음 | 2·3번 턴만 보고 3번 턴의 대상을 확정해 조회 |
| 오래된 대화에만 대상이 있음 | 2·3번 턴에서 부족하다고 판단해 1·2·3번 턴을 확인하고 1번 턴의 대상을 조회 |
| 대상이 둘 이상이라 모호함 | 추가 확인을 요청하고 검색·기록 조회 0회 |

3건 모두 통과했다. 실제 모델 호출은 합계 10회였다. 최종 답변 입력은 세 표본 모두 `system → user → assistant → user`였다.

### 실제 Advanced 자료와 Chromium

하나의 새 채팅에서 세 질문을 순서대로 보내며 브라우저가 이력을 쌓는 과정부터 확인했다. 로컬 Advanced 번들, CPU, NumPy exact 설정, 공통 SQLite와 GPT-6 Luna `medium`을 사용했다.

| 턴 | 사용자 동작 | 확인 결과 |
| --- | --- | --- |
| 1 | 두 예약의 실제 반출일을 질문 | 공통 SQLite에서 두 기록을 찾고 각각의 날짜와 출처를 반환 |
| 2 | 그중 한 건의 두 청구 항목 합계를 질문 | 어느 예약인지 되물었고 조회·출처 0건 |
| 3 | 예약번호만 입력 | 앞선 두 청구 항목의 합계 질문으로 복원하고, 해당 예약의 청구 기록으로 답변 및 출처를 검증 |

요청의 이력 길이는 0·1·2턴이었다. 마지막 답변 모델에는 `system → user → assistant → user → assistant → user`가 전달됐다. 세 요청 모두 SSE `final`까지 받았고, 응답 헤더·이벤트·저장 기록의 요청 ID가 일치했다. 성공한 대화에서 모델 호출은 7회였다. 최초 브라우저 계측 실패 후 재실행까지 합하면 9회였다.

### 검사와 증거

- 구현 커밋 스냅샷에서 Ruff와 전체 pytest 692개·하위 검사 11개가 통과했다. 동일 웹 코드로 단위 검사 4개, Chromium 검사 8개, 타입 검사와 정적 빌드를 통과했다.
- 실제 Advanced 연결은 동시 개발 변경이 있던 당시 작업 트리에서 확인했다. 해당 실행의 소스와 번들 지문은 `plan.json`에 있다. 이 표본은 SQLite 초기 조회와 되묻기 연결을 검증했다. 하이브리드 검색, 표 계산 도구, Qdrant, 원격 Langfuse, 운영 배포는 이 실행의 검증 범위 밖이다.
- 합성 실행은 `artifacts/verification/chat-multiturn-live-20261113/`의 `results.json`, `execution.json`, `provider-requests.jsonl`에 있다. 실제 Advanced 실행은 `artifacts/verification/chat-multiturn-advanced-20261113/`의 `verified-results.json`, `plan.json`, `browser-replay/`에 있다. 검색 질문과 실제 최종 모델 메시지 원본은 로컬 증거에 보관한다.
