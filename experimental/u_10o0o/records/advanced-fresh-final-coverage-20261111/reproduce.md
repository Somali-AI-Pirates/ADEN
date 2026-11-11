# 재현 명령

저장소 루트의 기존 .venv를 사용한다. 이 실험은 기존 E/F 저장 결과의 고정 SHA를 검사한다. 다른 후보/순위를 입력으로 바꾸는 CLI는 제공하지 않는다. 다른 입력은 이 고정 비교의 재현 범위가 아니다.

## 네 조건 결과 저장

```bash
.venv/bin/python -m experimental.u_10o0o.fresh_record_hybrid.final_coverage \
  --results artifacts/u_10o0o/advanced-fresh-final-coverage-20261111/NEW_RUN/results.jsonl
```

E/E_coverage/F/F_coverage의 모든160문항, 총640행을 저장한다. 이미 결과·partial·manifest가 있는 경로는 거절한다. 후보20와 KURE 순위/점수를 재사용하며 기존 최종 문맥의 정확한 복원도 확인한다. 새 임베딩·검색·리랭킹·LLM 호출은 없다. 고정 모델 정보는 E/F 원래 실행 명세와 새 실행의 source_reranker_models에 남긴다.

## 결과 완료 뒤 별도 채점

```bash
.venv/bin/python -m experimental.u_10o0o.fresh_record_hybrid.scoring \
  --conditions E E_coverage F F_coverage \
  --results artifacts/u_10o0o/advanced-fresh-final-coverage-20261111/NEW_RUN/results.jsonl \
  --questions artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/evaluation/questions.jsonl \
  --references artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/evaluation/references-expanded-evidence.jsonl \
  --pages artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/pages.jsonl \
  --units artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/units.jsonl \
  --output artifacts/u_10o0o/advanced-fresh-final-coverage-20261111/NEW_SCORES
```

원래 동결 근거 점수는 references.jsonl과 다른 출력 경로를 사용한다. 의미 검수의 대체 근거를 인정한 민감도 점수는 기본 점수와 분리하며, 모든 조건에 같은 추가 근거를 적용한다. 기존 참고 파일과 결과는 변경하지 않는다.

## 검사

```bash
.venv/bin/ruff check .
.venv/bin/ruff check --isolated --select E4,E7,E9,F,I experimental/u_10o0o/fresh_record_hybrid
.venv/bin/ruff format --check --isolated experimental/u_10o0o/fresh_record_hybrid
.venv/bin/python -m pytest -q tests experimental/u_10o0o/fresh_record_hybrid/tests
.venv/bin/python -m build --no-isolation
```

선택 발동/보류 사유와 연결 출처는 각 새 조건의 final_selection에 있다. 조회·리랭킹 메타데이터와 지연은 과거 결과에서 재사용한 값이며 새 호출이 아니다. 새 조건의 timings_ms는 계획 및 선택·복원만 기록한다. 골드와 유형 라벨은 검색·선택기의 입력이 아니다.
