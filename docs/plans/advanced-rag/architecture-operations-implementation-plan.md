# TableRAG 구조 정리·로컬 환경·CI/CD 구현 계획

- 작성일: 2026-11-12
- 상태: **작업 브랜치의 구현과 검사 이력은 진행 기록에서 관리. 서버 적용 상태는 클라우드 준비 기록에서 확인.**
- 선행 검토: [코드 구조·로컬 환경·운영 검토](architecture-operations-review.md).
- 제품 기준: [DB 우선 통합 TableRAG](README.md). 제품 코드의 현재 동작과 아래 제안·예정 파일을 구분한다.
- 배포 저장소: `Somali-AI-Pirates/ADEN`, 기본 브랜치 `main`. 공개 저장소임을 GitHub 메타데이터로 확인했다.
- 확정 주소: `https://aden.29232923.xyz`. 공개 접근, 기존 자원만 사용한다.
- 실제 설정·검증 결과: [클라우드 준비 기록](cloud-setup-record.md).

## 현재 실행 범위

로컬 리팩터링 뒤 P4 요청 수명, 개인 YAML과 CUDA, P5 컨테이너, P6 CI, P7 배포·복구를 작업 브랜치에 반영한다. 운영 서버 적용과 main 반영은 별도 단계다. 검사와 커밋 단위는 [진행 기록](local-refactor-progress.md), 실제 실행 방법은 [배포 가이드](../../guides/deployment.md)를 따른다.

후속 사용자 지시로 레거시 정리도 허용됐다. 아래 과거 운영 계획의 레거시 보존 문구보다 이 범위 변경을 우선한다. 현재 TableRAG가 사용하는 공통 기능과 실제 소비처를 확인하고 호환 전용 분기·중복 구현을 정리하되, 데이터와 개인 실험은 보존한다.

확정한 구현 기준:

- 실행 설정은 TableRagSettings로 모으고 명시 CLI > 프로세스 환경 > 루트 .env > 해당 설정 > 기본값 순서로 한 번 해석한다. 경로는 저장소 루트 기준이다.
- 고정 제품 정책은 한곳에 모으되 값을 바꾸거나 새 조정 옵션을 추가하지 않는다. 레거시 YAML 키의 이름·사용 범위를 보존한다.
- 비동기 서비스 초기화가 자원 정리를 소유하고, 부분 실패 시 이미 생성한 자원을 역순으로 닫는다.
- State에는 요청 데이터만 둔다. 공유 검색·저장소는 생성자, 요청별 planner·trace·콜백은 LangGraph Runtime context로 전달한다.
- 네 도구의 처리 함수는 관측·검색/SQL 결과·출처·시간을 반환한다. 그래프가 메시지·횟수·목록을 한 번만 갱신하며 현재 overwrite 방식과 오류·호출 한도를 유지한다.
- corpus가 문서 로드를 소유한다. 공통 코드 토큰 처리와 빌더 단계는 기존 내용·순서를 보존해 나눈다.
- 완성 번들은 보존하며 코드 지문이 달라진 중간 캐시를 호환된 것으로 위장하지 않는다. 기존 코드 재개 또는 새 경로 구축을 안내한다.
- 실제 Qdrant 조회는 기존 번들·저장 질문 벡터로 비교한다. 유료 생성·Judge는 이번 검증에 포함하지 않는다.

## 1. 운영 후속 계획의 범위와 고정 계약

아래 1~8절은 운영 설계와 첫 적용의 완료 조건이다. 구현·검사 현황은 진행 기록을 기준으로 한다.

### 필요한 동작

1. 개발자는 로컬 Qdrant로 서버를 실행하고, 필요할 때만 로컬 Langfuse를 켤 수 있다.
2. API·CLI·평가는 같은 설정과 서비스를 사용한다.
3. 질문을 `aden.29232923.xyz`의 웹 화면에서 보내고, 답변·출처·PDF를 같은 접근 정책으로 제공한다.
4. PR에서는 검사만 실행한다. `main`의 같은 커밋이 모든 필수 검사를 통과하면 그 커밋의 이미지가 운영 환경에 배포된다.
5. 새 배포가 준비 상태 검사를 통과하지 못하면 이전 이미지·설정·데이터 조합으로 복구하고 실패를 남긴다.
6. 일반 배포는 원본 재파싱·재임베딩·공유 컬렉션 재생성을 수행하지 않는다.
7. 결과 저장과 Langfuse 추적, 저장된 실행의 Ragas·Judge 평가가 배포 뒤에도 같은 계약을 따른다.

### 보존할 호환 경계

- 질문 API, SSE 종료·오류 계약, 요청 ID와 답변 인용 검사.
- `db_first=True`, 요청별 근거 확인 기능을 제외한 현재 통합 흐름.
- KURE-v3·KURE nano·Luna medium, 5:5 검색과 현재 후보·최종 문맥 조건.
- 읽기 전용 SQL, Decimal, 발견한 출처·표만 사용하는 제한.
- SQLite·검색 번들 manifest와 기존 실행 기록 형식.
- Basic 컬렉션, 개인 실험과 산출물, 진행 중인 다른 작성자의 변경.

이 계획에는 새 검색 알고리즘, 모델 교체, 자동 재학습, 다중 리전·무중단 보장, 범용 플랫폼 구축을 넣지 않는다. 기존 사용자가 있는 레거시 경로도 호출자 확인 없이 제거하지 않는다.

기존 지원 대안은 로컬 uv 실행, NumPy 검색, 현재 Cloud 연결과 수동 평가다. 새 배포 경로 검증 전까지 유지한다. 제품·CI 구현이나 커밋·푸시 완료를 이 문서 작성과 혼동하지 않는다.

## 2. 확정·미확정 항목

| 항목 | 상태 | 다음 조치 |
| --- | --- | --- |
| 도메인 | aden.29232923.xyz 확정 | Cloudflare A 레코드와 HTTPS 준비 완료 |
| GitHub 저장소·배포 브랜치 | 위 저장소의 main | 실제 배포 시점의 권한과 보호 규칙 재확인 |
| 기존 CI | Python 검사·샘플·패키지 검사 | 프런트와 lock 기반 설치, CD를 연결 |
| 웹 배포 형태 | Next.js 정적 export 확인 | 정적 파일과 API를 같은 호스트에서 제공 |
| 클라우드 | 기존 OCI oci-free-01 | 유효한 SSH로 조사·설정. OCI API 세션은 만료 |
| 서버 | Ubuntu 24.04.5, ARM64, 2 CPU, 약 12GB RAM | 기존 사내비와 분리. API loopback 18010 사용 |
| DNS·인증서 | Cloudflare·기존 Certbot | aden 전용 A 레코드·인증서 추가, 기존 레코드 보존 |
| 접근 범위 | 누구나 공개 | 앱 연결 전 수용·사용량·공개 PDF 경계를 검증 |
| 신규 유료 자원 | 허용하지 않음 | 기존 서버·외부 연결만 사용. 부족하면 배포를 보류 |
| 운영 Qdrant·Langfuse | 기존 연결 유지가 기본 제안 | 이번 배포와 자체 호스팅 이전을 동시에 진행하지 않음 |
| 배포 인증·registry | 제한된 ADEN SSH 키·GHCR 계획 | production은 main 전용. 현재 gate는 상태 확인만 허용 |

확정된 서버·도메인·접근 범위만 사용한다. 실제 파일·권한·DNS·GitHub 설정은 클라우드 준비 기록을 기준으로 한다. 신규 유료 자원을 만들거나 기존 사내비 서비스를 중단해 공간·성능을 확보하는 것은 범위에 없다.

## 3. 배포 구성

### 3.1 로컬

- 앱: uv와 VS Code에서 실행. 개발 중 코드를 바꿀 때마다 이미지를 다시 만들 필요가 없게 한다.
- Qdrant: 이미지 버전을 고정한 Docker 컨테이너와 영속 볼륨. 개발용 포트는 loopback에 바인딩한다.
- SQLite: 기존 번들 파일을 직접 읽는다.
- Langfuse: 별도 선택 실행 구성. 자체 웹·워커·저장소를 항상 켜도록 강제하지 않는다.
- 검증: 기본 CI에는 외부 키를 넣지 않고 가짜 모델·작은 데이터로 동작을 확인한다.

### 3.2 운영

기존 OCI 단일 VM의 Docker Compose와 호스트 Nginx를 사용한다. 사내비 컨테이너·vhost는 유지하고 ADEN 전용 경로·포트를 사용한다. 단일 호스트 배포이며 고가용성을 보장하지 않는다.

```text
브라우저
  → aden.29232923.xyz / HTTPS / 공개 접근
    → 웹 정적 파일
    → /api/* 프록시 → FastAPI 컨테이너
                       ├─ 공통 SQLite·원본·검색 번들 (읽기 전용)
                       ├─ 모델 캐시 (준비 후 읽기 전용)
                       ├─ Qdrant (운영 연결)
                       ├─ Luna API
                       └─ 실행 기록 볼륨 + Langfuse

별도 평가 실행 → 저장된 답변·문맥 → Ragas·Judge → 원래 trace 점수
```

현재 원본 PDF 디렉터리는 약 88MB, TableRAG 번들은 약 1.4GB였다. 이는 파일 크기이며 RAM 요구량이나 모델 캐시 크기를 뜻하지 않는다. 첫 앱 배포 전 모델 적재·실제 요청의 메모리를 따로 측정한다.

기존 Nginx·Certbot·Cloudflare를 재사용한다. 프런트 정적 산출물은 /srv/aden/releases/<run_number>-<run_attempt>-<commit>/web에 전달하고 /srv/aden/current로 선택한다. 웹 산출물 이미지는 전달·검증 용도이며 별도 Next.js·Nginx 웹 컨테이너를 상시 실행하지 않는다. 앱은 linux/arm64 API 이미지 하나로 실행한다.

## 4. 코드 정리 작업

### P1. 설정·자원 초기화 통합 — R1, R4

**수정 위치:** `shared/settings.py`, `app/rag/config.py`, `app/rag/service.py`, `app/api/lifespan.py`, RAG·평가 CLI, `configs/advanced.yaml`, `.env.example`.

- 현재 각 설정이 적용되는 호출자를 먼저 목록화한다.
- 기존 설정 모듈을 확장해 공통 경로와 TableRAG 정책을 타입으로 구분한다. 새 범용 설정 프레임워크는 만들지 않는다.
- 우선순위는 코드 기본값 < YAML < 로컬 .env < 프로세스 환경 < 명시 CLI 인자로 명시한다. 설정 파일·경로 선택은 먼저 처리한다.
- 시작 단계에서 한 번 읽고 검증하며, 하위 모듈의 독립적인 dotenv 로드·환경변수 조회를 제거한다.
- API·CLI·평가는 같은 초기화 함수를 호출하고 필요한 설정·클라이언트를 전달받는다.
- 모델 이름·리비전과 번들 manifest가 일치하는지 검사한다. 경로는 작업 디렉터리에 따라 달라지지 않게 해석한다.
- 활성 경로에서 사용하지 않는 레거시 설정은 적용 범위를 분리한다. 소비처를 확인한 설정만 제거한다.
- 배포용 경로와 키는 주입하고 실제 적용값의 비밀 제외 요약을 남긴다.
- Compose secret 파일을 쓰려면 필요한 키만 읽는 명시적인 설정 처리를 추가한다. `*_FILE`이 기존 SDK에서 자동 지원된다고 가정하지 않는다.

**확인:** 임시 YAML·환경변수로 우선순위를 검사하고 API·CLI·평가의 설정 지문이 같음을 확인한다. 모델 불일치와 누락 파일은 초기화 단계에서 거절한다.

### P2. 그래프와 도구 처리 분리 — R2, R3

**수정 위치:** `app/rag/graph.py`, `tools.py`, `service.py`. **예정 추가:** `app/rag/tool_handlers.py`, 필요하면 `state.py`.

- `tools.py`의 모델 대상 스키마는 그대로 사용한다.
- `lookup_records`, `search_evidence`, `read_sources`, `query_tables`의 검증·실행을 타입이 명확한 처리 함수로 옮긴다.
- 함수는 도구 결과와 관측·출처 갱신에 필요한 값을 반환한다. 그래프가 단계 이동·호출 횟수·종료 상태를 소유한다.
- `finish`는 현재 종료 계약과 선택 출처 보존을 유지한다.
- 요청 State에는 요청별 데이터만 둔다. 모델·저장소·콜백은 생성자 또는 Runtime context로 전달한다.
- SQL·발견한 출처 제한을 분리 과정에서 완화하지 않는다.
- LangChain·LangGraph는 기존 연결을 재사용한다. RRF·Decimal·예산 계산을 불필요하게 Runnable로 감싸지 않는다.

**확인:** 고정된 계획 응답과 작은 실제 저장소로 도구별 오류·출처·SQL·종료 상태를 비교한다. 같은 입력에서 후보와 최종 문맥이 바뀌면 구조 변경과 별도로 원인을 해결한다.

### P3. 검색·데이터 구축 책임 정리 — R4, R5, R7

**수정 위치:** `retrieval.py`, `corpus.py`, `qdrant_index.py`, `keyword_retriever.py`, `table_rag_builder.py`, 기존 구축 CLI.

- 원문·표 파일 로드는 corpus 경계로 모아 데이터 로더가 검색 구현을 역참조하는 부분을 줄인다.
- 코드 정규화·토큰 처리 중 구축과 검색이 실제로 공유하는 기능만 공개 공통 함수로 분리한다.
- Qdrant의 연결 설정을 배포 형태와 분리한다. 로컬 HTTP와 운영 TLS의 지원 범위를 명시한다.
- BM25·RRF·리랭커·SQLite·exact 검색 정책은 고정한다. 현재 dense 경계가 충분하면 새 저장소 계층을 추가하지 않는다.
- 구축은 원문 추출, 기록·표 저장, 벡터 생성, 전체 단계 조립으로 나눈다.
- 기존 명령·번들 이름·manifest·잠금·재개 절차를 보존한다. 제품이 개인 실험을 import하지 않는다.

**확인:** 기존 파싱·SQL·출처 검사를 유지하고 작은 원본부터 번들을 구축한다. 같은 벡터에서 로컬·Cloud exact 검색은 수치 오차·동점 처리와 근거 출처를 구분해 비교한다. Basic 컬렉션은 변경하지 않는다.

### P4. 준비 상태·동시 요청·저장 정책 — R6

이번 로컬 실행의 확정값은 readiness 캐시 10초·Qdrant 연결/요청 2초, 서비스별 실행 1개·FIFO 대기 2개·대기 10초·전체 예산 300초·종료 유예 30초다. 대기 초과는 429/Retry-After 10, 미준비·종료는 503, 요청 예산 초과는 504를 사용한다. 동기 작업 정리까지 슬롯과 자원을 유지하므로 예산은 강제 반환 시각이 아니다. 실제 구현·검사 상태는 [P4 진행표](local-refactor-progress.md#p4--준비-상태요청-수용시간-제한-2026-11-12), 사용자 계약은 [빠른 시작](../../guides/quickstart.md#상태-확인과-요청-제한)을 따른다.


**수정 위치:** API 상태 경로, `RagService`, 실행 저장·추적, 프록시 설정.

- 기존 `/health` 의미를 유지하고 `/health/live`, `/health/ready`를 추가한다.
- live는 프로세스 상태, ready는 번들·모델·필수 저장소 준비와 결과 저장 가능성을 나타낸다.
- Qdrant 상태 확인은 제한 시간·캐시를 사용한다. Langfuse 장애나 유료 LLM 호출을 readiness 조건으로 두지 않는다.
- 요청 수용 제한과 모델 작업 제한을 구분한다. 대기 한도 초과·전체 시간 초과의 오류 계약을 정의한다.
- 스트림 시작 전 실패는 HTTP 오류, 시작 후 실패는 SSE error로 전달하고 final을 보내지 않는다.
- 현재 필수 결과 저장 계약을 유지한다. 저장 실패를 정상 응답으로 바꾸거나 임시 메모리 큐로 숨기지 않는다.
- 클라이언트 취소·배포 종료 때 요청을 정리한다. 진행 중인 동기 추론은 즉시 취소되지 않을 수 있음을 반영한다.
- CPU worker·스레드·큐 길이와 프록시 타임아웃은 측정에 따라 고정한다. 초기 worker는 1개로 시작한다.

**확인:** 준비 실패, Qdrant 중단, Langfuse 중단, 디스크 저장 실패, 동시 SSE, 취소·종료를 각각 검사한다. 지연·처리량은 측정값만 보고한다.

## 5. 컨테이너와 데이터 준비

### P5. 파일과 역할

| 위치 | 역할 |
| --- | --- |
| `infra/compose.local.yaml` | 로컬 Qdrant |
| `infra/compose.observability.yaml` | 선택 실행 Langfuse 구성과 영속 볼륨 |
| `infra/compose.production.yaml` | 기존 서버의 API 서비스, 볼륨, 자원·상태 검사. 웹은 호스트 Nginx에서 제공 |
| `infra/api.Dockerfile` | 잠금 파일 기반 Python 런타임 |
| `infra/web.Dockerfile` | npm ci·정적 빌드 후 전달용 산출물 이미지. 호스트 Nginx가 추출된 파일을 제공 |
| `.dockerignore` | 원본·번들·실행 기록·.env·개인 실험·로컬 캐시 제외 |
| `infra/deploy.sh`, `infra/rollback.sh` | 검증된 release 조합의 적용과 복구 |
| `infra/release.schema.json` 또는 동등한 타입 검사 | 이미지·데이터 지문을 포함한 배포 입력 검증 |
| `docs/guides/deployment.md` | 확정된 환경의 실제 배포·복구·운영 절차 |

Docker·Compose·배포 스크립트와 서버 설치 원본은 [infra/production](../../../infra/production/README.md)과 [배포 가이드](../../guides/deployment.md)에서 연결한다. 기존 서버에 설치된 설정은 클라우드 준비 기록을 기준으로 한다.

### 이미지

- API 이미지는 Python·uv·CPU PyTorch 설치를 lock 기준으로 고정한다. GPU 런타임을 의도 없이 포함하지 않는다.
- app·shared·config·프롬프트 등 필요한 파일만 명시적으로 복사한다. `COPY . .`만으로 공개 저장소 작업 트리를 통째로 포함하지 않는다.
- 프런트는 잠금 파일로 빌드한 정적 결과와 PDF worker 자산을 포함한다.
- 런타임은 비특권 사용자, 필요한 쓰기 경로만 허용한다.
- 이미지에는 키·PDF·SQLite·벡터·평가 정답·실행 기록을 넣지 않는다.
- 서버에서는 소스를 pull해서 다시 빌드하지 않고 검증된 이미지 digest를 사용한다.

### 데이터·모델

- 원본, 번들, 모델 캐시는 서버의 별도 저장 위치에 준비하고 manifest로 검증한다.
- 앱 내부의 경로와 파일명은 기존 `artifacts/advanced-table-rag/`, `records.sqlite3`, `tables.sqlite3` 등을 유지한다. 배포 release 메타데이터가 실제 마운트와 지문을 가리킨다.
- SQLite·원본·준비된 모델은 읽기 전용, `artifacts/rag-runs/`는 요청 결과 저장용 영속 쓰기 볼륨이다.
- 최초 준비 때만 검증된 자산을 전송하고 고정 모델 리비전을 준비한다. 외부에 공개하는 이미지·Actions artifact에 원문을 넣지 않는다.
- Qdrant 컬렉션과 로컬 원문·표 단위의 지문이 일치해야 준비 완료로 인정한다.
- 데이터 교체는 코드 배포와 별도 단계다. 새 데이터 조합을 검증한 뒤 전환하고 이전 조합을 복구용으로 보존한다.
- 기존 VM의 약 11GB 여유 공간과 공유 메모리 안에서 이미지·모델·번들·복구 자산의 실제 사용량을 확인한다. 다른 프로젝트의 이미지·데이터를 지우지 않는다.
- ARM64 빌드는 공개 저장소의 표준 ubuntu-24.04-arm runner를 우선 사용한다. CPU PyTorch·토크나이저 wheel과 이미지 실행을 같은 아키텍처에서 확인한다. 지원이나 무료 범위가 달라지면 유료 runner로 자동 전환하지 않는다.
- 최초 앱 자원 제한은 메모리 6GB·CPU 1개·worker 1개를 후보로 검증한다. 실측 전 처리량을 보장하지 않으며, 기존 서비스에 영향을 주면 활성화하지 않는다.

## 6. CI/CD 상세 흐름

### P6. CI

기존 `.github/workflows/ci.yml`의 `checks` 이름과 필수 검사 의미를 보존한다. 변경 전 원격 branch protection의 필수 job 이름을 확인한다.

| 실행 | 검사 |
| --- | --- |
| Python | uv lock 일치, lock 기반 test 설치, Ruff, 전체 pytest, 기존 CLI·가상 샘플, wheel·리소스 |
| 프런트 | npm ci, typecheck, 단위 테스트, Chromium E2E, 정적 build |
| 저장소 경계 | 작은 Qdrant 서비스와 테스트 SQLite로 HTTP·재시작·출처 검사 |
| 이미지 | API·웹 빌드, 런타임 파일·금지 데이터 포함 여부, 가짜 모델 기반 스모크 |
| 배포 정의 | Compose 해석·release 입력·스크립트 문법과 실패 시 복구 검사 |

실제 서비스 키·원문 없이 수행한다. 과거 문서에 남은 테스트 실패를 현재 실패로 단정하지 않고 최신 트리에서 재확인한다. 실패를 배포 통과를 위해 skip 처리하지 않는다.

### P7. CD

CI 워크플로에서 재사용 release 워크플로의 publish·deploy job을 `needs`로 연결해 검사한 커밋과 배포 커밋을 일치시킨다. 별도 workflow가 필요해지기 전에는 `workflow_run`으로 권한이 높은 흐름을 추가하지 않는다.

```text
PR → Python / 프런트 / 통합 / 이미지 검사 → 종료

main push 또는 main에서 수동 실행
  → 동일 검사 통과
  → API·웹 이미지 게시
  → release manifest 생성
  → production 환경의 배포 job
  → 서버 사전 검사 → 적용 → readiness·외부 스모크
  → 성공 기록 또는 이전 조합 복구
```

- PR·fork 실행은 publish·deploy하지 않고 운영 비밀값에 접근하지 않는다.
- main의 검사한 commit SHA를 끝까지 전달한다. 서버에서 `git pull`이나 latest 태그로 다른 코드를 고르지 않는다.
- registry는 GHCR를 사용한다. 공개 저장소의 코드·의존성·웹 자산만 포함하는 공개 이미지를 계획하고, 원문·비밀·실행 기록이 포함되지 않음을 게시 전 검사한다. 새 유료 registry나 장기 registry 토큰을 만들지 않는다.
- 게시 작업에만 registry 쓰기 권한, 배포 작업에만 필요한 클라우드 권한을 준다.
- 이번 OCI 단일 VM은 기존 SSH 경로와 전용 aden-deploy 계정을 사용한다. GitHub production의 허용 브랜치는 main으로 설정했다. 개인 관리 키를 CI에 넣거나 OCI의 만료되는 개인 세션을 CD에 사용하지 않는다.
- 기존 서버에 설치된 키의 gate는 aden-status만 허용한다. 새 설치 원본은 ADEN 소유 경로와 검증된 registry digest에만 작동하며 첫 운영 적용 절차에서 교체한다. 일반 PR runner를 운영 서버에 설치하지 않는다.
- ADEN_DEPLOY_ENABLED=false는 구현 완료까지 유지한다. 정상 배포·실패 복구와 용량 검증 뒤에만 true로 변경한다.
- `production` 환경의 concurrency는 하나로 고정하고 진행 중인 배포를 중간 취소하지 않는다.
- 새 커밋이 먼저 배포된 뒤 오래된 job이 덮어쓰지 않도록 서버 측 배포 잠금과 현재 release 순서 검사를 둔다. 명시적 rollback은 별도 동작으로 구분한다.
- 이미지 태그는 커밋 SHA, 실제 적용은 digest를 사용한다. GitHub Actions도 검증된 버전·commit으로 고정한다.
- main 통과 후 자동 배포가 목표다. 기존 PR 리뷰 규칙과 배포 승인 규칙을 혼동하지 않는다. 추가 승인자가 필요한지는 사용자 정책으로 결정한다.

release manifest에는 commit SHA, API·웹 digest, 비밀 제외 설정 지문, 번들·모델 지문, Qdrant 컬렉션 식별자·지문, 배포 시각·실행 ID를 기록한다. 비밀값은 넣지 않는다.

### 적용과 복구

1. 서버·프로젝트·경로가 ADEN 전용 대상인지 확인하고 배포 잠금을 획득한다.
2. 이전 성공 release와 볼륨 연결을 보관한다. 새 이미지 pull·manifest·디스크·데이터·secret 파일 존재를 먼저 확인한다.
3. 기존 데이터 서비스를 재생성하지 않고 API·웹만 갱신한다.
4. 모델 준비 시간을 고려한 제한 안에서 readiness를 확인한다. 단순 HTTP 200만 성공으로 판단하지 않는다.
5. HTTPS·웹 자산·공개 API·PDF 경계와 배포된 commit을 확인한다.
6. 실패하면 이전 digest·설정·번들 연결로 복구하고 복구 readiness까지 확인한다. 복구 실패도 별도 상태로 기록한다.

기본안은 단일 인스턴스 갱신이며 짧은 서비스 중단이 있을 수 있다. 충분한 메모리와 무중단 요구가 확인되기 전 이중 모델 적재가 필요한 blue/green을 기본으로 넣지 않는다. Compose restart만으로 무중단이나 자동 rollback이 제공된다고 가정하지 않는다.

## 7. 클라우드에서 직접 수행할 설정

기존 서버와 충돌하지 않는 DNS·HTTPS·전용 경로·제한 배포 접속·GitHub 환경을 준비했다. 아래 전체 절차 중 완료·미완료 구분은 클라우드 준비 기록을 따른다.

| 순서 | 실행 작업 | 완료 증거 |
| --- | --- | --- |
| C1 | 계정·리전·기존 서버·DNS 관리 위치 확인 | 대상 목록과 ADEN 사용 범위 |
| C2 | 기존 프로세스·포트·디스크·메모리·CPU 아키텍처 확인 | 재사용 가능한 자원과 추가 자원 필요 여부 |
| C3 | ADEN 전용 사용자·디렉터리·볼륨·컨테이너 네트워크 준비 | 권한·경로·영속성 확인 |
| C4 | 배포 인증·registry 권한·서버 명령 범위 설정 | 최소 범위의 실제 접근 검사 |
| C5 | runtime secret의 서버 보관·주입 경로 준비 | 비밀값 출력 없이 이름·권한·존재 확인 |
| C6 | aden DNS와 TLS·접근 정책 구성 | DNS 응답·인증서·접근 허용/거절 확인 |
| C7 | 로그 회전·디스크 경보·백업·재시작 구성 | 설정 조회·복원 또는 재시작 검사 |
| C8 | GitHub production 연결과 배포 입력 등록 | 환경·브랜치 제한·비밀 제외 변수 조회 |
| C9 | 제품·이미지 준비 뒤 첫 배포와 복구 연습 | 배포 commit·상태·복구 결과 기록 |

- 기존 aden 레코드가 다른 서비스를 가리키면 덮어쓰지 않고 소유 용도를 확인한다.
- DNS A/AAAA/CNAME의 종류와 값은 실제 서버·프록시 구성에 따라 선택한다. 기존 다른 서브도메인은 변경하지 않는다.
- 웹·API·PDF를 공개 HTTPS로 제공한다. 별도 로그인은 추가하지 않으며 스트림·PDF Range와 질의 제한이 작동하는지 검사한다.
- 기본 외부 노출은 웹 HTTPS다. Qdrant·SQLite·내부 상태 경로·관리 포트를 임의로 공개하지 않는다.
- runtime 키는 기존 비밀 관리 경로를 재사용하고 대화·문서·이미지에 복사하지 않는다.
- 공개 접근 결정에 따라 앱 연결 전에 유료 질의 남용 제한·PDF 노출 범위·사용량 경보를 검증한다. 내부 실행 기록·DB·관리 포트는 공개하지 않는다.
- 이번 범위에는 신규 유료 자원을 생성하지 않는다. 기존 자원이 부족하면 배포를 보류하고 사용자에게 결과를 알린다. 이전 실험의 API 예산을 인프라 예산으로 해석하지 않는다.

## 8. 완료 검증과 실행 기록

### 검증 표

| 영역 | 완료 기준 |
| --- | --- |
| 구조 변경 | 고정 입력에서 조회·후보·SQL·출처·오류 계약 유지 |
| 로컬 실행 | Cloud 계정 없이 Qdrant 구동, SQLite 사용, Langfuse 선택 실행 |
| CI | Python·프런트·통합·이미지 검사 통과, PR에서 운영 접근 없음 |
| CD | main의 검사 SHA와 서버 release SHA 일치, 실패 시 이전 release 복구 |
| 도메인 | aden HTTPS, 웹·API·PDF 동일 접근 정책, SSE·Range 정상 |
| 데이터 | manifest 일치, 기록·표 읽기 전용, 재배포 후 실행 기록 보존 |
| 장애 | Qdrant·Langfuse·디스크·시간 초과를 각 계약대로 처리 |
| 평가 | 별도 실행에서 저장된 결과를 그대로 채점하고 원래 trace와 연결 |
| 문서 | 새 체크아웃 준비부터 운영·복구까지 명령과 실제 경로 일치 |

실제 유료 질의·Judge 호출은 클라우드 기반 준비와 분리한다. 준비 상태 검사 때문에 유료 요청을 반복하지 않는다. 모델과 추적의 실제 연결 검증은 사용 대상·전송 범위가 확인된 표본으로 수행하고 호출량을 기록한다.

독립 리뷰는 이 별도 대화에서 허용되지 않으므로 현재 문서는 자체 점검한다. 후속 코드·영속 상태·배포 권한 변경은 허용된 검토 절차를 적용하고 미실시 검사를 완료로 표시하지 않는다.

### 운영 기반 조사 당시 상태

| 작업 | 상태 |
| --- | --- |
| 기존 코드·CI·웹 배포 구조 확인 | 완료 |
| GitHub 저장소·main·공개 여부 조회 | 완료 |
| 원본·번들 파일 크기 확인 | 완료. 모델 메모리 측정과는 별개 |
| 구현·CI/CD 작업과 검증 기준 작성 | 이 문서 |
| 클라우드·도메인·접근·예산 | 기존 OCI, aden.29232923.xyz, 공개, 신규 유료 자원 없음 |
| DNS·HTTPS·전용 경로·GitHub 환경 | 준비 완료. 앱과 자동 배포는 비활성 |
| 제품 리팩터링·Docker·CI/CD 코드 적용 | 최신 작업 브랜치 상태는 진행 기록에 별도 기록 |
| TLS·갱신·제한 SSH·기존 서비스 회귀 | 통과. 앱 배포·rollback·평가는 미실행 |
| 커밋·푸시·병합 | 이 운영 준비 기록 작성 시점에는 미실행. 이후 로컬 작업 저장 결과는 진행 기록에서 관리 |

실제 자원·변경 전후·검증 명령은 [클라우드 준비 기록](cloud-setup-record.md)에 남겼다. DNS·HTTPS·배포 기반 준비와 제품·CD 운영 완료를 구분한다.

## 9. 공식 참고자료

- [FastAPI Settings](https://fastapi.tiangolo.com/advanced/settings/): 설정 검증과 의존성 주입.
- [FastAPI 배포 개념](https://fastapi.tiangolo.com/deployment/concepts/): 프로세스·메모리·준비 작업.
- [LangGraph Runtime context](https://docs.langchain.com/oss/python/langgraph/graph-api#runtime-context): 요청 상태와 실행 의존성 분리.
- [Qdrant 설치](https://qdrant.tech/documentation/installation/): 로컬 Docker와 자체 운영·Cloud의 책임.
- [SQLite 사용 기준](https://www.sqlite.org/whentouse.html): 내장 DB와 서버형 DB의 경계.
- [Langfuse Docker Compose](https://langfuse.com/self-hosting/deployment/docker-compose): 선택적 자체 호스팅.
- [Docker Compose 운영](https://docs.docker.com/compose/how-tos/production/): 단일 서버 배포와 운영 설정.
- [Compose 시작 순서](https://docs.docker.com/compose/how-tos/startup-order/): running과 ready의 구분.
- [Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/): 서비스별 파일 주입. *_FILE 자동 지원을 가정하지 않음.
- [GitHub 배포 제어](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/control-deployments): environment·브랜치·동시 배포.
- [GitHub OIDC](https://docs.github.com/en/actions/concepts/security/openid-connect): 지원되는 클라우드의 단기 자격 증명.
- [GitHub 이미지 게시](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images): 이미지 빌드·registry 게시.
- [uv GitHub Actions](https://docs.astral.sh/uv/guides/integration/github/): lock 기반 CI 설치.

- [GitHub 표준 runner](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job): 공개 저장소 ARM64 빌드의 `ubuntu-24.04-arm` 선택 근거.
- [Certbot webroot·자동 갱신](https://eff-certbot.readthedocs.io/en/stable/using.html#webroot): 기존 Nginx를 유지한 인증서 발급·갱신.
