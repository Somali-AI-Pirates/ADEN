# 검증 기록

2026-11-11 최종 상태에서 실행했다.

| 검증 | 결과 |
|---|---|
| 저장소 Ruff: `.venv/bin/python -B -m ruff check .` | 통과 |
| 개인 기록 코드 Ruff: `ruff check --isolated experimental/u_10o0o/records/advanced-table-rag-peer-20261111` | 통과 |
| 개인 기록 코드 서식 검사: `ruff format --check --isolated ...` | 통과 |
| 공통 전체 테스트와 TableRAG 실험 테스트: `.venv/bin/python -B -m pytest -q tests experimental/u_10o0o/table_rag` | 342 passed, 15 subtests passed (6.52초) |
| A/B 304개씩 결과 파일·프로토콜·질문 지문 | score/finalize에서 확인 |
| 최종 문맥 최대8쪽·8,000토큰 | 실행기와 scorer에서 전수 확인 |
| 참고답 조회 전 검색 저장 완료 | A/B run.json 게이트와 score-manifest로 확인 |
| 자동 충분58건의 어시스턴트 검토 | 전부 완료;9건 기각 |
| 자동 부정 판정2건의 보정 인용 | 인정한 반대 설정의 인용이 해당 실제 문맥에도 존재하는지 finalize에서 확인 |
| 원래 성공 결과 의미 전수 검수 | 미실시; 보정 지표를 답변 정확도로 해석하지 않음 |
| 독립 사람 검수·독립 에이전트 리뷰 | 미실시; 사이드 대화의 위임 금지에 따라 자체 점검 |
| 답변 생성·기준선 교체·커밋·푸시 | 이번 실행에 포함하지 않음 |

원시 검증 출력은 `artifacts/u_10o0o/advanced-table-rag-peer-20261111/v2/final-ruff.log`, `final-pytest.log`에 있다. 준비 단계에는337개 테스트가 통과했으며 위 표는 완료 시점의 실행 결과를 사용한다. 공용 런타임과 데이터의 기존 변경은 수정하지 않았다.

빌드·패키징·공개 CLI·운영 API를 수정하지 않아 패키지 빌드는 이번 검증에서 실행하지 않았다. 새 실험 실행기의 오류 중단 조건은 실제 API http_status 필드를 읽도록 자체 점검했고, 실행 당시 코드는 artifacts의 executed-code에 따로 보존했다.
