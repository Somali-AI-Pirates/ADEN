# ADEN — Basic 해운 RAG Q&A

제공된 Basic 해운 PDF를 근거로 예약·서류·비용 관련 질문에 답하는 팀 프로젝트다. 기능별 모듈과 하나의 FastAPI 서비스를 구성하고 세 가지 RAG 검색 방식을 비교한다.

현재는 **Qdrant의 Naive 검색, 한국어 답변 생성·출처 검증과 Langfuse 기록을 FastAPI에 연결**한 단계다. 저장소는 `Somali-AI-Pirates/ADEN`이며 설계 문서를 `docs/`에서 관리한다. 하이브리드·리랭킹은 후속 작업이다.

## 설계 문서

- [문서 목차](docs/README.md)
- [시스템 설계](docs/design/architecture.md): 범위, 모듈, 함수 계약, API, 평가와 구현 순서
- [Git 저장소·협업 설계](docs/guides/git-workflow.md): 폴더, 데이터 추적, 브랜치, 커밋, PR와 결과 관리
- [Basic 당일 작업 계획 — 3명](docs/plans/basic-day-plan-2026-11-05.md): A/B/C 담당, 통합 시점, 평가와 마감 기준

## 개발 방향

- 우선 Basic 해운 데이터셋을 사용한다.
- `app/` 아래 기능별 폴더에서 개발하고 `main.py`는 앱 생성 함수를 호출한다.
- 공통 입력·출력 타입을 먼저 정하고 모듈을 연결한다.
- 설계는 `docs/`, 제출할 실행 결과는 `reports/`에 모은다.
- 작업별 브랜치에서 개발하고 PR로 `main`에 통합한다.

## 로컬 실행

저장소 루트에서 가상환경을 준비한다. 이미 설치한 환경에서는 서버 실행 명령부터 사용한다.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e .
.venv/Scripts/python -m uvicorn main:app --reload
```

- 서버는 시작할 때 E5 모델과 Qdrant 컬렉션을 한 번 준비한다. 모델을 처음 사용하는 환경에서는 모델 파일 다운로드가 필요하다.
- 저장소 루트의 `.env` 또는 실행 환경에 `OPENAI_API_KEY`, `QDRANT_URL`, `QDRANT_API_KEY`를 설정한다. 컬렉션과 모델, 최종 k와 토큰 한도는 `configs/basic.yaml`에서 확인한다.
- `GET /health`에서 앱의 실행 상태와 `query_ready`를 확인한다. 준비에 실패하면 `query_ready`는 false이며 `/query`의 사유로 설정을 확인할 수 있다.
- `http://127.0.0.1:8000/docs`에서 `POST /query`의 요청과 응답 형식을 확인한다.
- `/query`에서 `naive`를 선택해 실제 검색과 답변 생성을 실행한다. `hybrid`와 `hybrid_rerank`는 준비 중이므로 501을 반환한다.
- 빈 질문이나 알 수 없는 검색 방식은 422, 검색 의존성 오류는 503, 모델·출처 오류는 502, 모델 처리 시간 초과는 504로 구분한다. 응답과 `X-Request-ID` 헤더에 같은 요청 ID를 남긴다.

## Langfuse 기록 확인

Python 3.11 이상과 Langfuse SDK 5.x를 사용한다. `.env` 또는 실행 환경에 `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`을 설정하고 서버를 시작한다.

`/query` 응답의 `request_id`로 Langfuse의 `rag-query` 기록을 찾는다. 루트 기록의 metadata에 같은 ID가 남는다.

- `retrieval`에서 검색 후보와 검색 시간을 확인한다.
- `final-context`에서 실제 전달한 문서 조각, 출처 ID와 문맥 해시를 확인한다.
- `generation-attempt-1`에서 실제 모델 입력·출력, 모델명과 입력·출력 토큰을 확인한다.
- `rag-query`에서 최종 상태·답변·출처, 파이프라인 처리 시간과 오류 종류를 확인한다.

수집은 비동기로 진행된다. 짧은 검증 프로그램은 `flush()` 후 `verify_trace()`로 저장 여부를 확인할 수 있다. API 종료 시에도 전송 대기 중인 기록을 정리한다.

## B 담당 기능 확인

- `generation.build_context()`는 검색 순서를 유지하며 최대 k개의 청크를 선택한다. 본문·제목·문서명·페이지를 포함한 전체 문맥의 토큰 수를 확인하며, 한도를 넘는 청크는 통째로 제외한다. 호출할 때 실제 생성 모델에 맞는 토큰 계산 함수를 전달한다.
- `generation.resolve_citations()`는 최종 문맥에 있는 자료 ID를 실제 파일명과 페이지로 연결한다. 문맥에 없는 출처 ID는 오류로 처리한다.
- `generation.generate_answer()`는 저장된 문맥과 한국어 프롬프트로 LangChain 모델을 호출한다. 답변 형식과 출처 ID를 검증하고 실제 모델 입력·출력과 토큰 사용량을 반환한다.
- `prompts/shipping_qa.txt`에 한국어 답변과 추가 확인·보류, 적용 조건을 확인하는 지시를 정리했다.

가상 검색 결과로 문맥 예산과 인용을 확인한다. 테스트에서는 토큰 계산 함수를 글자 수로 대체해 경계를 검증한다.

```bash
.venv/Scripts/python -m unittest discover -s tests -v
```
