# 평가와 실행 기록

현재 가상 샘플과 기존 색인의 [검색 전용 측정](data.md#검색만-확인)이 확인돼 있다. 실제 색인·생성 모델·Langfuse를 연결한 전체 서비스와 실제 Judge·RAGAS 평가, 일반 답변·보안 비교는 검증이 필요하다.

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
- 일반 평가 `run_benchmark`에 `--questions`와 해당 split의 `--references`, `--adapter 모듈:이름`, `--config` 또는 `--config-hash` 중 하나를 전달한다. 둘을 함께 주면 기록된 해시와 실제 실행 설정이 어긋날 수 있어 실행 전에 거부하며, 설정 파일이 있으면 `--config`를 쓴다. `--ids`로 먼저 한 문항을 고른다. `--references`의 기본값은 질문 파일이므로 제공 자료처럼 파일이 분리되면 둘 다 지정한다.
- 일반 평가의 `--k`는 채점용 상위 청크 수이며 실제 검색 k와 같아야 한다. `--config`가 있으면 그 `retrieval.k`를 쓰고, `--k`를 함께 주면 같은 값일 때만 실행한다. `--config` 없이 `--config-hash`로 실행하면 `--k`를 반드시 지정한다(코드 기본값으로 추정하지 않는다). `--k`만 바꿔도 검색 개수는 바뀌지 않으므로 서비스/어댑터 설정과 맞춰 지정한다. 가상 샘플(`--sample`)만 기본 8을 쓴다. `--dataset-id`를 생략하면 설정 파일의 `dataset`을 기록한다(설정 파일이 없으면 basic).
- 보안 평가 `run_security`는 `--security-file`, 초기화된 `--retrieve`·`--generate`, `--retrieval-mode`, 설정이나 설정 해시를 받는다. 문항별로 같은 최종 문맥을 기본·방어 프롬프트에 재사용한다. 공격 미노출과 생성 실패를 방어 성공으로 세지 않는다.
- `--config`는 표준 `app.service.bootstrap`의 실제 실행 설정으로 전달한다. CLI는 모듈의 선택적 `configure_evaluation(config_path)` 훅을 자원 준비 전에 호출한다. 사용자 정의 어댑터는 이 훅을 제공하거나 자체 설정 로더를 같은 파일에 맞춰야 한다. 이미 준비된 표준 자원은 다른 설정 경로나 같은 파일의 내용 변경을 감지하면 거부하므로 다른 설정의 실험은 새 실행으로 시작한다.
- 초기화된 함수는 `app.service.bootstrap`이 제공한다. 일반 평가는 `--adapter app.service.bootstrap:query_adapter`, 보안 평가는 `--retrieve app.service.bootstrap:retrieve_context --generate app.service.bootstrap:generate_with_context`를 지정하고 `--config configs/basic.yaml`을 함께 준다. 자원은 첫 사용 때 준비되며 계약은 [어댑터 계약](../design/contracts.md#평가-어댑터)을 따른다. Judge·Faithfulness는 `--judge-model`·`--ragas-model`을 지정하지 않으면 `unavailable`이다. RAGAS는 공통 설치에 포함되지 않는다.

## 평가 규칙

| 지표 | 집계 기준 |
| --- | --- |
| Hit@k · Recall@k | `evidence_groups`의 고유 문서와 실제 최종 문맥의 상위 k개 청크 문서를 비교한다. 근거 문서가 없으면 해당 없음, 빈 문맥이면 0, 실행 실패로 문맥을 모르면 평가 불가다 |
| Faithfulness | 질문, 생성 출력과 실제 최종 청크를 평가한다. 사실 주장이 없으면 해당 없음, 문맥 없이 사실을 주장하면 0, 평가기 오류는 평가 불가다 |
| Judge | 사실·조건·근거·판단을 각각 0/1과 이유로 기록한다. 서비스 오류는 판단 0, 다른 항목은 평가 불가다. 기준 버전과 변경 이력은 [채점 기준 이력](judge-rubric.md)에 남긴다 |
| 평균·운영 지표 | `scored`만 평균하며 0점은 포함한다. `not_applicable`·`unavailable`은 건수를 따로 센다. 오류율은 `error` 요청만 세며 서비스 시간·토큰과 평가기 사용량은 분리한다 |

## 재채점

채점 기준(Judge 프롬프트)이나 Judge 모델만 바꿀 때는 서비스를 다시 실행하지 않고 저장된 답변을 다시 채점한다. 원본 `artifacts/experiments/<원본 ID>/runs.jsonl`의 답변·최종 문맥을 그대로 쓰므로 생성 비용이 없고 같은 답변끼리 점수를 비교할 수 있다.

```bash
python -m app.evaluation.scripts.regrade --source <원본 실험 ID> --references <정답 파일> \
    --judge-model <모델> [--judge-prompt <프롬프트 파일>] [--ragas-model <모델>]
```

- 원본 폴더는 수정하지 않고 새 실험 ID(기본 `<원본 ID>-regrade-<시각>`)로 저장한다. manifest의 `regrade`에 원본 ID·원본 Judge와 프롬프트 해시를, `benchmark.judge_prompt`에 새 프롬프트 해시를 남긴다.
- 검색 지표는 원본 `k`로 다시 계산한다. Faithfulness는 `--ragas-model`이 없으면 원본 점수를 옮긴다. 원본 점수의 적용 여부는 원본 Judge의 `has_factual_claims`로 정해졌으므로, 새 Judge 판단과 맞춰야 하면 `--ragas-model`로 다시 계산한다.
- 답변 규칙(생성 프롬프트)·검색 설정이 바뀌면 답변이 달라지므로 재채점이 아니라 `run_benchmark`로 새로 실행한다.
- 가상 샘플 기록은 모의 Judge로만 재채점한다: `python -m app.evaluation.scripts.regrade --source <sample-ID> --references app/evaluation/samples/sample_eval.json`

## Langfuse

`LANGFUSE_PUBLIC_KEY`와 `LANGFUSE_SECRET_KEY`로 추적을 활성화한다. `LANGFUSE_BASE_URL`로 서버를 지정할 수 있다. 요청 ID에서 trace ID를 만들며, 추적은 `rag-query` → `retrieval` → `final-context` → `generation-attempt-N` 순서로 이어진다.

추적에는 질문, 검색·문맥 본문, 모델 입출력이 포함될 수 있다. 팀 공식 데이터의 첫 전송 전에 프로젝트·접근 범위·보존 기준을 확인한다. 저장은 실제 관측을 조회해 확인하고, 일반 평가는 `langfuse_verification`(`verified`, `not_found`, `failed`)을 남긴다.

```bash
python -m app.evaluation.scripts.run_benchmark --verify-langfuse <trace_id>
```

## 공식 비교 재현 증거

- manifest의 커밋·미커밋 상태, 실험 ID, 질문·검색 방식, 설정 해시와 평가 모델을 확인한다.
- 실행 명령, 질문·정답 파일과 색인 manifest의 SHA-256, 컬렉션, 유효 설정·프롬프트·의존성 버전, [시간 측정 범위](../design/contracts.md#기록-형식과-측정-범위)를 결과에 보충한다. 미커밋 코드로 실행했다면 해당 diff도 남긴다. 확인되지 않은 토큰은 `null`이다.
- 전체 실행 기록은 `artifacts/experiments/<id>/`에, 선별한 공식 비교·제출 자료는 `reports/<id>/`에 둔다. 조건이 바뀌면 새 실험 ID로 저장한다. 제출 시 실제 실행 커밋을 태그하고 결과를 프로젝트 소개와 팀 작업에 연결한다.
