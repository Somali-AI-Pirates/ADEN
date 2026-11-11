# KURE-v2 입력·순위·평가 검수 결과

공식60·카테고리220을 실행했다. 지정 근거 채점은 258개, 범위 모호 SEM-32는 대체 근거 검수 집계에서 분리한다. 이미 확인한 개발·회귀 세트의 자체 검수 결과이며 독립 검증이나 답변 생성 평가가 아니다.

[실험 조건](protocol.md) · [과정](process.md) · [검수 판정](audit.md) · [검증](validation.md)

| 조건 | 지정 근거 전체 /258 | 사전 검수 | 전체 사후 검수 | 공식 검수 | 일상 표현 검수 | p50/p95(ms) |
|---|---:|---:|---:|---:|---:|---:|
| A | 212 | 218/257 | 242/257 | 53/58 | 29/39 | 426.7/784.6 |
| B | 211 | 217/257 | 241/257 | 52/58 | 29/39 | 434.5/808.2 |
| C | 213 | 217/257 | 242/257 | 53/58 | 30/39 | 428.6/802.5 |
| D | 214 | 218/257 | 243/257 | 53/58 | 31/39 | 584.0/976.3 |
| E | 207 | 211/257 | 234/257 | 50/58 | 28/39 | 1266.8/1537.2 |

A=기존64/512·Top3+RRF, B=질문128, C=전체 리랭커 순서, D=리랭커1024, E=검색기별50(합집합최대100). 모든 조건의 최종8쪽·8000토큰과 원문 조회는 같다.

사후 검수는 같은 승인 근거 묶음을 모든 조건의 실제 반환 문맥과 대조한다. 다른 사건의 값이나 적용 범위가 다른 규칙을 전용하지 않는다. 지정 페이지를 못 찾아도 충분한 대체 원문을 실제로 반환했다면 통과한다. 사전 검수 효과와 사후 추가 출처 효과를 분리했다.

## 재현

```bash
.venv/bin/python -m experimental.u_10o0o.embedding_ablation.kure_resolution prepare
.venv/bin/python -m experimental.u_10o0o.embedding_ablation.kure_resolution retrieve
.venv/bin/python -m experimental.u_10o0o.embedding_ablation.kure_resolution score
.venv/bin/python -m experimental.u_10o0o.embedding_ablation.kure_resolution timing
.venv/bin/python -m experimental.u_10o0o.embedding_ablation.kure_resolution report
```

report 전에 로컬 post-review.json의 전체 실패 검수를 완료해야 한다. 이전 경로 덮어쓰기는 금지하며 --output으로 새 경로를 지정한다.
