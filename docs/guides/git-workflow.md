# Git 협업과 실험 운영

작성일: 2026-11-05 · 수정일: 2026-11-06

## 현재 운영 기준 — 2026-11-05~13 (2026-11-06 확인)

작업 위치와 Notion·Slack 기록의 구분은 [문서·기록 안내](../README.md)를 따른다. 진행 작업은 [ADEN 팀 Notion의 기존 WBS & Tasks](https://app.notion.com/p/4b8b8d4a06f782d1a01d01c2e3aee28b)에 연결하고, 회의록·개인 작업일지는 문서 목차의 실제 링크를 사용한다. 역할표의 템플릿 예시는 실제 배정으로 사용하지 않는다.

- WBS 작업마다 합의된 담당자, 목표일, 목표·산출물, 완료 증거, PR, 상태와 다음 조치를 기록한다. 담당자·리더·백업·새 개인별 배정이 미정이면 `팀 확인 필요`로 둔다. README의 A/B/C는 연락 지점이며 네 번째 구성원의 역할을 정하지 않는다.
- 범위·일정·비용 결정은 Notion에 이유·영향·후속 조치와 함께 남기고, 기술 계약·설정 변경은 관련 호출자와 제공자가 PR에서 조정해 문서에 반영한다. 결정이 미정이면 임의로 확정하지 않는다.
- 부재 인계에는 마지막 상태, 브랜치·PR, 변경·검증 결과, 막힌 점과 다음 조치를 남긴다. 백업은 팀이 확인하기 전까지 지정하지 않는다.
- PR 완료는 요청 범위의 변경, 필요한 검증·문서 증거, 관련 리뷰가 끝나고 변경이 병합된 상태다. 이는 해당 WBS 작업이나 전체 프로젝트 완료를 뜻하지 않는다. 작업은 합의된 완료 증거를 WBS에 연결한 뒤 닫는다. Basic 프로젝트 완료에는 합의된 범위의 실제 서비스 실행, 세 검색 방식 비교, 보안 비교, 재현 가능한 결과, 최종 시연과 확인된 커밋을 포함한다. 세부 수량·완료 기준은 WBS에서 확인하고, 빠진 항목은 완료로 표시하지 않는다.

## 1. 공통 구조와 책임

제품 코드는 `app/`의 기능별 모듈에서 함께 관리한다. 전체 구조는
[시스템 설계](../design/architecture.md), 입출력·호출·기록 형식은
[공통 계약](../design/contracts.md)을 기준으로 한다.

- `app/ingestion`, `indexing`, `retrieval`: 데이터 처리와 검색.
- `app/generation`, `service`, `api`: 근거 선택·답변 생성과 서비스 연결.
- `app/observability`, `evaluation`: 실행 기록·품질·보안 평가.
- `shared/`: 공통 타입·설정·예외. 기능 모듈에 역으로 의존하지 않는다.
- `tests/`: 기능별 테스트와 기능 사이의 통합 테스트.

기존 A/B/C 구분은 초기 작업과 연락·검토 대상을 설명하는 용도로만 사용한다.
담당자가 폴더를 독점하지 않는다. 다른 기능을 함께 수정할 때는 변경 의도와
연결 영향을 공유하고 해당 기능을 아는 팀원이 검토한다. 공통 타입·설정이
바뀌면 호출부·테스트·문서를 같은 PR에서 맞춘다.

Basic·Intermediate·Advanced는 데이터와 설정으로 구분한다. 난이도별로
제품 코드를 복제하거나 장기 브랜치·별도 저장소를 만들지 않는다.

## 2. 개인 실험과 공통 코드 반영

개인 공간은 `experimental/<GitHub 사용자명>/`이다. 그 안의 폴더, 파일,
도구, 작업일지와 기록 양식은 자율이며 필수 양식을 두지 않는다.
[개인 실험 공간 안내](../../experimental/README.md)를 함께 참고한다.

이 자율성은 개인 실험 공간의 내부 구성과 기록 형식에 적용한다. 팀 업무의
일일 작업 기록과 TIL은 [문서·기록 안내](../README.md)에 안내된 필수 기록이며,
이를 `experimental/`에 두거나 그 안의 양식을 바꾸도록 요구하지 않는다.

1. 각자 필요한 탐색·비교·노트북·작업 기록을 개인 공간에 둔다.
2. 실험은 공통 `app/`·`shared/`를 가져다 사용할 수 있다. 제품 코드와
   기본 테스트·설치 과정에서는 개인 공간을 import하거나 참조하지 않는다.
3. 채택할 결과는 변경 이유와 확인한 근거를 PR에서 공유하고, 필요한
   제품 코드·테스트·공통 설정·문서를 해당 기능 위치에 반영한다.
4. 팀 비교·제출 결과는 `reports/`에 모으고 코드 버전·설정·질문셋과
   결과의 관계를 남긴다. 개인 기록 양식은 그대로 두어도 된다.

기본 CI는 개인 실험을 Ruff 검사·pytest 기본 수집·제품 패키징에서 제외한다.
개인 실험에 필요한 검사와 의존성 관리는 각자 선택한다. 공통 설치 의존성에는
제품 코드 또는 공통 검사에 채택한 의존성만 반영한다.

## 3. Git 추적과 결과 기준

| 대상 | 관리 위치와 추적 기준 |
| --- | --- |
| 제품 소스·테스트·프롬프트·설계 | `app/`, `shared/`, `tests/`, `docs/`에 포함 |
| 개인 실험 코드·메모·작업일지 | `experimental/<사용자>/`에 필요에 따라 포함 |
| 공통 실행 설정·설치 의존성 | `configs/`, `pyproject.toml`에 포함 |
| 제공 원본 PDF·평가 JSON | `data/`의 안내·자리표시만 추적, 실제 자료 제외 |
| 추출 텍스트·청크·색인·모델·캐시 | 로컬 산출물로 관리, Git 제외 |
| 전체 실행 기록·가상 샘플 결과 | `artifacts/`에 생성, Git 제외 |
| 정리한 팀 비교·제출 결과 | `reports/`에 선별 포함 |
| 실제 키·인증정보·가상환경 | Git 제외, PR·문서·로그에 넣지 않음 |

실제 제외 규칙의 기준은 루트 `.gitignore`다. 같은 자료를 개인 실험 공간에
복제하기보다 기존 데이터·산출물을 참조한다. Git은 빈 폴더를 기록하지
않으므로 사용자 폴더의 `.gitkeep`은 위치 표시일 뿐이며 삭제할 수 있다.

팀 비교·제출에 채택할 결과에는 실제 실행 코드 SHA, 미커밋 변경 여부, 데이터·질문 ID,
청킹·모델·색인·프롬프트·설정 버전, 실행 명령과 결과 위치를 기록한다.
실행 후 결과를 추가한 커밋과 실제 실행 코드 SHA는 다를 수 있다.
프롬프트나 비교 조건이 바뀌면 새 실험 ID로 저장해 이전 결과를 보존한다.
선택한 질문의 실패·평가 불가 사유도 함께 남기며 가상 샘플은 실제 성능과
구분한다. 자세한 평가 방식은 [시스템 설계](../design/architecture.md)를 따른다.

## 4. 작업 브랜치와 PR

`main`에서 작업별 브랜치를 만들고 작은 검토 가능한 단위로 PR을 올린다.
사람별 상시 브랜치나 `develop`은 두지 않는다.

| 접두어 | 용도 | 예시 |
| --- | --- | --- |
| `feat/` | 기능 추가 | `feat/hybrid-search` |
| `fix/` | 오류 수정 | `fix/citation-validation` |
| `refactor/` | 구조 정리 | `refactor/functional-layout` |
| `docs/` | 문서만 변경 | `docs/evaluation-guide` |
| `chore/` | 설치·도구·CI | `chore/test-checks` |

새 브랜치를 처음 원격에 올릴 때는 현재 브랜치에 upstream을 연결한다. `git push -u origin <현재브랜치>`는 형식 예시이며, 꺾쇠 괄호를 실제 브랜치 이름으로 바꿔 실행한다. upstream을 연결한 뒤 자기 브랜치의 새 커밋을 받을 때는 `git pull --ff-only`를 사용한다. `main`의 변경을 작업 브랜치에 반영하는 절차는 다음 절의 `git fetch origin` 후 `git merge origin/main`을 따른다.

커밋은 `종류(영역): 변경 내용`으로 작성한다. PR에는 변경 전 문제와
변경 후 동작, 확인 방법·결과, 공통 계약·설정 영향, 관련 작업을 설명한다.
코드·설정·API·공통 계약·검사 규칙을 바꾸는 PR은 작성자 외 관련 팀원
1명의 리뷰를 받는다. 공유 계약은 영향받는 호출자 또는 제공자가 리뷰한다.
공유 운영 규칙이나 기술 기준을 바꾸는 문서 PR도 관련 팀원 1명의 리뷰를
받는다. 맞춤법·링크처럼 의미를 바꾸지 않는 문서 수정은 CI 통과 후
작성자가 병합할 수 있다.
`docs/`·README와 함께 코드·설정·프롬프트가 바뀌면 해당 코드 PR에 포함한다.

2026-11-06 확인 기준으로 GitHub `main` 브랜치 보호와 필수 상태 검사는
설정되어 있지 않다. 따라서 아래 점검은 GitHub가 강제하지 않으며 병합자가
수동으로 확인한다.

1. PR의 최신 커밋에서 `checks` CI가 통과했는지 확인한다.
2. 위 기준에 해당하는 PR에는 작성자 아닌 리뷰어의 확인이 있는지 확인한다.
3. 관련 WBS 항목과 확인 결과를 연결한 뒤 squash merge하고 브랜치를 삭제한다.

코드·문서는 항상 PR로 `main`에 반영하며 직접 수정하지 않는다. 보호 규칙이
설정되면 실제 GitHub 설정을 확인해 이 수동 절차를 조정한다. 다음 작업은
최신 `main`에서 새 브랜치를 만든다. 이미 squash 병합된 작업 브랜치는 재사용하지 않는다.
강제 푸시·`main` 삭제는 하지 않는다.

## 5. 기존 작업과 새 구조 맞추기

진행 중인 로컬 작업은 커밋하거나 안전하게 보관한 뒤 원격 변경을 받는다.
작업 브랜치에서 `git fetch origin`, `git merge origin/main`으로 갱신하며,
문서만 고르는 대신 그 시점까지 main의 코드·설정 변경도 함께 받는다.
공유 이력을 강제로 다시 쓰거나 다른 사람의 변경을 일괄 덮어쓰지 않는다.

이번 전환의 기존 경로와 새 경로는 다음과 같다. 아래 기존 경로는 이전
작업을 연결하기 위한 설명이며 현재 import·실행 경로로 사용하지 않는다.

| 이전 | 현재 |
| --- | --- |
| `member_ysl727727/ingestion`, `indexing`, `retrieval` | `app/` 아래 같은 기능 이름 |
| `member_chsopo89/api`, `generation`, `service` | `app/` 아래 같은 기능 이름 |
| `member_Damsebi/observability`, `evaluation` | `app/` 아래 같은 기능 이름 |
| A의 `scripts/build_index.py` | `app/indexing/scripts/build_index.py` |
| A의 `scripts/eval_retrieval.py` | `app/evaluation/scripts/eval_retrieval.py` |
| C의 일반·보안 평가 스크립트 | `app/evaluation/scripts/` |
| B 답변 프롬프트·C 방어 프롬프트 | `app/generation/prompts/` |
| C 판정 프롬프트·공용 샘플 | `app/evaluation/prompts/`·`samples/` |
| 각자의 `tests/` | 공통 `tests/`의 기능·통합 테스트 |

이전 경로의 import·모듈 명령·테스트 mock은 새 경로에 맞춘다.
`main:app`, API 응답, 공통 함수·기록 계약, 설정과 프롬프트 내용은 유지한다.
`data/`, `artifacts/`, `reports/`와 기존 Qdrant 색인을 그대로 사용하며,
구조 변경 때문에 색인을 다시 만들거나 기존 결과를 덮어쓰지 않는다.
변경을 받은 뒤 공통 설치와 검사를 다시 실행한다.

## 6. 공통 검사와 실행 확인

프로젝트는 Python 3.11 이상을 선언하지만 공통 개발·CI 기준은 Python 3.13이다. `.python-version`과 CI 설정도 3.13 기준이며, 3.11 이상 전체의 호환성을 검증했다는 뜻은 아니다. Python 3.13 인터프리터를 선택한 뒤 저장소 루트에서 환경을 처음 만들고 개발 의존성을 설치한다.

```bash
python -m venv .venv
```

가상환경을 활성화한 뒤 다음을 실행한다.

```bash
python -m pip install -e '.[test]'
python -m ruff check .
python -m pytest -q tests
python -m app.indexing.scripts.build_index --help
python -m app.evaluation.scripts.eval_retrieval --help
python -m app.evaluation.scripts.run_benchmark --help
python -m app.evaluation.scripts.run_security --help
python -m app.evaluation.scripts.run_benchmark --sample
python -m app.evaluation.scripts.run_security --sample
python -m build
```

VS Code를 사용할 때는 저장소 권장 확장(Python `ms-python.python`, Pylance `ms-python.vscode-pylance`, Ruff `charliermarsh.ruff`, EditorConfig `editorconfig.editorconfig`)을 설치하고 Python 인터프리터로 저장소의 `.venv`를 선택한다. 테스트 탐색은 `tests/`를 대상으로 한다. Ruff가 공통 린터와 포매터로 설정되어 있으며, 저장 시 자동 포맷과 포맷 전용 CI gate는 없다. 명시적으로 포맷할 때도 현재 작업 범위에 해당하는 파일만 선택한다. 워크스페이스 상세값은 `.vscode/`와 `pyproject.toml`을 기준으로 한다.

Pylance는 `pyproject.toml`의 Pyright `basic` 설정에 따라 `main.py`, `app/`, `shared/`를 편집기에서 진단하고 `experimental/`은 제외한다. `pythonVersion = "3.11"`은 정적 분석의 Python 버전 기준이며, 로컬 개발·CI 인터프리터 기준은 앞서 적은 3.13이다. 2026-11-06 초기 점검은 45개 파일에서 오류 15건을 보고했다. 이는 통과 결과가 아니며 편집기 진단용이다. 프로젝트의 공통 검증 절차에 Pyright CLI 검사나 CI 타입 검사 gate는 없다.

`.github/workflows/ci.yml`의 `checks`는 main 대상 PR·main 푸시에서 실행한다.
Python 3.13과 CPU용 PyTorch를 준비하고 `.[test]`를 설치한 뒤 위 검사,
공통 테스트, CLI 도움말, 가상 일반·보안 평가, 빌드와 wheel 리소스 검사를
수행한다. 실제 API 키·모델 호출·클라우드 색인·PDF 전체 임베딩은 사용하지 않는다.
빌드에서는 답변·방어·판정 프롬프트와 샘플 JSON이 포함되고 개인 실험·테스트
패키지가 제품 wheel에 들어가지 않는지 확인한다.

실제 Basic 데이터·외부 모델·Langfuse 검증은 별도 실행하고 근거를 남긴다.
자동 검사·가상 샘플 통과를 실제 서비스 품질 검증으로 표시하지 않는다.
Basic 제출 시에는 실제 실행을 확인한 커밋을 태그로 고정하고 README와
팀 작업 기록에서 결과 위치를 연결한다.

## 7. 공식 실행과 외부 자원

이 절은 공유 결과를 만드는 **팀 공식 실행**에 적용한다. 개인 실험의 내부
형식이나 기존 실행 권한에 별도 승인·기록 양식을 추가하지 않으며, 이미
명시된 실행 권한을 다시 승인받게 하지 않는다. `--sample`은
가상 자원으로 실제 모델·평가기 호출을 하지 않는다([평가 가이드](c-module-guide.md)).

팀 공식 실행 전에는 Notion 작업에 실행 담당자, 목적, 범위·데이터·질문 ID,
검색 방식, 모델·평가기와 예상 호출 수·비용, 비용 한도·중단 기준, 설정·결과
위치를 기록한다. 실행 담당자·예산 승인자·한도·중단 기준은 현재 확정되지 않았으므로
팀이 정하기 전에는 공식 유료 실행을 시작하지 않는다. 금액이나 담당자를
임의로 정하지 않는다. 계획한 범위나 합의한 한도에 도달하면 실행을 멈추고,
부분 결과를 확인한 뒤 재개 여부를 팀과 정한다.

비밀값은 로컬 `.env`, 프로세스 환경 변수 또는 승인된 CI·플랫폼 비밀 설정으로
주입한다. Git, Notion, Slack, PR, 작업 기록, 모델 입력·결과에 키를 넣지 않는다.
키 노출이 의심되면 제공자에서 즉시 폐기하고 교체한 뒤 팀에 노출 사실만 알린다.
공용 키의 발급·회수 담당자는 팀 확인이 필요하다.

Langfuse trace에는 질문, 검색·최종 문맥 본문, 실제 모델 입출력과 사용량이
포함될 수 있다([기록 계약](../design/contracts.md), [평가 가이드](c-module-guide.md)).
팀 공식 실행의 첫 전송 전 계정·프로젝트, 접근 범위와 보존 기준을 확인한다.
미확정이면 팀 공식 데이터를 전송하지 않는다. trace ID나 `flush()`만으로
완료 처리하지 말고 실제 trace 조회 결과를 기록한다.

실제 실행의 코드 SHA·변경 상태, 질문·설정·실험 ID와 결과 경로는 [평가
가이드](c-module-guide.md)에 따라 남기고 샘플과 구분한다. 공용 Qdrant 재생성은
[데이터 안내](../../data/README.md)에 따른 팀 합의가 필요하다. 담당이 정해지지
않은 재생성은 실행 전에 팀이 확인한다.
