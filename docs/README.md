# 설계 문서 목차

프로젝트 설계 Markdown은 `docs/`에 모은다. 루트 README는 프로젝트 소개와 바로 실행할 명령을 안내한다.

```text
docs/
├── README.md
├── design/
│   ├── architecture.md
│   └── contracts.md
├── plans/
│   └── basic-day-plan-2026-11-05.md
└── guides/
    ├── git-workflow.md
    └── c-module-guide.md
```

| 문서 | 기준 내용 | 읽는 시점 |
| --- | --- | --- |
| [design/architecture.md](design/architecture.md) | Basic 범위, 기능별 구조, 현재 동작·검증 상태, 통합 순서 | 개발·검토 전 |
| [design/contracts.md](design/contracts.md) | 공통 타입·로그 형식·API·함수·Resources·실행 계약 | 인터페이스 변경·연결 전 |
| [guides/git-workflow.md](guides/git-workflow.md) | 기능별 경로, 협업·실험·검토·결과 보존 절차 | 협업·PR 전 |
| [guides/c-module-guide.md](guides/c-module-guide.md) | 평가·보안 실행기, 샘플, 서비스 어댑터 연결 방법 | 평가·보안 작업 전 |
| [plans/basic-day-plan-2026-11-05.md](plans/basic-day-plan-2026-11-05.md) | 2026-11-05 원안과 현행 실행안의 구분 | 과거 계획과 현재 상태 비교 시 |

## 문서 관리 규칙

1. 계속 갱신하는 `design/`·`guides/` 문서는 파일명을 고정하고 본문에 작성일·수정일을 표시한다. `final`, `v2`, 사람 이름을 붙인 설계 사본을 만들지 않고 Git 이력으로 관리한다. 날짜별 계획은 기록 대상 날짜를 파일명에 보존한다.
2. 코드 변경으로 현재 동작·설계·함수 계약이 달라지면 해당 문서를 같은 변경에서 갱신한다.
3. 문서를 추가하거나 옮기면 이 목차와 관련 링크를 함께 수정한다. 저장소 내부 링크는 상대 경로를 사용한다.
4. 현재 동작, 채택한 설계, 제안, 과거 사실을 구분한다. 과거 계획과 결과에는 시점을 표시하고 미구현 기능이나 검증 전 설정을 완료된 것처럼 쓰지 않는다.
5. 상세 데이터·함수 계약은 [design/contracts.md](design/contracts.md)를 기준으로 한다. 다른 문서는 요약과 링크를 둔다. `shared/contracts.py`·샘플·문서 계약은 함께 맞춘다.
6. 현재 기능 영역별 연락 담당자와 초기 주담당은 협업 연락 지점이며 폴더별 독점 소유권을 뜻하지 않는다. 여러 영역의 변경은 관련 구성원이 함께 조정한다.
7. 개인 실험은 선택적으로 `experimental/<GitHubID>/`에 둘 수 있다. 하위 구조·파일·기록 양식은 정하지 않는다. 제품 모듈은 이 경로를 참조하지 않으며, 채택한 코드와 결과는 각각 `app/`·`shared/` 및 `reports/`로 통합한다.

[프로젝트 안내로 돌아가기](../README.md)
