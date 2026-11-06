# 문서와 기록 안내

수정일: 2026-11-06

ADEN은 4명이 2026-11-05~11-13에 진행하는 Basic 해운 RAG Q&A 프로젝트다. 구현과 기술 규약은 저장소에서 관리하고, 일정·회의·진행 상태는 팀 Notion에서 관리한다. 같은 내용을 여러 곳에 복제하는 대신 기준 위치를 연결한다.

## 어디서 무엇을 확인하나

| 정보 | 기준 위치 | 관리 방식 |
| --- | --- | --- |
| 실행·기능 구조·현재 검증 상태 | [프로젝트 README](../README.md), 아래 설계·가이드 | 코드와 같은 PR에서 갱신 |
| 타입·API·호출·로그 계약 | [공통 계약](design/contracts.md), 실제 `shared/`·구현·테스트 | 불일치는 수정 대상이며 변경 제안은 채택 상태와 구분 |
| 진행 작업·담당·목표일·완료 증거, WBS | [ADEN 팀 홈](https://app.notion.com/p/6e9b8d4a06f782039952010095ade408)의 [기존 WBS & Tasks](https://app.notion.com/p/4b8b8d4a06f782d1a01d01c2e3aee28b) | 해당 ADEN 작업 항목에 GitHub Issue/PR·결과 링크 연결 |
| 목표·역할·일정 변경, 회의·결정·문제 해결 | 팀 Notion의 [회의록](https://app.notion.com/p/0aab8d4a06f7833c99d181a4046c952d) | 이유·영향·결정 상태를 남기고 기술 규약 변경은 PR에 반영 |
| 매일 개인 작업일지·TIL | [개인 작업일지 DB](https://app.notion.com/p/497b8d4a06f78349bf4c01df787dba65)와 [TIL 제출](https://kant.spartaclub.kr/) | 작업·문제·해결 시도·다음 작업·산출물 링크를 기록. 공식 일지는 본인이 작성하고 개인 실험의 자유 형식과 구분 |
| 막힘·부재·검토 요청 알림 | 팀 Slack | 결정·완료의 근거는 Notion 또는 PR에 남기고 알림에서 연결 |
| 개인 탐색·노트북·자유로운 작업 로그 | [experimental/](../experimental/README.md) | 개인 내부 구성·필수 파일·양식 없음 |
| 공식 비교·제출 결과 | `reports/` 및 보존한 `artifacts/` | [데이터 안내](../data/README.md)와 [평가 가이드](guides/c-module-guide.md)의 재현 정보 연결 |

2026-11-06에 소말리아 AI 해적단의 ADEN 팀 홈과 기존 WBS·회의록·개인 작업일지의 위치를 확인했다. Notion에서 저장소·기술 문서를 찾을 때는 [Github Rules](https://app.notion.com/p/5ddb8d4a06f783d2910b01949a1052ca)를 이용한다. 기존 DB와 기록을 계속 사용하고 저장소에 별도 현행 작업판을 만들지 않는다. 팀 홈의 역할표와 일부 DB 선택지에는 템플릿 예시가 남아 있으므로 이를 실제 담당 배정으로 해석하지 않는다. 리더·통합 담당·백업·실행 예산의 확정 여부는 여전히 팀 확인이 필요하다.

개인 실험의 자율성과 교육 과정의 필수 기록은 별개다. 개인 폴더에 정해진 일지 양식을 만들 필요는 없지만, 공식 안내의 일일 작업일지·TIL 의무는 따른다. 프로젝트 기간에는 데일리 퀘스트가 중단된다. 일정·기록 요건의 근거는 [공식 프로젝트 공지](https://2026kant.slack.com/archives/C0BBUDU7AEQ/p1793837232391099)와 [과제 안내](https://app.notion.com/p/teamsparta/3-RAG-Q-A-40e2dc3ef514802f9c64c57bfb19336c?source=copy_link)다.

## 작업별 읽을 문서

처음 참여·복귀하면 [README](../README.md) → 이 목차 → [협업 가이드](guides/git-workflow.md)를 읽고 현재 작업판과 관련 PR을 확인한다.

| 문서 | 기준 내용 | 읽는 시점 |
| --- | --- | --- |
| [design/architecture.md](design/architecture.md) | 기능 흐름·의존 방향·현재 구현과 목표·통합 순서 | 기능 개발·검토 전 |
| [design/contracts.md](design/contracts.md) | 공통 타입·API·함수·Resources·로그·실행 계약 | 인터페이스 변경·연결 전 |
| [design/knowledge-data.md](design/knowledge-data.md) | 세 난이도의 정제 지식·출처·규칙 참조·Git 관리·검색 연결 경계 | 데이터 준비·규칙 검토 전 |
| [정제 데이터 안내](../data/curated/README.md) | 실제 규칙·행별 기록·사례 색인과 검토 상태·재현 방법 | 데이터 확인·수정 전 |
| [guides/git-workflow.md](guides/git-workflow.md) | 작업 추적·부재 인계·실험 채택·PR·완료·운영 절차 | 협업·PR·공식 실행 전 |
| [guides/c-module-guide.md](guides/c-module-guide.md) | 평가·보안 CLI·샘플·연결 조건·미완료 항목 | 평가·보안 작업 전 |
| [데이터 안내](../data/README.md) | 원본·질문/정답 분리·색인·공식 결과 재현 정보 | 데이터·색인·평가 변경 전 |
| [개인 실험 안내](../experimental/README.md) | 개인 공간 자율성과 제품에 채택하는 경계 | 개인 실험·공통 코드 반영 전 |
| [AGENTS.md](../AGENTS.md) | 에이전트가 작업별 기준 문서를 읽고 검증·보고하는 방법 | 에이전트 작업 시작 시 |
| [plans/basic-day-plan-2026-11-05.md](plans/basic-day-plan-2026-11-05.md) | 11월 5일 당시 계획과 현행 안내의 구분 | 과거 결정 맥락 확인 시 |
| [plans/structured-data.md](plans/structured-data.md) | 데이터 작업 브랜치의 범위·검토·검증 기록 | 이번 변경 검토 시 |

## 문서 관리 규칙

1. 계속 갱신하는 `design/`·`guides/` 문서는 파일명을 고정하고 본문에 수정일을 표시한다. `final`, `v2`, 사람 이름을 붙인 설계 사본 대신 Git 이력으로 관리한다. 날짜별 계획은 대상 날짜를 파일명에 보존한다.
2. 코드 변경으로 동작·설계·계약이 달라지면 해당 기준 문서를 같은 PR에서 갱신한다. 필드 상세는 계약 문서에 두고 다른 문서는 요약과 링크로 연결한다.
3. 문서를 추가·이동하면 이 목차와 관련 링크를 함께 수정한다. 저장소 내부 링크는 상대 경로를 사용한다.
4. **현재 구현·검증 결과 / 채택한 목표 / 제안·미합의 / 과거 기록**을 구분한다. 미구현 기능·실행하지 않은 평가·미합의 역할을 완료·확정으로 쓰지 않는다. 문서와 코드가 다르면 실제 동작을 확인하고 관련 기준을 함께 정정한다.
5. 기능별 초기 주담당은 연락 지점이며 독점 수정 권한이 아니다. 공통 계약·여러 영역에 걸친 변경은 관련 구성원과 검토한다.
6. 개인 실험의 형식은 정하지 않는다. 채택한 코드와 결과는 각각 `app/`·`shared/` 및 `reports/`로 통합한다. 공식 기록의 위치와 의무는 위 표를 따른다.

## 에이전트가 문서를 읽도록 연결하기

루트 [AGENTS.md](../AGENTS.md)가 공통 진입점이다. Codex는 프로젝트 루트부터 작업 디렉터리까지 지침 파일을 읽고, 이 파일은 작업별로 필요한 기준 문서를 추가로 읽도록 안내한다. 모든 문서를 항상 자동으로 읽었다고 가정하지 않는다. 지침을 추가한 뒤 이미 열린 Codex 작업에는 파일을 읽도록 지정하거나 새 세션에서 적용 여부를 확인한다. 다른 에이전트 도구는 해당 도구가 지원하는 지침 설정에서 루트 파일을 참조한다. [OpenAI 공식 AGENTS.md 안내](https://learn.chatgpt.com/docs/agent-configuration/agents-md)를 참고했다(확인: 2026-11-06).

이 규모에서는 추가 위키·문서 프레임워크·별도 ADR 체계를 도입하지 않는다. [Diátaxis의 문서 역할 구분](https://diataxis.fr/start-here/)과 [GitHub Flow의 PR 중심 협업](https://docs.github.com/en/get-started/using-github/github-flow)을 참고해 기존 목차·가이드·Notion·PR로 운영한다. 규칙이 실제 작업을 막거나 같은 사실을 중복 관리하게 되면 관련 PR에서 단순화한다.

[프로젝트 안내로 돌아가기](../README.md)
