# Git 저장소·협업 설계

작성일: 2026-11-05 · 수정일: 2026-11-06. 대상은 우선 Basic을 구현하는 해운 RAG 팀 프로젝트다. 아래 소스 구조·GitHub 설정은 구현 시 적용할 설계안이며, 현재 실제로 정리한 파일은 루트 README와 `docs/` 문서다.

관련 문서: [문서 목차](../README.md) · [시스템 설계](../design/architecture.md)

## 1. 기본 방침

하나의 GitHub 저장소에서 서비스, 데이터 처리, 검색, 평가를 함께 관리한다. 현재 연결된 저장소는 [Somali-AI-Pirates/ADEN](https://github.com/Somali-AI-Pirates/ADEN)이다.

- 사람별 최상위 패키지 안에서 기능을 모듈화하고 공통 입력·출력 계약으로 연결한다. A·B·C의 코드는 각자의 패키지 경로를 유지한 채 최종 통합한다.
- 설계 Markdown은 `docs/`에 모은다.
- 코드·설정 변경은 `main`에서 만든 작업 브랜치에서 개발하고 PR로 `main`에 병합한다.
- 문서(`docs/`, 루트 `README.md`)만 바꾸는 변경은 `main`에 직접 커밋한다. 절차는 5절을 따른다.
- 각 작업은 리뷰할 수 있는 크기로 나누고 초기부터 연결한다.
- 원본 데이터·색인·캐시는 로컬에서 관리하고, 코드·설정·선별 정리한 제출 결과는 Git에 남긴다.

Basic·Intermediate·Advanced는 같은 코드의 설정과 데이터 경로로 구분한다. 난이도별 장기 브랜치나 별도 저장소를 만들지 않는다.

## 2. 저장소 목표 구조

```text
ADEN/
├── README.md
├── main.py                         # B: 앱 생성 함수만 호출
├── pyproject.toml                  # B: 의존성 변경 취합
├── uv.lock                         # uv 채택 시 추적하는 잠금 파일
├── .env.example                    # 실제 키를 제외한 변수 안내
├── .gitignore
├── .github/                        # PR 템플릿·CI, 구현 단계에 추가
├── shared/                         # 공통 계약·설정, B가 변경 취합
│   ├── __init__.py
│   ├── contracts.py                # 청크·응답·실행 기록 타입
│   ├── settings.py
│   └── exceptions.py
├── member_ysl727727/               # A의 최종 Python 패키지
│   ├── __init__.py
│   ├── ingestion/                  # PDF 추출·청킹
│   ├── indexing/                   # 임베딩·Dense/BM25 색인
│   ├── retrieval/                  # 세 검색 방식, service.py에 retrieve()
│   ├── scripts/
│   │   └── build_index.py
│   └── tests/
├── member_chsopo89/                # B의 최종 Python 패키지
│   ├── __init__.py
│   ├── api/                        # app.py에 create_app()
│   ├── generation/                 # context.py, chain.py, citations.py
│   ├── service/
│   │   ├── query.py                # A·B·C 함수 연결
│   │   └── resources.py            # 준비된 색인·모델 로드/종료
│   ├── prompts/
│   │   └── shipping_qa.txt
│   └── tests/
├── member_Damsebi/                 # C의 최종 Python 패키지
│   ├── __init__.py
│   ├── observability/
│   │   └── tracing.py              # Langfuse·시간·토큰·오류 기록
│   ├── evaluation/
│   │   ├── dataset.py
│   │   ├── runner.py
│   │   ├── metrics.py
│   │   ├── graders.py
│   │   ├── security.py
│   │   └── report.py
│   ├── prompts/
│   │   ├── judge.txt
│   │   └── defense.txt
│   ├── scripts/
│   │   ├── run_benchmark.py
│   │   └── run_security.py
│   └── tests/
├── configs/
│   └── basic.yaml                  # B가 공통 설정 취합
├── docs/
│   ├── README.md
│   ├── design/architecture.md
│   ├── design/contracts.md          # 데이터·함수·실행 계약
│   ├── guides/git-workflow.md
│   └── plans/basic-day-plan-2026-11-05.md
├── data/
│   ├── README.md                   # A: 데이터 배치·처리 안내
│   └── basic/
│       ├── documents/              # 제공 PDF 원본, Git 제외
│       └── evaluation/             # 제공 질문·정답 JSON, Git 제외
├── presentations/                  # 발표자료 작성 시 추가
├── artifacts/                      # 추출·청크·색인·실행 기록, Git 제외
│   ├── basic/
│   └── experiments/<experiment_id>/
└── reports/<experiment_id>/         # C: 제출용 결과, Git 추적
    ├── README.md
    ├── manifest.json
    ├── comparison.csv
    ├── answers.jsonl
    ├── scores.jsonl
    └── security_comparison.csv
```

`uv.lock`은 uv를 채택할 때 사용한다. 강의 환경의 다른 패키지 관리자를 유지한다면 그 방식에 맞는 의존성 잠금 파일 하나를 사용한다. 도구별 잠금 파일을 중복 관리하지 않는다.

Git은 빈 폴더를 기록하지 않는다. 필요한 데이터 배치 위치는 `.gitkeep`으로 남길 수 있다. `artifacts/`와 저장소 내부 캐시 경로는 실행 코드가 생성하도록 한다. 미래에 쓸 빈 폴더와 빈 설정 파일을 미리 모두 만들 필요는 없다.

## 3. Git에 포함할 파일

| 구분 | Git 추적 | 이유 |
| --- | --- | --- |
| 소스·테스트·설계·프롬프트 | 포함 | 협업·리뷰·재현 대상 |
| `configs/basic.yaml`, 의존성 잠금 파일 | 포함 | 공통 실행 조건 재현 |
| `.env.example` | 포함 | 필요한 변수 이름 공유 |
| `data/README.md`, `.gitkeep` | 포함 | 자료를 받는 위치와 배치 안내 |
| 제공 ZIP·PDF·질문·정답 JSON | 제외 | 제공 자료를 별도 설치하고 원본 보존 |
| 추출 텍스트·청크·벡터 색인·모델 캐시 | 제외 | 코드와 설정으로 재생성 |
| `.env`, API 키, 로그의 인증정보 | 제외 | 비밀값은 로컬 환경변수로 관리 |
| `artifacts/` 전체 실행 파일 | 제외 | 로컬 분석용 생성물 |
| `reports/` 제출 결과 | 포함 | 발제문이 요구하는 비교 결과·근거 보존 |
| 최종 발표자료 | 포함 또는 README에 열람 링크 | 팀 Notion에서도 연결 |

`data/README.md`에는 발제문·Basic 데이터셋 링크, 해운 ZIP 2개를 푸는 위치, PDF 15개·총 360쪽 확인 방법, 색인 생성 명령을 기록한다. 실제 파일을 확보한 뒤 파일 목록과 해시를 기록한다. 확인 전 해시나 목록을 만들지 않는다.

제공 평가 JSON을 Git에서 제외하는 것만으로 평가 누수가 방지되는 것은 아니다. 색인 입력은 `documents/*.pdf`로 제한하고, 정답 파일은 답변 생성 후 평가 모듈에서만 읽는다.

### `.gitignore` 초기안

다음은 향후 생성할 설정 예시다. 현재 `.gitignore` 파일을 적용한 상태는 아니다.

```gitignore
# Python / 도구 캐시
__pycache__/
*.py[cod]
.venv/
venv/
.pytest_cache/
.ruff_cache/
.mypy_cache/
.coverage
htmlcov/
*.egg-info/
build/
dist/

# 로컬 비밀값
.env
.env.*
!.env.example

# 제공 자료: 안내와 자리표시 파일만 추적
/data/**
!/data/**/
!/data/**/.gitkeep
!/data/README.md

# 로컬 생성물과 모델/DB 저장소
/artifacts/
/models/
/storage/
/logs/
.cache/
*.log

# 운영체제 파일
.DS_Store
Thumbs.db
```

Qdrant 데이터는 예를 들어 `storage/qdrant/`에 둔다. 경로를 바꾸면 제외 규칙도 맞춘다. `*.json`, `*.csv`, `*.pdf`를 저장소 전체에서 막는 규칙은 사용하지 않는다. 설정·비교 결과·발표자료까지 누락되기 때문이다. 이미 추적 중인 파일에는 `.gitignore`가 소급 적용되지 않으므로 PR의 파일 목록을 확인한다.

## 4. 브랜치 구조

```text
main
 ├── docs/project-structure       → PR → main
 ├── feat/12-pdf-ingestion        → PR → main
 ├── feat/18-hybrid-search        → PR → main
 ├── feat/23-api-tracing          → PR → main
 └── fix/31-citation-page         → PR → main
```

작업 브랜치는 오래 유지하지 않고, 하나의 검토 가능한 기능이나 수정이 끝나면 병합한다. 첫 설계 단계의 `main`은 문서 기준선이며, 구현이 시작되면 현재까지 구현한 기능의 검사와 실행이 통과하는 상태를 유지한다.

| 접두어 | 용도 | 예시 |
| --- | --- | --- |
| `feat/` | 새 기능 | `feat/18-hybrid-search` |
| `fix/` | 오류 수정 | `fix/31-citation-page` |
| `docs/` | 문서 변경 | `docs/git-workflow` |
| `refactor/` | 동작을 유지한 구조 정리 | `refactor/retrieval-interface` |
| `test/` | 검증 추가·수정 | `test/retrieval-metrics` |
| `chore/` | 의존성·도구·CI | `chore/python-ci` |

이슈가 있으면 번호를 붙인다. 이름은 영문 소문자와 하이픈을 사용한다. 사람 이름을 붙인 상시 브랜치 대신 작업 단위로 만든다. 현재 규모에는 `develop`과 릴리스 전용 브랜치를 두지 않는다.

### 작업 시작 예시

원격 저장소와 초기 `main`이 준비된 뒤, 변경 사항을 커밋해 작업 디렉터리가 깨끗한 상태에서 실행한다.

```bash
git fetch origin
git switch main
git pull --ff-only origin main
git switch -c feat/18-hybrid-search
```

작업 파일을 확인한 뒤 관련 파일을 명시해서 커밋한다.

```bash
git status --short
git add member_ysl727727/retrieval/hybrid.py
git diff --cached
git commit -m "feat(retrieval): BM25와 Dense 결과를 RRF로 결합"
git push -u origin feat/18-hybrid-search
```

그다음 `main`을 대상으로 PR을 만든다. 위 명령은 사용 예시이며 이번 설계 작업에서 실행한 명령이 아니다.

## 5. 커밋과 PR 규칙

커밋 메시지는 `종류(영역): 변경 내용`으로 작성한다. 한 커밋에는 설명 가능한 변경을 담고, 문서와 코드가 같은 변경을 설명하면 함께 포함한다.

```text
feat(ingestion): PDF 페이지와 문서 ID를 보존해 추출
feat(retrieval): 하이브리드 검색 추가
fix(generation): 문맥에 없는 출처 ID 거부
docs(architecture): 공통 응답 타입 변경 반영
chore(deps): 실행 의존성 버전 고정
```

PR은 다음 내용을 짧게 담는다. 구현 시작 시 이 형식으로 `.github/pull_request_template.md`를 만든다.

```markdown
## 변경 내용
어떤 문제를 해결하고 어떤 동작이 바뀌는지 설명합니다.

## 확인 방법
실행한 검사와 결과를 적습니다. 실행하지 못한 항목도 적습니다.

## 공통 계약·설정 영향
입출력 타입, 설정 키, 환경변수, 데이터나 색인 재생성 필요 여부를 적습니다.

## 관련 작업
연결할 이슈와 후속 작업을 적습니다.
```

- 작성자 외 팀원 1명이 리뷰한 뒤 병합하는 것을 기본으로 한다.
- CI를 도입한 뒤에는 해당 변경의 필수 검사를 통과해야 한다.
- 병합 방식은 squash merge로 통일하고, 완료한 원격 작업 브랜치는 삭제한다.
- 각 PR을 squash merge한 뒤에는 최신 `main`에서 다음 작업 브랜치를 새로 만든다. 이미 squash된 작업 브랜치를 계속 재사용하지 않는다. 예전 커밋이 다시 PR에 나타나거나 불필요한 충돌이 생기는 것을 줄이기 위한 규칙이다.
- PR에 좋은 결과만 남기지 않고 실패·미완료 사항을 함께 적는다.
- 원본 데이터, 비밀값, 로컬 생성물이 들어오지 않았는지 파일 목록을 확인한다.
- GitHub 설정으로는 `main`의 force push와 삭제를 제한한다. 문서 직접 커밋이 가능하도록 PR 필수 규칙은 설정으로 강제하지 않고, 코드 변경의 PR·리뷰는 팀 규칙으로 지킨다. 필수 검사를 등록할 때도 문서 직접 커밋이 막히지 않는지 확인한다.

### 문서 변경을 main에 직접 반영하기

`docs/`와 루트 `README.md`만 바꾸는 변경은 PR 없이 `main`에 커밋한다. 코드·설정·프롬프트·`.gitignore`가 함께 바뀌거나, 코드 변경 때문에 설계·계약 문서를 고치는 경우는 해당 코드 PR에 포함한다.

```bash
git switch main
git pull --ff-only origin main
git add docs/ README.md
git commit -m "docs: 변경 내용"
git push origin main
```

`git add` 전에 `git status`로 문서 외 파일이 섞이지 않았는지 확인한다. 푸시가 거절되면 다른 사람이 먼저 main을 바꾼 것이므로 `git pull --ff-only origin main` 또는 `git pull --rebase origin main`으로 받은 뒤 다시 푸시하고, force push는 하지 않는다. 푸시 후 팀에 알리고, 각 담당자는 6절의 "작업 브랜치에서 main의 문서 변경 받기" 절차로 작업 브랜치에 main을 병합해 받는다.

이 문서는 위 운영 규칙을 제안한다. 실제 브랜치 보호나 리뷰어 지정은 아직 적용하지 않았다.

## 6. 모듈 협업과 충돌 처리

사람별 패키지 `member_ysl727727/`(A), `member_chsopo89/`(B), `member_Damsebi/`(C)는 담당 작업의 최종 위치이며 Basic의 import 경로로 고정한다. 대소문자까지 그대로 사용한다. 패키지명을 다시 바꾸려면 경로·import·설정·실행 명령을 같은 변경에서 맞춘다. 각 패키지에 별도의 `app/` 전체를 만들거나 공통 설정·타입을 복제하지 않는다. `shared/`, 루트 설정·의존성·메인 파일은 B가 변경을 취합하며 공통 계약이 달라지면 호출부도 함께 맞춘다.

데이터 구조·필수 필드·동기 호출·Resources·모듈 실행 방법은 [공통 입출력·호출 계약](../design/contracts.md)을 따른다. A·B의 중첩 객체와 C의 평탄 로그를 혼용하지 않고 연결부에서 명시적으로 변환한다. 임시 공통 타입과 추적 구현도 통합 시 단일 정의로 맞춘다.

| 변경 지점 | 함께 확인할 대상 |
| --- | --- |
| `shared/contracts.py` | API·검색·생성·평가의 입력과 출력 |
| `configs/basic.yaml` | 실제 로드 코드·설계 문서·실험 설정 |
| 프롬프트·청킹·임베딩 모델 | 색인 재생성 여부·기존 비교 결과의 유효성 |
| `main.py`, API 초기화 | 리소스 로드·종료·요청 추적 |
| 의존성 파일 | 잠금 파일·설치 안내·CI |

공통 타입·최소 앱 연결 PR을 먼저 병합하고 그다음 각 기능을 병렬 개발한다. 다른 미병합 PR에 의존한다면 PR 설명에 명시하고 통합 전에 의존 순서를 맞춘다.

작업 중 `main` 변경을 받아야 할 때는 커밋을 정리한 뒤 `git fetch origin`, `git merge origin/main`으로 동기화할 수 있다. 충돌은 해당 부분 담당자와 변경 의도를 확인해 해결하고 관련 검사를 다시 실행한다. 다른 사람의 변경을 일괄 덮어쓰거나 공유 브랜치 이력을 강제로 바꾸지 않는다.


### 작업 브랜치에서 main의 문서 변경 받기

`main`에 문서 변경이 커밋되어도 다른 브랜치로 자동 전파되지는 않는다. 각 담당자가 자신의 브랜치에 `origin/main`을 병합한다. 커밋된 작업은 병합 이력에 유지되며, 같은 파일·부분을 다르게 수정한 경우에는 충돌 해결이 필요하다.

이 병합은 문서만 골라 받는 명령이 아니다. 그 시점까지 main에 반영된 코드·설정 변경도 함께 가져온다.

먼저 본인의 작업 브랜치에서 `git status`를 확인하고 진행 중인 작업을 커밋하거나 stash로 보관한다. 그다음 아래 명령을 실행한다. 아래는 C의 브랜치 예시이며 A·B는 자신의 브랜치를 사용한다.

```bash
git switch feat/basic-evaluation-tracing
git fetch origin
git merge origin/main
git push origin feat/basic-evaluation-tracing
```

미커밋 작업을 잠시 보관하려면 병합 전에 `git stash push -u -m "before-main-sync"`를 실행할 수 있다. 병합 후 `git stash apply`로 복원하고 결과를 확인한다. stash를 복원할 때도 충돌이 생길 수 있다.

단순히 `git pull`만 실행하면 현재 작업 브랜치의 upstream을 받으므로, main의 변경을 가져온다는 보장은 없다. 현재 작업 브랜치에서 `git fetch origin` 후 `git merge origin/main`을 사용하면 가져올 대상을 명확히 할 수 있다.

문서는 main에 직접 커밋하는 것을 기본으로 한다(5절). main에 변경을 넣는 방법이 직접 커밋이든 PR 병합이든, 다른 작업 브랜치가 그 변경을 받는 위 절차는 같다. 기능 구현은 PR 규칙을 유지한다.

이번 커밋은 설계 문서만 수정한다. 다른 브랜치의 소스는 이동·삭제하지 않는다. 이미 예전 `app/` 구조로 작성한 소스는 문서를 병합한 뒤 각자 담당 패키지로 옮기고 import·실행 명령을 수정한다. 문서 병합만으로 코드 경로가 자동 변경되지는 않는다. 옮긴 뒤 작은 샘플로 호출과 테스트를 확인하고 코드 변경 PR로 통합한다.

## 7. CI 범위

첫 구현에서는 다음 정도를 권장한다.

- Ruff 등 선택한 도구로 소스 형식·정적 오류 검사.
- 모의 클라이언트를 이용한 API 입력·출력·오류 계약 검사.
- 작은 가상 문서로 출처 매핑과 문서 단위 Hit/Recall 계산 확인.
- 각 담당자의 `member_*/tests/`에서 실제 임베딩·LLM을 로드하지 않아도 import와 테스트가 가능한 구조 유지.

일반 PR CI에는 실제 모델 API 호출이나 PDF 전체 임베딩을 넣지 않는다. 유료 일반 평가와 보안 비교는 고정 설정으로 별도 실행하고 결과를 PR에서 검토한다. 문서만 바뀌면 문서 링크·내용 확인으로 범위를 맞춘다.

현재 CI는 `.github/workflows/ci.yml`의 `checks` 작업 하나다. main 대상 PR과 main 푸시에서 `pytest`·`ruff`만 설치해 Ruff 기본 오류 검사(`E9,F63,F7,F82`)와 `member_Damsebi/tests`를 실행한다. A·B의 테스트와 의존성이 생기면 설치 단계와 테스트 대상을 같은 파일에서 넓힌다.

## 8. 실행 결과와 제출 버전

`artifacts/`는 실행 중 생성된 전체 자료, `reports/`는 팀이 공유하고 제출할 결과다. 결과를 정리할 때 선택한 일반 질문 전체의 세 방식 결과와 실패를 포함한다. 일부 성공 질문만 고르는 것은 결과 정리 기준이 아니다.

`reports/<experiment_id>/manifest.json`에는 다음을 기록한다.

- 실행 시각, Basic 데이터 식별자와 확인된 데이터 해시.
- 실행 코드의 커밋 SHA, 설정·프롬프트 버전, 의존성 버전.
- 선택한 질문 ID와 선택 이유.
- 임베딩·리랭커·생성·평가 모델과 실제 설정.
- 공통 k·문맥 예산, 검색 방식별 후보 수·결합 설정.
- 비교 결과 파일 위치와 Langfuse 기록 확인 방법.

실험은 커밋된 코드 기준으로 실행하는 것을 권장한다. 실행 후 결과를 추가한 커밋과 실제 실행 코드 SHA는 다를 수 있으므로 둘을 혼동하지 않는다. 로컬 미커밋 변경으로 실행했다면 그 사실을 표시하고 제출 전 재현 가능한 기준으로 다시 실행한다.

`comparison.csv`에는 검색 지표, Faithfulness, Judge의 항목별 점수, 시간·오류·입력/출력 토큰과 해당 없음·평가 불가 건수를 남긴다. 실제 답변·인용·점수 사유가 길면 같은 폴더의 `answers.jsonl`, `scores.jsonl`로 연결한다. 보안 답변 4개와 노출·결과 분석도 함께 보존한다.

원본 전체 문맥과 실제 모델 입출력은 Langfuse와 로컬 실행 기록에서 추적하고, 저장소에는 제출에 필요한 결과를 남긴다. API 키와 인증정보는 포함하지 않는다. 프롬프트·설정이 바뀐 실험은 새로운 ID로 저장하여 기존 결과를 덮어쓰지 않는다.

Basic 완료 시 실행 검증을 마친 `main` 커밋에 `v0.1.0-basic` 같은 태그를 붙여 발표·제출 기준을 고정한다. 이후 확장도 작업 브랜치에서 진행하고, 발표에 사용한 태그와 결과 경로를 README와 팀 Notion에서 연결한다.

## 9. 저장소를 시작할 때 적용할 순서

1. 이번에 정리한 루트 README와 `docs/`를 초기 문서 기준선으로 사용한다.
2. 기존 `ADEN` 저장소의 `main`을 기준으로 작업 브랜치를 만들어 변경을 관리한다.
3. 최소 소스 구조, 공통 타입, 의존성, `.gitignore`, `.env.example`을 추가한다.
4. PR 템플릿과 실행 가능한 CI를 추가한 뒤 `main` 보호 규칙을 설정한다.
5. 작은 이슈 단위로 모듈 작업을 나누고 질문 하나의 전체 흐름부터 연결한다.
6. 평가 결과를 `reports/`에 정리하고 Basic 제출 기준 커밋에 태그를 붙인다.

설계 문서는 병합된 `main`의 `docs/`에서 관리한다. CI·브랜치 보호·제출 태그는 해당 구현 단계에서 적용한다.
