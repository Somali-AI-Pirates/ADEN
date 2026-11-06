# 문서와 기록 안내

ADEN은 4명이 2026-11-05~11-13에 진행하는 해운 RAG Q&A 프로젝트다. 아래에서 필요한 문서를 선택한다. 개발 참여·PR·문서 관리 규칙은 [CONTRIBUTING](../CONTRIBUTING.md)을 따른다.

## 목적별 문서

| 알고 싶은 것 | 문서 |
| --- | --- |
| 무엇을 만들고 어떻게 시작하나 | [프로젝트 소개·빠른 시작](../README.md) |
| 팀 연락·협업·개인 실험 채택·PR·완료 기준 | [공통 기여·협업 규칙](../CONTRIBUTING.md) |
| Python·uv·VS Code·Pylance·검사 방법 | [개발 환경 가이드](guides/development.md) |
| 서비스 흐름과 기능의 책임·설계 이유 | [시스템 설계](design/architecture.md) |
| 타입·API·함수·Resources·기록 형식 | [공통 계약](design/contracts.md) |
| 정제 지식의 구성과 런타임 연결 경계 | [지식 데이터 설계](design/knowledge-data.md) |
| 원본 배치·해시·색인 생성·검색 확인 | [데이터 안내](../data/README.md) |
| 실제 정제 파일·검토·재현 방법 | [정제 데이터 안내](../data/curated/README.md) |
| 평가·보안 실행과 연결 조건·현재 한계 | [평가 가이드](guides/c-module-guide.md) |
| 개인 폴더를 어떻게 쓰나 | [개인 실험 안내](../experimental/README.md) |
| AI가 이 저장소를 수정·검토할 때 지킬 지침 | [AGENTS.md](../AGENTS.md) |

## 팀 기록의 위치

| 정보 | 기준 위치 |
| --- | --- |
| 진행 작업·담당·목표일·완료 증거 | [ADEN 팀 홈](https://app.notion.com/p/6e9b8d4a06f782039952010095ade408)의 [WBS & Tasks](https://app.notion.com/p/4b8b8d4a06f782d1a01d01c2e3aee28b) |
| 회의·목표·역할·일정 변경과 결정 이유 | [팀 회의록](https://app.notion.com/p/0aab8d4a06f7833c99d181a4046c952d) |
| 필수 개인 작업일지·TIL | [개인 작업일지 DB](https://app.notion.com/p/497b8d4a06f78349bf4c01df787dba65), [TIL 제출](https://kant.spartaclub.kr/) |
| 검토 요청·막힘·부재 알림 | 팀 Slack. 근거가 있는 Notion·PR 링크를 연결 |
| 저장소·기술 문서로 가는 팀 Notion 안내 | [Github Rules](https://app.notion.com/p/5ddb8d4a06f783d2910b01949a1052ca) |
| 공식 비교·제출 결과 | `reports/`와 보존한 `artifacts/`; 작성 조건은 [평가 가이드](guides/c-module-guide.md#공식-비교-재현-증거) |

## 과거 계획과 검증 기록

| 기록 | 다루는 시점·범위 |
| --- | --- |
| [Basic 당일 작업 계획](plans/basic-day-plan-2026-11-05.md) | 2026-11-05의 3명 기준 원안 |
| [정제 지식 데이터 작업 기록](plans/structured-data.md) | Basic v1과 세 난이도 v2의 구현·검증 결과 |
| [기능별 구조 전환 기록](plans/functional-layout-2026-11-06.md) | PR #11에서 보존한 경계·경로 대응·테스트·편집기 점검 기록 |

날짜별 기록은 그 시점의 증거다. 현재 작업 상태는 Notion WBS와 관련 PR에서 확인한다.
