# ADEN — B 담당 구현

이 작업 폴더는 **B의 답변 생성·API·통합 코드**와 공통 계약을 관리한다.
A/C 소스는 각 담당 PR에서 관리하며 이 폴더에 복사하지 않는다.
기준은 [공통 입출력·호출 계약](docs/design/contracts.md)이다.

## 현재 구조

```text
main.py                       FastAPI 앱 생성
shared/                       공통 타입·설정·오류
member_b/
├── api/                      한국어 API·입력 검증·요청 ID
├── generation/               문맥 선택·모델 호출·출처 검증·검증 실패 시 재생성
├── service/
│   ├── resources.py          외부 A/C 모듈·Qdrant·모델 연결·정리
│   ├── query.py              질문 한 건의 실행 순서
│   ├── records.py            C에 넘길 평탄 기록 조립
│   └── adapters.py           C 일반·보안 평가기의 B 호출 연결
├── prompts/shipping_qa.txt   한국어 말투·근거·보류·인용 규칙
└── tests/                    가짜 검색·모델·추적으로 B만 검증
```

[문서 목차](docs/README.md) · [공통 계약](docs/design/contracts.md) ·
[초기 설계](docs/design/architecture.md) · [Git 규칙](docs/guides/git-workflow.md)

## 실행

Python 3.11 이상, 저장소 루트에서 실행한다.

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -e .
.venv/Scripts/python -m uvicorn main:app --reload
```

- API 설명: `http://127.0.0.1:8000/docs`
- 상태: `GET /health`
- 질문: `POST /query`

```json
{"question": "예약 접수번호를 받으면 확정된 건가요?", "retrieval_mode": "naive"}
```

**A 미연결 상태:** API 문서와 상태 확인은 열리며 `query_ready=false`다.
`/query`는 A 연결이 필요하다는 503을 반환한다. 실제 답변을 만든 상태로 표시하지 않는다.
C가 없으면 Langfuse 추적을 활성화하지 않는다.

현재 패키지 경로는 로컬 공통 계약의 `member_a`, `member_b`, `member_c`, `shared`다.
팀이 다른 패키지명을 채택하면 경로·import·설정을 함께 조정해야 한다.

## A/C와 연결할 내용

A의 `Embedder`와 동기 `retrieve(question, mode, resources, top_n=20)`를 자원 준비
시 연결한다. B Resources는 `embedder`, `qdrant`, `collection`과 생성·토큰 계산·정리
자원을 제공한다. FastAPI는 `asyncio.to_thread()`로 동기 파이프라인을 실행한다.

C의 `Tracer`와 `compute_context_hash`를 연결하면 후보·최종 문맥·모델 입출력·시도별
토큰을 전달한다. 객체 청크는 평탄 로그로 바꾸고, 실제 문맥 문자열 해시와 C의
청크 배열 해시를 구분한다. SDK 사용량이 없으면 0으로 꾸미지 않고 null을 유지한다.

일반·보안 평가 연결:

```python
from member_b.service.adapters import make_query_adapter, make_security_adapters

adapter = make_query_adapter(resources, settings)
retrieve_fn, generate_fn = make_security_adapters(resources, settings, defense=defense_text)
```

`adapter`는 질문·검색 방식·요청 ID를 받아 실행 기록을 반환한다. 보안 연결은
한 번 준비한 문맥으로 base/defense를 생성하며 재검색하지 않는다. 두 팩터리는
C 문맥 해시 콜백이 없으면 준비 오류를 반환한다. 정답·기대 행동을 서비스 입력에 넣지 않는다.

HTTP 실행 기록을 저장하려면 `create_app(run_record_sink=save_record)`에 C의 저장
콜백을 전달한다. 공개 응답에 전체 문맥이나 내부 로그를 추가하지 않는다.
`latency_scope`로 HTTP 전체 처리, 서비스 직접 실행, 고정 문맥 생성을 구분한다.

## 설정·키

- 루트 `.env`: `OPENAI_API_KEY`, `QDRANT_URL`, `QDRANT_API_KEY`.
- C 추적용: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`.
- 빈 양식은 `.env.example`에 있으며 실제 키·원본 데이터·`artifacts/`는 Git 제외 대상이다.
- 실행 환경의 값이 `.env`보다 우선한다.
- 모델·컬렉션·최종 k·문맥 예산·시간 제한·재생성 한도(`generation_max_attempts`)는 `configs/basic.yaml`에서 지정한다.
- A manifest의 `vector_store.collection`, `embedding.model`을 YAML과 맞춘다.
  서버 시작 때 PDF를 읽거나 색인을 재생성하지 않는다.
- `context_tokenizer`는 문맥 예산용 로컬 추정이며 실제 사용량은 모델 usage로 확인한다.

## B 검증

```powershell
.venv/Scripts/python -m unittest discover -s member_b/tests -v
```

A/C 파일 없이 API import·문서·503 안내, 가짜 검색 결과 → 문맥 → 답변·출처,
입력·시간 초과·출처 오류, 실패 시도 기록, 고정 문맥 비교를 검증한다.
가짜 응답은 테스트에서만 사용한다. 실행 서버에 가짜 업무 답변 경로를 넣지 않는다.

현재 실제 검색은 A 모듈 연결이 필요하다. Hybrid·리랭킹·평가 채점·보안 효과 측정·
Advanced 전체 색인과 클라우드 부하 검증을 완료한 것으로 표시하지 않는다.
이번 로컬 수정은 커밋·푸시·병합과 별개다.
