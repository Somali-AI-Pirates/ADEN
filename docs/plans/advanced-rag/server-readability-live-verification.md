# 서버 가독성 리팩터링 실제 연결 검증

검증일: 2026-11-13. 대상은 [서버 코드 설계](../../design/server-code-design.md)의 리팩터링이다. 사용자가 실제 질문 실행, Langfuse 조회, 저장된 답변 평가와 점수 게시, 커밋·PR 병합을 요청했다. 기존 승인된 인증과 비용 한도 없음 조건을 사용했다. 운영 배포는 별도 범위다.

## 실행 범위

로컬 CUDA·Qdrant exact 검색·공통 SQLite와 기존 Luna medium을 사용했다. 완성 번들과 검색 정책은 변경하지 않았다. 공식 Advanced 개발 문항의 질문만 서버에 보냈으며, 참고답은 실행이 끝난 뒤 채점할 때 읽었다.

일반 문서 검색 AS-DEV01은 JSON, 명시 코드가 있는 AS-DEV17은 SSE로 실행했다. 이 두 건은 연결 검증 표본이며 전체 검색·답변 성능을 대표하지 않는다. 기존 개인 GPU 실험과 개발 서버는 중단하지 않았다. 검증은 별도 포트 8013의 자동 재시작 없는 서버에서 실행했고, 종료 뒤 정상적으로 자원을 해제했다.

## 실제 실행 결과

| 문항 | 경로 | HTTP 전체 시간 | 응답 | 모델 호출 | 인용 출처 |
| --- | --- | ---: | --- | ---: | ---: |
| AS-DEV01 | /query | 9.12초 | answered | 2 | 1 |
| AS-DEV17 | /query/stream | 13.39초 | answered | 3 | 4 |

두 요청 모두 HTTP 200이며 헤더·응답·실행 기록의 요청 ID가 같았다. 저장된 서비스 모듈 지문이 실행 당시 소스와 일치했다. SSE는 start 1개, 진행 알림, 답변 조각, final 1개 순서로 끝났고 final을 받은 시점에 원자적 저장이 완료돼 있었다. 실행 후 readiness의 6개 구성요소도 정상이다. 측정 시간은 동시 개인 실험이 있는 로컬 GPU에서의 두 요청이며 처리량 벤치마크가 아니다.

AS-DEV01은 하이브리드로 시작했다. AS-DEV17은 공통 기록 DB로 시작한 뒤 read_sources를 실행했다. 이 표본은 query_tables를 실행하지 않았다. SQL 안전성·거절과 나머지 도구 경계는 기존 고정 계획 재생 및 자동 테스트로 확인한 범위다.

## 실행 중 발견하고 수정한 결함

### 추적 필터의 긴 문자열 지연

처음 실제 요청에서 liveness까지 지연됐다. 예외 본문을 지우던 중첩 정규식에 64자 식별자를 넣자 별도 프로세스의 3초 제한을 넘겼다. 첫 부분 수정도 점이 반복되는 긴 이름에서는 겹치는 검색 때문에 느려질 수 있어 채택하지 않았다.

최종 구현은 식별자 토큰을 비중첩으로 순회하고 Error/Exception 접미사와 콜론을 확인한다. 예외 문장을 가린 뒤 인증값 필터를 항상 적용한다. 100,000자 일반·점 포함 문자열, 단어 경계, 예외 형식과 앞에 있는 인증값을 subprocess 시간 제한으로 회귀 검사했다. 실행·추적 담당 독립 리뷰 두 건을 거쳤다.

최초 지연 요청은 약 178초 뒤 응답했으며 이후 진단 서버 종료 중이던 SSE는 완료 표본으로 세지 않았다. 중간 수정본의 정상 응답도 별도 보존하고, 위 표는 최종 토큰 스캐너를 불러온 서버의 결과만 사용한다.

### 원격 기록의 토큰 총량 복원

공식 Langfuse 콜백은 입력 캐시와 출력 추론 토큰을 usageDetails의 별도 항목으로 나눈다. 이전 HTTP 복원기는 bare input/output만 읽어 한 계획 호출을 126/26으로 복원했지만 실제 총량은 7,693/86이었다.

복원기는 inputUsage/outputUsage 총량을 우선하고, 총량이 없으면 분리된 항목을 합산한다. 미측정 null과 API 기본값 0을 구분한다. 최종 두 trace에서 복원한 모든 호출의 총량이 로컬 기록과 일치한다. 기존 수동 기록, 캐시·추론 토큰, 명시 0과 미측정 경계를 테스트했고 독립 리뷰를 받았다.

## Langfuse 원격 확인

요청별 trace가 분리되고 rag-query 아래 그래프와 답변 모델이 연결됐다. 모델 호출당 generation이 하나이며 planner-N / answer-N 이름이 유지됐다. retrieval·final-context에서 복원한 실제 문맥과 hash가 로컬 기록과 같았다. 원격 데이터를 읽어 비밀키 값과 숨겨진 추론 payload가 없는지 확인했다.

Langfuse의 공개 프로젝트 식별자는 SDK instrumentation scope의 public_key 필드에 남는다. 이는 모델 입출력에 들어가지 않으며 비밀키와 구분한다. 공식 masking hook의 보호 범위는 관측 속성이고 모든 임의 OpenTelemetry scope·event·resource를 수정하는 것은 아니다.

| 문항 | 요청 ID | trace ID | generation |
| --- | --- | --- | ---: |
| AS-DEV01 | f4ec82408e214a1193dd15ccab47edd7 | 185855cac464641f05134b656c4d9214 | 2 |
| AS-DEV17 | 1ecd92cef11d4a518409752245504a19 | db2e09005d066df175375e55c2b197cf | 3 |

## 저장된 답변 평가

| 문항 | 참고답 | Ragas Faithfulness | Judge 사실·조건·근거·판단 |
| --- | --- | ---: | --- |
| AS-DEV01 | 사용하지 않음 | 0.667 | 참고답이 없어 unavailable |
| AS-DEV17 | 공식 개발 파일의 같은 문항 | 1.000 | 네 축 모두 1.000 |

AS-DEV17의 최종 페이지 Recall과 전체 필수 근거 충족도 1.0이었다. AS-DEV01의 Faithfulness는 1.0이 아니며, 연결 성공을 답변의 완전한 정확성으로 해석하지 않는다. Ragas wrapper가 제공하지 않는 사용량은 null로 유지했다. 점수를 올리기 위해 질문·검색·프롬프트·참고답을 바꾸지 않았다.

채점 결과 1개와 20개, 합계 21개 점수를 원래 trace에 게시했다. 최초 5초 조회에서는 수집 지연으로 not_found였고, 이후 읽기 전용 조회에서 모두 verified를 확인했다. 같은 ID로 재게시한 뒤에도 21개가 verified이고 식별자가 그대로였다. not_found를 성공으로 계산하지 않았다.

## 재현과 증거

질문·소스 지문·원격 복원·평가·게시 조회 결과는 로컬 artifacts/verification/server-readability-live-20261113/에 있다. 실제 답변·원문 문맥은 artifacts/rag-runs/<request_id>/에 보존하고 Git에 포함하지 않는다.

- 질문/API: questions.json, plan.json, api-results.json, final-readiness.json
- 장애 진단: masking-stall.json, before-mask-fix-api-results.json, trace-usage-diagnosis.json
- 원격 확인: trace-readback.json, score-readback.json, 각 grade의 republish.json
- 평가: 각 실행의 grades/live-faithfulness 또는 grades/live-reference
- 코드 검사: final-checks.json과 해당 로그, 파일 지문·리뷰 기록

서버는 기본 빠른 시작으로 실행하고 아래 평가 명령의 RUN_DIRECTORY를 저장된 경로로 바꾼다. 두 번째 문항만 REFERENCES·REFERENCE_ID와 Judge 옵션을 함께 사용한다.

    .venv/bin/python -m app.evaluation.scripts.run_table_rag grade --run-directory RUN_DIRECTORY --grade-id NEW_GRADE_ID --ragas-model gpt-6-luna
    .venv/bin/python -m app.evaluation.scripts.run_table_rag grade --run-directory RUN_DIRECTORY --references data/advanced/evaluation/development.json --reference-id AS-DEV17 --grade-id NEW_GRADE_ID --judge-model gpt-6-luna --ragas-model gpt-6-luna
    .venv/bin/python -m app.evaluation.scripts.run_table_rag publish --grade-directory GRADE_DIRECTORY --verify

최종 로컬 Ruff, pytest 685개·하위 검사 11개, lock, sdist·wheel 빌드가 통과했다. 이전 677개 검사 이후 추가한 마스킹·사용량 회귀를 포함한 결과다. CLI·샘플 평가와 패키지 리소스·문서 링크 확인은 최종 검사 기록을 따른다. PR의 원격 CI와 병합 SHA는 GitHub PR 및 로컬 integration.json을 기준으로 한다. main 병합 뒤 이미지 게시가 실행될 수 있으나 운영 배포는 활성화하지 않는다.
