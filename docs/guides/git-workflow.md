# Git 협업과 실험 운영

작성일: 2026-11-05 · 수정일: 2026-11-06

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

커밋은 `종류(영역): 변경 내용`으로 작성한다. PR에는 변경 전 문제와
변경 후 동작, 확인 방법·결과, 공통 계약·설정 영향, 관련 작업을 설명한다.
코드·설정 PR은 작성자 외 팀원 1명의 리뷰를 기본으로 한다.
문서만 바꾸는 PR은 CI 통과 후 작성자가 병합할 수 있다.
`docs/`·README와 함께 코드·설정·프롬프트가 바뀌면 해당 코드 PR에 포함한다.

검사와 리뷰를 확인한 뒤 squash merge하고 브랜치를 삭제한다. 다음 작업은
최신 main에서 새 브랜치를 만든다. 이미 squash 병합된 작업 브랜치는
재사용하지 않는다. main 직접 변경·강제 푸시·삭제는 하지 않는다.

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

저장소 루트의 가상환경에서 실행한다.

```bash
python -m pip install -e '.[test]'
python -m ruff check --select E9,F63,F7,F82 .
python -m pytest -q tests
python -m app.indexing.scripts.build_index --help
python -m app.evaluation.scripts.eval_retrieval --help
python -m app.evaluation.scripts.run_benchmark --help
python -m app.evaluation.scripts.run_security --help
python -m app.evaluation.scripts.run_benchmark --sample
python -m app.evaluation.scripts.run_security --sample
python -m build
```

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
