# 재현 명령과 산출물 경계

저장소 루트의 WSL 환경에서 실행했다. 기존 결과를 덮어쓰지 않도록 재실행은 새로운 로컬 출력 경로를 사용한다. 아래 경로는 이번에 완료한 실행을 식별한다. 로컬 모델·원본·기존 계획과 색인이 필요하며, OpenAI API 키는 필요하지 않다.

## 이번 실행

```bash
.venv/bin/python -m experimental.u_10o0o.advanced_semantic_rank_replay \
  --output-dir artifacts/u_10o0o/advanced-semantic-first-miss-20261111/ranks-v4

.venv/bin/python -m experimental.u_10o0o.advanced_semantic_first_miss \
  --output-dir artifacts/u_10o0o/advanced-semantic-first-miss-20261111/stages-v6 \
  --rank-dir artifacts/u_10o0o/advanced-semantic-first-miss-20261111/ranks-v4

.venv/bin/python -m experimental.u_10o0o.advanced_is_windows \
  --output-dir artifacts/u_10o0o/advanced-semantic-first-miss-20261111/is-windows-v1

.venv/bin/python -m experimental.u_10o0o.advanced_semantic_window_ab \
  --index-dir artifacts/u_10o0o/advanced-semantic-first-miss-20261111/is-windows-v1 \
  --output-dir artifacts/u_10o0o/advanced-semantic-first-miss-20261111/ab-v1

.venv/bin/python -m experimental.u_10o0o.advanced_semantic_report \
  --stages-dir artifacts/u_10o0o/advanced-semantic-first-miss-20261111/stages-v6 \
  --run-dir artifacts/u_10o0o/advanced-semantic-first-miss-20261111/ab-v1 \
  --output-dir experimental/u_10o0o/records/advanced-semantic-first-miss-20261111
```

진단은 보관된 검색 결과를 읽는 사후 분석이다. 순위 재생은 참고 근거를 읽지 않고 기존 질문·계획만 실행하며, RRF·합집합 순서를 86개 모두 대조한다. A/B는 640개 검색 결과와 최종 문맥을 모두 저장한 뒤 참고 근거를 읽는다. 동일 문항 두 조건을 짝지은 변화 기록은 320행이다. 40개 일상 표현은 주 비교와 원 질문 회귀에 각각 포함되므로 고유 질문은 280개다.

## 저장 파일

- `stages-v6/stage-diagnosis.json`: 40문항 단계별 충족과 후보 실패 19개 분류.
- `stages-v6/retriever-diagnosis.json`: 재생한 BM25 상위 80·벡터 상위 1,000에서 요구 근거의 관측 깊이. 실제 A/B 검색 깊이는 각각 상위 20으로 유지했다.
- `ranks-v4/manifest.json`: 순위 재현·입력 및 코드 지문. 과거 재생 시도는 별도 경로에 보존했다.
- `is-windows-v1/manifest.json`: IS 창의 원문 범위·벡터·모델·설정 지문. 원본 18,000쪽 벡터는 변경하지 않았다.
- `ab-v1/traces.jsonl`, `contexts.jsonl`, `request-ranks.jsonl`: 검색 순위와 원문 문맥. 로컬에만 보관한다.
- `ab-v1/summary.json`, `per-question.json`, `differences.json`: 전체·문항별 채점.
- 공유 기록의 `*-sanitized.json`, `ab-summary.json`: 질문·정답 인용·문맥·출처 ID를 제외한 수치와 지문.

## 검증 명령

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest -q tests \
  experimental/u_10o0o/pattern_retrieval/tests \
  experimental/u_10o0o/tests/test_advanced_semantic_ab.py \
  experimental/u_10o0o/tests/test_advanced_is_windows.py
.venv/bin/python -m build
```

실험 디렉터리는 공통 Ruff 제외 대상이므로 새 모듈과 테스트에는 `--isolated --select E4,E7,E9,F,I` 검사를 따로 적용했다. 새 파일의 서식도 따로 확인했다. 결과는 [validation.json](validation.json)에 남긴다.

## 해석할 때 유지할 경계

- 이번 통제군은 인용된 기존 실행의 `bigram-id` 구성이다. 실행 도중 다른 작업에서 기본값이 `bigram-id-normalized`로 바뀌었으므로 실험 프로세스에서만 과거 설정을 고정했다. 동시 변경 파일은 보존했다.
- 일상 표현 주 비교는 저장된 요청 계획을 사용한다. 공식·카테고리 회귀는 원 질문을 그대로 검색한다. 두 실험의 점수 차이를 요청 계획 자체의 효과로 해석하지 않는다.
- 계획 검수는 검색 순위와 독립된 AI 검수이며 사람 검수나 미사용 검증 세트가 아니다.
- 배치 실행 시간은 온라인 요청 지연 측정이 아니다. 공유 계산과 캐시를 사용했으므로 이번 결과에서 p50·p95나 속도 우열을 주장하지 않는다.
- 기존 기준선·운영 API·기본 CLI는 이번 실험에서 교체하지 않았다.
