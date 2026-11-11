# ADEN 해운 문서 질의응답

ADEN은 해운 PDF를 검색해 예약·서류·처리 상태·비용 조건에 답하고 문서명과 페이지를 인용한다. 현재 서비스는 Basic 색인을 사용하며 Dense, Hybrid, Hybrid + Reranking 검색을 제공한다. [오프라인 정제 도구](design/knowledge-data.md)로 세 난이도의 정제본을 로컬에 생성할 수 있다.

[설치와 서버 실행](guides/development.md#서버-실행)을 마치면 [Swagger UI](http://127.0.0.1:8000/docs)에서 질문할 수 있다.

## 개발 문서

| 찾는 내용 | 문서 |
| --- | --- |
| 발제문 요구사항, 평가 규칙, 선택 과제와 제출 | [프로젝트 발제문 요구사항](project-brief.md) |
| 작업 브랜치, PR, 리뷰, 개인 실험 | [기여 가이드](CONTRIBUTING.md) |
| 코드 배치, 의존 방향, 요청 흐름 | [시스템 설계](design/architecture.md) |
| HTTP, 타입, 함수, 실행 기록 | [공통 계약](design/contracts.md) |
| 설치, 실행, 검사 | [개발 가이드](guides/development.md) |
| 원본 배치, Qdrant 색인, 검색 확인 | [데이터 가이드](guides/data.md) |
| 정제본 구조와 서비스 연결 범위 | [지식 데이터 설계](design/knowledge-data.md) |
| 일반·보안 평가, 공식 실행과 검증 상태 | [평가 가이드](guides/c-module-guide.md) |
| Judge 채점 기준 버전과 점수 변화 | [채점 기준 이력](guides/judge-rubric.md) |

로컬 B·C 연결과 메모리 Qdrant 사용은 [Advanced 로컬 B·C 개발](guides/local-bc.md)을 따른다. A 없는 API 실행·PR49 프롬프트 확인·request_id로 trace 조회는 [B/C 고정 근거 API 실행](guides/local-bc.md#bc-고정-근거-api-실행)을 따른다.
