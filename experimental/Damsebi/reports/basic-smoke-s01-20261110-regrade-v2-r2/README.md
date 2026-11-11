# 평가 결과 — basic-smoke-s01-20261110-regrade-v2-r2

- 데이터: `basic` · 설정 해시: `sha256:9f802f7c4882277341f09502642d7bc89ae5c8b968614ba89dd970013557d4aa`
- 실행 코드: `8434792dc2dd05375694ac793511ab0c2b840a31` (미커밋 변경: True)
- 일반 평가 k=8, 방식: naive, hybrid, hybrid_rerank
- 선택 질문(1개): `S01`
- 선택 이유: 1문항 점검: 형식·Judge 파싱·RAGAS·Langfuse·시간·토큰 확인
- 평가기: Judge=gpt-6-luna, Faithfulness=gpt-6-luna
- Langfuse 전송: `flushed_unverified` (`flushed_unverified`는 전송 요청만 끝난 상태이며 저장 확인이 아니다)
- Langfuse 저장 확인(요청 수): `{'verified': 3}` (`verified`만 API에서 관측을 조회한 것, `None`은 추적 비활성·어댑터 관리)
- 재채점: 원본 `basic-smoke-s01-20261110`의 저장 답변을 다시 채점했다(답변 재생성 없음). Judge 프롬프트 `judge.txt`, Faithfulness recomputed

## 방식별 품질 요약

값은 평균 (평가 대상/해당 없음/평가 불가 건수). 해당 없음·평가 불가는 평균에서 제외하고 0점은 포함한다.

| 지표 | naive | hybrid | hybrid_rerank |
| --- | --- | --- | --- |
| Hit@k | 1.000 (1/0/0) | 1.000 (1/0/0) | 1.000 (1/0/0) |
| Recall@k | 1.000 (1/0/0) | 1.000 (1/0/0) | 1.000 (1/0/0) |
| Faithfulness | 0.867 (1/0/0) | 0.897 (1/0/0) | 0.688 (1/0/0) |
| Judge 사실 | 1.000 (1/0/0) | 1.000 (1/0/0) | 1.000 (1/0/0) |
| Judge 조건 | 1.000 (1/0/0) | 1.000 (1/0/0) | 1.000 (1/0/0) |
| Judge 근거 | 1.000 (1/0/0) | 1.000 (1/0/0) | 1.000 (1/0/0) |
| Judge 판단 | 1.000 (1/0/0) | 1.000 (1/0/0) | 1.000 (1/0/0) |

## 운영 지표 (서비스 요청만, 평가 호출 제외)

| 방식 | 요청 | 오류율 | 평균 시간(정상) | 입력 토큰 | 출력 토큰 | 사용량 확인(완전/부분/미확인) | 재시도 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| naive | 1 | 0.0% (0) | 7187 ms | 4986 | 651 | 1/0/0 | 0 |
| hybrid | 1 | 0.0% (0) | 7315 ms | 4986 | 658 | 1/0/0 | 0 |
| hybrid_rerank | 1 | 0.0% (0) | 188527 ms | 5065 | 619 | 1/0/0 | 0 |

정상적인 추가 확인·보류는 오류가 아니다. 토큰 합계는 확인된 값만 더한 것이며 부분·미확인 건수를 함께 본다.
평가 호출(RAGAS·Judge)의 시간·토큰은 `summary.json`의 `grading`에 따로 기록한다.

## Langfuse 기록 확인

각 요청의 trace ID는 `answers.jsonl`·`comparison.csv`의 `langfuse_trace_id`다. trace ID는 `Langfuse.create_trace_id(seed=request_id)`로 만들므로 request_id로도 다시 계산할 수 있다.

```bash
python -m app.evaluation.scripts.run_benchmark --verify-langfuse <trace_id>
```

Langfuse UI에서는 trace ID로 검색해 `rag-query` → `retrieval` → `final-context` → `generation-attempt-N` 순서를 확인한다.

## 파일

- `manifest.json`: 재현 설정 · `comparison.csv`: 질문×방식 점수·사유·운영값
- `answers.jsonl`: 실제 답변·상태·인용·요청 ID · `scores.jsonl`: 평가 상세
- `summary.json`: 방식별 요약 · `security_comparison.csv`: 보안 답변 4개
