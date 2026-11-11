# 재현 명령

저장소 루트와 기존 `.venv`에서 실행한다. 원본과 질문/정답 인용은 로컬 `artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/`에 보관한다. 공유 기록에는 원문을 복사하지 않는다. 검색 결과가 있는 경로는 덮어쓰지 않으므로 재실행 때 새 결과 경로를 사용한다.

## 사용한 입력과 모델

- 원본: `data/advanced/documents/`의 120 PDF, 새 Poppler layout 추출 18,000쪽. PDF별 책 ID·해시·쪽 수는 로컬 `raw-manifest.json`이 기준이다.
- 새 문항: `evaluation/questions.jsonl`, 참고 근거: `evaluation/references.jsonl`.
- 문항 작성 및 원문 근거 검수는 별도 에이전트가 담당했다. 과거 질문/점수/정제본/벡터는 입력이 아니다.
- 로컬 모델 스냅샷과 고정 조건은 [plan.md](plan.md), 코드 지문은 [code-freeze.json](code-freeze.json)에 있다.

## 원문과 단위 생성

```bash
.venv/bin/python -m experimental.u_10o0o.fresh_record_hybrid.extract \
  --output artifacts/u_10o0o/NEW_RUN
.venv/bin/python -m experimental.u_10o0o.fresh_record_hybrid.units \
  --pages artifacts/u_10o0o/NEW_RUN/pages.jsonl \
  --output-dir artifacts/u_10o0o/NEW_RUN
```

새 질문을 다시 작성하면 동일 평가셋 재현이 아니라 새 평가가 된다. 아래는 이번에 고정한 로컬 질문과 색인을 재사용하는 검색 재현 명령이다. 참고 근거 파일은 검색기에 전달하지 않는다.

## 색인과 검색

```bash
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export HF_HUB_DISABLE_PROGRESS_BARS=1 TOKENIZERS_PARALLELISM=false
export OPENBLAS_NUM_THREADS=4 OMP_NUM_THREADS=4
.venv/bin/python -m experimental.u_10o0o.fresh_record_hybrid.retrieval \
  build-vectors --device cuda --threads 4 --embedding-batch-size 4
.venv/bin/python -m experimental.u_10o0o.fresh_record_hybrid.retrieval \
  retrieve --device cuda --threads 4 --rerank-batch-size 8 \
  --questions artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/evaluation/questions.jsonl \
  --results artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/run-v1/results.jsonl
```

기본 pages/units/relations/vectors 경로는 이번 새 실험 폴더다. 다른 폴더에서는 `--pages`, `--units`, `--relations`, `--vectors-dir`를 명시한다. 벡터 체크포인트는 입력·모델 지문과 저장 배열 해시를 확인한 뒤 재개한다. B의 동일 텍스트는 이번 실행에서 만든 A 벡터를 재사용하며, 과거 색인을 가져오지 않는다.

## 저장 이후 채점과 보고서

```bash
.venv/bin/python -m experimental.u_10o0o.fresh_record_hybrid.scoring \
  --results artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/run-v1/results.jsonl \
  --questions artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/evaluation/questions.jsonl \
  --references artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/evaluation/references.jsonl \
  --pages artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/pages.jsonl \
  --units artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/units.jsonl \
  --output artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/scores-v1
.venv/bin/python -m experimental.u_10o0o.fresh_record_hybrid.reporting \
  --score-dir artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/scores-v1 \
  --output experimental/u_10o0o/records/advanced-fresh-record-hybrid-20261111
```

채점기는 모든 문항의 A/B/C 결과, 질문 해시, 후보와 복원 원문 연결, 실제 토큰 수를 확인한다. 그 뒤 참고 근거를 읽는다.

## 검사

```bash
.venv/bin/ruff check .
.venv/bin/ruff check --isolated --select E4,E7,E9,F,I experimental/u_10o0o/fresh_record_hybrid
.venv/bin/ruff format --check --isolated experimental/u_10o0o/fresh_record_hybrid
.venv/bin/python -m pytest -q tests experimental/u_10o0o/fresh_record_hybrid/tests
.venv/bin/python -m build --no-isolation
```

공통 CI가 개인 실험에 의존하지 않는 저장소 규칙은 유지했다. 이번 작업은 기본 검색 구성의 교체, 커밋, 푸시를 포함하지 않는다.

## 이번 실제 실행 묶음

실제 GPU 계산은 문항 검수와 병행하기 위해 `dev-frozen-questions.jsonl`80개, `validation-part1-frozen-questions.jsonl`56개, `validation-part2-frozen-questions.jsonl`24개를 각각 실행했다. 출력은 `run-dev-v1`, `run-validation-part1-v1`, `run-validation-part2-v1`이다. `run-v1`은 같은 설정과 중복 없는480행을 검증해 합친 출력이다. 각 묶음은 모델/색인을 준비하고3회 워밍업한 뒤 조건순서를 순환했다. 원문/질문/모델 지문은 [run-manifest.json](run-manifest.json)에 있다. 위 단일160문항 명령은 같은 고정 질문과 설정으로 재현하는 방법이며 초기화 횟수는 이번3회 실행과 다르다.
