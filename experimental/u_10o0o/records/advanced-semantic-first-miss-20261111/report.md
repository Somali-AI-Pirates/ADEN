# 일상 표현 검색의 첫 누락 진단과 IS 창 벡터 A/B

## 범위와 고정 조건

주 비교는 같은 캐시 계획을 사용하는 일상 표현 40개다. 회귀는 공식 60개와 카테고리 220개를 원 질문으로 검색하며 두 분모의 점수는 합치지 않았다. 비교 320쌍·검색 결과 640개이며, 일상 표현 40개가 두 경로에 중복되므로 고유 질문은 280개다.

IS 벡터 검색은 내용 256토큰과 특수 토큰 2개(창 인코딩 총 258토큰), 중첩 64토큰의 원문 창을 사용하고 부모 페이지 점수는 창별 최대 코사인 유사도다. KURE와 최종 문맥에는 원본 페이지를 사용한다. 격리 프로세스에서 BM25 토크나이저를 `bigram-id`로 고정했다.

계획·검색어, BM25, 검색기별 상위20, RRF 5:5/상수60, KURE 512/FP16, 후보 병합, HG 조회, 최종 8쪽·8,000토큰을 고정했다. 외부 API 호출 없이 수행하며 기존 기준선은 유지한다.

## 보관 요청40의 첫 누락

후보 근거 실패는 19/40개다. 계획 판정과 첫 누락 단계를 함께 집계했다.

| 첫 누락 | 문항 수 | 충실 계획 | 오류 계획 | 불확실 |
| --- | ---: | ---: | ---: | ---: |
| 요청 후보 병합 | 1 | 0 | 1 | 0 |
| 미지원 계획 기준선 복귀 | 4 | 3 | 0 | 1 |
| 검색기 합집합 | 14 | 14 | 0 | 0 |

### 실패 19개 문항별 판정

| ID | 첫 누락 단계 | 독립 계획 판정 |
| --- | --- | --- |
| AS-SEM-01 | 검색기 합집합 | 충실 |
| AS-SEM-08 | 검색기 합집합 | 충실 |
| AS-SEM-10 | 미지원 계획 기준선 복귀 | 충실 |
| AS-SEM-18 | 검색기 합집합 | 충실 |
| AS-SEM-20 | 검색기 합집합 | 충실 |
| AS-SEM-21 | 검색기 합집합 | 충실 |
| AS-SEM-22 | 검색기 합집합 | 충실 |
| AS-SEM-23 | 검색기 합집합 | 충실 |
| AS-SEM-26 | 검색기 합집합 | 충실 |
| AS-SEM-28 | 검색기 합집합 | 충실 |
| AS-SEM-30 | 요청 후보 병합 | 요청 누락/오류 |
| AS-SEM-31 | 검색기 합집합 | 충실 |
| AS-SEM-33 | 검색기 합집합 | 충실 |
| AS-SEM-34 | 미지원 계획 기준선 복귀 | 충실 |
| AS-SEM-35 | 검색기 합집합 | 충실 |
| AS-SEM-36 | 검색기 합집합 | 충실 |
| AS-SEM-38 | 검색기 합집합 | 충실 |
| AS-SEM-39 | 미지원 계획 기준선 복귀 | 판단 불확실 |
| AS-SEM-40 | 미지원 계획 기준선 복귀 | 충실 |
## 미충족 필수 근거별 검색기 관측 깊이

| ID | 첫 누락 | 계획 판정 | 요건 | BM25 최선(80) | Dense 최선(1000) | 보관 단계 충족 |
| --- | --- | --- | ---: | ---: | ---: | --- |
| AS-SEM-01 | retriever_union | faithful | 1 | 61 | 197 | retriever_union=0; request_nomination_union=0; merged20=0; expanded20=0; final8=0 |
| AS-SEM-08 | retriever_union | faithful | 1 | >80* | 717 | retriever_union=0; request_nomination_union=0; merged20=0; expanded20=0; final8=0 |
| AS-SEM-10 | plan_fallback | faithful | 1 | >80* | 51 | retriever_union=0; request_nomination_union=0; merged20=0; expanded20=0; final8=0 |
| AS-SEM-18 | retriever_union | faithful | 1 | >80* | 236 | retriever_union=0; request_nomination_union=0; merged20=0; expanded20=0; final8=0 |
| AS-SEM-20 | retriever_union | faithful | 1 | >80* | 563 | retriever_union=0; request_nomination_union=0; merged20=0; expanded20=0; final8=0 |
| AS-SEM-20 | retriever_union | faithful | 2 | >80* | 563 | retriever_union=0; request_nomination_union=0; merged20=0; expanded20=0; final8=0 |
| AS-SEM-21 | retriever_union | faithful | 1 | >80* | 126 | retriever_union=0; request_nomination_union=0; merged20=0; expanded20=0; final8=0 |
| AS-SEM-22 | retriever_union | faithful | 1 | >80* | 32 | retriever_union=0; request_nomination_union=0; merged20=0; expanded20=0; final8=0 |
| AS-SEM-23 | retriever_union | faithful | 1 | 35 | 28 | retriever_union=0; request_nomination_union=0; merged20=0; expanded20=0; final8=0 |
| AS-SEM-23 | retriever_union | faithful | 2 | 59 | >1000* | retriever_union=0; request_nomination_union=0; merged20=0; expanded20=0; final8=0 |
| AS-SEM-26 | retriever_union | faithful | 2 | >80* | >1000* | retriever_union=0; request_nomination_union=0; merged20=0; expanded20=0; final8=0 |
| AS-SEM-28 | retriever_union | faithful | 2 | >80* | 458 | retriever_union=0; request_nomination_union=0; merged20=0; expanded20=0; final8=0 |
| AS-SEM-31 | retriever_union | faithful | 2 | >80* | >1000* | retriever_union=0; request_nomination_union=0; merged20=0; expanded20=0; final8=0 |
| AS-SEM-31 | retriever_union | faithful | 3 | >80* | 44 | retriever_union=0; request_nomination_union=0; merged20=0; expanded20=0; final8=0 |
| AS-SEM-33 | retriever_union | faithful | 1 | >80* | 68 | retriever_union=0; request_nomination_union=0; merged20=0; expanded20=0; final8=0 |
| AS-SEM-33 | retriever_union | faithful | 2 | >80* | 43 | retriever_union=0; request_nomination_union=0; merged20=0; expanded20=0; final8=0 |
| AS-SEM-34 | plan_fallback | faithful | 2 | >80* | 918 | retriever_union=0; request_nomination_union=0; merged20=0; expanded20=0; final8=0 |
| AS-SEM-35 | retriever_union | faithful | 2 | >80* | 408 | retriever_union=0; request_nomination_union=0; merged20=0; expanded20=0; final8=0 |
| AS-SEM-36 | retriever_union | faithful | 1 | >80* | 112 | retriever_union=0; request_nomination_union=0; merged20=0; expanded20=0; final8=0 |
| AS-SEM-38 | retriever_union | faithful | 1 | >80* | 403 | retriever_union=0; request_nomination_union=0; merged20=0; expanded20=0; final8=0 |
| AS-SEM-39 | plan_fallback | uncertain | 2 | >80* | >1000* | retriever_union=0; request_nomination_union=0; merged20=0; expanded20=0; final8=0 |

`*` 해당 검색기 기록 상한 안에서 요구 묶음 위치가 관측되지 않음. 상한 초과 또는 미기록을 뜻하며 코퍼스 부재를 뜻하지 않는다. 저장 순위 목록과 출처 ID는 포함하지 않았다.


계획 감사 전체는 충실 17, 요청 누락/오류 1, 판단 불확실 1개다. 감사는 사람 검수가 아니다. RRF20과 KURE head40은 별도 후보 지명 경로이므로, head40만으로 첫 누락을 정하지 않았다.

검색기 깊이 합계는 후보 실패 19문항의 전체 35개 요건(이미 충족된 요건 포함) 대상이다. BM25 상위80에서 관측 15, 상한 내 미관측 20; dense 상위1000에서 관측 31, 상한 내 미관측 4다. 이 깊이는 원 질문과 실행된 모든 계획 질의에서 요건이 관측된 최선값을 합친 것이며 단일 질의의 순위가 아니다. 검색기 합집합에서 빠진 21개 요건만 보면 BM25 관측 3, 상한 내 미관측 18; dense 관측 17, 상한 내 미관측 4다. 미관측은 기록 상한 밖 또는 미기록을 뜻하며 코퍼스 부재를 의미하지 않는다.

동일 입력 로컬 재생은 요청 순위 86/86개, 결과 `pass`, 보관 불일치 0개다. 입력 지문 보존: True; 지문 전후 일치: True. 첫 누락 진단의 19개 근거 충족 재현: True.

## A/B 결과

### 계획이 있는 의미 질의 40개

| 조건 | 채점/전체 | 초기 후보 | 원문 조회 후 | 최종 문맥 |
| --- | ---: | ---: | ---: | ---: |
| 기존 검색 | 40/40 | 21/40 | 21/40 | 19/40 |
| IS 창 벡터 | 40/40 | 21/40 | 21/40 | 21/40 |


#### 요청별 후보 단계

| 단계 | 기존 검색 | IS 창 벡터 |
| --- | ---: | ---: |
| 검색기 합집합 | 23/40 | 25/40 |
| 별도 RRF20 경로 | 21/40 | 22/40 |
| 요청 지명 합집합 | 22/40 | 23/40 |
| 병합 후보20 | 21/40 | 21/40 |
| 원문 확장20 | 21/40 | 21/40 |
| 최종 문맥8 | 19/40 | 21/40 |

후보20 완전 충족은 기존 21/40에서 IS 창 벡터 21/40로 같았다. 후보 단계 개선 2개(semantic_queries:AS-SEM-01, semantic_queries:AS-SEM-21), 하락 2개(semantic_queries:AS-SEM-14, semantic_queries:AS-SEM-24)다. 최종8 완전 충족은 19/40에서 21/40로 변했다 (차이 +2); 최종 개선 3개(semantic_queries:AS-SEM-01, semantic_queries:AS-SEM-13, semantic_queries:AS-SEM-21), 하락 1개(semantic_queries:AS-SEM-14), 동일 36개다. 이 표본 결과만으로 핵심 후보 실패가 해결됐다거나 통계적 유의성이 있다고 해석하지 않는다.

### 계획 없는 원 질문 회귀 280개

공식 전체는 58/60개 채점, 카테고리 전체는 200/220개 채점이다. 카테고리의 미채점 20개는 negative control이며 성공으로 계산하지 않았다.

| 범주 | 채점/전체 | 기존 조회 후20 | IS 조회 후20 | 기존 최종8 | IS 최종8 | 기존 최종 페이지 Recall | IS 최종 페이지 Recall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 공식 전체 | 58/60 | 39/58 | 42/58 | 32/58 | 33/58 | 77.0% | 78.7% |
| 공식 복합 근거 | 51/51 | 35/51 | 37/51 | 28/51 | 28/51 | 79.7% | 79.7% |
| 카테고리 전체 | 200/220 | 172/200 | 174/200 | 170/200 | 172/200 | 87.9% | 88.7% |
| 시나리오 01 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 100.0% | 100.0% |
| 시나리오 02 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 100.0% | 100.0% |
| 시나리오 03 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 100.0% | 100.0% |
| 시나리오 04 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 100.0% | 100.0% |
| 시나리오 05 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 100.0% | 100.0% |
| 시나리오 06 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 100.0% | 100.0% |
| 시나리오 07 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 100.0% | 100.0% |
| 업무 규정 | 20/20 | 18/20 | 19/20 | 17/20 | 18/20 | 90.0% | 92.5% |
| 의미 질의 | 40/40 | 14/40 | 15/40 | 13/40 | 14/40 | 44.6% | 47.1% |

후보 열은 원문 조회까지 마친 최종 후보 20쪽이다. 초기 검색 후보와의 차이, 세부 집계와 문항별 변화는 `changes.md`에 있다. 공식은 58/60개가 채점됐고 카테고리는 200/220개가 채점됐다. 나머지 카테고리 20개는 negative control로 미채점이며 통과 수에 포함하지 않았다.

문항별 개선 5, 회귀 2, 동일 251, 미채점 22개. 실행 manifest 상태 `complete`, 외부 API 0회. 보관 계획 재생에서 pool·확장·문맥이 모두 일치한 문항은 40/40개다.

이 결과는 개발 비교와 회귀 검사이며 변경 채택이나 기준선 교체 근거로 단정하지 않는다. 별도 독립 holdout은 실행하지 않았다.

질문·문맥·순위 목록·페이지/출처 ID는 보고서와 요약 JSON에서 제외했다. 판단과 재현 절차는 [conclusions.md](conclusions.md), [reproduce.md](reproduce.md)에 있다. 계획 감사와 실행 조건은 [plan-audit.json](plan-audit.json), [process.md](process.md)에 있다. IS 창 인덱스의 모델·설정·입력 지문은 [is-window-manifest-sanitized.json](is-window-manifest-sanitized.json)에 기록했다.
