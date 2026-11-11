# 검증 결과

개인 실험 파일 3개와 새 기록 경로가 변경 범위다. 기존 검색 코드·설정·평가 정답·색인과 다른 작성자의 작업은 변경하지 않았다. 새 CLI는 기존 KURE-v2 색인과 기준선의 검색·리랭킹·문맥 조립 함수를 재사용한다.

| 검사 | 결과 |
|---|---|
| `python -m ruff check .` | 통과 |
| 새 `kure_followup.py`, `kure_followup_report.py`, `test_kure_followup.py`의 `ruff check --isolated` | 통과 |
| 위 3개 파일의 `ruff format --check --isolated` | 통과 |
| `python -m pytest -q tests experimental/u_10o0o/embedding_ablation` | 296 passed, 15 subtests passed |
| 새 CLI `--help`와 prepare/audit/retrieve/score/timing/report 실제 실행 | 통과 |
| 기존 KURE-v2의 후보·순위·최종 문맥 지문 재현 | 280문항, 차이 0건 |
| 최종 최대 8쪽·8,000토큰 및 문맥/출처 일치 | 4조건 × 280문항 검사 통과 |
| 저장 결과·원래 입력·원래 정답의 지문 | 일치 |
| 온라인 측정과 품질 평가의 출처/문맥 비교 | 근거 채점 대상 표본은 동일. 범위 밖 AS-S08-01 한 문항에서 base20/scope20의 배치별 문맥 차이를 기록 |

5개의 새 집중 테스트는 미인식 양식 보존, 원문 코드·화주 단서가 있는 질문의 전체 검색 유지, RRF에서 탈락하는 벡터 근거의 합집합 보존, 범위 필터의 Top20 이전 적용, 미완료 조건에서 정답을 읽지 않는 경계를 확인한다.

이 별도 대화는 독립 에이전트를 사용할 수 없어 자체 검토를 수행했다. 독립 리뷰·사람 검수는 미실시다. 공식/카테고리 개발 세트를 재사용한 결과이며 독립 채택 검증을 대신하지 않는다. 공통 패키지와 의존성은 변경하지 않았고 개인 실험은 배포 패키지에서 제외되므로 이번 작업에서는 패키지 빌드를 반복하지 않았다.
