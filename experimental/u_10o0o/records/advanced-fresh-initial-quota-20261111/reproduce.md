# F 재현

저장소 루트의 기존 .venv와 로컬 고정 모델을 사용한다. 초기 Top50은 동결된 D 파일에서 읽으며, 저장된 E의 Top50과 동일하다. 입력 경로·SHA 검사와 모델·검색예산 검증은 기존 replay가 담당한다.

## F 실행

```bash
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_PROGRESS_BARS=1
export TOKENIZERS_PARALLELISM=false OPENBLAS_NUM_THREADS=4 OMP_NUM_THREADS=4
.venv/bin/python -m experimental.u_10o0o.fresh_record_hybrid.relation_overlap \
  --condition F --device cuda --threads 4 \
  --results artifacts/u_10o0o/advanced-fresh-initial-quota-20261111/NEW_RUN/results.jsonl
```

기본 condition은 E다. F는 Top10 합집합을 먼저 선정하고 같은 Top50의 전체 RRF 순서로 보충·정렬한다. 이후 E 관계 정책을 현재 F 후보에 적용한다. .jsonl 결과와 run-manifest.json이 없는 새 폴더를 사용한다. 기존 결과·partial·manifest가 있으면 덮어쓰지 않는다.

D 기본 SHA는 검증된 동결값이며, 원문/질문/관계 지문, 정확한 Top50/20과 기존 RRF 연결도 검사한다. 다른 입력을 의도적으로 쓸 때는 --d-results와 신뢰할 수 있는 기존 기록의 --d-results-sha256 및 관련 원문 경로를 함께 명시해야 한다. 해당 변경은 이번 고정 실험의 재현이 아니라 별도 실험이다.

## 전체 결과 저장 후 E/F 채점

E 원본 결과를 수정하지 않고 새 F와 합친다. 모든 경로는 새 출력 경로로 맞춘다.

```bash
mkdir -p artifacts/u_10o0o/advanced-fresh-initial-quota-20261111/NEW_COMBINED
cat artifacts/u_10o0o/advanced-fresh-relation-overlap-20261111/run-E-v1/results.jsonl \
    artifacts/u_10o0o/advanced-fresh-initial-quota-20261111/NEW_RUN/results.jsonl \
  > artifacts/u_10o0o/advanced-fresh-initial-quota-20261111/NEW_COMBINED/results.jsonl
.venv/bin/python -m experimental.u_10o0o.fresh_record_hybrid.scoring \
  --conditions E F \
  --results artifacts/u_10o0o/advanced-fresh-initial-quota-20261111/NEW_COMBINED/results.jsonl \
  --questions artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/evaluation/questions.jsonl \
  --references artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/evaluation/references-expanded-evidence.jsonl \
  --pages artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/pages.jsonl \
  --units artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/units.jsonl \
  --output artifacts/u_10o0o/advanced-fresh-initial-quota-20261111/NEW_SCORES
```

원래 동결 근거를 쓰려면 references.jsonl을 지정하고 채점 출력도 별도 폴더로 바꾼다. 초기20의 사실 채점과 단계별 진단은 같은 검색결과의 hybrid_unit_ids를 원문으로 복원해 별도 분석하며, 검색 정책에 참고 근거를 넣지 않는다.

## 검사

```bash
.venv/bin/ruff check .
.venv/bin/ruff check --isolated --select E4,E7,E9,F,I experimental/u_10o0o/fresh_record_hybrid
.venv/bin/ruff format --check --isolated experimental/u_10o0o/fresh_record_hybrid
.venv/bin/python -m pytest -q tests experimental/u_10o0o/fresh_record_hybrid/tests
.venv/bin/python -m build --no-isolation
```

과거 ABC/D/E 입력과 결과는 보존한다. 새 문서·질문 임베딩이나 유료 API 호출은 없다. 이 실험은 반복 관찰한 평가셋에 대한 회귀 비교이며 운영 기준선을 자동 교체하지 않는다.
