# 평가 결과 — custom-2026-11-07

- 데이터: `basic` · 설정 해시: `9f802f7c4882277341f09502642d7bc89ae5c8b968614ba89dd970013557d4aa`
- 실행 코드: `361d92fa316990068f8477fdba8119a3491fec1b` (미커밋 변경: True)
- 일반 평가 k=8, 방식: naive, hybrid, hybrid_rerank
- 선택 질문(40개): `S01`, `S02`, `S03`, `S04`, `S05`, `S06`, `S07`, `S08`, `S09`, `S10`, `S11`, `S12`, `S13`, `S14`, `S15`, `S16`, `S17`, `S18`, `S19`, `S20`, `M01`, `M02`, `M03`, `M04`, `M05`, `M06`, `M07`, `M08`, `M09`, `M10`, `M11`, `M12`, `M13`, `M14`, `M15`, `M16`, `M17`, `M18`, `M19`, `M20`
- 선택 이유: 사용자 작성 Basic 질문 40개(기대 답변·출처 포함). 평가 데이터셋과 별개이며 독립 검증 세트로 검증되지 않음
- 평가기: Judge=gpt-6-luna, Faithfulness=ragas (gpt-6-luna, max_tokens=8192)
- Langfuse 전송: `None` (`flushed_unverified`는 전송 요청만 끝난 상태이며 저장 확인이 아니다)
- Langfuse 저장 확인(요청 수): `None` (`verified`만 API에서 관측을 조회한 것, `None`은 추적 비활성·어댑터 관리)

## 방식별 품질 요약

값은 평균 (평가 대상/해당 없음/평가 불가 건수). 해당 없음·평가 불가는 평균에서 제외하고 0점은 포함한다.

| 지표 | naive | hybrid | hybrid_rerank |
| --- | --- | --- | --- |
| Hit@k | 0.975 (40/0/0) | 0.975 (40/0/0) | 0.975 (40/0/0) |
| Recall@k | 0.821 (40/0/0) | 0.908 (40/0/0) | 0.883 (40/0/0) |
| Faithfulness | 0.830 (40/0/0) | 0.854 (39/0/1) | 0.890 (40/0/0) |
| Judge 사실 | 0.425 (40/0/0) | 0.538 (39/0/1) | 0.550 (40/0/0) |
| Judge 조건 | 0.850 (40/0/0) | 0.846 (39/0/1) | 0.875 (40/0/0) |
| Judge 근거 | 0.700 (40/0/0) | 0.795 (39/0/1) | 0.825 (40/0/0) |
| Judge 판단 | 0.950 (40/0/0) | 0.925 (40/0/0) | 0.975 (40/0/0) |

## 운영 지표 (서비스 요청만, 평가 호출 제외)

| 방식 | 요청 | 오류율 | 평균 시간(정상) | 입력 토큰 | 출력 토큰 | 사용량 확인(완전/부분/미확인) | 재시도 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| naive | 40 | 0.0% (0) | 7582 ms | 174537 | 28155 | 40/0/0 | 1 |
| hybrid | 40 | 2.5% (1) | 7558 ms | 182942 | 28010 | 40/0/0 | 1 |
| hybrid_rerank | 40 | 0.0% (0) | 13613 ms | 182673 | 27912 | 40/0/0 | 0 |

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
