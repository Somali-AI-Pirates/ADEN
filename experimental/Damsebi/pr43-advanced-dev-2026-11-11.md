# PR #42·#43 Advanced development 30문항 비교 (2026-11-11)

PR #43(B의 생성 프롬프트·출처/문맥 검증·날짜 대조·재시도 피드백)이 같은 검색 조건에서 답변과 채점을 어떻게 바꾸는지 보려고 #42 베이스라인과 비교했다. **A의 공식 색인이 아니라 이 비교용으로 만든 임시 로컬 색인**을 썼고, 버전마다 1회 실행했다. 아래 차이는 단일 실행의 관찰이며 성능 향상·저하로 확정하지 않는다.

## 요약

- 검색은 두 버전이 완전히 같았다. 90건(30문항 × 3방식) 모두 후보 20개의 순서, 최종 문맥 해시, 모델 문맥 해시가 일치한다. 차이는 생성 단계에서만 생겼다.
- 상태: #42는 answered 80·추가 확인 3·보류 6·오류 1, #43은 answered 82·추가 확인 3·보류 5·오류 0. #42의 오류 1건은 AS-DEV16 hybrid의 `LengthFinishReasonError`(출력 2,048토큰 한도)로 검증 실패가 아니다.
- 전체 평균(평가된 건만): Faithfulness 0.929→0.926, Judge 사실 0.618→0.678, 조건 0.865→0.811, 근거 0.830→0.753, 판단 0.833→0.833. 문항 단위로는 사실 상승 7·하락 2, 조건 3·8, 근거 4·10, 판단 5·5다.
- 같은 버전·같은 문맥으로 AS-DEV17을 두 번(점검·본 실행) 돌렸을 때도 Judge 사실·근거가 0↔1로 바뀐 칸이 있었다. 단일 실행 차이에는 이 정도 변동이 섞여 있다.
- #43의 새 검증은 이번 실행에서 거의 발동하지 않았다. 두 버전 모두 재시도 0건이고, 잘못된 출처 표기·본문 인용 누락은 #42 출력에도 없었다. 날짜 보조표는 90건 중 AS-DEV19 hybrid_rerank 1건에만 붙었고(날짜 행 필수), #43 답변은 첫 시도에 날짜 행 4줄로 통과했다. 같은 #42 답변은 #43 검사였다면 "보관료 무료 날짜 행 누락"으로 실패했을 출력이지만 Judge 사실·판단은 두 버전 모두 1이었다.
- M19는 custom 세트 문항이라 development에 없다. 재시도 피드백의 효과는 이번 데이터로 확인하지 못했다.

## 비교 범위와 기준 커밋

| 항목 | 값 |
| --- | --- |
| 베이스라인 | #42 병합 커밋 `2e2ed06bf5d474576bc86602bdb6873468b122ff`(tree `78fb95da…`). 실행은 이 커밋에 둔 로컬 브랜치 `chore/pr43-eval-compare`에서 했다 |
| 비교 대상 | #43 병합 커밋 `acee02c89505cd935a273d7000c7b3d3971cfc3e`(tree `25222e46…`). `2e2ed06` 바로 위의 단일 커밋이며 별도 worktree(detached)에서 실행했다. 그 뒤의 main 변경은 섞지 않았다 |
| 이후 main | 비교 뒤 #44(`ddc24fc`, 생성: 날짜 구간 압축과 승인·질문 범위 규칙)와 #45(`fc9c221`, 평가 코드의 조건식 리팩터링)가 병합됐다. 이 결과는 #44의 생성 변경을 반영하지 않는다 |
| #43 코드 차이 | 16파일. 실행에 영향을 주는 것은 `app/generation/{calendar.py(신규), chain.py, context.py, prompts/shipping_qa.txt}`, `app/service/query.py`, `app/observability/tracing.py`. 평가(`app/evaluation`)·검색·색인·추출·설정·의존성은 바뀌지 않았다 |
| 생성 프롬프트 `shipping_qa.txt` | #42 `1c3737e0…`, #43 `24d9af01…` |
| 그대로인 것 | `judge.txt` `4079487d…`, `defense.txt` `d91d14dc…`, `graders.py`·`metrics.py`·`runner.py`, `configs/advanced.yaml` `6de5b9b6…` |

#43 전체 커밋으로 답변을 새로 생성했다. #42 저장 답변의 재채점으로 대신하지 않았다.

## 데이터

| 항목 | 값 |
| --- | --- |
| 질문 | `data/advanced/evaluation/development_questions.json` `0d386545…`. 30문항 AS-DEV01~30, 고유 ID 30개 |
| 참고 정답 | `development.json` `d3bc5a84…`. ID 집합이 질문과 같다. expected_action 답변 27·추가 확인 1·보류 2 |
| 문서 | `data/advanced/documents/` AS001~AS120 120개(18,000쪽). development 정답에 맞춰 선별하지 않았다 |

- 참고 정답은 실행이 끝난 뒤 채점 단계에서만 읽었다. 정답·rubric·expected_action·evidence_groups는 색인·검색·생성 입력에 들어가지 않는다.
- additional 30문항은 이번 작업에서 조회·실행·채점·조정에 쓰지 않았다. 단, 이 작업 지시 전 데이터 수령 확인 때 additional의 개수·키 구조와 첫 항목(질문 1개, 참고 답변 1개) 내용을 한 번 출력한 적이 있다. 이후 어떤 판단·설정에도 쓰지 않았다.

## 임시 로컬 색인

| 항목 | 값 |
| --- | --- |
| 저장 방식 | embedded Qdrant(`QdrantClient(path=…)`, 디스크). 서비스의 기존 메모리 Qdrant와 같은 엔진이다. localhost 서버·Qdrant Cloud·`memory_copy`는 쓰지 않았다 |
| 위치·컬렉션 | `artifacts/local-index/advanced-pr43/qdrant`, `advanced_snowflake_ko_pages`(설정의 컬렉션 이름을 실험 전용 경로 안에 만들었다) |
| 연결 | `ADEN_QDRANT_CLIENT=experimental.Damsebi.local_advanced_index:open_store` + `ADEN_LOCAL_QDRANT`. 실행마다 같은 저장소를 열고 끝날 때 닫았다. 실행은 모두 순차 |
| 추출·청킹 | 기존 `extract_pages`(pypdf 6.21.4, plain) → `chunk_pages`(문단 800자, 페이지 경계 유지) → `index_text`(제목+본문). PDF 120·페이지 18,000·청크 22,355, 빈 페이지 0, 실패 0 |
| 해시 | 문서 목록 `documents_sha256`은 `index_manifest.json`, `chunks.jsonl` `10db0d3a…`, `vectors.npy` `0073853c…` |
| 임베딩 | `dragonkue/snowflake-arctic-embed-l-v2.0-ko`(리비전 `55ec6e93`), 1024차원, 정규화, 기존 `Embedder` 접두어(문서 `passage: `, 질문 `query: `), 입력 최대 587토큰(한도 8,192, 초과 0). 문서 벡터는 GPU(RTX 5060 Laptop, torch 2.15.0+cu132)로 약 14분 |
| 거리 | 코사인 |
| 점검 | 표본 41개의 id·payload·벡터 일치 41/41, 저장 벡터 자기 검색 41/41. 청크 본문만 질문으로 넣은 자기 검색 1위는 25/41이었다. 놓친 16건은 다른 문서의 거의 같은 서식 문단(본문 유사도 0.84~0.98)이 앞섰다. 사례 ID가 제목에만 있어서 본문만으로는 사례를 구분하기 어렵다 |

- 청킹 정책 근거: main에는 Advanced 전용 청커가 없고 아키텍처 문서는 "페이지 단위 청킹"(`chunk_pages`)을 가리킨다. 이전 팀 Advanced 실행 기록(chsopo89, 2026-11-09)의 청크 ID도 `AS039-p116-c1` 형식이었다. 다만 컬렉션 이름 `_pages`는 페이지 통째 청크를 뜻할 수도 있어 A의 실제 색인과 다를 수 있다.
- `build_index` CLI(Cloud 연결, Basic 기본값)는 실행하지 않았다. 임베딩 모델은 바꾸지 않았다.

## 실행 조건

| 항목 | 두 버전 공통 값 |
| --- | --- |
| 설정 | `advanced-cuda-pr43.yaml` `d4c5431e…` = `configs/advanced.yaml`에서 `embedding_device: cuda`만 바꾼 임시 설정. 실행 때는 `experimental/Damsebi/`에 두고 `ADEN_CONFIG`와 `--config`에 같은 절대 경로를 지정했다(지금은 `artifacts/pr43-compare/tools/`) |
| 검색 | naive·hybrid·hybrid_rerank, 후보 20, k 8, 문맥 8,000토큰(`o200k_base`), 리랭커 `BAAI/bge-reranker-v2-m3`(코드가 장치를 지정하지 않아 같은 환경에서 `cuda:0`으로 올라가는 것을 확인) |
| 생성 | `gpt-6-luna`, 최대 출력 2,048토큰, 시간 제한 60초, `generation_max_attempts` 2 |
| 평가 | Judge `gpt-6-luna`(`judge.txt` v2), RAGAS 0.4.3 Faithfulness `gpt-6-luna`(출력 한도 8,192) |
| 환경 | Python 3.13.15, openai 2.54.0, langfuse 5.2.0, sentence-transformers 6.2.0, transformers 5.22.1, torch 2.15.0+cu132, qdrant-client 1.20.0 |
| Langfuse | 추적은 기존대로 기록했다(실험 ID로 구분, 요청 186건 모두 저장 확인). 점수 게시는 하지 않았다 |

- 프로젝트 가상환경의 torch를 `2.15.0+cpu`에서 공식 `2.15.0+cu132`로 바꿨다(`--no-deps`, 다른 패키지·`uv.lock`·`pyproject.toml` 변화 없음). `uv sync`를 하면 잠금 파일 기준으로 되돌아갈 수 있다.
- 비용 한도는 기존 합의(experimental/Damsebi/README, 두지 않음)를 따랐다. 예상 호출은 버전당 서비스 90건(생성 90~180회), Judge ≤90, RAGAS ≤90건이었다. 중단 기준은 점검에서 Judge 파싱 실패·RAGAS 평가 불가가 나오거나 평가 호출이 연속 3회 실패하는 경우로 두었고, 해당하는 일은 없었다.
- 실행 중 프롬프트·채점 기준·검색 설정을 바꾸지 않았다.

### 실험 ID와 실제 호출

| 실험 ID | 코드 | 범위 | 시간 | 생성 토큰(입력/출력) | Judge 호출·토큰(입력/출력) | RAGAS |
| --- | --- | --- | --- | --- | --- | --- |
| `adv-dev-pr42-pilot-20261111` | `2e2ed06` | AS-DEV17 × 3 | 점검 | - | 3 · 17,715/2,842 | 3 |
| `adv-dev-pr43-pilot-20261111` | `acee02c` | AS-DEV17 × 3 | 점검 | - | 3 · 17,884/3,288 | 3 |
| `adv-dev-pr42-20261111` | `2e2ed06` | 30 × 3 | 10:48~11:45 | 425,592/61,598 | 89 · 461,977/71,318 | 89 |
| `adv-dev-pr43-20261111` | `acee02c` | 30 × 3 | 11:45~12:43 | 455,140/56,879 | 90 · 467,969/66,301 | 90 |

- #42 결과를 먼저 저장하고 파일 해시를 고정했다(`artifacts/pr43-compare/baseline-pr42.sha256`). #43 실행 뒤에도 해시가 같다. 원본 결과는 덮어쓰지 않았다.
- 점검 결과는 본 실행에 합치지 않았다. AS-DEV17은 본 실행에서 다시 돌렸고, 그 두 번의 결과를 같은 버전 반복 변동 확인에 썼다.

## 점검(AS-DEV17)

AS-DEV17은 #43 날짜 검사의 질문 조건(계산 표현, 예약·장비 각 1개, 조회 시각)을 통과하는 development 문항이다(AS-DEV19·21도 해당).

- 로컬 색인으로 실제 검색했다(`provided_context=false`, 검색기 dense / hybrid / reranker, 후보 20).
- 세 방식이 각각 실행됐고 최종 문맥(8청크, 3,747~3,916토큰)과 실행 기록, Langfuse trace가 남았다.
- Judge·RAGAS가 실제로 채점됐다.
- 두 버전의 후보 순서·최종 문맥·모델 문맥이 같았다.
- 두 버전 모두 첫 시도에 통과해 #43의 검증 실패·재시도 기록은 점검에서 볼 수 없었다. 시도 기록에는 오류 종류만 남으므로, 저장된 실제 출력을 각 버전의 검증 함수로 다시 검사하는 `replay_validation.py`를 만들었다. 일부러 `[S1, S2]`를 넣은 출력을 #43은 거절하고 #42는 통과시키는 것으로 동작을 확인했다(모델 호출 없음).

## 결과

문항별 점수 요약은 [부록](#부록-문항별-비교)에 있다. 두 버전의 답변·reason·인용·오류·시도별 검증 전체는 로컬 `artifacts/pr43-compare/report-files/per_question.csv`(Git 제외)에, 실행기별 보고서는 `artifacts/pr43-compare/run-reports/`에 있다.

### 방식별 요약

평균은 평가된(scored) 건만으로 냈다. 평가 불가·해당 없음은 0점으로 바꾸지 않았고, 분모가 30이 아니면 괄호에 적었다. 서비스 오류 건의 판단 항목은 기존 규칙대로 0점으로 채점된다.

| 방식 | 버전 | answered·추가확인·보류·오류 | Hit@k | Recall@k | Faithfulness | 사실 | 조건 | 근거 | 판단 | 지연 평균 ms | 생성 토큰 입력/출력 | 재시도 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| naive | #42 | 23·1·6·0 | 0.862 (29) | 0.532 (29) | 0.944 | 0.333 | 0.800 | 0.733 | 0.700 | 8,236 | 143,175/19,189 | 0 |
| naive | #43 | 24·1·5·0 | 0.862 (29) | 0.532 (29) | 0.912 | 0.433 | 0.667 | 0.621 (29) | 0.667 | 7,521 | 150,975/16,360 | 0 |
| naive | 변화 | +1·0·−1·0 | 0 | 0 | −0.032 | +0.100 | −0.133 | −0.113 | −0.033 | −714 | +7,800/−2,829 | 0 |
| hybrid | #42 | 28·1·0·1 | 1.000 (29) | 0.736 (29) | 0.918 (29) | 0.655 (29) | 0.897 (29) | 0.862 (29) | 0.867 | 9,989 | 137,924/21,526 | 0 |
| hybrid | #43 | 29·1·0·0 | 1.000 (29) | 0.736 (29) | 0.926 | 0.800 | 0.867 | 0.767 | 0.900 | 9,245 | 151,118/20,535 | 0 |
| hybrid | 변화 | +1·0·0·−1 | 0 | 0 | +0.008 | +0.145 | −0.030 | −0.095 | +0.033 | −744 | +13,194/−991 | 0 |
| hybrid_rerank | #42 | 29·1·0·0 | 1.000 (29) | 0.802 (29) | 0.926 | 0.867 | 0.900 | 0.897 (29) | 0.933 | 9,746 | 144,493/20,883 | 0 |
| hybrid_rerank | #43 | 29·1·0·0 | 1.000 (29) | 0.802 (29) | 0.939 | 0.800 | 0.900 | 0.867 | 0.933 | 9,998 | 153,047/19,984 | 0 |
| hybrid_rerank | 변화 | 0 | 0 | 0 | +0.013 | −0.067 | 0 | −0.030 | 0 | +252 | +8,554/−899 | 0 |

- Hit·Recall의 분모 29는 참고 근거 문서가 없는 추가 확인 문항 AS-DEV12가 해당 없음이기 때문이다. 검색이 같아 두 버전의 값도 같다.
- #42 hybrid의 분모 29(Faithfulness·사실·조건·근거)는 서비스 오류 1건의 평가 불가다. naive #43과 rerank #42의 근거 분모 29는 Judge가 해당 없음으로 판정한 건이다.
- #43은 생성 입력 토큰이 버전 합계 6.9% 늘고(프롬프트가 길어짐) 출력 토큰이 7.7% 줄었다.

### 문맥 일치

90건 모두 후보 순서, `final_context_hash`, `model_context_hash`가 같다. 따라서 동일 검색 조건 비교다. #43이 추가하는 날짜 보조표·재검증 피드백은 시스템 메시지에만 들어가고 원문 검색 문맥(`model_context_text`)과는 분리돼 있다. 저장된 최종 청크로 문맥을 다시 만들었을 때의 해시도 두 버전 180건 모두 실행 기록과 같았다.

### #43 목적별 확인

| 항목 | 결과 |
| --- | --- |
| 출처 표기(기계적 형식) | 시도별 검증 재현: #42 통과 89·형식 읽기 실패 1(위 오류 건, 출력 없음), #43 통과 90. 두 버전 모두 잘못된 대괄호 표기 0개, answered인데 본문 `[S#]` 없음 0건. reason에 `[S#]` 인용은 #42 83건, #43 88건 |
| #42 출력을 #43 검사로 | 통과 88, 형식 읽기 실패 1(오류 건), 날짜 대조 실패 1(AS-DEV19 hybrid_rerank) |
| 근거 정확성(의미) | 기계적 검사와 별개로 Judge 근거와 Faithfulness로 본다. 근거 평균은 0.830→0.753, Faithfulness는 0.929→0.926 |
| 날짜 보조표 | #43 시도 기록의 `calendar_reference`가 채워진 건은 AS-DEV19 hybrid_rerank 1건(무료·유료 날짜 행 필수). 답변에 날짜 행 4줄, 첫 시도 통과, Judge 사실·판단 1→1. AS-DEV17·21의 세 방식과 AS-DEV19 naive·hybrid는 최종 문맥에서 보조표 생성 조건(단일 산입 규칙, 비용별 실제 사건 날짜 등)을 채우지 못해 생략됐다(조건별 원인은 확인하지 않았다) |
| M19 무료·유료 날짜 | M19는 custom 세트 문항이라 development에 없어 확인하지 않았다 |
| 재시도 후 성공 | 두 버전 모두 재시도 0건. 재시도 피드백의 효과는 이번 실행으로 판단할 수 없다 |
| 오류 | #42 AS-DEV16 hybrid `LengthFinishReasonError`(모델 호출 오류라 재시도 대상 아님). #43의 같은 건은 answered. 출력이 짧아진 영향일 수 있으나 1건이라 단정하지 않는다 |

### 같은 버전 반복 변동(AS-DEV17, 같은 문맥)

| 버전 | 방식 | Faithfulness | 사실 | 조건 | 근거 | 판단 |
| --- | --- | --- | --- | --- | --- | --- |
| #42 | naive | 1→1 | 0→0 | 1→1 | 0→1 | 1→1 |
| #42 | hybrid | 1→1 | 1→1 | 1→1 | 1→1 | 1→1 |
| #42 | hybrid_rerank | 1→1 | 0→1 | 1→1 | 1→1 | 1→1 |
| #43 | naive | 1→1 | 0→0 | 1→1 | 0→0 | 1→1 |
| #43 | hybrid | 1→1 | 1→1 | 1→1 | 1→1 | 1→1 |
| #43 | hybrid_rerank | 1→1 | 0→1 | 1→1 | 1→1 | 1→1 |

생성과 Judge 출력이 실행마다 달라질 수 있어, 코드가 같아도 0/1 판정이 뒤집히는 칸이 있다. 방식별 평균 차이 0.03~0.15는 문항 1~4개의 판정 변화에 해당하므로 이 변동과 구분되지 않는다.

### 관찰(확정 아님)

- 근거 하락 10건, 조건 하락 8건 중 다수에서 Judge는 #43 답변의 "확인할 수 없다"는 보류 서술을 문제 삼았다. 예: 근거는 인용 자료가 '확인 불가' 주장을 뒷받침하지 않는다(AS-DEV17·22·23·26 naive, AS-DEV22 hybrid, AS-DEV25 hybrid_rerank), 조건은 확정 가능한 결론을 미확정으로 남겼다(AS-DEV23·25·30 naive). 하락은 naive에 몰려 있다(근거 하락 10건 중 6건). #43 프롬프트의 보류·승인 주체 규칙과 관련 있을 수 있지만 단일 실행이라 원인으로 단정하지 않는다.
- 사실은 hybrid·naive에서 올랐다(상승 7·하락 2).

## 실패·미검증 범위

- 임시 색인: A의 공식 Advanced 색인이 아니다. 청킹(800자 문단)과 문서 벡터 장치(GPU)가 A의 설정과 다를 수 있어 절대 점수를 공식 성능으로 쓰지 않는다. embedded Qdrant는 점 2만 개 초과 시 권장하지 않는다는 경고를 냈다(동작 문제는 없었다).
- 질의 임베딩과 리랭커도 GPU에서 돌았다(임시 설정 `embedding_device: cuda`). CPU 대비 임베딩 코사인 1.000000을 16개 표본으로 확인했지만 전체 후보 순위의 CPU 동일성은 확인하지 않았다.
- 버전당 1회 실행이다. 반복 실행·통계 검정은 하지 않았다.
- #43의 재시도 피드백과 실패 후 재시도 성공은 발동하지 않아 검증하지 못했다. 날짜 보조표도 1건에만 적용됐다. M19는 범위 밖이다.
- 생성 모델과 평가 모델이 모두 `gpt-6-luna`라 절대 점수가 후하게 나올 수 있다(두 버전 동일 조건).
- 실행 중 본 checkout의 `app/evaluation/runner.py`가 11:25:46에 미커밋 상태로 바뀌었다. 내용은 뒤에 병합된 #45의 `runner.py` 변경(조건식을 if/else로 푼 동작 동일 리팩터링)과 같았고, 이 비교 작업에서 만든 변경이 아니다. #42 본 실행은 10:48에 모듈을 이미 불러왔고 시작 시점의 미커밋 항목에는 이 파일이 없었다. #43은 worktree의 원본 코드를 썼다. 그 미커밋 변경은 이후 작업 트리에서 사라졌다.

## 재현

실험용 도구와 임시 설정은 Git에 넣지 않고 로컬 `artifacts/pr43-compare/tools/`에 보관했다. 실행 당시에는 `experimental/Damsebi/`에 두고 아래처럼 모듈로 실행했으므로, 다시 돌릴 때는 그 폴더로 복사한 뒤 같은 명령을 쓴다.

```bash
# 임시 색인(한 번). 기존 저장소가 있으면 중단한다
python -m experimental.Damsebi.local_advanced_index --store artifacts/local-index/advanced-pr43 \
    --config experimental/Damsebi/advanced-cuda-pr43.yaml

# 실행 환경(두 버전 동일). .env의 키를 환경에 올리고 Qdrant Cloud 변수는 지운다
set -a; . ./.env; set +a; unset QDRANT_URL QDRANT_API_KEY
export ADEN_CONFIG="$PWD/experimental/Damsebi/advanced-cuda-pr43.yaml"
export ADEN_QDRANT_CLIENT=experimental.Damsebi.local_advanced_index:open_store
export ADEN_LOCAL_QDRANT="$PWD/artifacts/local-index/advanced-pr43"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

# #42: 이 checkout(2e2ed06), #43: acee02c worktree에서 같은 명령(질문·정답·설정·출력은 이 checkout의 절대 경로).
# worktree에는 local_advanced_index.py를 같은 내용으로 복사해 둔다
python -m app.evaluation.scripts.run_benchmark --adapter app.service.bootstrap:query_adapter \
    --config "$ADEN_CONFIG" \
    --questions <checkout>/data/advanced/evaluation/development_questions.json \
    --references <checkout>/data/advanced/evaluation/development.json \
    --judge-model gpt-6-luna --ragas-model gpt-6-luna --experiment-id adv-dev-pr42-20261111 \
    --artifacts-dir <checkout>/artifacts/experiments --reports-dir <checkout>/experimental/Damsebi/reports

# 비교(모델 호출 없음). replay는 해당 버전 코드 폴더에서 실행한다
python -m experimental.Damsebi.replay_validation artifacts/experiments/adv-dev-pr42-20261111/runs.jsonl artifacts/pr43-compare/replay42.jsonl
python -m experimental.Damsebi.compare_versions --base artifacts/experiments/adv-dev-pr42-20261111 \
    --target artifacts/experiments/adv-dev-pr43-20261111 --base-replay … --target-replay … --cross-replay … --out …
python -m experimental.Damsebi.pr43_focus --base … --target … --base-pilot … --target-pilot … --replay-dir … --out …
```

## 임시 도구와 산출물

모두 로컬에만 있다(Git 제외). 저장소에는 이 문서만 둔다.

| 파일(`artifacts/pr43-compare/tools/`) | SHA-256 앞 16자 | 용도 |
| --- | --- | --- |
| `advanced-cuda-pr43.yaml` | `d4c5431e4bb7c3f6` | 두 버전 공통 임시 설정 |
| `local_advanced_index.py` | `d9ec89d9b2c5b416` | 임시 색인 생성·`open_store` 팩터리(#43 worktree에도 같은 파일을 복사해 썼다) |
| `replay_validation.py` | `3a1d8302b66d7051` | 저장 출력의 검증 메시지 복원 |
| `compare_versions.py` | `fd5fd7b64ea66809` | 두 실험 연결·방식별/문항별 표 |
| `pr43_focus.py` | `7ebe5bb6b93b31c1` | #43 목적별 표·반복 변동 |

| 위치 | 내용 |
| --- | --- |
| `artifacts/experiments/adv-dev-pr4{2,3}{,-pilot}-20261111/` | 실행 기록(`runs.jsonl`)·점수·manifest |
| `artifacts/pr43-compare/run-reports/` | 각 실험의 run_benchmark 보고서(README·summary·answers 등) |
| `artifacts/pr43-compare/report-files/` | 방식별·문항별 전체 표(`comparison.md`, `per_question.csv`, `summary_by_mode.csv`, `focus.md`), 도구 diff(`tools.diff`), 색인 기록, 결과 해시 |
| `artifacts/pr43-compare/` | 시도별 검증 재검사(`replay42.jsonl`, `replay43.jsonl`, `cross42on43.jsonl`), 점검 비교(`pilot/`) |
| `artifacts/local-index/advanced-pr43/` | 임시 색인(청크·벡터·embedded Qdrant·`index_manifest.json`) |

- 실행에 쓴 #43 worktree와 따로 만들었던 GPU 가상환경은 실행 뒤 삭제했다. 프로젝트 가상환경의 CUDA torch는 남겨 두었다.
- 실험 도구·결과 파일은 커밋하지 않았다. 저장소에는 이 문서만 올린다.

## 부록: 문항별 비교

점수는 Judge·Faithfulness 값이며 `평가불가`·`해당없음`은 0점으로 바꾸지 않았다. 날짜 보조표 열은 #43 시도 기록의 `calendar_reference` 적용 여부다.

| question_id | retrieval_mode | #42 상태 | #43 상태 | #42 시도 | #43 시도 | #43 날짜 보조표 | 최종 문맥 같음 | #42 Faithfulness | #43 Faithfulness | #42 Judge 사실 | #43 Judge 사실 | #42 Judge 조건 | #43 Judge 조건 | #42 Judge 근거 | #43 Judge 근거 | #42 Judge 판단 | #43 Judge 판단 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| AS-DEV01 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV01 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV01 | naive | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV02 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 1 | 0 |
| AS-DEV02 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 1 | 0.625 | 0 | 0 | 1 | 1 | 0 | 0 | 0 | 0 |
| AS-DEV02 | naive | answered | answered | 1 | 1 | 미적용 | True | 0.809524 | 0.666667 | 0 | 0 | 1 | 1 | 1 | 0 | 0 | 0 |
| AS-DEV03 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 1 | 0.8 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV03 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV03 | naive | answered | answered | 1 | 1 | 미적용 | True | 1 | 0.833333 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV04 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 0.8 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV04 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV04 | naive | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV05 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV05 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV05 | naive | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 1 | 1 |
| AS-DEV06 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 0 | 0 | 1 | 1 | 0 | 0 | 1 | 1 |
| AS-DEV06 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 1 | 0.888889 | 0 | 0 | 0 | 1 | 0 | 0 | 1 | 1 |
| AS-DEV06 | naive | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 0 | 0 | 1 | 1 | 0 | 0 | 1 | 1 |
| AS-DEV07 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 0.8 | 0.882353 | 0 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV07 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 1 | 0.846154 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV07 | naive | answered | answered | 1 | 1 | 미적용 | True | 0.941176 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV08 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 0.833333 | 0.875 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| AS-DEV08 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 0.9 | 1 | 1 | 1 | 1 | 1 | 0 | 1 | 1 | 1 |
| AS-DEV08 | naive | answered | answered | 1 | 1 | 미적용 | True | 0.833333 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| AS-DEV09 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 0.769231 | 0.818182 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |
| AS-DEV09 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 0.928571 | 0.857143 | 0 | 0 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV09 | naive | answered | answered | 1 | 1 | 미적용 | True | 1 | 0.9375 | 1 | 1 | 1 | 0 | 1 | 0 | 1 | 1 |
| AS-DEV10 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 1 | 0.923077 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV10 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 0.923077 | 0.866667 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV10 | naive | answered | answered | 1 | 1 | 미적용 | True | 1 | 0.583333 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV11 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV11 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 0.75 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV11 | naive | answered | answered | 1 | 1 | 미적용 | True | 0.666667 | 0.75 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV12 | hybrid | needs_clarification | needs_clarification | 1 | 1 | 미적용 | True | 0.666667 | 0.714286 | 0 | 0 | 0 | 0 | 1 | 1 | 0 | 0 |
| AS-DEV12 | hybrid_rerank | needs_clarification | needs_clarification | 1 | 1 | 미적용 | True | 0.5 | 1 | 0 | 0 | 0 | 0 | 해당없음 | 1 | 0 | 1 |
| AS-DEV12 | naive | needs_clarification | needs_clarification | 1 | 1 | 미적용 | True | 1 | 0.5 | 0 | 1 | 0 | 0 | 1 | 해당없음 | 0 | 1 |
| AS-DEV13 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 0.947368 | 1 | 0 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV13 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV13 | naive | answered | answered | 1 | 1 | 미적용 | True | 1 | 0.9375 | 0 | 0 | 0 | 0 | 1 | 1 | 1 | 0 |
| AS-DEV14 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 0 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV14 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 1 | 0.8 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV14 | naive | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 0 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV15 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV15 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 1 | 1 |
| AS-DEV15 | naive | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV16 | hybrid | error | answered | 1 | 1 | 미적용 | True | 평가불가 | 0.666667 | 평가불가 | 1 | 평가불가 | 1 | 평가불가 | 0 | 0 | 1 |
| AS-DEV16 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV16 | naive | abstained | answered | 1 | 1 | 미적용 | True | 1 | 1 | 0 | 0 | 0 | 1 | 1 | 1 | 0 | 1 |
| AS-DEV17 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV17 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV17 | naive | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 0 | 0 | 1 | 1 | 1 | 0 | 1 | 1 |
| AS-DEV18 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 0.944444 | 0.428571 | 1 | 1 | 1 | 1 | 1 | 0 | 1 | 1 |
| AS-DEV18 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 0.9375 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV18 | naive | answered | answered | 1 | 1 | 미적용 | True | 0.842105 | 1 | 0 | 0 | 1 | 0 | 0 | 1 | 1 | 1 |
| AS-DEV19 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV19 | hybrid_rerank | answered | answered | 1 | 1 | 날짜행 필수 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV19 | naive | abstained | abstained | 1 | 1 | 미적용 | True | 1 | 1 | 0 | 0 | 1 | 1 | 1 | 1 | 0 | 0 |
| AS-DEV20 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 0.9 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV20 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV20 | naive | abstained | abstained | 1 | 1 | 미적용 | True | 0.916667 | 1 | 0 | 0 | 1 | 1 | 0 | 0 | 0 | 0 |
| AS-DEV21 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV21 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV21 | naive | abstained | abstained | 1 | 1 | 미적용 | True | 0.909091 | 1 | 0 | 0 | 0 | 1 | 0 | 1 | 0 | 0 |
| AS-DEV22 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 1 | 0.666667 | 0 | 0 | 1 | 1 | 1 | 0 | 1 | 1 |
| AS-DEV22 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 0.846154 | 0.846154 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV22 | naive | answered | answered | 1 | 1 | 미적용 | True | 1 | 0.875 | 0 | 0 | 1 | 1 | 1 | 0 | 1 | 1 |
| AS-DEV23 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV23 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 1 | 0.95 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV23 | naive | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 0 | 0 | 1 | 0 | 1 | 0 | 1 | 0 |
| AS-DEV24 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 0.4 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV24 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 0 | 0 | 1 | 1 | 1 | 1 |
| AS-DEV24 | naive | answered | answered | 1 | 1 | 미적용 | True | 0.888889 | 0.333333 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV25 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV25 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 0 | 1 | 0 | 1 | 0 | 1 | 0 |
| AS-DEV25 | naive | abstained | abstained | 1 | 1 | 미적용 | True | 1 | 1 | 0 | 0 | 1 | 0 | 1 | 1 | 0 | 0 |
| AS-DEV26 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 0.9 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV26 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 1 | 0.888889 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV26 | naive | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 0 | 0 | 1 | 1 | 1 | 0 | 1 | 0 |
| AS-DEV27 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV27 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV27 | naive | abstained | abstained | 1 | 1 | 미적용 | True | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| AS-DEV28 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 0.714286 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV28 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 0 | 0.714286 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV28 | naive | answered | answered | 1 | 1 | 미적용 | True | 0.5 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV29 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 0.944444 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV29 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 1 | 0.888889 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV29 | naive | answered | answered | 1 | 1 | 미적용 | True | 1 | 0.933333 | 0 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV30 | hybrid | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 0 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV30 | hybrid_rerank | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 1 | 0 | 1 | 1 | 1 | 1 | 1 | 1 |
| AS-DEV30 | naive | answered | answered | 1 | 1 | 미적용 | True | 1 | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
