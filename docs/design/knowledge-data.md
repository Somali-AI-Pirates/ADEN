# Basic 지식 데이터 구성

작성일·수정일: 2026-11-06. 오프라인 데이터 준비의 현재 구현을 설명한다. 벡터DB 업로드·검색 성능 비교는 후속 작업이다.

## 데이터와 모델 지침

모델이 따를 행동 지침과 질문에 답할 업무 지식은 서로 다른 경로로 관리한다.

| 구분 | 기준 위치 | 사용 방식 |
| --- | --- | --- |
| 모델 행동 지침 | `app/generation/prompts/shipping_qa.txt`, `defense.txt` | 기존 생성 코드가 시스템 메시지로 적용. 방어 지침은 기존의 선택 조건 유지 |
| 조건부 업무 규칙 | `app/ingestion/catalogs/basic.json` → `rules.jsonl` | 출처를 확인한 범위·조건·효과·예외. 검색할 근거이며 시스템 지침이 아님 |
| 안내 원문 | `guidance.jsonl` | 아직 조건·효과를 정규화하지 않은 업무 안내. 원문 검색을 유지하며 추가 검토 가능 |
| 상담·사례 | `records.jsonl` | 고객 문의 또는 문서의 설명 사례. 실제 예약·승인·완료 원장으로 취급하지 않음 |
| 원본 근거 | PDF → `pages.jsonl`, `blocks.jsonl` | 원문, 파일 해시, 문서·페이지·문단 식별자를 보존 |
| 평가 질문·정답 | `data/basic/evaluation/` | 기존 평가 실행기에서만 사용. 데이터 준비·검색 입력에서는 읽지 않음 |

`answer_policy.json`은 지침의 기준 경로·해시·활성화 조건을 기록한 참조 파일이다. PDF에서 지침을 생성하지 않으며, 프롬프트 본문도 복제하거나 색인하지 않는다. 이 파일을 만들었다는 이유로 서비스의 프롬프트나 방어 설정이 바뀌지는 않는다.

## 생성과 저장 위치

설치는 루트 README의 공통 절차를 따른다. 이후 저장소 루트에서 실행한다. 데이터가 다른 경로에 있으면 `--data`에 `documents/`의 상위 폴더를 지정한다.

```bash
python -m app.ingestion.scripts.prepare_knowledge --help
python -m app.ingestion.scripts.prepare_knowledge --data data/basic --output artifacts/knowledge/basic
```

모델 다운로드, API 키, Qdrant 연결이 필요 없다. `--catalog`는 검토한 출처·규칙 파일을 바꾸는 옵션이고, `--max-chars`의 기본값은 800이다. 이 길이는 글자 기준이며 임베딩 모델의 토큰 한도 충족을 보장하지 않는다. 규칙의 조건·예외를 지우거나 중간에서 잘라 길이에 맞추지 않고 초과하면 실패한다.

```text
data/basic/                         # 원본, 변경하지 않음
├── documents/SB01.pdf ~ SB15.pdf
└── evaluation/*.json
app/ingestion/catalogs/basic.json    # 출처 분류·원본 해시·검토한 규칙의 기준
artifacts/knowledge/basic/<build_id>/
├── answer_policy.json              # 기존 지침의 참조, 색인 제외
├── pages.jsonl                     # 추출한 페이지 원문
├── blocks.jsonl                    # 페이지 안의 문단 근거
├── rules.jsonl                     # 조건·효과·예외를 구조화한 핵심 업무 규칙
├── guidance.jsonl                  # 정규화 전 안내 원문
├── records.jsonl                   # 사례·고객 문의 원문
├── chunks.jsonl                    # 기존 Chunk 형식의 검색 입력
├── index_entries.jsonl             # 청크·임베딩용 텍스트·지식 메타데이터 연결
├── schemas.json                    # 실제 모델에서 생성한 JSON Schema
├── validation_report.json          # 개수·근거 보존·현재 한계
└── manifest.json                   # 입력·코드·설정·출력 해시
```

산출물과 원본 PDF는 기존 `.gitignore` 규칙에 따라 Git에 올리지 않는다. Git에는 변환 코드, 검토 규칙, 테스트와 안내를 보존한다. 기존 `artifacts/basic/` 및 Qdrant 컬렉션은 이 명령의 대상이 아니다.

빌드 ID는 입력 PDF 해시·catalog 해시·변환 코드·pypdf 버전·청킹 설정·지침 참조에서 결정한다. 같은 조건의 재실행은 산출물 해시를 확인한 뒤 기존 빌드를 사용한다. 파일이 달라져 있으면 덮어쓰지 않고 실패한다. 새 빌드는 검증을 마친 뒤 별도 폴더로 게시한다. 여러 프로세스의 동시 실행은 지원 범위가 아니다.

## 스키마와 출처

실제 스키마의 기준은 `app/ingestion/knowledge_schema.py`와 생성된 `schemas.json`이다.

| 객체 | 주요 필드와 의미 |
| --- | --- |
| `SourceRef` | `dataset`, `book_id`, `file_name`, `file_sha256`, `page`, `block_id`. 페이지는 원본 PDF의 1부터 시작하는 위치 |
| `EvidenceBlock` | 원문 `text`, 원본 제목 `heading`, `source_origin`, `contains_quotation`, `source` |
| `BusinessRule` | `rule_id`, `title`, `text`, `scope`, `conditions`, `effect`, `exceptions`, `sources`. `review_status=source_checked`, `executable=false` |
| `GuidanceExcerpt` | `guidance_id`, `title`, `text`, `sources`. `review_status=needs_review`. 추정한 효과·적용 조건은 넣지 않음 |
| `CaseRecord` | `record_id`, `record_type`, `title`, `text`, `sources`. `actual_state=null`. 실제 거래 상태·금액을 추정하지 않음 |
| `IndexEntry` | `schema_version=1`, 기존 형식의 `chunk`, `embedding_text`, `metadata`. 메타데이터는 종류·지식 ID·출처를 연결 |

출처의 역할은 `carrier_guidance`와 `customer_inquiry_collection`으로 구분한다. 이는 문서의 성격을 표시하며 신뢰된 명령이라는 뜻이 아니다. **모든 검색 지식의 `trust`는 `retrieved_evidence`**다. 인용문이 들어간 문단은 그대로 보존하고 `contains_quotation`으로 표시한다. 인용 여부는 공격 판정이나 발언자의 권한 판정이 아니다.

Basic의 사례 분류는 외부 문의 문서와 페이지 제목의 `사례`·`상담 기록` 표지를 사용한다. 한 페이지에 여러 문의가 있을 수 있으므로 `records` 개수는 실제 사건 수가 아닌 페이지 단위 원문 묶음 수다. 다른 안내 페이지 안에도 사례·가정이 섞일 수 있으며, 모든 문장을 사실·규칙으로 자동 판정한 결과가 아니다.

## 구조화한 핵심 규칙

검토한 규칙 17개는 서비스 범위, 접수와 예약 확정, 변경 전후 SI 마감과 제출 주소, VGM 신규 40GP·20GP·기존 접수·정정 경로, VGM 마감과 중량 정의, 반입 허용·완료·예외 승인, 초안 승인과 발행, 실제 출항, 도착 예정과 인도의 구별이다.

예를 들어 SI 변경은 `scheduled_departure_date >= 2026-11-01`이면 달력일 이틀 전 `12:00 Asia/Seoul`로 구성한다. VGM 포털 변경은 `vgm_new_receipt_date >= 2026-11-03`, `equipment_type=40GP`, `submission_kind=new`를 구별한다. 기존 VGM 정정은 원래 접수 경로와 접수 기록에 연결한다. 알 수 없는 포털 URL이나 예약별 VGM 마감은 `null`로 남기며 임의의 값이나 0으로 채우지 않는다.

각 규칙은 catalog에 명시한 원본 해시와 근거 인용문을 검증한다. 원본 해시나 인용문이 달라지면 기존 정규화를 재사용하지 않고 실패한다. 인용문 일치는 변환 시점의 출처 연결 검사이며, 조건·효과의 의미를 자동으로 증명하는 검증은 아니다. 규칙 내용 변경에는 사람이 원문·예외를 함께 검토해야 한다.

범위나 예외가 다른 문장을 유사하다는 이유로 합치지 않는다. 검토한 공통 규칙은 ID로 한 번 정의하고, 안내 원문은 출처별로 유지한다. 이 데이터에는 실행기가 없으며 어떤 규칙이 특정 질문에 적용되는지 자동 확정하지 않는다. 문서 발행일이나 시스템의 오늘 날짜만으로 적용 대상을 고르지 않는다.

## 벡터 색인과 후속 연결

`chunks.jsonl`에는 원문 검색 입력과 구조화 규칙의 검색용 표현을 함께 둔다. 원문 입력은 기존 청커를 재사용한다. 규칙의 검색용 표현에는 범위·조건·효과·예외를 함께 담고, 인용은 실제 원본 문서·페이지에 연결한다. 현재 인용 계약을 유지하기 위해 구조화 규칙 하나는 한 페이지의 근거로 완결하도록 제한한다. 작성한 규칙 제목과 원본 문서 제목은 구별하며 `Chunk.heading`은 원본 제목을 쓴다.

`chunks.jsonl`의 각 행은 기존 `Chunk(**row)`로 읽을 수 있다. `index_entries.jsonl`은 추가 메타데이터를 가진 **오프라인 산출물**이며 현재 Qdrant payload 형식이 아니다. 이 행 전체를 `Chunk(**payload)`에 전달하면 안 된다. 후속 색인 연결에서 스키마 버전을 확인하고 메타데이터와 기존 Chunk 필드를 명시적으로 매핑해야 한다.

이번 변경은 새 산출물을 서비스 설정에 자동 연결하거나 원격 컬렉션에 업로드하지 않는다. 기존 `app.indexing.scripts.build_index`는 종전대로 PDF에서 기존 색인을 만든다. 후속 연결은 별도 컬렉션·manifest를 사용하고, 같은 질문·모델·검색 방식·문맥 예산에서 원문 기준과 비교한다. 임베딩 토큰 한도와 검색·답변 품질도 그 단계에서 확인한다.

## 현재 검증 범위와 확장

전체 Basic 15개·360페이지에서 본문 문단 1,600개를 보존한다. 지식 파일은 구조화 규칙 17개, 안내 원문 277개, 사례·문의 83묶음이고 검색 입력은 637개다. 안내 277개는 추가 정규화·내용 검토가 남아 있다. `validation_report.json`의 통과는 파일 처리·출처 연결·본문 보존을 뜻하며 업무 의미 전체의 검토나 RAG 성능 통과를 뜻하지 않는다.

Intermediate·Advanced는 처리하지 않는다. 표의 행·열 연결, 계약·장비 식별, 실제 사건과 승인·정정 관계, 비용 경계와 요율 계산은 해당 데이터의 별도 설계·검증을 거쳐 확장한다. Basic의 페이지 제목과 꼬리말 가정을 다른 난이도에 재사용하지 않는다.

[데이터 배치·기존 색인 안내](../../data/README.md) · [공통 런타임 계약](contracts.md)
