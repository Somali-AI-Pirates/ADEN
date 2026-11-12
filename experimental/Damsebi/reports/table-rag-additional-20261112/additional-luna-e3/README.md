# 평가 결과 — table-rag-additional-20261112

- 데이터: `advanced` · 설정 해시: `86404dfe8dd91f1d856883172dc240247a3fc90750ce7f9d5a0560bd51987bb7`
- 실행 코드: `92e8c038a432c334400619ff38e603be5d863c2e` (미커밋 변경: False)
- 일반 평가 k=8, 방식: table_rag
- 선택 질문(30개): `AS-ADD01`, `AS-ADD02`, `AS-ADD03`, `AS-ADD04`, `AS-ADD05`, `AS-ADD06`, `AS-ADD07`, `AS-ADD08`, `AS-ADD09`, `AS-ADD10`, `AS-ADD11`, `AS-ADD12`, `AS-ADD13`, `AS-ADD14`, `AS-ADD15`, `AS-ADD16`, `AS-ADD17`, `AS-ADD18`, `AS-ADD19`, `AS-ADD20`, `AS-ADD21`, `AS-ADD22`, `AS-ADD23`, `AS-ADD24`, `AS-ADD25`, `AS-ADD26`, `AS-ADD27`, `AS-ADD28`, `AS-ADD29`, `AS-ADD30`
- 선택 이유: Saved TableRAG request grading
- 평가기: Judge=gpt-6-luna, Faithfulness=gpt-6-luna, Judge 프롬프트 `sha256:5c29900bc2f7`, 출력 스키마 `judge-output-v2`
- Langfuse 전송: `None` (`flushed_unverified`는 전송 요청만 끝난 상태이며 저장 확인이 아니다)
- Langfuse 저장 확인(요청 수): `None` (`verified`만 기대한 관측이 모두 조회된 것, `partial`은 일부만 조회, `None`은 추적 비활성·어댑터 관리)

## 방식별 품질 요약

값은 평균 (평가 대상/해당 없음/평가 불가 건수). 해당 없음·평가 불가는 평균에서 제외하고 0점은 포함한다.

| 지표 | table_rag |
| --- | --- |
| Hit@k | 1.000 (29/1/0) |
| Recall@k | 0.902 (29/1/0) |
| Faithfulness | 0.756 (30/0/0) |
| Judge 사실 | 0.767 (30/0/0) |
| Judge 조건 | 0.867 (30/0/0) |
| Judge 근거 | 0.931 (29/1/0) |
| Judge 판단 | 0.967 (30/0/0) |

## 검색·문맥 누락 진단

페이지 포함은 인용문 전체 보존을 뜻하지 않는다. 예산 제외 ID로 확인된 누락만 예산 누락으로 센다.

| 지표 | table_rag |
| --- | --- |
| budget_lost_page_recall | 0.000 (29/1/0) |
| candidate_doc_recall | 0.974 (29/1/0) |
| candidate_page_recall | 0.793 (29/1/0) |
| context_lost_page_recall | 0.034 (29/1/0) |
| final_page_recall | 0.759 (29/1/0) |

## 검산 진단 (Judge, 공식 점수 아님)

complete 검산 완료 / incomplete 요청받은 검산 미완료 / not_applicable 검산 요청 없음 / unavailable 진단 불가(구조 오류·호출 실패·서비스 실패) / missing 진단이 없는 이전 기록. 대상별 부족 근거는 `comparison.csv`의 `calculation_items`에 있다.

| 방식 | 문항 수(상태별) |
| --- | --- |
| table_rag | complete 5, incomplete 4, not_applicable 21 |

## 운영 지표 (서비스 요청만, 평가 호출 제외)

| 방식 | 요청 | 오류율 | 평균 시간(정상) | 입력 토큰 | 출력 토큰 | 사용량 확인(완전/부분/미확인) | 재시도 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| table_rag | 30 | 0.0% (0) | 25236 ms | 791568 | 18694 | 30/0/0 | 62 |

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
