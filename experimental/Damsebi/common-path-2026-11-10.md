# 2026-11-10 공통 실행 경로와 평가 세트별 점수 게시

[PR #40 통합 검증](pr40-integration-2026-11-10.md)에 이어 세 가지를 보완하고 Basic 1문항(S01)으로 실제 확인한 기록이다. 대량 평가는 하지 않았다.

1. 재채점 결과를 게시 전에 저장한다.
2. Langfuse 점수 이름을 `<평가 세트>.<지표>`로 나눠 v1·v2·반복 채점의 집계가 섞이지 않게 한다.
3. API와 평가 CLI가 같은 자원 준비(`load_resources`)와 같은 환경변수로 실행된다. 개인 어댑터 `bc_adapter.py`는 없앴다.

규칙은 [평가 가이드](../../docs/guides/c-module-guide.md#평가-점수-게시)와 [로컬 B·C 안내](../../docs/guides/local-bc.md#api와-평가-cli의-공통-실행)에 있다.

## 실행 조건

| 항목 | 값 |
| --- | --- |
| 코드 | 커밋 `a3ee99e` + 이 보완의 미커밋 변경(manifest의 `git_dirty: true`). 실행 뒤 코드는 바꾸지 않고 그대로 커밋했다. diff는 `artifacts/experiments/basic-common-s01-20261110/uncommitted.diff` |
| 설정 | `configs/basic.yaml` SHA-256 `9f802f7c…`(k 8, 4,000토큰, 후보 20개, `gpt-6-luna`). 실행 기록·trace의 `config_hash`는 `45ca63d0…` |
| 프롬프트 | 생성 `shipping_qa.txt` `1c3737e0…`, Judge v2 `judge.txt` `4079487d…`, v1 `de8f990b…`(`git show 07788a3:app/evaluation/prompts/judge.txt`) |
| 질문·정답 | `custom_questions.json` `f0629771…`, `custom.json` `3b36be5a…`. 문항 S01(개발용) |
| 평가 | Judge·RAGAS `gpt-6-luna`, RAGAS 출력 한도 8,192 |
| 색인 | Qdrant Cloud `basic_multilingual-e5-small_p800`(620개)을 프로세스마다 메모리 Qdrant로 복사 |
| Langfuse | `https://cloud.langfuse.com`(EU), 프로젝트 `Somali-AI-Pirates` |

## 공통 실행 경로

같은 환경변수로 API 서버와 평가 CLI를 따로 실행했다.

```bash
export ADEN_CONFIG=configs/basic.yaml
export ADEN_QDRANT_CLIENT=experimental.Damsebi.evidence_sources:memory_copy

fastapi run --host 127.0.0.1    # 검증에서는 같은 앱을 uvicorn으로 127.0.0.1:8765에 띄웠다
python -m app.evaluation.scripts.run_benchmark --adapter app.service.bootstrap:query_adapter \
    --config configs/basic.yaml --questions data/basic/evaluation/custom_questions.json \
    --references data/basic/evaluation/custom.json --ids S01 --judge-model gpt-6-luna --ragas-model gpt-6-luna \
    --publish-langfuse-scores --experiment-id basic-common-s01-20261110 --reports-dir experimental/Damsebi/reports
```

### 실제 검색(메모리 Qdrant)

API는 `GET /health`가 `dataset=basic`, `query_ready=true`였고 `POST /query` 세 건이 모두 200·`answered`였다. API 요청의 trace와 평가 실행 기록을 방식마다 13개 항목으로 대조했다: 질문, 검색 방식, 데이터셋, 설정 해시, 후보 20개 순서, 최종 문맥의 청크·출처 ID, 최종 문맥 해시, 모델 문맥 해시, k·문맥 토큰, API 출처가 같은 문맥의 출처인지, 응답 `request_id`와 trace의 연결, 그리고 이전 두 실행(Cloud 직접 `basic-smoke-s01-20261110`, 개인 어댑터 `basic-bc-memory-s01-20261110`)과의 문맥 해시.

| 방식 | API trace | 평가 trace | 최종 문맥 | 대조 |
| --- | --- | --- | --- | --- |
| naive | [`c5d438d8…`](https://cloud.langfuse.com/project/cmw31qdbs01yvad0japltbo24/traces/c5d438d819eb1447bc95707c787842af) | [`a4ec7f32…`](https://cloud.langfuse.com/project/cmw31qdbs01yvad0japltbo24/traces/a4ec7f320e15c68d3f7465db9148514d) | 8청크 3,317토큰 `3314ab27…` | 13개 일치 |
| hybrid | [`f84147cd…`](https://cloud.langfuse.com/project/cmw31qdbs01yvad0japltbo24/traces/f84147cd865c68bddc14de822c4d1d0a) | [`4c5d6abc…`](https://cloud.langfuse.com/project/cmw31qdbs01yvad0japltbo24/traces/4c5d6abcd4221f5dc65e4bd8b1b23944) | 8청크 3,317토큰 `ac65a9ec…` | 13개 일치 |
| hybrid_rerank | [`fb2512c4…`](https://cloud.langfuse.com/project/cmw31qdbs01yvad0japltbo24/traces/fb2512c4bd6d941653d5723b6409fac4) | [`8e1ace7a…`](https://cloud.langfuse.com/project/cmw31qdbs01yvad0japltbo24/traces/8e1ace7a8facf8009f728b119f53e976) | 8청크 3,396토큰 `8aae0f3a…` | 13개 일치 |

답변 문장과 모델이 인용한 출처는 호출마다 달라질 수 있다(hybrid는 API가 3개, 평가가 1개를 인용). 인용된 출처는 모두 같은 최종 문맥의 출처 ID였다. 평가 실행 3건은 Judge 1/1/1/1, Faithfulness 0.889·1.0·0.857이고 저장 확인은 `verified` 3건, 관측 구간은 기록과 일치했다(예: `rag-query` 10,418ms ↔ 10,419ms).

### 근거 주입(저장 후보 재생)

고정 근거 B·C 검증이다. 검색 방식의 성능 비교가 아니다.

```bash
export ADEN_EVIDENCE_PROVIDER=experimental.Damsebi.evidence_sources:stored_evidence
export ADEN_EVIDENCE_RUNS=artifacts/experiments/basic-smoke-s01-20261110/runs.jsonl
```

API([`ad50ba47…`](https://cloud.langfuse.com/project/cmw31qdbs01yvad0japltbo24/traces/ad50ba473c23ea8415022ff2ce72d868))와 평가(`basic-common-replay-s01-20261110`, [`ea2bc117…`](https://cloud.langfuse.com/project/cmw31qdbs01yvad0japltbo24/traces/ea2bc117d9883f87eba237381fe2f6de))를 naive 1건씩 실행했다. `provided_context=true`, 최종 문맥 해시가 원본 실행과 같고 12개 항목이 일치했다. Judge 1/1/1/1, Faithfulness 0.857.

### 준비 실패 안내

| 상황 | API | 평가 CLI |
| --- | --- | --- |
| 연결 지정 없음 | `/health` `query_ready=false`, `/query` 503 "검색 색인 'basic_multilingual-e5-small_p800'이 없습니다. ADEN_QDRANT_CLIENT로 … ADEN_EVIDENCE_PROVIDER로 …" | 첫 질문 전에 같은 문구로 멈춤. 실험 폴더를 만들지 않음 |
| 없는 함수 지정 | 503 "ADEN_QDRANT_CLIENT=…:no_such_function 준비에 실패했습니다(AttributeError). …" | 단위 테스트로 확인 |
| 재생할 기록 경로 누락 | 실행하지 않음 | "ADEN_EVIDENCE_RUNS에 재생할 실행 기록(runs.jsonl) 경로를 지정해 주세요." |
| 없는 `ADEN_CONFIG` 파일 | 실행하지 않음 | "ADEN_CONFIG가 가리키는 설정 파일이 없습니다: <경로>" |

이전 기록의 "표준 어댑터는 모든 요청이 503으로 기록된다"는 틀렸다. 평가 CLI는 요청을 기록하지 않고 첫 질문 전에 멈춘다.

## 평가 세트별 점수 게시

같은 trace 3건(`basic-common-s01-20261110`의 답변)에 네 세트를 게시했다.

| 실험 | 평가 세트 | 채점 | 지표군 | 게시 | 사실·조건·근거·판단 (naive / hybrid / hybrid_rerank) |
| --- | --- | --- | --- | --- | --- |
| `basic-common-s01-20261110` | (기본) 실험 ID | v2, RAGAS 계산 | 전체 | 36 | 1111 / 1111 / 1111 |
| `…-regrade-v1-r1` | `common-s01-v1-r1` | v1, Faithfulness 옮김 | judge | 12 | 0111 / 1111 / 1111 |
| `…-regrade-v2-r1` | `common-s01-v2-r1` | v2, Faithfulness 옮김 | judge | 12 | 1111 / 1111 / 1111 |
| `…-regrade-v2-r2` | `common-s01-v2-r2` | v2, RAGAS 재계산, `--publish-groups all` | 전체 | 36 | 1111 / 1111 / 1111 |

v1의 naive 사실 0점은 답변이 묻지 않은 "이메일만으로 예약이 확정되지 않는다"를 빠뜨렸다는 이유다. [기준 이력](../../docs/guides/judge-rubric.md)의 v1·v2 차이와 같은 유형이며 한 문항이라 새 근거로 쓰지 않는다.

서버 조회 결과(`check_langfuse.py scores`):

- 다섯 실험의 점수 108개가 모두 수신됐고 이름·값·대상 trace·metadata(평가 세트, 채점 조건 해시, 실험 ID, 원본 실험 ID, 평가 모델, 프롬프트·정답·설정 해시)가 로컬 기록과 같았다.
- trace당 점수는 12 + 4 + 4 + 12 = 32개이고 같은 이름은 1개씩이다. 세트 이름으로 온 점수가 보낸 것과 정확히 같아, 고르지 않은 지표군(옮겨 온 Faithfulness, 그대로인 검색 지표)은 게시되지 않았다.
- 이름으로 조회하면 그 세트의 점수만 나온다. Langfuse 집계(Metrics API, 이름 기준 평균)도 세트별로 나뉜다.

| 점수 이름 | 건수 | 평균 |
| --- | --- | --- |
| `common-s01-v1-r1.judge.fact` | 3 | 0.667 |
| `common-s01-v2-r1.judge.fact` | 3 | 1.0 |
| `common-s01-v2-r2.judge.fact` | 3 | 1.0 |
| `judge.fact`(세트 이름 도입 전, v1·v2·반복 채점이 섞임) | 15 | 0.867 |

- 같은 결과 재게시(`republish_scores.py`로 `common-s01-v2-r1` 12개): 점수 ID가 이전과 같고 trace당 32개 그대로였다.
- 세트 재사용 거부: v2 조건을 `common-s01-v1-r1`에 게시하려는 재채점과, 같은 조건의 반복 채점을 `common-s01-v2-r1`에 게시하려는 재채점이 채점 호출 전에 멈췄고 실험 폴더가 생기지 않았다.
- 이전에 게시한 점수(이름에 세트가 없는 185개)는 그대로 있다. 비교에 쓰지 않는다.

재채점 선저장(게시 전 저장, 게시 중 중단·전송 실패 시 결과 보존)은 모의 클라이언트에 중단과 실패를 주입한 단위 테스트로 확인했다. 실제 서버를 상대로 게시를 중단시키는 실행은 하지 않았다.

## 재현 명령

```bash
# 재채점: 기준·회차마다 다른 평가 세트. 기본은 Judge만 게시
python -m app.evaluation.scripts.regrade --source basic-common-s01-20261110 \
    --references data/basic/evaluation/custom.json --judge-model gpt-6-luna [--judge-prompt <v1 파일>] \
    --publish-langfuse-scores --evaluation-set <세트> --experiment-id <새 ID> --reports-dir experimental/Damsebi/reports
# 최종 비교용: RAGAS를 다시 계산하고 모든 지표군을 한 세트로 게시
#   ... --ragas-model gpt-6-luna --publish-groups all --evaluation-set <최종 세트>

# 수신·중복 확인, 같은 결과 재게시
python experimental/Damsebi/check_langfuse.py scores artifacts/experiments/<채점 ID> [<원본 runs.jsonl>]
python experimental/Damsebi/republish_scores.py artifacts/experiments/<채점 ID> [<원본 runs.jsonl>]
```

보고서는 [reports/](reports/) 아래 `basic-common-s01-20261110`, `…-regrade-v1-r1`, `…-regrade-v2-r1`, `…-regrade-v2-r2`, `basic-common-replay-s01-20261110`이다. 전체 기록은 `artifacts/experiments/` 아래 같은 ID다(Git 제외).

## 검사

`ruff check .` 통과, `pytest -q tests` 199개 통과. 추가한 검사: 게시 중 중단·전송 실패 주입, 세트 이름·지표군 선택·세트 재사용 거부, 환경변수 연결로 API와 평가 어댑터가 같은 설정·문맥을 쓰는지, 잘못된 연결의 안내 문구.

## 확인하지 못한 것

- Langfuse 화면의 세트별 선택·비교. 내장 브라우저가 로그인돼 있지 않아 보지 못했고 점수 조회 API와 Metrics API로만 확인했다.
- Advanced 실행. 이 PC와 Qdrant Cloud에 Advanced 색인이 없다(아래).
- 기본 설정(Advanced)으로 앱을 띄운 실제 기동. Advanced 임베딩 모델을 내려받아야 해서 Basic 설정으로만 띄웠다.
- 여러 문항 실행과 공식 세 방식 비교, 보안 비교의 실제 실행.
- 다른 PC에서 같은 세트 이름으로 게시했을 때의 충돌. 세트 대조는 이 PC의 `artifacts/experiments/`만 본다.

## Advanced 1문항에 필요한 것

| 필요 | 현재 |
| --- | --- |
| 색인 `advanced_snowflake_ko_pages`의 벡터·payload | Qdrant Cloud에 없음(Basic 컬렉션 3개만 있음). 로컬 보관본 없음 |
| 색인을 메모리 Qdrant에 적재해 반환하는 함수 `(settings) -> QdrantClient` | 없음. A가 제공하면 `ADEN_QDRANT_CLIENT`에 지정한다. Cloud에 컬렉션을 올리면 `memory_copy`로도 실행할 수 있다 |
| 임베딩 모델 `dragonkue/snowflake-arctic-embed-l-v2.0-ko` | 이 PC에 받은 적 없음. 첫 실행 때 내려받는다 |
| 문서 `data/advanced/documents/` | 없음. 색인을 새로 만들 때만 필요 |
| 설정 `configs/advanced.yaml`(k 8, 8,000토큰) `6de5b9b6…` | 있음 |
| 질문·정답 `data/advanced/evaluation/custom_questions.json`·`custom.json`(40문항) | 있음 |

색인이 준비되면 `ADEN_CONFIG=configs/advanced.yaml`과 적재 함수만 바꿔 위와 같은 절차로 1문항을 실행한다.
