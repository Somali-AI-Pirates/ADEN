# ADEN 해운 문서 질의응답

ADEN은 Advanced 해운 PDF의 기록·규정·표를 조회해 답변과 원본 페이지를 제공한다. 기본 서버는 DB 우선 통합 TableRAG를 사용한다. 공통 SQLite의 HG·HA·IS 조회와 페이지·표 하이브리드 검색을 다섯 도구로 연결한다.

[빠른 시작](guides/quickstart.md)에서 원본 PDF와 SQLite·모델 준비부터 Swagger 질문, 저장된 답변 채점과 Langfuse 확인까지 진행한다.

## 개발 문서

| 찾는 내용 | 문서 |
| --- | --- |
| 발제문 요구사항, 평가 규칙, 선택 과제와 제출 | [프로젝트 발제문 요구사항](project-brief.md) |
| 작업 브랜치, PR, 리뷰, 개인 실험 | [기여 가이드](CONTRIBUTING.md) |
| 코드 배치, 의존 방향, 요청 흐름 | [시스템 설계](design/architecture.md) |
| Advanced TableRAG의 설계·구현 단계·평가 | [Advanced RAG 구현 계획](plans/advanced-rag/README.md) |
| HTTP, 타입, 함수, 실행 기록 | [공통 계약](design/contracts.md) |
| 설치, 실행, 검사 | [개발 가이드](guides/development.md) |
| 원본 배치, TableRAG 구축, 기존 Qdrant 색인 | [데이터 가이드](guides/data.md) |
| 정제본 구조와 서비스 연결 범위 | [지식 데이터 설계](design/knowledge-data.md) |
| 일반·보안 평가, 공식 실행과 검증 상태 | [평가 가이드](guides/c-module-guide.md) |
| Judge 채점 기준 버전과 점수 변화 | [채점 기준 이력](guides/judge-rubric.md) |

## 기존 B/C 개발 경로

검색을 대체하는 근거 주입과 메모리 Qdrant 테스트만 [레거시 B/C 가이드](guides/local-bc.md)를 따른다. 기본 DB 우선 TableRAG의 설치·실행에는 이 절차가 필요하지 않다.

- [브라우저 채팅·출처·PDF 열람](../web/README.md): 클라이언트 실행, 스트리밍 계약, 정적 배포와 검사.
