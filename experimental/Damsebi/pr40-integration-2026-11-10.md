# 2026-11-10 PR #40 통합과 실제 검증

[PR #40](https://github.com/Somali-AI-Pirates/ADEN/pull/40)(B·C 근거 주입 경로, 단계별 Langfuse 기록, 평가 점수 게시)을 C 평가 작업([1문항 점검·Judge 비교](results-2026-11-10.md))과 합치고, PR이 검증하지 못한 실제 Langfuse 서버 수신과 RAGAS를 확인한 기록이다. 대량 평가는 하지 않았다.

## 통합 방식

| 항목 | 내용 |
| --- | --- |
| 방식 | 전체 병합 후 보완. 통합 브랜치 `feat/bc-eval-integration` = `feat/evaluation-langfuse-check`(`f74817c`) + PR #40 head `0f33bad`(ysl727727) 병합 커밋 `81446b0` |
| 근거 | PR이 단일 커밋이고 stage → chain → query → 테스트가 맞물려 있어 일부만 고르면 PR의 테스트가 깨진다. 두 브랜치의 기준 커밋이 같고(`8434792`) 충돌은 두 파일뿐이었다 |
| 충돌 해결 | `regrade.py`: 양쪽이 같은 문제(.env 미로드)를 고쳤다. 공통 `load_env()`로 통일. `tracing.py`: retrieval 입력과 PR의 stage 상태를 모두 유지 |
| 보존 | 원래 C 브랜치와 이전 실험 기록(`artifacts/experiments/`, `reports/`)은 건드리지 않았다 |

유지 여부를 확인한 C 작업: `.env` 로드(`--verify-langfuse`, `regrade`), RAGAS 출력 한도 8,192, retrieval 입력, 반복 재채점·v1·v2 비교 기록과 도구.

## PR 설명과 실제 대조

| PR의 설명 | 확인 결과 |
| --- | --- |
| `pytest -q tests` 190 passed, ruff 통과 | 병합 직후 190개 통과, ruff 통과 |
| 후보만 주면 공통 `build_context`로 문맥 구성 | 실제 실행에서 저장 후보 20개 재생 → 최종 문맥 해시가 원본 실행과 3방식 모두 같음 |
| 최종 문맥까지 주면 다시 구성하지 않음 | 실제 실행에서 주입한 문맥의 해시가 그대로 기록·추적됨 |
| 관측을 실제 시작·끝 시각으로 기록, validation은 generation 아래 | 실제 서버에서 확인. 생성 구간 3,328ms ↔ 기록 3,327ms 등 11건 모두 일치 |
| 기대 관측 일부만 있으면 `partial` | 실제 trace에 없는 `generation-attempt-2`를 기대값에 넣어 `partial`과 누락 이름 확인 |
| 미평가 값은 0점으로 보내지 않음 | 평가 불가 Faithfulness 3건, 미실행 Judge가 서버에 없음을 조회로 확인 |
| uuid5 ID라 다시 보내도 중복되지 않음 | 같은 평가 36개 재게시 후 trace당 점수 수가 12개 그대로 |
| 게시가 실패해도 보고서 저장 | 단위 테스트로 확인. `run_benchmark`는 채점 결과를 게시 전에 저장하도록 순서를 보강 |
| 기본 앱·bootstrap은 색인 적재 전 503 | 단위 테스트로 확인. 실제 기본 앱 기동은 하지 않았다(Advanced 임베딩 모델을 내려받은 뒤 503이 되는 순서라 실행하지 않음) |
| 실제 Langfuse 수신·RAGAS 미확인 | 이번에 확인(아래) |

## 보완한 것

| 변경 | 이유 |
| --- | --- |
| `prepare_evidence`의 retrieval 구간에 질문·검색 방식 입력 | PR의 stage 경로에서는 `record_retrieval`의 retrieval 부분이 건너뛰어져 우리가 넣은 입력이 사라졌다. 실제 서버에서 입력 0자 → 783자 확인 |
| 게시 점수에 채점 조건 metadata, 채점 기록에 `langfuse_score_ids` | 재채점 점수가 원본 trace에 같은 이름으로 쌓인다. metadata(`experiment_id`, `judge_prompt`, `references_sha256` 등)로 구분하고, 보낸 ID로 서버 수신을 조회할 수 있게 했다 |
| `score_items()` 분리 | 게시와 수신 대조가 같은 이름 규칙을 쓰도록 한곳에 뒀다 |
| `run_benchmark`가 채점 결과를 게시 전에 저장 | 게시 중 예상하지 못한 오류가 나도 Judge·RAGAS 결과가 남는다 |
| 보고서 문구에 `partial` 반영 | `langfuse_verification`의 새 값이 보고서 설명과 맞지 않았다 |
| 평가 가이드·계약·데이터 가이드·로컬 B·C 안내 갱신 | PR이 평가 가이드를 고치지 않아 `partial`, 점수 게시, 503 영향, 색인 CLI와의 차이가 문서에 없었다 |

## 기존 Basic 경로에 미치는 영향

- 서비스 자원 로더가 메모리 Qdrant만 쓰므로 `app.service.bootstrap:query_adapter`로는 Qdrant Cloud의 Basic 컬렉션을 읽지 못한다. 색인을 같은 실행에서 적재하지 않으면 모든 요청이 503 오류로 기록된다.
- `build_index`·`eval_retrieval`은 설정 파일을 읽지 않고 자체 기본값(`data/basic`)과 Cloud 키를 그대로 쓴다. 기본 설정이 Advanced로 바뀐 영향은 없지만 서비스와 색인을 공유하지 않는다.
- 확보한 실행 경로: [bc_adapter.py](bc_adapter.py). 저장 후보 재생(임베더·Qdrant 없음), 최종 문맥 주입, Cloud 컬렉션을 메모리로 복사한 실제 검색 세 가지다. 절차는 [로컬 B·C 안내](../../docs/guides/local-bc.md#평가-cli에-연결)에 적었다.
- 메모리 복사 경로의 Basic 색인(청크 620개)에서 S01의 세 방식 후보 20개 순서와 최종 문맥 해시가 Cloud를 직접 읽은 이전 실행(`basic-smoke-s01-20261110`)과 모두 같았다.
- Basic 결과는 `configs/basic.yaml`(k 8, 4,000토큰, E5-small), Advanced 기본은 `configs/advanced.yaml`(k 8, 8,000토큰, snowflake-ko)이다. 문맥 예산과 임베딩이 달라 두 결과를 바로 비교할 수 없다. 오늘 실행은 모두 Basic이다.

## 실행 조건

| 항목 | 값 |
| --- | --- |
| 코드 | 병합 커밋 `81446b0` + 미커밋 보완(위 표). manifest의 `git_dirty: true`. diff는 `artifacts/experiments/basic-bc-fixed-s01-20261110/uncommitted.diff` |
| 설정 | `configs/basic.yaml` SHA-256 `9f802f7c…`(k 8, 4,000토큰, 후보 20개, `gpt-6-luna`, 최대 생성 2회). 참고: `configs/advanced.yaml` `6de5b9b6…` |
| 프롬프트 | 생성 `shipping_qa.txt` `1c3737e0…`, Judge v2 `judge.txt` `4079487d…`, v1 `de8f990b…` |
| 질문·정답 | `custom_questions.json` `f0629771…`, `custom.json` `3b36be5a…`(manifest의 `references_sha256`). 문항 S01 |
| 평가 | Judge·RAGAS `gpt-6-luna`, RAGAS 출력 한도 8,192 |
| Langfuse | `https://cloud.langfuse.com`(EU), 프로젝트 `Somali-AI-Pirates` |
| 어댑터 | `experimental.Damsebi.bc_adapter:query_adapter` |

## 실제 검증 결과

### 고정 근거 B·C 검증 (저장 후보 재생)

`basic-bc-fixed-s01-20261110`. `basic-smoke-s01-20261110`의 방식별 후보 20개를 재생했다. 검색 방식의 성능 비교가 아니다.

| 방식 | 상태 | 서비스 시간 | 생성 구간(Langfuse ↔ 기록) | 토큰 | 문맥 해시 = 원본 | Judge | Faithfulness | 점수 게시·수신 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| naive | answered | 3.4초 | 3,328 ↔ 3,327ms | 4,986 / 297 | 같음 | 1/1/1/1 | 0.82 | 12 / 12 |
| hybrid | answered | 3.5초 | 3,502 ↔ 3,501ms | 4,986 / 266 | 같음 | 1/1/1/1 | 0.91 | 12 / 12 |
| hybrid_rerank | answered | 5.1초 | 5,134 ↔ 5,132ms | 5,065 / 562 | 같음 | 1/1/1/1 | 0.82 | 12 / 12 |

세 요청 모두 관측 5개(`rag-query` 아래 `retrieval`·`final-context`·`generation-attempt-1`, 그 아래 `validation-attempt-1`)가 조회됐고 `provided_context=true`, 문맥 토큰 수·예산 제외 ID가 실행 기록과 같았다. 근거 진단은 후보·최종 쪽 포함 1.0, 누락 0이다.

### 최종 문맥 주입과 지원하지 않는 방식

`basic-bc-context-s01-20261110`. naive만 저장된 기록으로 naive·hybrid를 요청했다. Judge·RAGAS는 실행하지 않았다.

| 방식 | 결과 | 확인 |
| --- | --- | --- |
| naive | answered. 주입한 최종 문맥의 해시가 원본과 같음 | 관측 5개, 구간 일치. Judge·Faithfulness 미실행 5항목은 게시하지 않고 7개만 게시·수신 |
| hybrid | error(`NotImplementedError`, "이 검색 방식은 준비 중입니다."). 모델 호출 없음 | `rag-query`·`retrieval`이 ERROR로 기록됨. 판단 0점 1개만 게시·수신 |

### 메모리 Qdrant 실제 검색

`basic-bc-memory-s01-20261110`. Cloud의 Basic 컬렉션을 메모리로 복사해 세 방식을 실제로 검색했다.

| 방식 | 상태 | 서비스 시간 | retrieval 구간 | 후보 순서·문맥 해시 = Cloud 실행 | Judge | Faithfulness | 점수 게시·수신 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| naive | answered | 5.0초 | 33ms | 같음 | 1/1/1/1 | 0.83 | 12 / 12 |
| hybrid | answered | 6.1초 | 78ms | 같음 | 1/1/1/1 | 0.77 | 12 / 12 |
| hybrid_rerank | answered | 17.3초 | 11,907ms | 같음 | 1/1/1/1 | 0.93 | 12 / 12 |

`retrieval` 관측의 구간이 metadata의 `latency_ms`와 같고 `provided_context=false`다. 1문항이라 방식 간 차이는 해석하지 않는다.

### 검증 실패·재시도·생성 실패 (모의 모델, 실제 Langfuse)

`mock-retry-20261110`. [mock_retry_trace.py](mock_retry_trace.py)로 모델만 모의 객체로 바꿔 서비스 경로를 그대로 실행했다. 모델 호출 비용은 없다.

| 상황 | 실행 기록 | 서버 관측 |
| --- | --- | --- |
| 검증 실패 후 재시도 성공 | answered, 시도 2회(1회차 `ValueError`) | 7개. `generation-attempt-1`·`validation-attempt-1`이 ERROR, 2회차는 정상 |
| 두 번 모두 검증 실패 | error(`ValueError`) | 7개. 두 시도와 `rag-query`가 ERROR |
| 모델 호출 실패 | error(`RuntimeError`), 시도 1회 | 4개. `generation-attempt-1`이 출력 없이 ERROR, validation 없음 |

이전 기록에서 확인하지 못했던 `generation-attempt-2`와 오류 trace가 여기서 확인됐다.

### 평가 점수 게시

| 확인 | 방법 | 결과 |
| --- | --- | --- |
| 수신과 값 일치 | 보낸 ID로 점수를 조회해 이름·값·대상 trace·metadata의 실험 ID 대조 | 게시한 6개 실험 185개(36+36+36+33+8+36) 모두 일치 |
| 같은 평가 재게시 | `basic-bc-fixed-s01-20261110`의 36개를 다시 게시 | ID 동일, trace당 12개 그대로 |
| v1·v2 재채점 게시 | 같은 저장 답변을 v1, v2로 재채점해 원본 trace에 게시 | trace당 12 → 24 → 36개. 원본 평가 점수는 그대로, 각 평가의 36개가 각각 일치 |
| 평가 불가 미게시 | Faithfulness가 평가 불가였던 `basic-smoke-s01-20261110`을 재채점해 게시 | 33개 게시, 3개 건너뜀. 서버의 해당 trace에 `faithfulness` 점수 없음 |
| 식별 | 점수 metadata | `experiment_id`, `config_hash`, `k`, `judge`, `faithfulness`, `judge_prompt`, `references_sha256`, `evaluation_hash` |

한계: 같은 trace에 평가마다 같은 이름(`judge.fact` 등)이 하나씩 쌓인다. Langfuse의 이름별 집계는 v1·v2·반복 채점을 섞으므로 metadata로 걸러 보거나 최종 비교에 쓸 평가만 게시한다. 재채점은 바뀌지 않은 검색 지표와 재사용한 Faithfulness도 다시 게시한다.

### 저장된 개발용 실행의 근거 누락 진단

`basic-dev-20261110`의 저장 기록(8문항 × 세 방식)에 PR의 진단을 적용했다. 모델 호출은 없다.

| 방식 | Recall@8(문서) | 후보의 정답 쪽 | 최종 문맥의 정답 쪽 | 후보에는 있었으나 최종에서 빠짐 | 예산으로 빠짐 |
| --- | --- | --- | --- | --- | --- |
| naive | 0.812 | 0.542 | 0.438 | 0.104 | 0 |
| hybrid | 0.875 | 0.667 | 0.542 | 0.125 | 0 |
| hybrid_rerank | 0.875 | 0.667 | 0.667 | 0 | 0 |

- 문서 단위 Recall은 높지만 정답 쪽은 후보 20개 단계에서 이미 빠진 경우가 많다. M03은 세 방식 모두 정답 쪽 3개가 후보에 없다.
- 후보에는 있는데 최종 8개에서 밀린 경우는 naive·hybrid에만 있다(예: S09 hybrid). 토큰 예산 때문에 빠진 근거는 없다.
- 쪽 포함은 엄격한 기준이다. S09는 정답 쪽이 없어도 다른 쪽의 근거로 Judge 사실 1점을 받았다.

## 대표 trace

| 내용 | trace |
| --- | --- |
| 고정 근거, naive(점수 v2·v1·v2 재채점 36개) | [`44daf089…`](https://cloud.langfuse.com/project/cmw31qdbs01yvad0japltbo24/traces/44daf089868607bb6fd822903fdde1f1) |
| 메모리 Qdrant 실제 검색, hybrid_rerank | [`b7d9486a…`](https://cloud.langfuse.com/project/cmw31qdbs01yvad0japltbo24/traces/b7d9486a9d8a70bf298661987a54f688) |
| 지원하지 않는 방식 오류 | [`9c28a5bf…`](https://cloud.langfuse.com/project/cmw31qdbs01yvad0japltbo24/traces/9c28a5bfe27933d49f1f9bae5b8a34dc) |
| 검증 실패 후 재시도 성공(모의 모델) | [`4bcb88bc…`](https://cloud.langfuse.com/project/cmw31qdbs01yvad0japltbo24/traces/4bcb88bc1aaff9a85528a421d216668f) |
| 평가 불가 Faithfulness 미게시(이전 형식 trace) | [`d1a09535…`](https://cloud.langfuse.com/project/cmw31qdbs01yvad0japltbo24/traces/d1a09535f074de241a3e40df632134e6) |

나머지 trace ID는 각 보고서의 `answers.jsonl`에 있다.

## 재현 명령

```bash
# 고정 근거 B·C 검증(저장 후보 재생) + 점수 게시
ADEN_EVIDENCE_RUNS=artifacts/experiments/basic-smoke-s01-20261110/runs.jsonl \
python -m app.evaluation.scripts.run_benchmark --adapter experimental.Damsebi.bc_adapter:query_adapter \
    --config configs/basic.yaml --questions data/basic/evaluation/custom_questions.json \
    --references data/basic/evaluation/custom.json --ids S01 --judge-model gpt-6-luna --ragas-model gpt-6-luna \
    --publish-langfuse-scores --experiment-id <새 ID> --reports-dir experimental/Damsebi/reports

# 메모리 Qdrant 실제 검색: ADEN_EVIDENCE_RUNS 없이 같은 명령(.env에 QDRANT_URL·QDRANT_API_KEY)
# 저장 답변 재채점 + 원본 trace에 게시
python -m app.evaluation.scripts.regrade --source <원본 ID> --references data/basic/evaluation/custom.json \
    --judge-model gpt-6-luna [--judge-prompt <v1 파일>] --publish-langfuse-scores --reports-dir experimental/Damsebi/reports

# 서버 저장 내용과 점수 수신 대조
python experimental/Damsebi/check_langfuse.py runs artifacts/experiments/<ID>/runs.jsonl
python experimental/Damsebi/check_langfuse.py scores artifacts/experiments/<채점 ID> [<원본 runs.jsonl>]
```

## 결과 위치

전체 기록은 `artifacts/experiments/<ID>/`(Git 제외), 보고서는 [reports/](reports/) 아래 같은 ID다.

| 실험 ID | 내용 |
| --- | --- |
| `basic-bc-fixed-s01-20261110` | 고정 근거(후보 재생) 3건, v2 채점, 점수 게시 |
| `basic-bc-fixed-s01-20261110-regrade-v1`, `-v2` | 같은 답변 v1·v2 재채점, 원본 trace에 게시 |
| `basic-bc-context-s01-20261110` | 최종 문맥 주입 1건, 지원하지 않는 방식 오류 1건 |
| `basic-bc-memory-s01-20261110` | 메모리 Qdrant 실제 검색 3건, v2 채점, 점수 게시 |
| `basic-smoke-s01-20261110-regrade-publish` | 평가 불가 항목이 있는 이전 기록 재채점, 이전 형식 trace에 게시 |
| `mock-retry-20261110` | 모의 모델 3건(실행 기록만, 보고서 없음) |

## 검증한 것과 못 한 것

검증:

- 병합 후 `ruff check .`, `pytest -q tests`, `run_benchmark`·`regrade`의 `--help`, 가상 샘플과 가상 점수 게시
- 실제 생성(`gpt-6-luna`)·Judge·RAGAS·Langfuse로 provider 세 경로(후보만, 후보+문맥, provider 없음)
- 실제 서버의 관측 존재·상하 관계·실제 구간, 문맥·출처·해시·토큰의 실행 기록 일치
- 점수 수신, 재게시 중복 없음, v1·v2 분리 보존, 미평가 미게시, 정답 파일 해시의 식별 반영
- 정답·근거 라벨이 provider와 생성 입력에 들어가지 않음(provider는 질문과 방식만 받고, 실행 기록은 정답을 읽기 전에 저장된다)

단위 테스트로만 확인(실제 실행 안 함):

- k·토큰 한도 초과 503, 빈 문맥 보류, 자원 종료, 점수 전송 실패 시 로컬 결과 보존
- 보안 비교의 base/defense 동일 문맥(Basic 보안 파일이 이 PC에 없다)

미검증:

- Advanced 설정·색인·질문으로의 실행, 기본 앱(`create_app()`)의 실제 기동과 HTTP 503
- 여러 문항·전체 문항 실행과 공식 세 방식 비교
- Langfuse UI에서의 점수 표시와 이름별 집계 화면(브라우저 로그인 없음, API 조회로만 확인)
- 게시 실패를 실제 서버 장애로 재현하는 것

## 남은 A 연결 작업

- 표준 bootstrap(`app.service.bootstrap`)에 색인 적재 경로 연결: 메모리 Qdrant에 벡터·payload를 적재해 `load_resources(settings, qdrant_client=...)`로 넘기기. 그때까지 평가 CLI는 근거 공급 어댑터가 필요하다.
- Advanced 색인(`advanced_snowflake_ko_pages`)의 보관·재적재 방식과 `build_index`·`eval_retrieval`·데이터 가이드의 Cloud 의존 정리.
- A가 후보만 넘길지 최종 문맥까지 넘길지 결정. 어느 쪽이든 실제 서비스 경로는 확인됐다.
- Advanced 질문·정답으로 1문항 점검 후 세 방식 비교.
