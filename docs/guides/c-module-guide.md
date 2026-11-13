# 평가와 실행 기록

기본 서버의 DB 우선 통합 TableRAG는 [TableRAG 평가](#tablerag-평가)를 따른다. 저장된 Swagger 요청은 [빠른 시작](quickstart.md#4-저장된-swagger-답변-평가)에서 바로 채점할 수 있다. 최신 실행 검증 결과는 [구현 기록](../plans/advanced-rag/unified-implementation-log.md)에 남긴다.

아래 일반·보안 평가의 3방식 비교와 Qdrant/bootstrap 설명은 기존 평가 어댑터용이다. TableRAG는 준비된 공통 번들을 사용한다. NumPy 경로에는 Qdrant 준비가 필요 없으며, Qdrant backend를 선택했다면 [빠른 시작](quickstart.md)의 전용 컬렉션 검사까지 마쳐야 한다.

## 가상 샘플

저장소 루트의 [공통 환경](development.md#설치)에서 실행한다. 실제 자료·키·모델 호출 없이 실행 기록부터 보고서 생성까지 확인할 수 있다.

```bash
python -m app.evaluation.scripts.run_benchmark --sample --experiment-id sample-guide-benchmark
python -m app.evaluation.scripts.run_security --sample --experiment-id sample-guide-security
```

재실행에는 새 `sample-` ID를 쓴다. 기본 출력은 `artifacts/experiments/`와 `artifacts/sample-reports/`이며 `data_kind=SAMPLE`로 표시된다.

## 팀 공식 실행

1. [팀 작업](../CONTRIBUTING.md#팀-기록)에 담당자, 목적, 질문·검색 방식, 모델·평가기, 예상 호출 수와 비용, 한도·중단 기준, 설정·결과 위치를 기록한다. 비용 한도와 담당을 합의한 뒤 유료 실행을 시작한다. 한도에 도달하면 멈추고 부분 결과를 확인한다.
2. 질문 ID와 선정 이유, 데이터·색인·모델·프롬프트·설정·`k`·문맥 예산을 고정한다. 선택한 일반 질문 전부를 같은 조건으로 세 방식에 실행한다.
3. 먼저 한 문항에서 모델·평가기·추적 연결, 형식, 시간, 확인 가능한 토큰 수를 점검한다. 생성 요청의 호출 한도와 별도로 평가기 호출 수·비용을 확인한다.

## 실제 연결

- 실행 전에 질문·참고 파일 구조, ID 대응과 채점 필드(`evidence_groups` 등)를 오프라인에서 확인한다. 질문은 `id`와 `question`으로 실행하고, 정답·근거·공격 라벨은 실행 후 채점에서 사용한다.
- 실제 일반·보안 평가는 항상 설정 파일 하나를 읽는다. 선택 순서는 `--config` → `ADEN_CONFIG` → `configs/advanced.yaml`이고 상대 경로는 저장소 루트 기준이다. 저장소 루트 `.env`의 `ADEN_CONFIG`도 반영하되 이미 설정된 프로세스 환경변수가 우선한다. 선택한 파일이 없거나 YAML·설정이 잘못되면 다른 파일로 대체하지 않고 어댑터·서비스 자원·Judge·RAGAS를 준비하기 전에 멈춘다.
- 데이터셋(`dataset`), 채점 k(`retrieval.k`), manifest의 설정 해시(파일 SHA-256)는 그 설정 파일에서 정한다. 2026-11-12에 `--config-hash`·`--k`·`--dataset-id`(일반), `--config-hash`·`--dataset-id`(보안)를 없앴다. 이전 명령에서 `--k`·`--dataset-id`는 같은 값을 가진 설정 파일을 `--config`로 주는 것으로, 설정 파일 없이 `--config-hash`로 실행하던 사용자 정의 어댑터는 실행 조건을 담은 설정 파일을 만들어 `--config`로 주는 것으로 바꾼다. 이 옵션을 주면 argparse 오류로 멈춘다. 과거 기록의 재현 명령은 당시 코드 기준이다.
- 일반 평가 `run_benchmark`에 `--questions`와 해당 split의 `--references`, `--adapter 모듈:이름`을 전달한다. `--ids`로 먼저 한 문항을 고른다. `--references`의 기본값은 질문 파일이므로 제공 자료처럼 파일이 분리되면 둘 다 지정한다. 가상 샘플(`--sample`)은 설정 파일을 읽지 않고 고정 조건(k 8)을 쓴다.
- 보안 평가 `run_security`는 `--security-file`, 초기화된 `--retrieve`·`--generate`, `--retrieval-mode`를 받고 두 callable에 같은 설정 경로를 전달한다. 문항별로 같은 최종 문맥을 기본·방어 프롬프트에 재사용한다. 노출 분석의 공격 문장은 항목의 `attack_text`, 없으면 `embedded_excerpt`(Advanced `security_tasks.json`)에서 읽는다. 공격 미노출과 생성 실패를 방어 성공으로 세지 않는다.
- 선택한 설정 경로는 표준 `app.service.bootstrap`의 실제 실행 설정으로 전달한다. CLI는 모듈의 선택적 `configure_evaluation(config_path)` 훅을 자원 준비 전에 호출한다. 사용자 정의 어댑터는 이 훅을 제공하거나 자체 설정 로더를 같은 파일에 맞춰야 한다. 이미 준비된 표준 자원은 다른 설정 경로나 같은 파일의 내용 변경을 감지하면 거부하므로 다른 설정의 실험은 새 실행으로 시작한다.
- 초기화된 함수는 `app.service.bootstrap`이 제공한다. 일반 평가는 `--adapter app.service.bootstrap:query_adapter`, 보안 평가는 `--retrieve app.service.bootstrap:retrieve_context --generate app.service.bootstrap:generate_with_context`를 지정하고 설정 파일(생략 시 위 선택 순서, Basic 재현은 `--config configs/basic.yaml`)을 정한다. 자원은 첫 사용 때 준비되며 계약은 [어댑터 계약](../design/contracts.md#평가-어댑터)을 따른다.
- 표준 bootstrap은 일반·보안 평가용 자원 준비(load_resources)를 사용한다. 색인을 적재한 메모리 Qdrant(ADEN_QDRANT_CLIENT)나 준비된 근거(ADEN_EVIDENCE_PROVIDER)는 [평가 CLI의 공통 bootstrap](local-bc.md#bc-평가-cli와-공통-bootstrap)에 연결한다. 연결이 없거나 잘못되면 첫 질문 전에 원인을 알리고 멈춘다. 저장된 후보를 주입한 실행은 고정 근거 B·C 검증이다. 세 방식의 검색 성능 비교는 각 방식이 실제로 검색한 후보가 준비됐을 때 한다. 현재 TableRAG 서버 설정은 [빠른 시작](quickstart.md)을 따른다.
- Judge·Faithfulness는 `--judge-model`·`--ragas-model`을 지정하지 않으면 `unavailable`이다. RAGAS는 공통 설치에 포함되지 않으며 [`eval` 추가 설치](development.md#설치)로 받는다(ragas 0.4.3 고정). RAGAS 평가 모델의 출력 한도는 8,192토큰으로 고정한다. ragas 기본값 1,024에서는 실제 답변의 출력이 잘려 평가 불가가 된다.

## TableRAG 평가

기본 API와 같은 DB 우선 서비스를 실행하고 저장된 결과를 별도로 채점한다. 서버에서 이미 질문한 결과는 [빠른 시작](quickstart.md#4-저장된-swagger-답변-평가)을 따라 request_id 폴더에서 채점한다.

공식 평가 파일이 준비되어 있으면 먼저 한 문항을 실행한다. 실행 ID와 grade ID는 기존 출력과 겹치지 않게 지정한다.

```bash
RUN_ID="table-rag-dev01-$(date +%Y%m%d-%H%M%S)"
uv run --locked python -m app.evaluation.scripts.run_table_rag run --questions data/advanced/evaluation/development_questions.json --ids AS-DEV01 --experiment-id "$RUN_ID"
```

명령이 출력한 실행 디렉터리를 다음 --run-directory에 지정한다.

```bash
uv run --locked --extra eval python -m app.evaluation.scripts.run_table_rag grade --run-directory <실행_디렉터리> --references data/advanced/evaluation/development.json --grade-id luna --judge-model gpt-6-luna --ragas-model gpt-6-luna
uv run --locked python -m app.evaluation.scripts.run_table_rag publish --grade-directory <실행_디렉터리>/grades/luna --verify
```

실행 명령은 run_table_rag다. 기본 데이터 디렉터리는 artifacts/advanced-table-rag다. API·RAG CLI와 같은 설정 우선순위를 사용하며, `--data-directory`를 명시하면 YAML과 환경값보다 우선한다. `--config`는 해당 실행의 YAML을 선택한다. `grade`의 점수 게시와 별도 `publish`도 같은 옵션을 받는다. 명시 YAML을 쓴 실행의 점수는 같은 YAML을 지정해 게시한다. 새 CLI 실행은 안전한 Langfuse 주소를 manifest에 남기고, 게시 시 설정 주소가 다르면 외부 호출 전에 거절한다. 기존 기록에 주소가 없으면 기본/명시 설정을 사용한다. 게시 대상은 실행과 동일한 우선순위로 해석한다. 프로세스의 연결 주소가 루트 `.env`에 기록된 다른 이름의 주소보다 우선한다. 연결 주소에는 인증 정보·쿼리·프래그먼트가 없는 HTTP(S) URL을 사용한다. 게시 설정 해석은 번들이나 모델을 로드하지 않는다.

참고답 없이 grade --ragas-model gpt-6-luna를 실행하면 Faithfulness를 계산한다. 참고답 기반 지표는 unavailable 사유로 표시한다. 참고답이 있으면 실제 실행 question_id에 해당하는 항목을 사용하며 SQL 기대값은 expected_sql_results의 columns와 rows에 기록한다.

개발·추가·커스텀 문항을 일괄 채점하려면 run에 해당 세트의 질문 파일, grade에 같은 세트의 참고 파일을 지정한다. 위 run 예시에서 --ids를 빼면 해당 세트의 전체 문항을 실행한다. 문항 ID로 연결하므로 요청별 참고 파일을 따로 만들 필요가 없다. grade --judge-model을 실행하면 기존 인용문은 그대로 전달하고, 인용문 없이 book_id와 page만 있는 근거는 실행에 사용한 TableRAG 번들의 pages.json에서 본문을 읽어 Judge의 참고 기준에 추가한다. 번들을 옮겼으면 grade --data-directory로 그 위치를 지정한다. 페이지가 없거나 번들 지문이 일치하지 않으면 채점 전에 오류를 표시한다. 보완에 사용한 페이지 파일의 지문은 채점 manifest에 기록한다.

참고 페이지는 Judge의 참고 기준으로만 전달된다. 근거·인용·검산 평가는 서비스가 실제 사용한 최종 문맥을 기준으로 하며, Faithfulness도 같은 최종 문맥을 사용한다. 실행·참고 파일은 그대로 두고 새 채점 폴더에 결과를 저장한다. grade --publish-langfuse-scores로 채점 후 원래 trace에 점수를 게시할 수 있다.

단일 Swagger 실행을 기존 데이터셋 질문과 연결할 때는 --reference-id AS-DEV01처럼 지정한다. 이 옵션은 저장된 질문을 다시 실행하지 않고 채점 ID만 연결하므로 실제 질문이 같은지 먼저 확인한다.


## 최종 Advanced 평가 세트 준비

**상태: 평가 코드와 집계 경로를 준비 중이며, 실제 프롬프트 교정과 280문항 실행은 아직 하지 않았다. 아래에는 결과 점수를 적지 않는다.** 질문과 참고 파일은 [`final_benchmark_questions.json`](../../data/advanced/evaluation/final_benchmark_questions.json)과 [`final_benchmark.json`](../../data/advanced/evaluation/final_benchmark.json)이며, ID로 연결한다. 최종 벤치마크는 각 질문을 한 번만 집계한다.

| 구분 | 문항 수 |
| --- | ---: |
| Development | 30 |
| Additional | 30 |
| 시나리오 1–7 | 140 |
| 부정 대조군(시나리오 8) | 20 |
| 업무 규정 | 20 |
| Custom S · Custom M | 각 20 |
| **합계** | **280** |

여기서 **카테고리 180문항**은 시나리오 1–8의 160문항과 업무 규정 20문항이다. Development, Additional, Custom S/M은 이 180문항에 다시 더하지 않고 별도 집계한다. 280개 중 258개에는 페이지 정답인 `evidence_groups`가 있고, 22개(AS-DEV12, AS-ADD12, AS-S08-01–20)에는 없다. 이 22개는 페이지 근거 충족률의 분모에서 제외하고 별도 건수를 표시한다. 보안 과제 두 개는 DEV14·ADD13의 질문을 재사용하므로 이 벤치마크에 섞지 않고 별도로 평가한다.

먼저 전체 답변을 저장하고, 저장이 끝난 뒤 동일 실행을 채점한다. `--resume`은 중단된 기존 ID를 같은 입력·설정으로 이어갈 때만 사용한다. 새 실행이나 채점에는 새 ID를 사용한다. 답변 재개는 이미 저장된 문항 결과를 재사용한다. 다만 `generation-inflight.json`에 요청 전송 후 저장되지 않은 문항이 있으면 자동 재호출을 거부하므로, 기록된 요청을 확인·복구해야 한다. 채점 재개는 문항 단위로 완료된 체크포인트를 재사용하지만, 현재 문항의 저장 전 중단으로 일부 Judge/Ragas 호출이 다시 실행될 수 있어 API 과금의 정확히 한 번 실행을 보장하지 않는다.

```bash
RUN_ID="advanced-final-$(date +%Y%m%d-%H%M%S)"
uv run --locked python -m app.evaluation.scripts.run_table_rag run \
    --questions data/advanced/evaluation/final_benchmark_questions.json \
    --experiment-id "$RUN_ID"

# 답변 실행이 중단된 경우에만, 같은 ID로 재개한다.
uv run --locked python -m app.evaluation.scripts.run_table_rag run \
    --questions data/advanced/evaluation/final_benchmark_questions.json \
    --experiment-id "$RUN_ID" --resume

RUN_DIR="artifacts/experiments/table-rag/$RUN_ID"
GRADE_ID="advanced-final-grade-01"
# 독립 검수가 끝나 sidecar가 accepted 상태가 된 뒤에만 채점을 실행한다.
uv run --locked --extra eval python -m app.evaluation.scripts.run_table_rag grade \
    --run-directory "$RUN_DIR" \
    --references data/advanced/evaluation/final_benchmark.json \
    --grade-id "$GRADE_ID" --judge-model gpt-6-luna --ragas-model gpt-6-luna \
    --derived-facts artifacts/experiments/advanced-final-benchmark-20261113-preparation/custom-required-facts.json \
    --faithfulness-profile advanced --diagnose-failures --final-benchmark-report

# 채점이 중단된 경우에만, 같은 ID와 같은 옵션으로 재개한다.
uv run --locked --extra eval python -m app.evaluation.scripts.run_table_rag grade \
    --run-directory "$RUN_DIR" \
    --references data/advanced/evaluation/final_benchmark.json \
    --grade-id "$GRADE_ID" --resume --judge-model gpt-6-luna --ragas-model gpt-6-luna \
    --derived-facts artifacts/experiments/advanced-final-benchmark-20261113-preparation/custom-required-facts.json \
    --faithfulness-profile advanced --diagnose-failures --final-benchmark-report
```

`--derived-facts`는 질문과 참고답만으로 별도 작성한 Custom S/M 필수 사실 파일을 읽는다. 파일은 원본 참고 파일 해시와 일치하고 독립 검토가 끝난 뒤에만 사용한다. 현재 준비 파일은 `draft` 상태이므로 채점에 넘기지 않는다. 원본 참고 파일은 수정하지 않으며 sidecar 해시를 manifest에 남긴다. `--final-benchmark-report`는 채점 폴더의 `final-benchmark/`에 전체·카테고리별 집계와 문항별 추적표를 만든다. `--diagnose-failures`는 저장된 검색 단계에서 근거가 처음 빠진 위치를 추적한다. 두 기능은 점수 결과를 미리 만들지 않는다. 실제 팀 평가를 시작하기 전에는 API 사용량·비용과 중단 기준을 확인한다.

Faithfulness는 4축 Judge와 별도로, 질문·저장 답변·실제 최종 문맥을 평가한다. **Judge의 `has_factual_claims`가 `false`여도 Faithfulness를 건너뛰지 않는다.** Ragas가 자체적으로 사실 주장을 추출한 결과 유효한 주장이 없을 때만 `not_applicable`로 기록한다. `default` 프로필은 Ragas 기본 프롬프트·출력 구조를 사용하고, `advanced`는 한국어 업무 기록 지침과 주장별 인용 근거 검증을 추가한다. CLI의 기본 프로필은 `advanced`이며 `--faithfulness-profile default`로 바꿀 수 있다. 둘 다 참고 정답을 Faithfulness 입력에 넣지 않는다.

평가 모델 호출은 `reasoning_effort=max`를 요청한다. API가 **처음부터** 해당 reasoning effort를 지원하지 않거나 허용하지 않는다고 명시적으로 거절하면 `xhigh`로 한 번 전환하고 해당 채점 세션에서 고정한다. 그 외 오류는 effort 변경 사유로 보지 않으며, 세션 도중 이미 고정된 effort가 거절되면 정책을 섞지 않고 평가를 중단한다. 실제 적용 effort와 전환 사유는 채점 manifest에 기록된다. 이 설정은 Judge·Ragas 평가 호출에 적용되며 답변 생성 설정과는 별도다.

주장별 원문 인용, 검증 프롬프트와 판정 세부는 실행 폴더의 `grades/<채점 ID>/evaluation-details.jsonl`에 로컬 상세 기록으로 저장된다. `scores.jsonl`과 보고서에는 이 상세 원문을 복사하지 않는다. 이 파일에는 평가 입력과 문서 내용이 들어갈 수 있으므로 로컬에서만 보관하고 Git에 커밋하거나 외부로 공유하지 않는다.

참고답 예시는 다음과 같다. Swagger의 question_id에는 응답의 request_id를 넣는다. 답변을 실행한 뒤 독립적으로 준비한 기대값과 원본 출처를 적는다.

```json
[{"id": "실행의_question_id", "reference_answer": "원본에서 확인한 기대 답변", "evidence_groups": [[{"book_id": "AS001", "page": 1}]]}]
```

Langfuse 점수는 원래 실행 trace에 연결된다. 재게시에는 첫 게시와 같은 점수 ID·이름·timestamp를 사용한다. --verify가 원격 점수를 조회해 확인한 결과를 저장하며, 전송 실패나 조회되지 않은 항목은 확인됨으로 집계하지 않는다.

## 평가 규칙

| 지표 | 집계 기준 |
| --- | --- |
| Hit@k · Recall@k | `evidence_groups`의 고유 문서와 실제 최종 문맥의 상위 k개 청크 문서를 비교한다. 근거 문서가 없으면 해당 없음, 빈 문맥이면 0, 실행 실패로 문맥을 모르면 평가 불가다 |
| 근거 누락 진단 | `evidence_groups`의 쪽 단위 근거가 검색 후보, 최종 문맥 중 어디서 빠졌는지 센다(`candidate_page_recall`, `final_page_recall`, `context_lost_page_recall`). `budget_lost_page_recall`은 토큰 예산으로 제외한 ID가 기록된 경우만 센다. Recall@k는 문서 단위라 쪽 단위 진단보다 높게 나올 수 있다 |
| Faithfulness | 질문, 생성 출력과 실제 최종 청크를 평가한다. 청크는 생성 모델 문맥처럼 제목(`heading`)을 본문 앞에 붙인다. Judge의 `has_factual_claims`와 독립적으로 Ragas가 주장을 추출하며, Ragas가 유효한 사실 주장을 찾지 못하면 해당 없음, 문맥 없이 사실을 주장하면 0, 평가기 오류는 평가 불가다 |
| Judge | 사실·조건·근거·판단을 각각 0/1과 이유로 기록한다. 최종 청크는 제목을 포함해 전달한다. 서비스 오류는 판단 0, 다른 항목은 평가 불가다. `has_factual_claims`와 네 항목의 status·score·reason이 빠지거나 맞지 않으면 네 항목을 모두 평가 불가로 두고 구조 오류를 사유에 남긴다(일부 항목만 골라 쓰지 않음). 기준 버전과 변경 이력은 [채점 기준 이력](judge-rubric.md)에 남긴다 |
| 검산 진단 | 공식 점수가 아니라 Judge의 대상별 진단 `calculation_checks`(complete/incomplete/not_applicable/unavailable, `missing_evidence`, 실제 문맥의 `supporting_source_ids`)다. 계산·검산을 요구하지 않는 질문은 not_applicable, 요청받은 검산을 못 했으면 incomplete, 진단 자체를 못 했으면 unavailable이다. 근거 ID가 문맥에 없거나 상태와 배열이 모순되면 진단만 평가 불가로 두고 네 점수는 유지한다. 보고서에는 상태별 건수, `comparison.csv`에는 대상별 내역이 남는다. Langfuse에는 게시하지 않는다 |
| 평균·운영 지표 | `scored`만 평균하며 0점은 포함한다. `not_applicable`·`unavailable`은 건수를 따로 센다. 오류율은 `error` 요청만 세며 서비스 시간·토큰과 평가기 사용량은 분리한다 |

## 재채점

채점 기준(Judge 프롬프트)이나 Judge 모델만 바꿀 때는 서비스를 다시 실행하지 않고 저장된 답변을 다시 채점한다. 원본 `artifacts/experiments/<원본 ID>/runs.jsonl`의 답변·최종 문맥을 그대로 쓰므로 생성 비용이 없고 같은 답변끼리 점수를 비교할 수 있다.

```bash
python -m app.evaluation.scripts.regrade --source <원본 실험 ID> --references <정답 파일> \
    --judge-model <모델> [--judge-prompt <프롬프트 파일>] [--ragas-model <모델>]
```

- 원본 폴더는 수정하지 않고 새 실험 ID(기본 `<원본 ID>-regrade-<시각>`)로 저장한다. manifest의 `regrade`에 원본 ID·원본 Judge와 프롬프트 해시를, `benchmark.judge_prompt`에 새 프롬프트 해시를 남긴다.
- 검색 지표는 원본 `k`로 다시 계산한다. Faithfulness는 `--ragas-model`이 없으면 원본 점수와 상태를 그대로 옮긴다. `--ragas-model`을 지정하면 Ragas가 저장된 질문·답변·최종 문맥을 독립적으로 다시 평가하며, Judge의 `has_factual_claims` 값은 평가 여부를 결정하지 않는다.
- 채점이 끝나면 게시 전에 manifest와 `scores.jsonl`을 저장하고, 게시 뒤에 게시 상태와 점수 ID를 다시 저장한다. 게시가 실패하거나 중단돼도 채점 결과가 남는다.
- 답변 규칙(생성 프롬프트)·검색 설정이 바뀌면 답변이 달라지므로 재채점이 아니라 `run_benchmark`로 새로 실행한다.
- 가상 샘플 기록은 모의 Judge로만 재채점한다: `python -m app.evaluation.scripts.regrade --source <sample-ID> --references app/evaluation/samples/sample_eval.json`
- 실제 재채점과 아래 `--verify-langfuse`는 서비스 bootstrap을 거치지 않으므로 저장소 루트 `.env`를 직접 읽는다. 이미 설정된 환경변수가 우선한다.

## Langfuse

통합 TableRAG는 rag-query 아래 계획 모델, 다섯 도구, 문맥 조립, 답변의 실제 입출력·시간·토큰을 남긴다. 자세한 실행은 [빠른 시작](quickstart.md)을 따른다. 아래 일반 retrieval/generation 단계 이름은 기존 검색 경로의 기록이다.

`LANGFUSE_PUBLIC_KEY`와 `LANGFUSE_SECRET_KEY`로 추적을 활성화한다. YAML의 `connections.langfuse_base_url`로 서버를 지정한다. 기존 `LANGFUSE_BASE_URL` 환경변수도 지원한다. 요청 ID에서 trace ID를 만들며, 추적은 `rag-query` 아래 `retrieval` → `final-context` → `generation-attempt-N` 순서로 이어지고 `validation-attempt-N`은 해당 생성 시도 아래에 둔다. 각 관측은 실제 실행 구간의 시작·끝 시각으로 기록한다. `retrieval`의 입력은 질문과 검색 방식이고 출력은 후보 배열(`candidates`)이다. 후보 본문은 배열을 펼쳐 각 항목의 `text`에서 본다. 주입한 근거를 쓴 요청은 `provided_context=true`이며 그 `retrieval` 구간은 과거 검색 시간이 아니다.

추적에는 질문, 검색·문맥 본문, 모델 입출력이 포함될 수 있다. 팀 공식 데이터의 첫 전송 전에 프로젝트·접근 범위·보존 기준을 확인한다. 저장은 실제 관측을 조회해 확인하고, 일반 평가는 `langfuse_verification`을 남긴다.

| 값 | 뜻 |
| --- | --- |
| `verified` | 실행 기록으로 기대한 관측(`rag-query`, 후보가 있으면 `retrieval`, 문맥이 있으면 `final-context`, 시도마다 `generation-attempt-N`)이 모두 조회됐다 |
| `partial` | 관측이 일부만 조회됐다. 누락 이름은 `--verify-langfuse` 출력의 `missing_observations`에 있다 |
| `not_found` · `failed` | 관측이 없다 · 조회 자체가 실패했다 |

```bash
python -m app.evaluation.scripts.run_benchmark --verify-langfuse <trace_id>
```

독립 `--verify-langfuse`는 실행 기록이 없으므로 `rag-query`만 기대한다. `verified`는 존재 확인이며 입출력·문맥·사용량의 내용 확인은 아니다.

### HTTP 측정

`http_benchmark`는 실행 중인 API의 `/query`를 호출해 클라이언트 측 응답 시간과 결과를 `artifacts/experiments/<ID>/http_runs.jsonl`에 남긴다. 서버는 [빠른 시작](quickstart.md)의 TableRAG 설정으로 실행한다.

현재 서버는 TableRAG만 제공하므로 --modes 옵션을 생략한다. 요청 본문에는 question만 들어가고, 선택 모드가 없는 TableRAG /query 경로가 실행된다.

```bash
python -m app.evaluation.scripts.http_benchmark --base-url http://127.0.0.1:8000 --questions <질문 파일> --ids <ID> --condition "TableRAG 기본 경로" --verify-langfuse
```

- 시간(`latency_scope=http_client`)은 요청 직전부터 응답 본문 수신 완료까지다. Judge·RAGAS 채점, trace 조회, 서버 기동은 넣지 않는다. 평가 CLI의 `latency_ms`(`service`, HTTP 없이 서비스 함수 실행)와 다른 값이다.
- `outcome`은 success, http_error(4xx·5xx, 응답 본문의 사유), timeout, connection_error, invalid_response다.
- 클라이언트 기본 --timeout은 150초다. TableRAG는 각 Luna 호출에 30초 제한과 자동 재시도 0회를 적용하며, 계획·도구 호출이 여러 차례 실행될 수 있다. 전체 검색·생성 시간은 한 모델 호출보다 길다. 측정 목적과 관측한 전체 지연에 맞춰 클라이언트 제한을 지정한다.
- 응답의 `request_id`(없으면 `X-Request-ID`)로 서버와 같은 방식의 trace ID를 계산한다. API 응답에는 근거·최종 문맥·모델 입력이 없으므로 `http_benchmark`는 RunRecord를 만들지 않는다. 채점이 필요하면 아래 `http_records`로 trace에서 복원한다.

### HTTP 답변 채점

HTTP로 받은 답변을 평가 CLI에서 다시 생성하지 않고 그대로 채점한다. http_records는 같은 request_id의 서버 trace에서 검색 후보·최종 문맥·planner와 answer 호출을 복원하고, TableRAG는 최종 문맥 JSON의 실제 SQL 결과도 보존한다.

```bash
python -m app.evaluation.scripts.http_records --source <HTTP 실험 ID> --questions <질문 파일> \
    --config <서버의 ADEN_CONFIG 파일>
python -m app.evaluation.scripts.regrade --source <HTTP 실험 ID>-records --references <정답 파일> \
    --judge-model <모델> --ragas-model <모델> --experiment-id <HTTP 실험 ID>-records-<기준>-e1
```

- 답변·상태·reason·인용·질문·검색 방식·request_id가 HTTP 응답과 trace 최종 출력에서 같을 때만 연결한다. 오류 응답은 API 오류 처리기가 문구를 정하므로 상태만 대조한다. 후보·최종 문맥·모델 시도·서버 처리 시간(`latency_ms`, `service`)은 trace에서, 클라이언트 시간은 `http_latency_ms`(`http_client`)로 옮긴다. 문맥·모델 문맥 해시는 RunRecord 정규화에서 다시 검사한다.
- request_id가 없는 요청(timeout·연결 오류)과 trace를 읽지 못했거나 응답과 다른 요청은 `runs.jsonl`에 넣지 않고 manifest의 `http_records.unlinked`에 사유를 남긴다. 비교표에는 누락으로 센다.
- `--config`는 서버가 쓴 설정 파일이다. 채점 `k`(trace의 `k`와 같아야 함)와 manifest의 설정 파일 해시에 쓴다. 실제 요청 trace의 서버 설정 해시도 manifest와 점수 게시 조건에 별도로 남긴다. 한 HTTP 실험에 서로 다른 서버 실행 설정 해시가 섞이면 거부한다. 서버 코드 버전은 HTTP 실험 manifest의 커밋(`http_records.source_git_commit`)이므로 서버와 `http_benchmark`를 같은 checkout에서 실행한다.
- 원본 HTTP 폴더는 수정하지 않는다. 채점 결과는 `regrade`의 새 실험 ID에 저장되며 점수 행의 `request_id`와 게시 점수의 trace가 HTTP 요청과 같다. 같은 답변을 다시 채점하면 새 실험 ID(`-e2`, `-e3` …)를 쓴다. 이 ID가 채점 회차 구분자이며 Langfuse 점수 metadata의 `experiment_id`에도 남는다.

### 평가 점수 게시

`run_benchmark`와 `regrade`에 `--publish-langfuse-scores`를 주면 채점값을 해당 trace의 점수로 보낸다. 점수 이름은 `<평가 세트>.<지표>`다. 지표는 `retrieval.hit@k`, `retrieval.recall@k`, `faithfulness`, `judge.<항목>`, `evidence.<진단>`이고 첫 마디(`retrieval`, `faithfulness`, `judge`, `evidence`)가 지표군이다.

- 평가 세트는 한 번의 채점 결과를 묶는 이름이다. `--evaluation-set`으로 정하며 기본값은 실험 ID다(예: `dev-v1-r1.judge.fact`, `dev-v2-r1.judge.fact`). 같은 trace를 다른 기준이나 반복 회차로 채점해도 이름이 달라 Langfuse의 이름별 집계가 섞이지 않는다.
- 한 세트에는 같은 채점 조건(데이터셋, 설정 해시, `k`, 평가 모델, Judge 프롬프트 해시, Judge 출력 스키마 `judge_schema`, 정답 파일 해시)만 게시한다. 스키마 기록이 없는 2026-11-11 이전 결과(`judge-output-v1`)와 이후 결과는 같은 세트에 넣을 수 없다. 같은 세트 이름을 쓴 다른 실험과 조건이 다르거나 같은 요청을 다시 채점했으면 서비스·평가 호출 전에 거부한다. 그래서 기준 버전(v1·v2)과 반복 회차는 서로 다른 세트가 된다. 이 대조는 `--artifacts-dir`에 저장된 실험만 본다. 다른 PC에서 게시한 점수와 겹치는지는 서버 점수의 metadata(`evaluation_hash`)를 조회해 확인한다.
- 게시할 지표군은 `--publish-groups`로 고른다. `run_benchmark`의 기본값은 전체다. `regrade`는 새로 계산한 지표군만 기본으로 게시한다: Judge, 그리고 `--ragas-model`로 다시 계산했으면 Faithfulness. 원본에서 옮겨 온 Faithfulness와 그대로인 검색 지표·근거 진단은 정답 파일이 바뀐 경우처럼 필요할 때 `--publish-groups retrieval evidence`나 `all`로 고른다.
- 최종 비교에는 평가 세트 하나만 쓴다. 기준을 확정한 뒤 `regrade --ragas-model <모델> --publish-groups all --evaluation-set <최종 세트>`처럼 모든 지표군을 한 세트로 게시하고 그 세트 이름을 결과에 적는다. 기준 비교나 반복 회차의 세트는 기록으로 남기되 최종 수치에 섞지 않는다.
- `scored`인 값만 보낸다. 해당 없음·평가 불가는 0으로 보내지 않는다. 서비스 오류의 판단 0점은 평가 규칙대로 보낸다.
- 점수 ID는 trace ID와 점수 이름으로 만든다. 같은 결과를 다시 게시하면 같은 ID라 서버 건수가 늘지 않는다. 채점 기록의 `langfuse_score_ids`에 보낸 이름과 ID를, 점수 metadata에 평가 세트, 실험 ID, 원본 실험 ID, 평가 모델, 프롬프트·정답·설정 해시를 남긴다.
- manifest의 `langfuse_scores.status`는 `recorded_unverified`(전송 요청까지), `partial`(일부 전송 실패), `failed`, `no_scores`, `disabled`, `incomplete`(게시가 끝나기 전에 중단)다. 서버 수신은 점수 조회로 따로 확인한다. 채점 결과를 게시 전에 저장하므로 게시가 실패하거나 중단돼도 로컬 답변·점수는 남는다.
- 세트 이름은 2026-11-10의 이 변경 이후 게시에 적용된다. 그 전에 게시한 점수(이름에 세트가 없는 `judge.fact` 등)는 지우지 않았다. v1·v2·반복 채점이 같은 이름에 섞여 있으므로 비교에 쓰지 않는다.

## 공식 비교 재현 증거

- manifest의 커밋·미커밋 상태, 실험 ID, 질문·검색 방식, 설정 해시, 정답 파일 해시(`references_sha256`)와 평가 모델을 확인한다.
- 설정 해시는 두 가지다. manifest의 `config_hash`는 `--config` 설정 파일 자체의 SHA-256이라 파일이 같으면 PC와 관계없이 같다. 실행 기록·trace의 `config_hash`(`experiment_config_hash`)는 읽어 들인 설정 JSON과 생성 프롬프트 해시로 계산하는데, `documents_dir`가 절대 경로로 들어가 저장소 위치가 다르면 같은 설정이라도 값이 다르다. 기존 기록과 대조할 수 있도록 계산식은 바꾸지 않는다.
- 같은 PC의 API와 평가 CLI는 실행 기록·trace의 `config_hash`로 대조할 수 있다. PC 사이에는 실행 커밋, `prompt_hash`, `final_context_hash`·`model_context_hash`와 함께 생성 모델, `reasoning_effort`, `max_output_tokens`, `model_timeout_seconds`, `generation_max_attempts`, `retrieval.k`, `context_token_budget`을 직접 비교한다. 해시 몇 개가 같다고 실행 조건 전체가 같은 것은 아니다.
- 실행 명령, 질문·정답 파일과 색인 manifest의 SHA-256, 컬렉션, 유효 설정·프롬프트·의존성 버전, [시간 측정 범위](../design/contracts.md#기록-형식과-측정-범위)를 결과에 보충한다. 미커밋 코드로 실행했다면 해당 diff도 남긴다. 확인되지 않은 토큰은 `null`이다.
- 전체 실행 기록은 `artifacts/experiments/<id>/`에, 선별한 공식 비교·제출 자료는 `reports/<id>/`에 둔다. 조건이 바뀌면 새 실험 ID로 저장한다. 제출 시 실제 실행 커밋을 태그하고 결과를 프로젝트 소개와 팀 작업에 연결한다.

### 최종 벤치의 연결 표본 먼저 저장

run 명령에 --stop-after 3을 붙이면 전체 질문 목록은 고정한 채 처음 세 답변만 저장하고 정상 종료한다. 상태는 partial이며 채점할 수 없다. 같은 experiment-id로 --resume을 실행하면 저장한 세 답변을 재사용하고 나머지를 이어간다. 이 연결 표본을 따로 재생성하지 않는다. 답변 생성 도중 입력 지문이 바뀌면 재개를 거절한다.
