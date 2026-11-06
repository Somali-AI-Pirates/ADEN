# 개발 환경과 검사

수정일: 2026-11-06

Python·의존성·편집기·검사의 사용법을 관리한다. 협업·PR·완료 기준은 [CONTRIBUTING](../../CONTRIBUTING.md), 데이터·색인 작업은 [데이터 안내](../../data/README.md), 실제 평가 조건은 [평가 가이드](c-module-guide.md)를 따른다.

## 1. 환경과 설치

모든 명령은 저장소 루트에서 실행한다. 프로젝트는 Python 3.11 이상을 선언하지만 공통 개발·CI 기준은 **Python 3.13**이다. `.python-version`과 CI도 3.13 기준이며 선언한 모든 버전의 호환성을 검증했다는 뜻은 아니다. 실제 선언·의존성은 [pyproject.toml](../../pyproject.toml)이 기준이다.

Python 3.13을 선택하고 환경을 만든다.

```bash
python -m venv .venv
```

Linux·macOS에서는 다음으로 활성화한다.

```bash
source .venv/bin/activate
```

Windows PowerShell에서는 다음을 사용한다.

```powershell
.venv\Scripts\Activate.ps1
```

활성화 후 설치한다. 현재 CI도 pip 설치 경로를 사용하며, CPU용 PyTorch를 별도 설치하는 세부 절차는 [CI 설정](../../.github/workflows/ci.yml)을 참고한다.

```bash
python -m pip install -e '.[test]'
```

uv를 사용하면 [uv.lock](../../uv.lock)의 잠근 버전으로 설치할 수 있다.

```bash
uv sync --extra test --locked
```

기존 환경의 추가 도구를 유지하려면 `--inexact`를 덧붙인다. 잠금 파일은 직접 편집하지 않는다. 의존성이 바뀌면 `pyproject.toml`과 함께 `uv lock`으로 갱신하고 `uv lock --check`로 정합성을 확인한다. 현재 CI가 잠금 파일로 설치한다고 해석하지 않는다.

서버 시작은 [README 빠른 시작](../../README.md#빠른-시작)을 따른다. `uv run server`는 자동 재시작 없이, `uv run dev`는 코드 변경 시 재시작하며 실행한다. pip로 editable 설치하고 가상환경을 활성화한 경우에는 설치된 `server`·`dev` 명령을 직접 사용한다. `.env.example`에는 키 이름과 예시만 두고 실제 키는 로컬 환경으로 주입한다. 서버 실행은 실제 자원 준비를 시도하므로 키 없이 가능한 검사와 구분한다.

## 2. VS Code·Ruff·Pylance

- 권장 확장은 Python `ms-python.python`, Pylance `ms-python.vscode-pylance`, Ruff `charliermarsh.ruff`, EditorConfig `editorconfig.editorconfig`다.
- 인터프리터는 저장소의 `.venv`를 선택한다. pytest 검색 대상은 `tests/`다. 상세 설정은 [.vscode/settings.json](../../.vscode/settings.json)과 [권장 확장](../../.vscode/extensions.json)을 따른다.
- Ruff는 공통 린터와 포매터다. 저장 시 자동 포맷과 포맷 전용 CI 검사는 없다. 포맷할 때는 요청된 변경 범위의 파일만 선택한다.
- Pylance는 `pyproject.toml`의 Pyright `basic` 설정으로 `app/`·`shared/`를 진단한다. 정적 분석의 `pythonVersion = "3.11"`과 실제 개발·CI 인터프리터 3.13은 다른 설정이다.
- Pylance의 오류가 없다고 가정하지 않는다. 편집기 진단은 공통 CI 통과 조건이 아니며 Pyright CLI는 기본 검사 명령이 아니다. 이전 진단 수는 [구조 전환 당시 기록](../plans/functional-layout-2026-11-06.md#당시-검증-증거)에 보존한다.
- `experimental/`은 기본 Ruff 검사·pytest 수집·제품 패키징에서 제외한다. 개인 검사 도구·내부 구성은 자율이다.
- 공백·줄바꿈은 [.editorconfig](../../.editorconfig)와 [.gitattributes](../../.gitattributes), 검사 옵션은 `pyproject.toml`이 기준이다.

## 3. 검사

공통 코드의 기본 검사다.

대표 HTTP 검사는 실제 내부 파이프라인을 통과하면서 외부 자원은 가짜로 대체하고, 평가 어댑터 검사는 어댑터 계약을 직접 확인한다. 두 경로가 쓰는 공통 모델·리소스 가짜는 `tests/pipeline_fakes.py`에 둔다. 생성·검색 단위 테스트와 앱 리소스 수명주기 테스트도 각 동작을 계속 확인한다.

```bash
python -m ruff check .
python -m pytest -q tests
```

CLI·샘플·패키징 변경에는 해당 검사를 추가한다. 아래 도움말·정제본 검사·가상 샘플은 실제 API 키·원본 PDF·클라우드 색인·모델 호출을 사용하지 않는다.

```bash
python -m app.ingestion.scripts.curate --help
python -m app.ingestion.scripts.curate --check
python -m app.ingestion.scripts.prepare_knowledge --help
python -m app.indexing.scripts.build_index --help
python -m app.evaluation.scripts.eval_retrieval --help
python -m app.evaluation.scripts.run_benchmark --help
python -m app.evaluation.scripts.run_security --help
python -m app.evaluation.scripts.run_benchmark --sample --experiment-id sample-local-benchmark-001
python -m app.evaluation.scripts.run_security --sample --experiment-id sample-local-security-001
python -m build
```

샘플 ID는 예시다. 재실행할 때는 사용하지 않은 새 ID로 바꾸고 기존 결과를 보존한다.

[CI의 `checks`](../../.github/workflows/ci.yml)는 main 대상 PR·main 푸시와 수동 실행에서 위 검사 및 wheel 리소스 검사를 수행한다. 답변·방어·판정 프롬프트와 샘플 JSON이 배포 파일에 들어가고 개인 실험·테스트가 제품 wheel에 포함되지 않는지 확인한다. 검사 목록과 옵션을 바꿀 때는 CI와 이 가이드를 함께 맞춘다.

문서만 바꾸면 링크·앵커·경로·코드/CLI와 의미 일치·작업 시나리오를 확인한다. 문서 구조 정리 때문에 유료 호출이나 전체 데이터 재생성을 실행하지 않는다. 실행·검증 설정까지 바뀌면 그 설정의 실제 동작 검사도 수행한다.

실제 Qdrant·모델·Langfuse 통합과 평가 품질은 별도 실행 증거가 필요하다. 모의 샘플·도움말·패키지 빌드 통과를 실제 품질 검증으로 표시하지 않는다.

## 4. 새 코드 작성과 리팩토링

상세 책임·의존 규칙은 [채택한 설계와 가독성 규칙](../design/architecture.md#가독성과-연결-작성-규칙)을 따른다.

1. 바뀔 사용자 시나리오와 HTTP·CLI·평가 기록 계약을 확인한다. 기능은 해당 모듈, 요청 순서는 서비스, 자원 준비·해제는 기존 초기화 경로에서 수정한다.
2. 실제 호출자를 확인하고 기존 진입점을 재사용한다. 테스트 관측용 제품 옵션보다 외부 클라이언트 대체·HTTP 로그·평가 기록 같은 기존 경계를 먼저 사용한다.
3. 기본값을 서비스에서 한 번 확정하고 상태 소유자를 정한다. BM25·리랭커는 `Resources.retrieval`을 사용하며 전역에 캐시하지 않는다.
4. 외부 모델·Qdrant만 가짜로 둔 실제 파이프라인으로 답변·출처·오류·요청 ID를 확인한다. 수명주기·동시성이 바뀌면 실패 중 정리·앱별 격리·최초 동시 호출도 확인한다.
5. 계약·예시·기준 문서를 맞추고 영향 테스트를 실행한다. 검토가 안정된 뒤 최종 검사를 수행한다. 개인 실험 폴더의 구조·양식은 바꾸지 않는다.

전체 제품 패키지를 설치한다. A/B/C 부분 설치와 필수 제품 모듈의 동적 대체 경로를 개발 방식으로 사용하지 않는다. 평가 CLI의 명시적 callable 입력 계약은 유지한다. 키·색인이 없을 때의 안내는 실제 준비 실패 경로에서 확인한다.

HTTP 운영 로그는 서버 CLI가 Uvicorn에 전달하는 표준 logging 설정을 사용한다. `app` 로거는 INFO이며 개발 서버가 재시작되어도 같은 설정을 적용한다. 실행·로그 설정의 기준은 [시스템 설계](../design/architecture.md#5-서버-시작과-요청-실행-흐름)다. `app.api.middleware`의 `http_request` 로그에서 요청 ID·메서드·라우트·상태 코드·처리 시간을 확인하며 질문·인증 헤더·쿼리 문자열을 추가하지 않는다. 요약 로그·Langfuse·평가 RunRecord의 차이는 [공통 계약](../design/contracts.md#기록의-소비자와-시간-범위)을 따른다.
