# 평가 결과 — basic-bc-context-s01-20261110

- 데이터: `basic` · 설정 해시: `sha256:9f802f7c4882277341f09502642d7bc89ae5c8b968614ba89dd970013557d4aa`
- 실행 코드: `81446b07bc14b8a0778287624b06c407b061671d` (미커밋 변경: True)
- 일반 평가 k=8, 방식: naive, hybrid
- 선택 질문(1개): `S01`
- 선택 이유: 고정 근거 B·C 검증: 최종 문맥까지 주입(naive), 저장 후보가 없는 방식(hybrid)은 오류 경로 확인. Judge·RAGAS 미실행
- 평가기: Judge=None, Faithfulness=None
- Langfuse 전송: `flushed_unverified` (`flushed_unverified`는 전송 요청만 끝난 상태이며 저장 확인이 아니다)
- Langfuse 저장 확인(요청 수): `{'verified': 2}` (`verified`만 API에서 관측을 조회한 것, `None`은 추적 비활성·어댑터 관리)
- Langfuse 평가 점수 게시: `{'status': 'recorded_unverified', 'score_status_counts': {'recorded_unverified': 8, 'skipped': 16}, 'flush': {'status': 'flushed_unverified'}, 'evaluation_hash': 'a3bf61bad31ab7c0db0613cf9687709b78e91b740c7ebcd90440d95617141e3f', 'sample': False}` (전송과 서버 저장 확인은 별도)

## 방식별 품질 요약

값은 평균 (평가 대상/해당 없음/평가 불가 건수). 해당 없음·평가 불가는 평균에서 제외하고 0점은 포함한다.

| 지표 | naive | hybrid |
| --- | --- | --- |
| Hit@k | 1.000 (1/0/0) | - (0/0/1) |
| Recall@k | 1.000 (1/0/0) | - (0/0/1) |
| Faithfulness | - (0/0/1) | - (0/0/1) |
| Judge 사실 | - (0/0/1) | - (0/0/1) |
| Judge 조건 | - (0/0/1) | - (0/0/1) |
| Judge 근거 | - (0/0/1) | - (0/0/1) |
| Judge 판단 | - (0/0/1) | 0.000 (1/0/0) |

## 검색·문맥 누락 진단

페이지 포함은 인용문 전체 보존을 뜻하지 않는다. 예산 제외 ID로 확인된 누락만 예산 누락으로 센다.

| 지표 | naive | hybrid |
| --- | --- | --- |
| budget_lost_page_recall | 0.000 (1/0/0) | - (0/0/1) |
| candidate_doc_recall | 1.000 (1/0/0) | - (0/0/1) |
| candidate_page_recall | 1.000 (1/0/0) | - (0/0/1) |
| context_lost_page_recall | 0.000 (1/0/0) | - (0/0/1) |
| final_page_recall | 1.000 (1/0/0) | - (0/0/1) |

## 운영 지표 (서비스 요청만, 평가 호출 제외)

| 방식 | 요청 | 오류율 | 평균 시간(정상) | 입력 토큰 | 출력 토큰 | 사용량 확인(완전/부분/미확인) | 재시도 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| naive | 1 | 0.0% (0) | 5345 ms | 4986 | 595 | 1/0/0 | 0 |
| hybrid | 1 | 100.0% (1) | - | None | None | 0/0/1 | 0 |

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
