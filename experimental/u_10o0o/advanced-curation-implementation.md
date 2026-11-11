# Advanced 정제와 관계 조회 구현 계획

이 문서는 [양식 조사와 SQLite 검토](advanced-data-patterns-and-search.md)를 구현할 때의 입력, 저장 구조, 조회 경계와 검증 조건을 정의한다. **첫 검색 변경은 IS 벡터 입력 정제이고, 첫 SQLite 변경은 검증된 HG 사건의 역할별 원본 쪽 조회**다. 두 변경을 각각 현재 개인 5:5 하이브리드와 비교한다. 원본 PDF, 정제 JSONL, 기존 조회가 비교 기준이며 검색 후보 20쪽과 최종 최대 8쪽·8,000토큰은 각 비교에서 고정한다.

표현 규칙의 SQLite 관리, 앞단 LLM의 질문 해석, SQLite 우선 조회 비교는 [Advanced 질문 해석과 조회 분기 실험 구현 계획](advanced-question-routing-plan.md)에 따로 정의했다.

## 1. 저장물과 원본 연결

| 저장물 | 보관할 내용 | 기준과 용도 |
| --- | --- | --- |
| `data/advanced/documents` | 원본 PDF | 인용과 업무 해석의 원천. 기존 경로와 파일을 사용한다. |
| 기존 정제 스냅샷 | `sources.jsonl`, `records/<book_id>.jsonl`, `rules.jsonl`, `manifest.json` | 원본에서 추출한 출처, 행, 문단, 규칙. SQLite와 검색 입력을 다시 만들 수 있는 입력이다. |
| 기존 복원 페이지 캐시 | 원본 페이지 전체를 복원한 텍스트 | 현재 BM25·KURE·최종 문맥의 입력. 검색으로 찾은 근거는 여기에서 조립한다. |
| 제안하는 검색 스냅샷 | 벡터용 페이지 텍스트, 벡터 배열, 행별 `source_id` 목록, manifest | 검색에만 쓰는 파생본. 첫 실험은 원본 페이지 한 쪽당 벡터 하나를 유지한다. |
| 제안하는 `advanced.sqlite3` | 검증된 키, 사건과 역할의 관계, 원본 위치, 추후 필요한 구조화 필드 | 기존 정제본에서 오프라인으로 재생성하는 로컬 산출물. 원본 본문과 전체 벡터는 각 기존 저장 경로에서 읽는다. |

원본을 찾는 경로는 **`record_id → source_id → (book_id, page) → PDF`**다. `source_id`는 기존 `advanced:{book_id}:p{page}`를 쓴다. `page`는 1부터 세는 물리적인 PDF 쪽 번호다. PDF 파일명과 SHA-256은 책 단위로 저장하고, 같은 `book_id`의 모든 쪽이 같은 파일 지문을 가리키는지 확인한다. PDF가 바뀌면 같은 `source_id` 표기가 재사용될 수 있으므로, 실행 결과에는 항상 입력 스냅샷 ID도 남긴다. [원본 출처 계약](../../docs/design/knowledge-data.md#파일-구조와-사용-규칙)

행이나 문단 인용은 기존 `record_id`, `line_start`, `line_end`를 사용한다. 두 줄 번호는 **원본 PDF에서 `pdftotext -layout`으로 추출한 그 페이지의 줄 번호**이며, 복원 페이지의 줄 번호와 구분한다. 검증할 때는 원본 파일 해시·추출기 버전·레이아웃 텍스트 해시를 확인한 뒤 해당 범위를 다시 읽는다. 열 단위 값은 행 위치에 원본 열 이름을 함께 남긴다. 현재 추출기에 없는 좌표는 저장 계약에서 제외한다. [레이아웃 캐시](../../app/ingestion/curation.py#L75) · [줄 범위와 행 ID](../../app/ingestion/curation.py#L145)

## 2. 무엇을 구조화할지 정하는 기준

페이지는 기존에 검증된 HG·HA 출처 연결, IS 머리말과 알려진 표 머리글, 기타·미분류 순서로 판정한다. 판정 근거는 PDF의 머리말·역할·표 열에서 나온다. 파일 번호는 감사 집계에 사용한다. 양식이 충돌하거나 분류가 애매하면 원본 검색으로 보내고 사유를 기록한다. 같은 페이지에서도 **확인된 표 행은 구조화하고, 해석이 필요한 문단과 각주는 원문으로 보존**할 수 있다.

| 원문에서 확인한 것 | 구조화할 범위 | 조회와 보류 처리 |
| --- | --- | --- |
| **HG 사건과 다섯 역할** | 검증된 예약 ID, 역할, 원본 쪽. 기존 인덱스에서 단일 사건·계약·역할이 일치한 연결을 사용한다. | 역할별 정확 조회. 역할 누락·중복, 사건 또는 계약 불일치는 해당 사건 전체를 정확 조회에서 보류한다. |
| **HG 역할의 표 값** | 이미 추출한 실제·예정·등록 날짜, 무료·유료일 수, 청구·수납 상태, 통화가 확인된 금액 등. 행·열, 비용 종류, 상태를 함께 연결한다. | 확인된 값만 필터에 쓴다. 통화 미상·요율 구간 불명·효력 미검증 값은 원문과 검토 사유를 보존한다. |
| **HA와 IS의 확인된 키** | HA는 기존 세 역할, IS는 알려진 두 표 양식의 `기록번호`와 `처리 기록`부터 옮긴다. | 독립 확장 실험에서 사용한다. 동일 키가 다른 원본 행에 있으면 모든 후보를 돌려준다. |
| **규정·예외·각주·고객 인용** | 원본의 쪽·절·줄 위치와 필요 시 규칙 식별자만 구조화한다. | 업무 의미는 원문 검색과 인용으로 전달한다. 고객·외부 인용은 문서 자체의 업무 규칙과 구분한다. |
| **병합 셀·정렬이 애매한 표·미확인 양식** | 확인한 페이지와 위치, 검토 사유만 남긴다. | 행·열 추정 결과는 정확 조회에 쓰지 않는다. 복원 페이지 전체를 검색 대상으로 둔다. |
| **공통 머리말·숫자 바닥글** | 원본 및 출처 정보에 보존한다. | 확인된 구간만 벡터 입력에서 덜어낸다. 근거를 담은 반복 규칙은 별도 단계에서 다룬다. |

**추출 상태와 업무 상태를 구분한다.** 기존 `parsed`는 행 구조를 읽었다는 뜻이고, 역할 필드의 `reported`는 원문에 식별 가능한 값이 있다는 뜻이다. 승인 완료나 계약 효력은 원문이 확인해 주는 별도의 판단이다. 원문에 0이 적혀 있으면 0, `미결`이면 값은 `NULL`이고 상태는 미결로 보존한다. 예정일을 실제일로 채우거나 통화 없는 단가를 다른 기록의 통화로 채우는 행위는 검토 없이 허용하지 않는다. [역할 필드와 상태 처리](advanced_role_facts.py#L42) · [비용 계산의 보류 조건](records/advanced-curation/charge-audit-20261109/report.md)

## 3. SQLite 저장 스키마

### 3.1 첫 버전의 네 테이블

첫 버전은 120개 문서, 18,000쪽의 출처와 **HG 2,730건·13,650개 역할 연결**을 대상으로 한다. 테이블 하나의 한 행이 아래 SQL에서 뜻하는 단위를 유지한다. 로컬 실험은 SQLite 3.37 이상을 확인한 뒤 `STRICT`를 사용하고, DB를 만드는 연결에서 트랜잭션을 열기 전에 외래 키를 켠다. [SQLite STRICT 테이블](https://www.sqlite.org/stricttables.html) · [SQLite 외래 키](https://www.sqlite.org/foreignkeys.html)

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE source_documents (
    book_id TEXT PRIMARY KEY,
    file_name TEXT NOT NULL UNIQUE,
    file_sha256 TEXT NOT NULL CHECK (length(file_sha256) = 64),
    page_count INTEGER NOT NULL CHECK (page_count > 0)
) STRICT;

CREATE TABLE source_pages (
    source_id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES source_documents(book_id),
    page INTEGER NOT NULL CHECK (page > 0),
    page_family TEXT NOT NULL CHECK (page_family IN ('HG', 'HA', 'IS', 'other', 'unknown')),
    heading TEXT NOT NULL,
    UNIQUE (book_id, page)
) STRICT;

CREATE TABLE cases (
    family TEXT NOT NULL CHECK (family IN ('HG', 'HA')),
    case_id TEXT NOT NULL,
    PRIMARY KEY (family, case_id)
) STRICT;

CREATE TABLE case_records (
    family TEXT NOT NULL,
    case_id TEXT NOT NULL,
    role TEXT NOT NULL,
    source_id TEXT NOT NULL UNIQUE REFERENCES source_pages(source_id),
    match_rule TEXT NOT NULL,
    PRIMARY KEY (family, case_id, role),
    FOREIGN KEY (family, case_id) REFERENCES cases(family, case_id)
) STRICT;
```

`role`은 기존 `ROLES`의 원문 역할명을 적재한다. 역할 집합과 반환 순서도 기존 `ROLES`를 따른다. SQL의 기본 키 순서나 한글 정렬을 기존 순서로 가정하지 않는다. SQL 제약 외에 빌드에서 **모든 사건에 허용된 역할 전체가 존재하는지, 역할별 쪽이 다르고 페이지 양식이 사건 계열과 일치하는지, 페이지가 실제 PDF 범위 안에 있는지** 확인한다. `case_records.source_id`의 유일성은 검증된 HG·HA에서 한 쪽에 사건과 역할이 하나라는 범위를 표현한다. 한 쪽에 여러 사건이 섞인 새 양식은 원본 검색으로 먼저 처리한다. [기존 HG 로더의 검증과 역할 순서](advanced_hg_lookup.py#L33)

### 3.2 조회 수요가 있는 필드부터 추가

| 추가 관계 | 필드, 키와 저장 규칙 | 추가 시점 |
| --- | --- | --- |
| `source_records` | `record_id TEXT PK`, `source_id TEXT FK`, `line_start/end INTEGER`, `section TEXT`, `kind TEXT`, `parse_status TEXT`. 기존 ID와 줄 범위를 그대로 가져온다. 줄은 양수이며 끝이 시작 이상이다. | HG의 역할별 값 또는 IS 행을 SQL에서 직접 조회할 때 |
| `role_facts` | `record_id TEXT FK`, `fact_code TEXT`, `cost_kind TEXT NOT NULL`, `column_label TEXT`, `raw_value TEXT`, `state TEXT`, `value_type TEXT`, `value_text TEXT NULL`, `value_int INTEGER NULL`, `unit TEXT NULL`, `currency TEXT NULL`, `review_reason TEXT NULL`. **PK는 `(record_id, fact_code, cost_kind)`**. 비용은 `storage`, `late_return`, 해당 없음 `none`으로 표현한다. | 기존 메모리 맵의 날짜·식별자 필드부터 옮기고, 금액·일수는 실제 조회가 생길 때 추가 |
| `case_profiles` | `family`, `case_id`, `source_record_id TEXT FK`, `shipper_key`, `cargo_key`, `spec_key`, `contract_key`. PK 및 사건 FK는 `(family, case_id)`. 기존 로더로 현장·청구의 화주·화물·장비 규격 일치와 단일 계약 키를 확인한 프로필을 적재한다. 현장 역할의 원본 행을 출처로 사용한다. 동일 계약이 여러 사건을 가질 수 있다. | 기존 복합 대상 조회를 SQLite로 옮길 때 |
| `handbook_records` | `key_type TEXT`, `key_value TEXT`, `record_id TEXT FK`. PK는 `(key_type, key_value, record_id)`. 쪽은 `source_records`를 통해 얻는다. 첫 허용 키는 기존 두 표 양식의 열 이름이다. | IS 정확 키 조회를 별도 비교할 때 |
| `rules`와 `rule_occurrences` | `rules(rule_id PK, kind, text, scope, review_status)`. `rule_occurrences(rule_id FK, source_id FK, line_start, line_end)`의 PK는 네 필드 전체다. 새 규칙의 ID는 정규화 문구와 **적용 범위의 정규 표현**을 함께 식별한다. | HG 반복 규칙 검색 실험에서 |

기존 역할 필드 107,016개를 확인한 결과, `(record_id, fact_code)`만 사용하면 **10,920개 키가 두 번** 나온다. 보관료와 반납지연료가 같은 원본 행에서 같은 종류의 값을 갖기 때문이다. `cost_kind`까지 포함했을 때 중복은 0개였다. 적재 시 역할 필드의 사건·역할·원본 쪽·줄 범위가 기존 행 및 사건 연결과 일치하는지 확인한다. [역할 필드 원본 연결](advanced_role_facts.py#L310)

타입은 허용된 `fact_code`별로 결정한다. 날짜는 엄격히 검증한 `YYYY-MM-DD` 문자열, 일수는 정수, 금액은 통화가 확인된 십진 문자열로 저장한다. 계산이 필요한 후속 실험은 파이썬 `Decimal`과 확인된 단위·통화를 사용한다. 정확 조회에 쓰는 `reported` 값은 타입에 따라 텍스트와 정수 열 중 하나만 채운다. `needs_review`와 미확정 상태는 원문·상태·사유를 담되 두 정규화 열은 비운다. 현재 요율 구간 같은 복합 검토 값은 기존 역할 필드 JSONL에서 참조한다. 날짜와 식별자 후보의 정규화는 기존 조회와 동일하게 하고, 대상 필드·값 인덱스는 실제 쿼리에 필요한 것만 추가한다. [현행 역할 필드 조회](advanced_target_lookup.py#L190) · [SQLite 부동소수점과 정밀도](https://www.sqlite.org/floatingpoint.html)

기존 `rules.jsonl`은 규칙이 나온 `source_id`를 가지고 있고, 규칙에 해당하는 블록이 일반 `CuratedRecord`로 저장되지 않을 수 있다. 따라서 `rule_occurrences`는 **레코드 ID에 의존하지 않고 레이아웃에서 실제 출현 줄을 확인**한다. 문구와 범위가 일치하지 않거나 출현 위치를 확정하지 못하면 원본 페이지에서 그대로 검색한다. [기존 규칙 추출](../../app/ingestion/curation.py#L206)

## 4. 생성과 조회 절차

### 4.1. IS 검색용 텍스트부터 만든다

1. 기준 복원 캐시와 정제본을 `source_id`로 대조하고, 해당 책에서 IS로 판정한 쪽에만 정제 규칙을 적용한다. 첫 두 블록은 **같은 책의 IS 쪽에서 고정임을 확인한 문자열과 정확히 일치**할 때 덜어낸다. 마지막 숫자 바닥글은 현재 쪽 번호와 실제 총쪽 수가 모두 맞을 때 덜어낸다. 경계가 불확실하거나 본문이 비면 그 페이지는 원본 텍스트로 임베딩한다. 페이지별로 제거 위치와 적용·보류 사유를 남긴다.
2. A는 원본 벡터, B는 IS에서 공통 블록을 덜어낸 벡터를 사용한다. 나머지 쪽과 그대로인 IS 쪽은 같은 모델·리비전·정규화에서 만든 기존 벡터를 재사용한다. 새 벡터는 **원래 `source_id` 순서**에 대응시킨다. 준비 과정에서 행 수·차원·순서·중복과 각 벡터 입력 지문을 확인한다. BM25와 제목 보너스, RRF, KURE의 본문은 현재 입력을 쓴다.
3. B의 효과가 확인되면 후속 C에서 업무 제목, 절, 원래 표의 열 순서와 행 값을 명시적으로 렌더링한다. 열 순서는 정렬된 JSON 키에서 추측하지 않고 원본 레이아웃의 머리글로 확인한다. 적용 조건·예외·각주는 원래 절 안에서 유지한다. 표를 확정하기 어려운 구간은 복원 페이지 본문으로 둔다. C에서도 우선 원본 한 쪽에 벡터 하나를 유지해 청크 개수의 효과를 분리한다.

검색 중에는 BM25와 벡터가 뽑은 원본 쪽을 5:5 RRF로 결합한다. 변경군의 벡터에서 찾은 쪽도 `source_id`로 복원 페이지 객체를 가져온 뒤 KURE와 기존 문맥 조립기에 넣는다. 최종 인용은 검색용으로 다듬은 문자열을 원문으로 취급하지 않고 원본 페이지에서 가져온다. [기존 하이브리드 조립](advanced_search.py#L359) · [출처 보강과 문맥 선택](advanced_source_lookup.py#L114)

### 4.2. 검증된 HG 관계를 SQLite로 옮긴다

1. 기존 정제본과 `case_sources.jsonl` 및 요약의 해시를 검사한다. PDF별 해시·쪽 수와 복원 페이지의 출처도 대조한다. 문서 → 페이지 → 사건 → 역할 연결 순으로 고정 INSERT 문에 입력값을 바인딩한다. 이 단계의 입력은 PDF에서 파생한 파일로 한정한다.
2. 로컬 `artifacts/u_10o0o/advanced-sqlite/` 아래 새 임시 디렉터리에서 하나의 트랜잭션으로 DB를 만든다. 건수, 역할 완전성, `PRAGMA foreign_key_check`, `PRAGMA integrity_check`를 확인한다. 검증 성공 시 연결을 닫고 `advanced.sqlite3`와 `manifest.json`을 담은 디렉터리를 같은 파일시스템의 `<build_id>/`로 옮긴다. 실패한 빌드는 결과 디렉터리로 공개하지 않는다. 동일 버전이 있으면 지문이 일치할 때만 재사용하고, 불일치 시 기존 파일을 보존한 채 실패로 처리한다.
3. manifest에는 스키마·빌더 버전, 원본과 정제본의 스냅샷 지문, 사건 인덱스 지문, 출력 파일 해시, 건수와 보류 사유 집계를 남긴다. 검색 스냅샷에는 별도로 복원 페이지 지문, 렌더링 버전, 모델·리비전, 순서·배열 파일 해시를 남긴다. DB와 색인을 함께 실험할 때는 공통 PDF·페이지 집합 지문을 검사한다. 같은 원본을 쓰는 IS 벡터 변경만으로 사건 DB를 다시 만들 필요는 없다.
4. 1차는 정확 조회가 이미 고른 사건에서 역할별 페이지를 읽는 부분부터 바꾼다. 현재 대상 해석, 제외 표현, 간접 단서, 모호 후보 판정은 기존 코드 경로를 사용한다. SQLite 결과는 기존 역할 순서로 정렬해 동일한 복원 페이지 객체로 바꾼다. 2차에서는 프로필과 날짜·식별자 조회를 옮겨 기존 복합 판정과 대조한다. HA·IS 키 연결은 그다음 선택 가능한 실험으로 둔다.

SQLite 실험은 지정한 스냅샷을 읽기 전용으로 연다. **키 없음·복수 후보**는 기존의 원본 검색·추가 확인 흐름으로 보낸다. **파일 누락·버전 불일치·쿼리 오류**는 실험 오류로 기록하고 해당 실행을 비교 집계에서 제외한다. 이 구분이 있어야 SQLite 사용을 요청한 실행에서 이전 경로의 결과가 섞이지 않는다. [SQLite 읽기 전용 URI](https://www.sqlite.org/uri.html)

### 4.3. 반복 규칙 색인을 별도로 시험한다

규칙 문장, 적용 범위, 효력 시점·비용 종류·예외를 원문과 대조한 뒤 같은 의미와 범위를 확인한 출현만 한 규칙에 연결한다. 검색에서 규칙이 적중하면 질문의 확인된 범위와 출현 조건에 맞는 원본 쪽을 찾는다. 적용 쪽을 고를 수 없는 경우에는 일반 원본 검색으로 처리한다. 정정 전후 기록과 기본 규칙·예외가 모두 필요한 경우는 각각의 원본 근거를 유지한다.

## 5. 단계별 완료 조건

| 실험 | 통과 기준과 측정 항목 |
| --- | --- |
| IS 벡터 입력 변경 | 원본 18,000쪽과 벡터의 출처·순서가 일치하고, 변경 대상은 검증된 IS에 한정된다. 일상 표현 40·업무 규정 20의 후보와 최종 전체 근거 충족, 개별 근거, 문항별 손실을 기록한다. 공식 60·상황별 140·범위 밖 20으로 회귀를 확인한다. |
| SQLite 1차 | HG 2,730건 전부에서 역할·원본 쪽·순서가 기존 로더와 동일해야 한다. 명시·간접·모호·두 사건 질문에서 후보와 최종 문맥을 대조한다. 건수와 무결성, 입력 지문 불일치, 역할 누락·중복, 범위 밖 쪽의 실패를 검사한다. |
| SQLite 2차와 양식 확장 | 예정·실제·등록 날짜, 비용 종류가 다른 값, 미결·0·통화 누락을 구분한다. 동일 계약과 장비가 여러 사건에 있는 경우, 복수 기록 키와 부정 표현을 확인한다. 반환한 후보 집합과 순서가 기존 해석과 일치하는지 먼저 검증한다. |
| 반복 규칙 색인 | 규칙별 원본 쪽과 줄이 유효하고 적용·효력 조건이 일치해야 한다. 공식 및 HG 평가에서 예외·정정·미결의 필수 근거가 유지되는지 채점한다. |

SQLite는 같은 환경에서 기존 메모리 조회와 **빌드 시간, 첫 로딩 시간, 동일 요청의 워밍업 후 p50·p95, 메모리, 파일 크기**를 비교한다. 저장층의 조회 비용과 전체 검색 실행의 비용을 따로 측정한다. 1차에서 계속 사용하는 대상 해석용 메모리 맵의 비용도 전체 실행에 포함한다. IS와 반복 규칙 실험은 필수 근거 충족 개선과 회귀를 같이 보고, 기존에 보지 않은 검수 표본에서 재현된 범위만 채택 근거로 삼는다. 실험마다 입력 지문, 후보와 최종 문맥의 출처, 문항별 개선·하락, 검토 사유를 기존 개인 실험 기록 형식으로 남긴다. [평가 묶음과 기준](advanced-data-patterns-and-search.md#4-다음-실험과-채택-조건)

IS 공통 블록·바닥글 정제와 HG 관계 SQLite 1차는 `advanced_experiment`에서 실행한다. [실행 방법](README.md#정제와-질문-라우팅-비교) · [IS·HG 비교 결과](records/advanced-curation-routing-20261109/report.md)
