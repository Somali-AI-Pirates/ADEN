# ADEN — Basic 해운 RAG Q&A

제공된 Basic 해운 PDF를 근거로 답변하고, 출처와 처리 상태를 제공하는 팀 프로젝트다. 구조 변경은 패키지 위치와 import 경로를 기능별로 정리하며 기존 API 계약, 설정, 데이터, 색인, 산출물의 위치와 의미를 유지한다.

## 현재 상태

- PDF 추출·청킹, E5 임베딩과 Qdrant 색인, Naive 검색, 답변 생성·API, 실행 기록·평가 모듈이 있다.
- `hybrid`와 `hybrid_rerank`는 선언되어 있으나 구현되지 않았다. 실제 세 방식 비교와 보안 비교 결과도 아직 없다.
- 가상·모의 인프라를 사용하는 테스트 92개와 하위 테스트 9개는 통과했다. 실제 Qdrant 색인과 생성 모델을 사용한 전체 서비스 통합 검증은 아직 실행하지 않았다.
- Python 테스트 명령은 저장소 루트에서 `python -m pytest -q tests`다.

## 기능 구조

```text
app/
├── ingestion/       # PDF 페이지 추출·청킹
├── indexing/        # 임베딩·Qdrant 색인·색인 생성 CLI
├── retrieval/       # 검색 방식과 retrieve() 진입점
├── generation/      # 문맥 구성·답변 생성·프롬프트
├── service/         # Resources와 요청 흐름 연결
├── api/             # FastAPI 앱과 라우트
├── observability/   # 요청 기록·Langfuse 추적
└── evaluation/      # 평가·보안 비교·실행 CLI
shared/              # 공통 계약·설정·예외
tests/               # 기능별·통합·패키지 구조 테스트
experimental/<GitHubID>/  # 개인 실험 공간, 제품 코드에서 참조하지 않음
```

현재 기능 영역별 연락 담당자와 초기 주담당은 다음과 같다. 이는 폴더별 독점 수정 권한을 뜻하지 않는다. 계약이나 여러 영역에 걸친 변경은 관련 담당자와 함께 조정한다.

| 영역 | 연락 담당자·초기 주담당 | 주요 경로 |
| --- | --- | --- |
| 데이터 처리·색인·검색 | A · `ysl727727` | `app/ingestion/`, `app/indexing/`, `app/retrieval/` |
| 답변 생성·서비스·API | B · `chsopo89` | `app/generation/`, `app/service/`, `app/api/` |
| 실행 기록·평가 | C · `Damsebi` | `app/observability/`, `app/evaluation/` |

`shared/`는 공통 계약의 기준 위치다. `experimental/<GitHubID>/`에는 개인이 원하는 노트북·스크립트·로그 등을 둘 수 있으며 필수 파일, 하위 구조, 일지 형식은 없다. 제품 코드는 실험 디렉터리를 import하거나 참조하지 않는다. 채택할 코드는 `app/` 또는 `shared/`로 통합하고, 공식 실험 결과는 `reports/`에 둔다.

## 문서

- [문서 목차](docs/README.md)
- [시스템 설계](docs/design/architecture.md): 현재 구조·기능 흐름·검증 상태
- [공통 입출력·호출 계약](docs/design/contracts.md): 데이터 타입, API·로그·호출 계약의 기준
- [Git 저장소·협업 설계](docs/guides/git-workflow.md): 기능 구조, 개인 실험, 협업·PR·결과 보존
- [평가 모듈 가이드](docs/guides/c-module-guide.md): 평가·보안 실행기와 연결 방식
- [2026-11-05 Basic 작업 계획](docs/plans/basic-day-plan-2026-11-05.md): 당시 원안과 현행 실행안의 구분
- [데이터 안내](data/README.md): 원본 배치, 해시, 색인 및 검색 전용 측정

## 실행

Python 3.11 이상, 가상환경을 활성화한 뒤 저장소 루트에서 실행한다.

```bash
python -m pip install -e '.[test]'
python -m uvicorn main:app --reload
```

- `http://127.0.0.1:8000/docs`에서 질문을 입력한다. `GET /health`의 `query_ready`로 질의 준비 여부를 확인할 수 있다.
- 루트 `.env`에 `OPENAI_API_KEY`, `QDRANT_URL`, `QDRANT_API_KEY`가 필요하다. Langfuse를 연결할 때는 해당 키를 추가한다. `.env.example`을 참고하고 실제 키는 Git에 올리지 않는다.
- 사전 색인은 [데이터 안내](data/README.md)의 절차로 생성한다. 서버는 시작할 때 PDF를 읽거나 색인을 다시 만들지 않는다.
- 생성 모델·컬렉션·최종 k·문맥 예산·시간 제한·재생성 한도는 `configs/basic.yaml`에서 정한다.

설치에 포함된 테스트 도구로 저장소 루트에서 검사를 실행한다.

```bash
python -m pytest -q tests
```

CI와 동일하게 import·CLI 진입점을 확인하려면 다음을 실행한다. `--help`와 가상 샘플은 실제 키, 색인, 모델 호출을 사용하지 않는다.

```bash
python -m ruff check --select E9,F63,F7,F82 .
python -m app.indexing.scripts.build_index --help
python -m app.evaluation.scripts.eval_retrieval --help
python -m app.evaluation.scripts.run_benchmark --help
python -m app.evaluation.scripts.run_security --help
python -m app.evaluation.scripts.run_benchmark --sample --experiment-id sample-readme-benchmark
python -m app.evaluation.scripts.run_security --sample --experiment-id sample-readme-security
python -m build
```
