# 재현

저장소 루트, 기존 .venv와 로컬 모델 스냅샷을 사용한다. 새 D는 fresh ABC 입력을 읽고 별도 경로에 저장한다. 아래 경로에 기존 결과가 있으면 NEW_D_RUN 등으로 결과와 채점 경로를 함께 바꾼다.

```bash
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_PROGRESS_BARS=1
export TOKENIZERS_PARALLELISM=false OPENBLAS_NUM_THREADS=4 OMP_NUM_THREADS=4
.venv/bin/python -m experimental.u_10o0o.fresh_record_hybrid.retrieval retrieve   --conditions D --device cuda --threads 4 --rerank-batch-size 8   --results artifacts/u_10o0o/advanced-fresh-page-relation-20261111/run-D-v1/results.jsonl
```

기본 입력 경로는 `artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/`의 pages.jsonl, units.jsonl, relation_index.json, evaluation/questions.jsonl, vectors-v1/다. 새 벡터를 만들지 않는다. 모델 리비전과 검색 설정, 입력 해시는 각 run-manifest.json에 저장된다.

입력 지문, 모델 리비전, 검색 예산이 같은지 확인하고 기존 ABC와 새 D를 새 파일에 합친다. 기존 결과는 수정하지 않는다. 검색 결과에는 질문과 참고 정답을 쓰지 않는다.

```bash
mkdir -p artifacts/u_10o0o/advanced-fresh-page-relation-20261111/combined-v1
cat artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/run-v1/results.jsonl     artifacts/u_10o0o/advanced-fresh-page-relation-20261111/run-D-v1/results.jsonl   > artifacts/u_10o0o/advanced-fresh-page-relation-20261111/combined-v1/results.jsonl
.venv/bin/python -m experimental.u_10o0o.fresh_record_hybrid.scoring   --conditions A B C D   --results artifacts/u_10o0o/advanced-fresh-page-relation-20261111/combined-v1/results.jsonl   --questions artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/evaluation/questions.jsonl   --references artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/evaluation/references-expanded-evidence.jsonl   --pages artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/pages.jsonl   --units artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/units.jsonl   --output artifacts/u_10o0o/advanced-fresh-page-relation-20261111/scores-expanded-v1
```

원래 채점은 references-expanded-evidence.jsonl 대신 references.jsonl을 지정하고 출력도 scores-original-v1으로 분리한다. 두 채점의 검색 출력은 동일하다. 모든 640행의 문항·조건·질문 해시·후보 출처·최종 원문과 예산을 검증한다.

```bash
.venv/bin/ruff check .
.venv/bin/ruff check --isolated --select E4,E7,E9,F,I experimental/u_10o0o/fresh_record_hybrid
.venv/bin/ruff format --check --isolated experimental/u_10o0o/fresh_record_hybrid
.venv/bin/python -m pytest -q tests experimental/u_10o0o/fresh_record_hybrid/tests
.venv/bin/python -m build --no-isolation
```

보고서의 요약과 문항별 표는 이 별도 채점 산출물에서 생성한다. 과거 ABC 점수를 보고 기준을 조정하는 재학습·재임베딩은 하지 않았다.
