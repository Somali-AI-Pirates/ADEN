# 평가 결과 — basic-common-s01-20261110-regrade-v2-r2

- 데이터: `basic` · 설정 해시: `sha256:9f802f7c4882277341f09502642d7bc89ae5c8b968614ba89dd970013557d4aa`
- 실행 코드: `a3ee99e730def3e86eba72289195c7efd489b25e` (미커밋 변경: True)
- 일반 평가 k=8, 방식: naive, hybrid, hybrid_rerank
- 선택 질문(1개): `S01`
- 선택 이유: 공통 자원 경로 1문항 점검(개발용 S01)
- 평가기: Judge=gpt-6-luna, Faithfulness=gpt-6-luna
- Langfuse 전송: `flushed_unverified` (`flushed_unverified`는 전송 요청만 끝난 상태이며 저장 확인이 아니다)
- Langfuse 저장 확인(요청 수): `{'verified': 3}` (`verified`만 기대한 관측이 모두 조회된 것, `partial`은 일부만 조회, `None`은 추적 비활성·어댑터 관리)
- Langfuse 평가 점수 게시: `{'status': 'recorded_unverified', 'evaluation_set': 'common-s01-v2-r2', 'groups': ['retrieval', 'faithfulness', 'judge', 'evidence'], 'evaluation_hash': 'b0eee8b3d2c5d289b69a0194ec1ba5f398381ec747c13b786543e5a3e76c7ba5', 'sample': False, 'score_status_counts': {'recorded_unverified': 36}, 'flush': {'status': 'flushed_unverified'}}` (전송과 서버 저장 확인은 별도)
- 재채점: 원본 `basic-common-s01-20261110`의 저장 답변을 다시 채점했다(답변 재생성 없음). Judge 프롬프트 `judge.txt`, Faithfulness recomputed

## 방식별 품질 요약

값은 평균 (평가 대상/해당 없음/평가 불가 건수). 해당 없음·평가 불가는 평균에서 제외하고 0점은 포함한다.

| 지표 | naive | hybrid | hybrid_rerank |
| --- | --- | --- | --- |
| Hit@k | 1.000 (1/0/0) | 1.000 (1/0/0) | 1.000 (1/0/0) |
| Recall@k | 1.000 (1/0/0) | 1.000 (1/0/0) | 1.000 (1/0/0) |
| Faithfulness | 0.846 (1/0/0) | 0.909 (1/0/0) | 0.846 (1/0/0) |
| Judge 사실 | 1.000 (1/0/0) | 1.000 (1/0/0) | 1.000 (1/0/0) |
| Judge 조건 | 1.000 (1/0/0) | 1.000 (1/0/0) | 1.000 (1/0/0) |
| Judge 근거 | 1.000 (1/0/0) | 1.000 (1/0/0) | 1.000 (1/0/0) |
| Judge 판단 | 1.000 (1/0/0) | 1.000 (1/0/0) | 1.000 (1/0/0) |

## 검색·문맥 누락 진단

페이지 포함은 인용문 전체 보존을 뜻하지 않는다. 예산 제외 ID로 확인된 누락만 예산 누락으로 센다.

| 지표 | naive | hybrid | hybrid_rerank |
| --- | --- | --- | --- |
| budget_lost_page_recall | 0.000 (1/0/0) | 0.000 (1/0/0) | 0.000 (1/0/0) |
| candidate_doc_recall | 1.000 (1/0/0) | 1.000 (1/0/0) | 1.000 (1/0/0) |
| candidate_page_recall | 1.000 (1/0/0) | 1.000 (1/0/0) | 1.000 (1/0/0) |
| context_lost_page_recall | 0.000 (1/0/0) | 0.000 (1/0/0) | 0.000 (1/0/0) |
| final_page_recall | 1.000 (1/0/0) | 1.000 (1/0/0) | 1.000 (1/0/0) |

## 운영 지표 (서비스 요청만, 평가 호출 제외)

| 방식 | 요청 | 오류율 | 평균 시간(정상) | 입력 토큰 | 출력 토큰 | 사용량 확인(완전/부분/미확인) | 재시도 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| naive | 1 | 0.0% (0) | 10419 ms | 4986 | 946 | 1/0/0 | 0 |
| hybrid | 1 | 0.0% (0) | 6453 ms | 4986 | 612 | 1/0/0 | 0 |
| hybrid_rerank | 1 | 0.0% (0) | 17345 ms | 5065 | 610 | 1/0/0 | 0 |

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
