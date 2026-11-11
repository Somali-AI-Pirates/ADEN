# E 재현

저장소 루트의 기존 .venv와 로컬 KURE 스냅샷을 사용한다. 기본 입력은 fresh ABC의 원문·질문·관계이며, D 결과는 advanced-fresh-page-relation-20261111/run-D-v1/results.jsonl이다. 기본 D SHA-256은 이번 실험 전에 검증한 동결값이다. 변경된 결과 파일은 거부한다.

## E 실행

완료/부분 결과 또는 run-manifest.json이 이미 있는 디렉터리는 거부한다. 재실행에는 새 디렉터리와 .jsonl 확장자를 사용한다.

```bash
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_PROGRESS_BARS=1
export TOKENIZERS_PARALLELISM=false OPENBLAS_NUM_THREADS=4 OMP_NUM_THREADS=4
.venv/bin/python -m experimental.u_10o0o.fresh_record_hybrid.relation_overlap \
  --device cuda --threads 4 \
  --results artifacts/u_10o0o/advanced-fresh-relation-overlap-20261111/NEW_RUN/results.jsonl
```

문서/질문 임베딩이나 BM25·벡터 검색은 실행하지 않는다. D 결과의 고정 해시, 입력 지문, BM25·dense 설정/Top50, 실제 hybrid20의 RRF 연결, 원문 관계를 확인한다. D가 선정한 새 페이지와 같은 목록으로 E를 만들고 단일 KURE·원문 복원을 실행한다.

다른 동결 D 실행을 의도적으로 재생할 때는 --d-results와 --d-results-sha256에 사전 검증한 파일 및 지문을 함께 명시한다. 변경된 파일에서 기대 해시를 즉석 생성해 넘기면 보존 검사가 무의미하므로, 별도로 보존한 신뢰할 수 있는 D 기록에서 확인해야 한다. 입력 경로도 --pages-path, --units-path, --relations-path, --questions-path로 맞추고 같은 모델 설정을 사용한다.

## 저장 후 채점

두 조건의 160행을 모두 저장한 다음 새 파일에 합친다. 새 경로를 사용한다. 질문/참고 근거 파일은 수정하지 않는다.

```bash
mkdir -p artifacts/u_10o0o/advanced-fresh-relation-overlap-20261111/NEW_COMBINED
cat artifacts/u_10o0o/advanced-fresh-page-relation-20261111/run-D-v1/results.jsonl \
    artifacts/u_10o0o/advanced-fresh-relation-overlap-20261111/NEW_RUN/results.jsonl \
  > artifacts/u_10o0o/advanced-fresh-relation-overlap-20261111/NEW_COMBINED/results.jsonl
.venv/bin/python -m experimental.u_10o0o.fresh_record_hybrid.scoring \
  --conditions D E \
  --results artifacts/u_10o0o/advanced-fresh-relation-overlap-20261111/NEW_COMBINED/results.jsonl \
  --questions artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/evaluation/questions.jsonl \
  --references artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/evaluation/references-expanded-evidence.jsonl \
  --pages artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/pages.jsonl \
  --units artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/units.jsonl \
  --output artifacts/u_10o0o/advanced-fresh-relation-overlap-20261111/NEW_SCORES
```

원래 동결 근거는 references.jsonl을 지정하고 다른 채점 폴더를 쓴다. 실제 이번 실행 출력은 run-E-v1, combined-v1, scores-expanded-v1, scores-original-v1이다. 참고 근거는 두 조건 전체 행 수·질문 해시·출처 연결을 확인한 다음 읽는다.

## 검증

```bash
.venv/bin/ruff check .
.venv/bin/ruff check --isolated --select E4,E7,E9,F,I experimental/u_10o0o/fresh_record_hybrid
.venv/bin/ruff format --check --isolated experimental/u_10o0o/fresh_record_hybrid
.venv/bin/python -m pytest -q tests experimental/u_10o0o/fresh_record_hybrid/tests
.venv/bin/python -m build --no-isolation
```

실행 뒤 리뷰에서는 저장 파일의 고정 SHA, 정확한 Top50/20 및 RRF 연결, 출력 파일 이름과 manifest 보존 검사를 보강했다. 실제 실행 코드는 로컬 run-E-v1/code에 보존했다. 검색 정책·질문·참고 근거는 변경하지 않았으며, 보강한 입력 검증으로 160문항의 E 후보·관계 기록이 모두 다시 같음을 확인했다. 리랭킹·원문 복원 함수도 실행 당시와 같았다. GPU 재실행으로 점수를 조정하지 않았다.
