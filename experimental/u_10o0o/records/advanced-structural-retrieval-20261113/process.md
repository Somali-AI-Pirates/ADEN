# 실행 과정과 범위

## 실행 전 검토
- 이전 추가 30문항 실험의 A 런타임과 입력을 고정했다. 90회 저장 결과는 재사용 표시를 붙였다.
- 신규 검증 문항은 검색 결과를 제공하지 않고 작성·근거 검수 호출을 분리했다. 첫 초안은 반복 화주명을 유일한 대상으로 오인할 위험이 있어 폐기하고, 명시/복수 대상에는 완전 코드, 간접 대상에는 화주·화물·실제 날짜를 요구해 다시 작성했다. 첫 초안의 API/검수 기록도 로컬에 보존했다.
- 모호성 5문항에는 임의 사건의 구체 금액을 정답으로 강제하지 않는다. 한 검수 응답이 모호한 질문을 답변 가능으로 표시한 오류를 실행 전에 바로잡고, 5문항 모두 추가 확인 동작을 별도 평가한다.
- 보조 세트는 새 질문이지 새 문서 세트가 아니다. IS 규정 및 일부 사건은 기존 세트와 공유한다. AI 작성·AI 검수·자체 점검이며 사람 검수 또는 독립 최종 채택 검증을 뜻하지 않는다.

## 구현 경계
S는 A의 관측 텍스트만 다시 읽는다. V는 최종 후보 20쪽 각각에서 머리말 64토큰과 질문 관련 원문 창을 합쳐 최대 약384토큰을 제공한다. 따라서 V는 관측 범위와 발췌 위치를 함께 바꾼 조건이며, 결과를 발췌 위치 하나의 효과로 해석하지 않는다.
S/V는 페이지 근거의 요청 연결을 시험한다. 기존 SQL 결과는 그대로 유지하지만 새 선택 스키마의 인용 대상은 페이지다. SQL 전용 요청의 충분 판정까지 완성된 제품 기능으로 주장하지 않는다.
R은 검색기별 Top50의 고유 페이지를 리랭커에 최대100쪽 전달하고 상위20쪽과 그 표만 노출한다. 후속 검색은 모델 선택에 따라 달라질 수 있다.
P는 IS의 규정·설명 문단을 보조 검색 단위로 더한다. 적용 범위가 같은 규칙을 자동 병합하지 않는다. BM25 전체 통계도 바뀌므로 효과는 보조 색인 구성의 효과다.

## 실행 전 검사
- 실험 Ruff 통과, 경계 테스트12개 통과.
- 공통 Ruff 통과, 전체 tests 666개 및 subtest11개 통과. Qdrant local exact 모드 경고1건은 기존 테스트에서 발생했다.
- A/R/P의 같은 첫 질문을 실제 호출해 결과 저장·후보20·최종8·8,000토큰 경계를 확인했다. 이 표본도 본 실험에 포함한다.
- 공개 기록에는 원문과 참고답 인용을 복사하지 않는다. 원문·질문·API 입출력은 ignored artifacts에 둔다.
- 사이드 대화 제한에 따라 하위 에이전트나 독립 리뷰를 사용하지 않고 자체 경계 검토를 수행했다.

## 실행 명령
저장소 루트에서 아래 명령을 사용한다. `freeze` 이후에는 질문/조건을 다시 생성하지 않는다. 재개는 완료 결과를 재사용하며 실패/중단 호출은 자동 재시도하지 않는다.

```bash
uv run python -m experimental.u_10o0o.structural_retrieval_ab.run freeze
uv run python -m experimental.u_10o0o.structural_retrieval_ab.run run
uv run python -m experimental.u_10o0o.structural_retrieval_ab.select_runs run
uv run python -m experimental.u_10o0o.structural_retrieval_ab.evaluate review
PYTHONPATH=. uv run python experimental/u_10o0o/records/advanced-structural-retrieval-20261113/audit_reviews.py
# 인용 검증 오류와 의미상 오류를 원문으로 검수한 뒤:
uv run python -m experimental.u_10o0o.structural_retrieval_ab.evaluate candidate-review
uv run python -m experimental.u_10o0o.structural_retrieval_ab.evaluate summarize
uv run python experimental/u_10o0o/records/advanced-structural-retrieval-20261113/score_status.py
```

중간 결과는 조건 선택에 사용하지 않는다. 모든 검색 결과 저장 뒤 고정된 필수 요구를 읽어 의미 채점한다. 원문 인용이 맞다는 사실만으로 역할·시점·적용 범위까지 맞다고 보지 않는다.

## 기술적 입력 수정
V의 토큰 창에서 한글 UTF-8 문자가 잘린 경계에 replacement character가 들어가는 문제를 원문 부분 문자열 검사로 발견했다. 의미 채점 전 해당 V26개를 무효화하고 raw/API 기록을 보존했다. 경계의 불완전 바이트만 제외해 원문 연속 구절이 되도록 수정하고, 한글 전용 테스트를 추가했다. 질문·참고 사실·선택 지침·검색 모델·검색 알고리즘은 바꾸지 않았다. A/R/P/S는 동일하다. 이전 프로토콜과 변경 사유는 technical-amendment.json 및 로컬 invalidated-selector-v1에 남겼다. 중단 당시 응답이 저장되지 않은 S1회는 수동 재발행으로 기록했으며, 미확인 공급자 사용량은 0으로 간주하지 않는다.

종료 상태는 고정 데이터의 accepted_action_labels를 사용해 answer→sufficient, clarify→needs_clarification, abstain→insufficient로 정확하게 대응한다. 일부 공식 문항은 answer/abstain을 모두 허용한다. 이 정렬은 검색 출력이나 의미 근거 점수를 바꾸지 않는다. 데이터가 특정 충돌 근거를 요구하는 보류 문항은 근거 충족도 함께 채점하며, 참고 근거가 없는 문항만 별도 상태 평가로 둔다.

후보 지표의 논리 일관성: 같은 질문·동일 후보 원문·동일 SQL에서 어느 한 최종 부분집합이 검증된 전체 근거를 확보했다면, 그 후보는 다른 비교군에서도 충분하다. 후보 전체를 다시 읽은 Judge가 이를 부정해도 검증된 부분집합 인용을 우선하며 원시 판정과 보정 근거를 함께 기록한다. 이 원칙은 품질 채점 전에 정했고, A/S/V의 동일 후보를 서로 다르게 채점하지 않기 위한 것이다.

검색 비용은 그래프의 source group 개수와 구분한다. 원시 searches 배열에는 records·tables·sql 결과 묶음도 들어 있다. 보고서의 실제 하이브리드 호출은 rrf_candidates가 있는 실행만 세며, 표 카탈로그·DB 결과 묶음·SQL 호출은 별도 집계한다. KURE 쌍·토큰 수는 실제 리랭커 진단 값을 사용한다.


## 채점 완료와 사후 검수
검색 1,900개 파일 저장 완료 시각은 2026-11-12 17:43:48 UTC, 첫 의미 채점 시작은 17:43:51 UTC다. 이 순서는 scoring-order.json으로 확인했다. 최종 문맥 668개, 2차 의미 검수 221개, 최종 판정에 연결된 후보 문맥 90개를 확인했다. 후보 첫 검수 묶음 93개와 이후 문항 범위·인용 정정 전 판정도 로컬에 보존했다.

인용 오류는 별도 repair_review_quotes.py 호출과 실제 원문 확인으로 해결했다. 표 이메일의 두 조각 사이에 다른 열이 들어간 경우 행 전체를 인용했고, 날짜를 청구 명세에 잘못 붙인 경우 실제 반환된 현장 사건 출처로 바로잡았다. 동일 질문의 동일 원문 인용에 서로 다른 지원 판정이 붙은 사례는 proof-consistency-review.json으로 정리했다. 이 과정에서 근거 부족을 통과시키는 수정만 한 것은 아니다. SI 정정 연락처를 신규 제출의 근거로 본 판정도 취소했다.

ADD02와 DEV09의 장비·화물 적용 범위 누락은 모든 조건의 후보·최종 점수에 같은 모호성 기준을 적용했다. 분모에서 제외하지 않았다. 새 HOLD002는 질문이 잔액을 묻는데 참고답의 추가 회수 절차까지 요구하던 판정을 질문 범위에 맞췄다. 원래 정답 파일·검색·계획 입력은 변경하지 않았으며 이전 판정과 점수는 before-question-scope-*에 보존했다.

생성된 audit-summary.json과 literal-repair.json은 각 중간 단계의 남은 오류 수다. 최종 완료 여부는 verification.json과 evaluation-protocol.json의 pending=0을 사용한다.

최종 집계는 다음 순서로 실행했다. 저장된 사후 판정 파일과 전체 로컬 실행 자산이 필요하다. 새로 모델을 호출하면 원래의 확률적 결과가 완전히 재현된다고 보장하지 않는다.

```bash
# 원문 검수와 사후 판정 파일을 확인한 뒤 저장 결과만 집계
uv run python -m experimental.u_10o0o.structural_retrieval_ab.evaluate summarize
uv run python experimental/u_10o0o/records/advanced-structural-retrieval-20261113/score_status.py
uv run python experimental/u_10o0o/records/advanced-structural-retrieval-20261113/candidate_consistency.py
uv run python experimental/u_10o0o/records/advanced-structural-retrieval-20261113/claim_changes.py
uv run python experimental/u_10o0o/records/advanced-structural-retrieval-20261113/request_accounting.py
uv run python experimental/u_10o0o/records/advanced-structural-retrieval-20261113/generate_metrics.py
uv run python experimental/u_10o0o/records/advanced-structural-retrieval-20261113/stage_flow.py
uv run python -m pytest -q -p no:cacheprovider experimental/u_10o0o/structural_retrieval_ab/test_experiment.py
```

pytest 실행 파일을 직접 실행하면 experimental import 경로가 빠지는 환경이 있었다. 저장소 루트에서 python -m pytest로 실행해 12개가 통과하는 것을 확인했다. 공통 최종 pytest는 동시 진행 중인 다른 작업을 포함한 현재 체크아웃에서 685개와 subtest 11개가 통과했다. 동결 실험 실행은 원래 런타임을 그대로 유지했다. 공통 ruff check는 통과했고, 추가 저장소 전체 format 검사는 범위 밖 96개 파일에서 실패해 일괄 수정하지 않았다.

호출 토큰은 모델 기록의 usage.input_tokens/output_tokens를 읽는다. 이를 최상위 필드로 잘못 집계하면 0이 되므로 실제 중첩 usage로 검증했다. S/V가 재사용한 A 계획 호출은 워크플로 지표에는 포함하지만 새 비용 합계에서는 다시 세지 않았다. 답변 생성은 0회다.
