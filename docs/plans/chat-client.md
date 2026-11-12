# ADEN 문서 채팅 클라이언트

## 범위와 호환 경계

사내비의 Next.js 화면·한국어 입력·오류/취소 UX를 참고해 `web/`에 독립 클라이언트를 만든다. assistant-ui가 대화 상태를 소유하고, 기존 Python TableRAG 서비스를 그대로 호출한다. 기존 `POST /query`와 CLI·평가 계약은 보존한다. 새 `POST /query/stream`은 진행 상태, 임시 답변 텍스트, 저장·검증 완료 후 최종 QueryResponse를 SSE로 보낸다. PDF는 설정된 원본 디렉터리의 문서 ID로만 제공한다. 사내비 저장소와 기존 데이터는 변경하지 않는다.

이번 범위는 스트리밍, 출처 카드, 출처 페이지 PDF 열람, 취소·오류·추가 확인 상태, 실행 방법과 검사다. 대화는 화면 메모리에만 있고 서버에는 현재 질문만 전달한다. 다중 턴 기억, 계정/권한 체계 신설, 문장 좌표 강조, 외부 배포, 커밋·푸시는 포함하지 않는다. 서버의 기존 인증 배포 경계는 API와 PDF에 동일하게 적용해야 한다.

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
