# 설계 문서 목차

프로젝트 설계 Markdown은 `docs/`에 모은다. 루트 README는 프로젝트 소개와 실행 안내를 위한 진입점이다.

```text
docs/
├── README.md
├── design/
│   └── architecture.md
├── plans/
│   └── basic-day-plan-2026-11-05.md
└── guides/
    └── git-workflow.md
```

| 문서 | 담는 내용 | 읽는 시점 |
| --- | --- | --- |
| [design/architecture.md](design/architecture.md) | Basic 범위, 기능별 모듈, 함수 계약, API, 평가, 구현 순서 | 개발 시작 전 |
| [guides/git-workflow.md](guides/git-workflow.md) | 저장소 구조, Git 추적 범위, 브랜치·커밋·PR, 결과 제출 | 협업 시작 전 |
| [plans/basic-day-plan-2026-11-05.md](plans/basic-day-plan-2026-11-05.md) | 2026-11-05 Basic을 진행할 3명의 담당·연결 계약·시간표·완료 기준 | 해당 작업 시작 전 |

## 문서 관리 규칙

1. 파일명은 영문 소문자와 하이픈을 사용한다. 계속 갱신하는 `design/`, `guides/` 문서는 파일명을 고정하고 본문에 작성일·수정일을 표시한다. `final`, `v2`, 사람 이름을 붙인 설계 사본을 만들지 않고 Git 이력으로 변경을 관리한다.
   날짜별 작업계획·회의록은 해당 용도 폴더에 두고 `basic-day-plan-2026-11-05.md`처럼 파일명 뒤에 기록 대상 날짜를 `YYYY-MM-DD`로 붙인다. `README.md`는 목차로 이름을 유지한다.
2. 코드 변경으로 설계·함수 계약이 달라지면 관련 문서를 같은 PR에서 갱신한다.
3. 문서를 추가하거나 옮기면 이 목차와 관련 링크를 함께 수정한다. 저장소 안의 링크는 상대 경로를 사용한다.
4. 현재 설계와 실제 구현 상태를 구분한다. 미구현 기능과 검증 전 설정을 완료된 것처럼 표시하지 않는다.
5. API·평가 설명이 커지면 `design/api-contract.md`, `design/evaluation-plan.md`로 분리하고 기존 문서는 링크로 연결한다. 지금은 시스템 설계에 함께 관리한다.
6. 이슈 진행 상황과 작업 담당은 GitHub Issues·PR에, 실험 결과와 최종 선택 근거는 `reports/`에 기록한다.

[프로젝트 안내로 돌아가기](../README.md)
