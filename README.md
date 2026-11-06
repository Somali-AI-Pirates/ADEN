# ADEN — Basic 해운 RAG Q&A

물류 담당자가 제공된 해운 PDF를 근거로 예약·서류·처리 상태·비용 조건을 확인하는 팀 프로젝트다. 답변에는 문서명과 페이지를 표시하고, 질문에 조건이 부족하면 추가 확인을 요청하며 근거가 부족하면 이유를 설명하고 답변을 보류한다.

## 기능과 범위

- PDF 추출·청킹, E5 임베딩과 Qdrant 색인, Naive Dense 검색, 답변 생성과 FastAPI, 실행 기록·평가 모듈이 있다.
- 서비스는 Basic 자료를 대상으로 한다. `hybrid`·`hybrid_rerank`는 선언되어 있으나 구현되지 않았다.
- Basic·Intermediate·Advanced의 [오프라인 정제 데이터](data/curated/README.md)를 관리한다. 정제본은 기존 색인과 서비스에 자동 적용되지 않는다.
- 실제 Qdrant·생성 모델·Langfuse를 연결한 서비스 전체 검증과 실제 세 검색 방식·보안 비교 결과는 아직 없다. 모의 테스트 통과와 실제 품질 검증을 구분한다.

## 처리 흐름과 구성

PDF를 미리 추출·청킹·색인한 뒤, 질문마다 **검색 → 문맥 구성 → 답변 생성 → 출처 검증 → 답변·상태·인용 반환** 순서로 처리한다. 서버는 요청마다 PDF를 다시 읽거나 색인을 생성하지 않는다.

```text
main.py                  # FastAPI 앱 진입점
app/
├── ingestion/           # PDF 추출·청킹·정제 데이터 준비
├── indexing/            # 임베딩·Qdrant 색인
├── retrieval/           # 검색
├── generation/          # 문맥·답변·출처 검증·프롬프트
├── service/             # 자원 초기화와 요청 흐름 연결
├── api/                 # FastAPI 앱과 라우트
├── observability/       # 실행 기록·Langfuse 추적
└── evaluation/          # 품질·보안 평가
shared/                  # 공통 타입·설정·예외
tests/                   # 공통 기능·통합 테스트
experimental/<GitHubID>/ # 자유로운 개인 실험
```

책임과 의존 방향은 [시스템 설계](docs/design/architecture.md), 필드와 호출 형식은 [공통 계약](docs/design/contracts.md)에서 확인한다.

## 빠른 시작

공통 개발·CI 기준은 **Python 3.13**이다. 저장소 루트에서 환경을 만들고 활성화한다. 아래 활성화 명령은 Linux·macOS 기준이며, Windows와 uv 사용법은 [개발 환경 가이드](docs/guides/development.md)를 따른다.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
```

`.env.example`을 참고해 로컬 `.env`에 OpenAI·Qdrant 접속 정보를 설정하고, [데이터 안내](data/README.md)에 따라 원본과 사전 색인을 준비한다. 실제 키는 Git에 올리지 않는다. 준비 후 서버를 실행한다.

```bash
python -m uvicorn main:app --reload
```

`http://127.0.0.1:8000/docs`에서 질문한다. `GET /health`의 `query_ready`로 질의 준비 여부를 확인한다. 모델·컬렉션·검색 수·문맥 예산 등은 `configs/basic.yaml`에서 설정한다. 외부 모델 호출에는 사용량이 발생할 수 있으며 팀 공식 실행은 [공통 운영 규칙](CONTRIBUTING.md#7-공식-실행과-외부-자원)을 따른다.

## 더 알아보기

- 처음 참여·복귀: [문서 목차](docs/README.md)에서 현재 작업에 맞는 안내를 선택한다.
- 개발 참여·팀 연락·PR: [공통 기여·협업 규칙](CONTRIBUTING.md)
- Python·uv·VS Code·Pylance·검사: [개발 환경 가이드](docs/guides/development.md)
- 데이터 준비·정제·평가: [데이터 안내](data/README.md), [지식 데이터 설계](docs/design/knowledge-data.md), [평가 가이드](docs/guides/c-module-guide.md)
- 일정·진행 작업·회의·개인 일지: [ADEN 팀 Notion](https://app.notion.com/p/6e9b8d4a06f782039952010095ade408)
- AI로 저장소 작업: [에이전트 지침](AGENTS.md)
