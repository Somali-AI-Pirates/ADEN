# 검증 결과

- 저장된 검색 491문항 × 4설정의 질문·출력 지문과 후보·최종 예산을 검사했다. 새 실행은 704개이며 기존 출력 재사용과 구분했다.
- 검수 필수 568개 조합과 완료 판정 집합이 일치하고 미완료는 0개다. 충분 판정 인용은 실제 반환 문맥에 존재하며 출처 지문도 일치한다.
- 공식·카테고리·동료·거짓 전제 집계를 문항별 결과에서 다시 계산해 확인했다. v2 B의 관측·선택 출처 유실은 0개이며 미관측 선택 1개는 별도로 표시했다.
- 실행 시 고정한 런타임 파일의 지문이 현재 파일과 일치한다. 기존 A와 v3 A/B의 프롬프트 일치도 확인했다.
- Ruff 공통 검사와 새 기록 스크립트의 별도 E4/E7/E9/F 검사가 모두 통과했다.
- 전체 공통 테스트와 개인 TableRAG 테스트: 359 passed, 15 subtests passed, 6.41초.
- 새 실행기의 CLI 도움말을 확인했다. 최종 집계 스크립트와 같은 검색 재생 스크립트가 저장된 결과로 실행됐다.
- Markdown 내부 링크와 새 Python 파일 구문을 확인했다. 원문·질문·정답 인용은 로컬 artifacts에 보관했다.

```bash
.venv/bin/ruff check .
.venv/bin/ruff check --isolated --select E4,E7,E9,F experimental/u_10o0o/records/advanced-table-rag-kure-v2-preservation-20261111
.venv/bin/python -B -m pytest -q tests experimental/u_10o0o/table_rag
.venv/bin/python -B -m experimental.u_10o0o.records.advanced-table-rag-kure-v2-preservation-20261111.finalize
```

독립 에이전트 리뷰와 독립 사람 검수는 수행하지 않았다. 이 대화의 별도 작업 범위에서 자체 검토했다. 실제 답변 생성, 반복 실행에 의한 분산 추정, 새 블라인드 검증, 운영 성능 검증은 이번 결과에 포함하지 않는다. 기존 기준선 변경과 커밋·푸시도 수행하지 않았다.
