# 검증

- 자체 리뷰: 관측된 출처만 승격, 기존 정책 재현, SQL·원문 공동 예산, 최종 미보존 출처의 충분 판정 차단을 확인했다. 사이드 대화 제약으로 독립 리뷰는 하지 않았다.
- `.venv/bin/python -m ruff check .`: 통과.
- 개인 변경 파일은 `ruff check --isolated --select E4,E7,E9,F`로 프로젝트와 같은 규칙을 적용해 통과. 초도 isolated 기본 규칙은 기존 코드의 미사용 unpack 변수와 import 정렬도 검사해 실패했으며, 코드 동작 오류가 아닌 프로젝트 외 추가 규칙이었다.
- `.venv/bin/python -B -m pytest -q tests experimental/u_10o0o/table_rag`: 343 passed, 15 subtests passed.
- 집중 출처 보존/기존 TableRAG 검사: 31 passed.
- 실행 코드와 입력 지문은 protocol.json에 고정하고 executed-code에 보존했다.
- 새 실험 CLI `run_benchmark --help`: 통과.
- 기준선·README의 로컬 링크 확인: 통과.
- 새 공개 API/패키징 변경은 없으며 개인 실험은 공통 배포 패키지에서 제외한다.

최종 결과 검증: runtime/input 지문 일치, 검색426개·검수196개 저장 완료, 중복 없는 자체 재판정36개, 문항213개 집계, 보고서 로컬 링크를 확인했다. 최신 실험 코드·보고서 생성 코드의 Ruff(E4,E7,E9,F)도 통과했다. 보고서 집계 중 미검수 상태 키(None)의 JSON 정렬 오류를 수정했고 최종 생성·재실행이 통과했다. 실행 중인 검색 조건은 수정하지 않았다.
