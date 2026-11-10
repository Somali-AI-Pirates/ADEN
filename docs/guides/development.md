# 개발 가이드

## 설치

저장소 루트에서 실행한다. 공통 개발·CI 환경은 Python 3.13이다.

```bash
uv sync --extra test --locked
```

pip를 쓰는 경우 Python 3.13 가상환경을 활성화하고 `python -m pip install -e '.[test]'`로 설치한다. 각 가이드의 `python` 명령은 가상환경을 활성화한 뒤 실행하거나 앞에 `uv run`을 붙인다.

실제 RAGAS 평가를 실행할 때는 `eval` 추가 설치를 함께 받는다: `uv sync --extra test --extra eval --locked` (pip: `python -m pip install -e '.[test,eval]'`). RAGAS 버전은 점수 재현을 위해 고정돼 있다.

VS Code에서 저장소 루트를 열고 [권장 확장](../../.vscode/extensions.json)을 설치한다. Python 확장은 루트의 `.venv`를 자동으로 찾는다. 다른 환경이 선택돼 있으면 `Python: Select Interpreter`에서 프로젝트 가상환경을 선택한다. [저장소 설정](../../.vscode/settings.json)은 테스트 탐색기에 pytest를 켜고 Python 기본 포매터를 Ruff로 지정한다. 테스트 검색 범위와 Ruff 규칙은 [pyproject.toml](../../pyproject.toml)에서 관리한다.

의존성은 [pyproject.toml](../../pyproject.toml)에서 수정하고 `uv lock`으로 [잠금 파일](../../uv.lock)을 갱신한다. `uv lock --check`로 일치를 확인한다. CI는 [워크플로](../../.github/workflows/ci.yml)의 pip 설치 경로를 사용한다.

## 서버 실행

[설치](#설치) 후 [환경변수 예시](../../.env.example)를 참고해 로컬 `.env`와 Advanced 설정을 준비한다. A 없이 B·C를 먼저 확인하는 방법과 메모리 색인의 같은 프로세스 적재 조건은 [로컬 B·C 안내](local-bc.md)를 따른다. 기본 앱은 색인 미준비 시 503을 반환하며 임의의 가상 근거로 답변하지 않는다.

```bash
uv run fastapi dev
```

자동 재시작 없이 로컬에서 실행하려면 `uv run fastapi run --host 127.0.0.1`을 사용한다. pip 환경에서는 `fastapi dev`를 직접 실행한다. [Swagger UI](http://127.0.0.1:8000/docs)에서 질문하고 `GET /health`의 `query_ready`로 질의 준비 여부를 확인한다.

키는 로컬 `.env`, 환경변수, 승인된 CI 비밀 설정으로 주입한다. 키를 Git·로그·문서·채팅에 기록하지 않는다. 노출이 의심되면 제공자에서 폐기·교체하고 팀에 알린다.

## 검사

```bash
python -m ruff check .
python -m pytest -q tests
```

- 기능 변경은 실제 진입점의 응답·출처·오류·자원 정리를 확인한다. 모델과 Qdrant처럼 외부 경계만 대체하고, 테스트는 공개 동작과 실패 위험을 기준으로 작성한다. 내부 호출 순서와 설정값을 그대로 복제하지 않는다.
- CLI·패키징을 바꾸면 해당 명령의 `--help`, 실제 연결이나 샘플, `python -m build` 등 관련 검사를 추가한다. [CI의 checks](../../.github/workflows/ci.yml)가 전체 목록을 관리한다. 평가 가상 실행은 [샘플 절차](c-module-guide.md#가상-샘플)를 사용한다.
- 문서 변경은 링크·앵커와 실제 명령·동작이 맞는지 확인한다. 실패나 미실행 검사는 검증 결과에 구분한다.
- Pylance는 편집기 진단용이다. CI는 Ruff와 pytest를 사용하며 `experimental/`은 공통 검사·패키징에서 제외한다. 휠 검사는 필수 코드·리소스의 포함 여부를 확인한다. 실행은 저장소 체크아웃과 editable 설치를 기준으로 한다.
