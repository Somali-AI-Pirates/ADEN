# ADEN — Basic 해운 RAG Q&A

제공된 Basic 해운 PDF를 근거로 예약·서류·비용 관련 질문에 답하는 팀 프로젝트다. 기능별 모듈과 하나의 FastAPI 서비스를 구성하고 세 가지 RAG 검색 방식을 비교한다.

A의 데이터 처리·Naive 검색, B의 답변 생성·API, C의 실행 기록·평가 코드를 구현했다. Hybrid·리랭킹과 세 방식 비교 평가, 보안 비교 결과는 아직 없다. 저장소는 `Somali-AI-Pirates/ADEN`이며 설계 문서를 `docs/`에서 관리한다. CI는 PR마다 기본 코드 오류 검사와 C 모듈 테스트를 실행한다.

## 설계 문서

- [문서 목차](docs/README.md)
- [시스템 설계](docs/design/architecture.md): 범위, 모듈, 함수 계약, API, 평가와 구현 순서
- [공통 입출력·호출 계약](docs/design/contracts.md): 데이터·JSON 예시, 함수·Resources·실행 방식과 기존 브랜치 연결 기준
- [Git 저장소·협업 설계](docs/guides/git-workflow.md): 폴더, 데이터 추적, 브랜치, 커밋, PR와 결과 관리
- [C 모듈 가이드](docs/guides/c-module-guide.md): 실행 기록·평가 모듈 사용법과 B 연결 방법
- [Basic 당일 작업 계획 — 3명](docs/plans/basic-day-plan-2026-11-05.md): A/B/C 담당, 통합 시점, 평가와 마감 기준

## 개발 방향

- 우선 Basic 해운 데이터셋을 사용한다.
- Basic의 import 경로는 `member_ysl727727/`(A), `member_chsopo89/`(B), `member_Damsebi/`(C), `shared/`로 고정하고(대소문자 그대로) 담당자의 모듈을 최종 위치에 모은다. 이름 변경이 필요하면 경로·import·설정·문서를 공통 변경으로 맞춘다.
- `shared/`에는 공통 타입·설정만 두고, `main.py`는 B의 앱 생성 함수를 호출한다.
- 공통 입력·출력 타입을 먼저 정하고 모듈을 연결한다.
- 설계는 `docs/`, 제출할 실행 결과는 `reports/`에 모은다.
- 작업별 브랜치에서 개발하고 PR로 `main`에 통합한다. 문서(`docs/`, README)만 바꾸는 변경도 `docs/` 브랜치에서 PR로 올린다.

## 실행

Python 3.11 이상, 저장소 루트에서 실행한다.

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -e .
.venv/Scripts/python -m uvicorn main:app --reload
```

- `http://127.0.0.1:8000/docs`에서 질문과 검색 방식을 입력한다. `GET /health`의 `query_ready`로 질의 준비 여부를 확인한다.
- 루트 `.env`에 `OPENAI_API_KEY`, `QDRANT_URL`, `QDRANT_API_KEY`가 필요하다. Langfuse 키를 넣으면 C 추적이 켜진다. 양식은 `.env.example`이며 실제 키는 Git에 올리지 않는다.
- 검색 색인은 [데이터 안내](data/README.md)의 절차로 미리 만든다. 서버는 시작할 때 PDF를 읽거나 색인을 다시 만들지 않는다.
- 생성 모델·컬렉션·최종 k·문맥 예산·시간 제한·재생성 한도는 `configs/basic.yaml`에서 정한다.

B 테스트는 가짜 검색·모델로 실행하며 실제 키나 색인이 필요 없다.

```powershell
.venv/Scripts/python -m unittest discover -s member_chsopo89/tests
```
