# Advanced HG 사건–출처 연결

2026-11-09에 [정제 계획의 1단계](../../../advanced-curation-plan.md#1-hg-사건과-원본-출처-연결)를 실행했다. 기존 Advanced 정제본의 `case_context`와 `sources.jsonl`을 대조해 머리말의 HG 사건 ID와 기록 역할이 일치하는 쪽을 연결했다. 한 사건의 다섯 역할과 동일 계약 ID를 확인한 뒤 조회 인덱스에 넣었다.

| 결과 | 건수 |
|---|---:|
| 검증된 사건 | 2,730 |
| 출처 연결 | 13,650 |
| 역할당 출처 연결 | 2,730 |
| 검토 대상 사건 | 0 |
| 사건 미식별 기록 | 0 |

HG 사건 ID의 SHA-256 순서로 뽑은 5개 사건의 25쪽을 원본 PDF 25개에서 다시 추출했다. 원본 파일 지문, 쪽 번호, 머리말의 사건 ID와 역할, 계약 ID가 정제 기록과 일치했다. 합성 입력에서 완전한 사건이 인덱스에 들어가고, 출처·기록 누락, 기록·역할 중복, 역할·사건 ID·계약 불일치가 검토로 분리되는 것을 확인했다. 저장소 Ruff, 실험 파일 Ruff·형식 검사, pytest 143건과 하위 검사 9건을 통과했다.

첫 실행의 로컬 경로는 `artifacts/u_10o0o/advanced-curation/hg-case-sources-20261109/`다. `case_sources.jsonl`에 사건 ID·역할·출처 ID·매칭 규칙을, `review.jsonl`에 보류 항목을, `summary.json`에 수량과 입력·출력 지문을 보관한다. 표본 검사는 원본 연결을 대상으로 했다. 검색 근거 충족률과 지연은 2단계에서 측정한다.

저장소 루트에서 [생성 스크립트](../../../advanced_case_sources.py)를 실행한다. 새 출력 경로를 사용한다.

```bash
uv run --no-sync python -m experimental.u_10o0o.advanced_case_sources \
  --output artifacts/u_10o0o/advanced-curation/hg-case-sources-replay
```
