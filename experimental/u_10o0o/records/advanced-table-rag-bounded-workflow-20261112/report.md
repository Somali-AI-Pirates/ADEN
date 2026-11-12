# 요청 계획과 원문 근거 단위를 분리한 TableRAG 비교

> 실험 종료 후 최종 선택은 [통합 DB 우선·인용 검사 OFF](../advanced-table-rag-closeout-20261112/decision.md)다. 아래 A/B·점수는 이 실행의 기록이며 다른 실행의 기준선 점수와 합치지 않는다.

**새 B를 기준선으로 채택하지 않는다.** 실제 근거 충족은 공식53→46/58, 카테고리188→158/200으로 낮아졌고 전체 p50은5.74→19.53초로 늘었다. 기존 DB 우선 A를 유지한다.

A는 기존 통합 DB 우선 경로, B는 새 원문 근거 색인·요청별 실행·근거 ID 선택을 함께 적용한 경로다. 양쪽 공식58·카테고리220을 새로 실행했다. 답변 생성은 하지 않았다.
KURE-v3 임베딩·KURE nano·Luna medium, 검색별 후보20·합친 후보20·원본8쪽·8,000토큰을 유지했다. 이번에는 완료 시간을 줄이기 위해 양쪽 동시8요청으로 고정했다. 과거 동시4요청 지연과 직접 비교하지 않는다.

## 실제 반환 근거와 지정 근거

| 묶음 | n | A 지정 | B 지정 | A 실제 문맥 | B 실제 문맥 | 개선/하락 |
|---|---:|---:|---:|---:|---:|---:|
| 공식 | 58 | 36/58 | 31/58 | 53/58 | 46/58 | 2/9 |
| 공식 복합 | 51 | 31/51 | 26/51 | 47/51 | 41/51 | 2/8 |
| 카테고리 근거형 | 200 | 179/200 | 144/200 | 188/200 | 158/200 | 3/33 |
| 번호로 단일 기록 | 20 | 20/20 | 20/20 | 20/20 | 20/20 | 0/0 |
| 번호 없는 단일 사건 | 20 | 20/20 | 15/20 | 20/20 | 15/20 | 0/5 |
| 한 사건 여러 기록 | 20 | 20/20 | 20/20 | 20/20 | 20/20 | 0/0 |
| 두 사건 비교 | 20 | 20/20 | 12/20 | 20/20 | 12/20 | 0/8 |
| 날짜·승인·금액 | 20 | 20/20 | 20/20 | 20/20 | 20/20 | 0/0 |
| 모호한 두 후보 | 20 | 17/20 | 3/20 | 17/20 | 7/20 | 0/10 |
| 확인 자료 부족 | 20 | 20/20 | 20/20 | 20/20 | 20/20 | 0/0 |
| 업무 규정 | 20 | 19/20 | 16/20 | 19/20 | 18/20 | 0/1 |
| 일상 표현 단일 | 20 | 17/20 | 15/20 | 20/20 | 20/20 | 0/0 |
| 일상 표현 복합 | 20 | 6/20 | 3/20 | 12/20 | 6/20 | 3/9 |

공식은 고정된58문항이며 이번 결과로 제외한 문항은 없다. 카테고리의 범위 밖20문항은 근거형200문항과 분리한다. 같은 대상·역할·적용 시점의 대체 근거는 인정하되, 다른 사건이나 일반 설명으로 필요한 값을 대신하지 않는다.
공식은 지정 통과도 포함한 모든 출력을 검수했고, 카테고리는 한쪽이라도 지정 근거를 놓친 문항의 양쪽과 기존 모호 문항을 검수했다. 총236개 출력이다. 전체 AI 판정과 원문 인용 검사를 수행했고, 동일 입력·동일 문맥의 기존 자체 판정56건을 재사용했다. 인용 오류·범위 해석 등 예외를 자체 대조했으며 모든 출력을 사람이 검수한 것은 아니다.

## 후보와 최종 원문

| 묶음 | A 모든 조회 전체 확보 | B 모든 조회 전체 확보 | A 후보20 전체 확보 | B 후보20 전체 확보 | A 페이지 Recall@8 | B 페이지 Recall@8 |
|---|---:|---:|---:|---:|---:|---:|
| 공식 | 41/58 | 39/58 | 39/58 | 36/58 | 81.5% | 74.3% |
| 카테고리 근거형 | 184/200 | 160/200 | 182/200 | 153/200 | 93.0% | 83.2% |
| 일상 표현 단일 | 17/20 | 17/20 | 17/20 | 16/20 | 85.0% | 75.0% |
| 일상 표현 복합 | 11/20 | 7/20 | 9/20 | 4/20 | 55.8% | 51.7% |

후보와 페이지 Recall은 지정 근거 기준이며 대체 근거 검수로 숫자를 수정하지 않았다. 모든 조회는 검색·DB·SQL 출처의 합집합이다.

## 호출과 지연

| 항목 | A | B |
|---|---:|---:|
| 전체 p50(초) | 5.74 | 19.53 |
| 전체 p95(초) | 21.19 | 40.89 |
| 계획/선택 API | 451 | 704 |
| 하이브리드·리랭킹 | 254 | 457 |
| 공통 기록 조회 | 168 | 173 |
| 모델 표 SQL | 0 | 6 |
| 입력 토큰 | 4732165 | 8673285 |
| 출력 토큰 | 109384 | 497094 |

B의 무모델 직접 조회는 이번 평가에서 0건이다. 지원 문법 밖의 질문에는 이 경로의 속도 이득을 주장하지 않는다. 별도 실제 원본 DB 점검에서는 API·검색0회와 원본 보존을 확인했다.
B의 요청당 최대 API는 3회, 최대 문서 검색은 5회였다. 검증되지 않은 DB 조건·질의 코드를 원질문 검색으로 넘긴 계획은 89건이다.
모델·색인 준비와 사후 검수는 지연에서 제외했다. 공유 장비·동시 요청·API 변동이 포함된 단일 실행이며 운영 지연 보장값은 아니다.

## 확인된 하락 경로

B에서는 89문항의 계획이 DB 조건·질의 코드 검증에서 원질문 검색으로 전환됐다. 번호 없는 단일 사건20문항 중19개, 모호한 두 후보20문항 전부에서 이 전환이 발생했다. 이때 정상적인 간접 단서까지 대상 조건과 함께 버려져 기존 DB 조회의 장점을 잃었다.
검색을 보고 사건 코드를 발견한 뒤 DB를 다시 조회하는 기존 도구 흐름과 달리, B는 검색 전에 계획을 확정하고 보충도 검색 또는 표 SQL 한 번으로 제한했다. 새로 발견한 코드의 기록 조회를 후속 단계에서 수행할 수 없다. 날짜·화주 등을 DB 필터에 사용해도 그 조건을 증명하는 원본 사건 기록이 최종 문맥에 자동 보존되지는 않는다.
실제 검수 기준으로 하락한42문항을 지정 근거의 위치로 진단하면,30문항은 전체 조회 합집합부터 지정 근거가 부족했고3문항은 후보20쪽 병합,9문항은 최종8쪽 선택에서 빠졌다. 가능한 모든 대체 근거의 존재 여부를 전수 판정한 수치는 아니다.
모든 비단순 질문에 계획과 선택 호출을 요구한 반면 실제 평가의 무모델 경로는0건이었다. 문항 안에서 같은 문서 질의를 반복한 호출도28회였다. 호출·생성 토큰 증가와 이 제한을 함께 확인했으므로, 이번 지연 증가를 리랭커만의 문제로 해석하지 않는다.
[문항별 원인과 유형별 집계](diagnosis.json). 이번 결과는 이 묶음 구현의 실패이며 원문 구조 보존이나 DB·하이브리드 조합 자체가 무효라는 뜻은 아니다. 한 번 더 전면 재구성하거나 이 결과에 맞춰 검색 조건을 수정하지 않았다.

## 문항별 개선·하락

| 문항 | A 실제 근거 | B 실제 근거 |
|---|---|---|
| official_development:AS-DEV02 | 충족 | 미충족 |
| official_development:AS-DEV08 | 미충족 | 충족 |
| official_development:AS-DEV09 | 미충족 | 충족 |
| official_development:AS-DEV13 | 충족 | 미충족 |
| official_development:AS-DEV20 | 충족 | 미충족 |
| official_development:AS-DEV22 | 충족 | 미충족 |
| official_development:AS-DEV24 | 충족 | 미충족 |
| official_additional:AS-ADD03 | 충족 | 미충족 |
| official_additional:AS-ADD05 | 충족 | 미충족 |
| official_additional:AS-ADD23 | 충족 | 미충족 |
| official_additional:AS-ADD25 | 충족 | 미충족 |
| scenarios:AS-S02-05 | 충족 | 미충족 |
| scenarios:AS-S02-11 | 충족 | 미충족 |
| scenarios:AS-S02-14 | 충족 | 미충족 |
| scenarios:AS-S02-16 | 충족 | 미충족 |
| scenarios:AS-S02-19 | 충족 | 미충족 |
| scenarios:AS-S04-10 | 충족 | 미충족 |
| scenarios:AS-S04-11 | 충족 | 미충족 |
| scenarios:AS-S04-13 | 충족 | 미충족 |
| scenarios:AS-S04-14 | 충족 | 미충족 |
| scenarios:AS-S04-15 | 충족 | 미충족 |
| scenarios:AS-S04-16 | 충족 | 미충족 |
| scenarios:AS-S04-17 | 충족 | 미충족 |
| scenarios:AS-S04-18 | 충족 | 미충족 |
| scenarios:AS-S06-01 | 충족 | 미충족 |
| scenarios:AS-S06-03 | 충족 | 미충족 |
| scenarios:AS-S06-04 | 충족 | 미충족 |
| scenarios:AS-S06-05 | 충족 | 미충족 |
| scenarios:AS-S06-09 | 충족 | 미충족 |
| scenarios:AS-S06-11 | 충족 | 미충족 |
| scenarios:AS-S06-12 | 충족 | 미충족 |
| scenarios:AS-S06-13 | 충족 | 미충족 |
| scenarios:AS-S06-16 | 충족 | 미충족 |
| scenarios:AS-S06-17 | 충족 | 미충족 |
| work_rules:AS-WR-19 | 충족 | 미충족 |
| semantic_queries:AS-SEM-21 | 미충족 | 충족 |
| semantic_queries:AS-SEM-22 | 충족 | 미충족 |
| semantic_queries:AS-SEM-23 | 미충족 | 충족 |
| semantic_queries:AS-SEM-26 | 충족 | 미충족 |
| semantic_queries:AS-SEM-27 | 충족 | 미충족 |
| semantic_queries:AS-SEM-30 | 충족 | 미충족 |
| semantic_queries:AS-SEM-33 | 충족 | 미충족 |
| semantic_queries:AS-SEM-35 | 충족 | 미충족 |
| semantic_queries:AS-SEM-36 | 미충족 | 충족 |
| semantic_queries:AS-SEM-38 | 충족 | 미충족 |
| semantic_queries:AS-SEM-39 | 충족 | 미충족 |
| semantic_queries:AS-SEM-40 | 충족 | 미충족 |

세부 요청 연결·호출·출처 선택은 [문항별 흐름](routing-per-question.json), 최초 누락 단계는 [단계별 결과](reviewed-per-question.json), 검수 교정은 [자체 대조 기록](assistant-review-changes.json)에 남긴다. 원문과 정답 인용은 로컬 artifacts에만 보존한다.

## 해석과 지원 범위

이번 B는 표현 단위·규정 후보 보충·계획·선택·실행 횟수를 함께 바꾼 구성이다. 점수 차이를 특정 요소 하나의 효과로 주장하지 않는다. 기존 기준선과 운영 API를 자동 교체하지 않았다.
색인은 검증된 표 셀과 원문 문단110,934개, 같은 원본 쪽·절 연결172,132개, 명시 코드 참조103,910개를 보존한다. 다른 사건·시점의 규정을 동일하다고 합치지 않으며, 모든 업무 의무를 자동으로 이해한 지식 그래프가 아니다.
개선된 근거 전달은 문서에 필요한 근거가 존재하는 것과 모델이 그 의미를 정확히 해석하는 것을 동일하게 만들지 않는다. 같은 세트를 반복 사용한 개발·회귀 비교이며 미사용 세트 일반화는 검증하지 않았다.

## 재현

```bash
.venv/bin/python -m experimental.u_10o0o.records.advanced-table-rag-bounded-workflow-20261112.run_benchmark freeze
.venv/bin/python -m experimental.u_10o0o.records.advanced-table-rag-bounded-workflow-20261112.run_benchmark run
.venv/bin/python -m experimental.u_10o0o.records.advanced-table-rag-bounded-workflow-20261112.analyze prepare
.venv/bin/python -m experimental.u_10o0o.records.advanced-table-rag-bounded-workflow-20261112.analyze review
.venv/bin/python -m experimental.u_10o0o.records.advanced-table-rag-bounded-workflow-20261112.analyze drafts
# 로컬 최종 문맥·인용·적용 범위를 대조한 뒤 adjudications.json에 자체 판정을 기록
.venv/bin/python -m experimental.u_10o0o.records.advanced-table-rag-bounded-workflow-20261112.analyze report
.venv/bin/python -m experimental.u_10o0o.records.advanced-table-rag-bounded-workflow-20261112.render_report
```

검수 판정은 [검수 범위](review-manifest.json)에 72건의 자체 대조·기존 판정 재사용과164건의 모델 판정·출처 검사를 구분했다. 전체 출력의 독립 사람 검수는 아니다.

완료 경로는 덮어쓰지 않는다. 새 유료 실행은 출력 경로를 새로 고정한다. 초기 v1은 통합 검증 문제를 발견해 채점 전에 중단한 개발 실행이며 최종 비교는 v2다.
[실행 조건·코드 지문](protocol.json) · [과정](work-log.md) · [검증](completion-validation.json) · [실행 설명](../../table_rag_workflow/README.md)
