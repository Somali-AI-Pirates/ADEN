# 완료 검증

- `ruff check .`: 통과.
- 변경한 개인 Python 파일 E4/E7/E9/F 검사: 통과.
- 변경한 Python 3개 파일 `ruff format --isolated --check`: 통과.
- 집중 검사 `test_default_policy.py`, `test_preservation.py`: 8개 통과.
- `python -B -m pytest -q tests experimental/u_10o0o/table_rag`: **361 passed, 15 subtests passed**, 5.98초.
- `python -B -m experimental.u_10o0o.table_rag --help`: 정상 종료.
- 실제 `benchmark.run` 경로를 가짜 외부 모델/검색으로 실행해 병합 상위20 밖의 선택 출처가 최종 근거에 남는지, 실제 지침과 프로토콜 프롬프트 지문이 같은지, 저장된 감사가 preserve 정책인지 확인했다.
- 이전 A 결과를 상속하는 reuse-proof가 있으면 새로운 B 프로토콜을 만들기 전에 거절하는 검사를 통과했다. 이전 산출물은 삭제하거나 바꾸지 않았다.
- 저장 결과 분석은 8개 실제 부족·13개 모호성·별도 모호성 오종료 2개를 처리했다. 원문·스냅샷·검수 입력 지문과 진단 코드 지문을 검사했다. 추적한 누락 대상이 관측 본문/스키마에 포함된 횟수는 0, 절단된 리랭커 입력은 0, 선택 출처 유실도 0이다.
- 변경 문서의 내부 파일 링크와 현재/과거 정책 설명을 확인했다. 변경 전 파일 사본과 최종 작업 차이는 로컬 artifacts에 보관했다.

독립 리뷰는 이 별도 대화의 제약에 따라 수행하지 않고 자체 검토했다. 실제 API를 통한 기본 실행의 재벤치·실제 답변 생성·새 리랭커 비교는 이번에 하지 않았다. 패키징·공통 의존성·운영 API 변경은 없으며 새 패키지 빌드는 수행하지 않았다. 커밋·푸시는 하지 않았다.
