# 평가 결과 — basic-dev-20261110-regrade-v2-r1

- 데이터: `basic` · 설정 해시: `sha256:9f802f7c4882277341f09502642d7bc89ae5c8b968614ba89dd970013557d4aa`
- 실행 코드: `8434792dc2dd05375694ac793511ab0c2b840a31` (미커밋 변경: True)
- 일반 평가 k=8, 방식: naive, hybrid, hybrid_rerank
- 선택 질문(8개): `S01`, `S04`, `S09`, `S12`, `M01`, `M03`, `M07`, `M16`
- 선택 이유: 개발용 8문항(기준 조정용). 2026-11-07 v1 결과에서 사실 0점 반복(S04·S09·M07), 보류·판단 엇갈림(S12·M03), 검색 누락(M01), 전 항목 충족 대조(S01·M16)를 S·M에서 고름. 나머지 32문항은 확인용으로 남김
- 평가기: Judge=gpt-6-luna, Faithfulness=원본 재사용(gpt-6-luna)
- Langfuse 전송: `flushed_unverified` (`flushed_unverified`는 전송 요청만 끝난 상태이며 저장 확인이 아니다)
- Langfuse 저장 확인(요청 수): `{'verified': 24}` (`verified`만 API에서 관측을 조회한 것, `None`은 추적 비활성·어댑터 관리)
- 재채점: 원본 `basic-dev-20261110`의 저장 답변을 다시 채점했다(답변 재생성 없음). Judge 프롬프트 `judge.txt`, Faithfulness reused

## 방식별 품질 요약

값은 평균 (평가 대상/해당 없음/평가 불가 건수). 해당 없음·평가 불가는 평균에서 제외하고 0점은 포함한다.

| 지표 | naive | hybrid | hybrid_rerank |
| --- | --- | --- | --- |
| Hit@k | 1.000 (8/0/0) | 1.000 (8/0/0) | 1.000 (8/0/0) |
| Recall@k | 0.812 (8/0/0) | 0.875 (8/0/0) | 0.875 (8/0/0) |
| Faithfulness | 0.791 (8/0/0) | 0.832 (8/0/0) | 0.831 (8/0/0) |
| Judge 사실 | 0.750 (8/0/0) | 0.625 (8/0/0) | 0.625 (8/0/0) |
| Judge 조건 | 1.000 (8/0/0) | 1.000 (8/0/0) | 0.750 (8/0/0) |
| Judge 근거 | 0.875 (8/0/0) | 1.000 (8/0/0) | 0.750 (8/0/0) |
| Judge 판단 | 1.000 (8/0/0) | 1.000 (8/0/0) | 0.875 (8/0/0) |

## 운영 지표 (서비스 요청만, 평가 호출 제외)

| 방식 | 요청 | 오류율 | 평균 시간(정상) | 입력 토큰 | 출력 토큰 | 사용량 확인(완전/부분/미확인) | 재시도 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| naive | 8 | 0.0% (0) | 9574 ms | 39123 | 8190 | 8/0/0 | 0 |
| hybrid | 8 | 0.0% (0) | 9765 ms | 41306 | 7462 | 8/0/0 | 0 |
| hybrid_rerank | 8 | 0.0% (0) | 15713 ms | 41355 | 7305 | 8/0/0 | 0 |

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
