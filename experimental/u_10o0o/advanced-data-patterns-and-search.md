# Advanced 문서 양식, 검색용 정제와 SQLite 검토

2026-11-09. 원본에서 확인한 양식, 현재 검색이 쓰는 데이터, 다음 실험에서 검증할 설계를 구분한다. 범위는 개인 Advanced 실험의 조사와 로컬 검증이다. 비교 기준은 [현재 5:5 하이브리드](records/decisions.md#2026-11-09--55-하이브리드를-개인-advanced-기준선에-채택)다. 출처는 기존 `source_id`와 `(book_id, page)`로 이어가고, 구조화가 불확실한 페이지는 기존 원본 검색으로 처리한다.

## 1. PDF에서 확인한 구조

복원 페이지 18,000쪽을 기존 `sources.jsonl`, HG 사건 출처, HA 출처와 대조했다. AS001·006·007·016·056·057·088·120의 1·75·150쪽을 원본 PDF에서 다시 추출해 24쪽 모두 복원 캐시와 일치했다. 표본 대조는 레이아웃 복원 확인이며 업무 의미와 적용 조건은 별도 검수 대상이다. [기존 양식 조사](advanced-curation-plan.md#데이터에서-확인한-패턴)

| PDF 범위 | 실제 페이지 구성 | 정제 기준 |
| --- | --- | --- |
| AS001–006 | HA 터미널 사례 594쪽, HG 인계철 300쪽, 기타 6쪽 | HA의 계약 조건, 사건·증빙, 비용 정산을 세 역할로 구분한다. |
| AS007–056 | IS 업무 자료집 3,750쪽과 HG 인계철 3,750쪽 | 50개 PDF 모두 두 양식이 섞여 있다. PDF별 IS는 60~95쪽이다. |
| AS057–120 | 9,600쪽 모두 수입 운송·비용 원문 인계철(HG) | 역할별 표를 추출하고 검증된 사건 ID와 원본 쪽을 연결한다. |

**HG는 사건 단위, IS는 업무 설명과 기록 단위다.** HG 2,730건은 각 사건에 비용 조건 약정, 현장 사건 수신, 계약 조건 대조, 비용 청구, 수납·미결 인계가 한 쪽씩 있다. 한 사건의 다섯 역할은 모두 서로 다른 PDF에 있었다. 108건은 AS007–056과 AS057–120에 걸친다. HG 연결에는 기존에 검증한 사건 ID와 역할을 사용한다. HA는 자체 세 역할과 별도 키를 사용한다. [HG 연결 검증](records/advanced-curation/hg-case-sources-20261109/report.md) · [HA·자료집 조회 검증](records/advanced-curation/format-extension-20261109/report.md)

**반복 문구에도 업무 의미가 있다.** HG 13,650쪽의 바닥글 직전 문단은 공백과 유니코드 표기를 정규화하면 일곱 가지다. 이 중 다섯 가지는 이미 정제본의 업무 규칙과 정확히 일치했고, 나머지 두 가지는 적용·효력 조건이 들어 있는 변형이었다. IS는 PDF별 앞 두 블록이 고정이고, 여섯 번째 블록의 표 머리글 후보는 37가지다. 반면 바닥글 직전 블록은 3,667가지다. IS에서는 표 양식의 재사용성이 높고, 말미 설명은 페이지마다 달라지는 경우가 많다. [정제 규칙과 출처 계약](../../docs/design/knowledge-data.md#파일-구조와-사용-규칙)

### 현재 검색에서 어디가 중요한가

- 일상 표현 40문항의 지정 근거 40쪽과 업무 규정 20문항의 지정 근거 27쪽은 모두 **AS007–056의 IS**였다. 일상 표현 실험에서 5:5 하이브리드 후보 800쪽 중 IS가 683쪽, HG가 51쪽(AS057–120은 30쪽), HA·기타가 66쪽이었다. 이 표본에서는 IS 안에서 맞는 쪽을 찾는 성능이 중요하다. HG 정제는 일부 후보 경쟁을 줄일 수 있다는 가설로 다룬다. [일상 표현 데이터](evaluation/advanced_scenarios/semantic_queries/README.md) · [저장된 비교](records/advanced-semantic-queries-20261109/report.md)
- 공식 60문항에서 근거가 있는 58문항의 고유 참고 쪽 146개는 HG 112쪽, IS 28쪽, 기타 6쪽이다. 일상 표현 점수와 공식 점수를 따로 보고, HG 정제의 영향은 공식 평가와 HG 사건 묶음에서 확인한다. 이 수치는 지정 근거의 분포다. [공식 Development](../../data/advanced/evaluation/development.json) · [공식 Additional](../../data/advanced/evaluation/additional.json)
- 현재 제목 보너스는 복원 페이지의 앞 최대 세 블록을 쓰고, Snowflake-ko는 복원 페이지 전문을 임베딩한다. 기존 실험에서 표 행 위주로 최종 문맥을 바꿨을 때 근거가 줄었던 이력이 있다. 첫 비교에서는 검색용 본문을 바꾸고 최종 문맥에는 복원 원본을 전달한다. [제목 보너스](advanced_field_roles.py#L200) · [페이지 임베딩](main.py#L26) · [최종 문맥 실험](advanced-curation-plan.md#5-출처를-보존하는-문맥-렌더링-비교)

## 2. 정제에서 보존할 단위

PDF 하나가 여러 양식을 담으므로 **페이지 분류 → 역할별 구조 추출 → 검색용 텍스트 생성**으로 나눈다. 분류는 기존 머리말·사건 ID·표 열 신호를 사용한다. 신호가 충돌하거나 알 수 없는 양식이면 원본 페이지를 색인한다. 검색 결과에서 `source_id`, 원본 파일 해시, PDF 쪽 번호와 원본 블록 또는 줄 범위에 접근할 수 있게 한다. 같은 규칙 문구를 공유해도 각 원본 출현 위치와 적용 조건은 별도로 남긴다. [기존 페이지 파서](../../app/ingestion/curation.py#L125) · [레이아웃·표 파서](../../app/ingestion/layout.py#L136)

| 원문 요소 | 정제 데이터와 검색 처리 |
| --- | --- |
| 책 전체의 공통 이름, 숫자 쪽 바닥글 | 출처 메타데이터로 보존한다. 숫자 바닥글은 벡터 본문에서 제외한다. 공통 머리말은 제목·범위 검색에 쓸 수 있도록 보존하고, 벡터 본문에서 덜어내는 효과는 따로 검증한다. |
| 페이지 제목, 기록 역할, 절 제목 | 필요한 기록을 구별하는 단서다. IS의 짧은 본문이나 표 앞에 해당 페이지의 업무명·절 제목을 붙인다. |
| 표와 해당 절의 조건·각주 | 열과 원본 행·셀의 관계를 유지한다. 필요하면 `열 이름=원문 값`으로 렌더링한다. 표의 제목·머리글과 행, 적용 조건을 함께 검색할 수 있게 한다. |
| 사건 키, 계약·장비 식별자, 기록번호 | 종류를 구분한 조회 키와 원문 표기를 보존한다. 예약·장비·계약·업무 기록 키를 각각의 타입으로 검증한다. |
| 여러 페이지에 반복되는 규칙 | 규칙 본문, 적용 조건, 원본 출현 목록을 만든다. 문구와 적용 범위가 같을 때 한 규칙으로 색인한다. 버전·효력·비용 종류·예외가 갈리면 별도 규칙으로 보존한다. |
| 미결·미확인·0, 예정·실제·등록 날짜, 정정 전후 값 | 원문과 정규화 필드를 연결하고 상태·날짜 역할·단위를 구분한다. 명시된 0은 0으로 남기고 미확정 값은 비워 두되 해당 상태를 남긴다. |

IS 검색용 표현은 **페이지별 제목 + 업무 설명 + 표 제목·머리글 + 관련 행·예외**로 구성한다. 한 페이지에 독립된 업무가 둘 이상이면 근거 단위를 나눌 수 있다. 검색으로 찾은 단위를 원본 쪽으로 환산하고 중복을 제거한 뒤, KURE와 최종 문맥 조립기에 원본 페이지를 넘긴다. HG의 여러 역할은 사건과 역할을 연결하는 조회로 보강한다.

### 공식 문서와 논문에서 가져올 부분

| 레퍼런스 | 확인한 패턴과 ADEN에의 적용 |
| --- | --- |
| Microsoft Document Intelligence | 페이지별로 분류하고 일정한 시각 양식에는 템플릿 추출을 적용한다. 레이아웃 분석에서는 제목·머리말·바닥글·각주를 별도 역할로 다룬다. ADEN은 기존 텍스트 레이아웃과 파서에서 분류와 추출을 분리하는 구조부터 가져올 수 있다. [페이지 분류](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/train/custom-classifier) · [템플릿 추출](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/train/custom-template) · [레이아웃 역할](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout) |
| AWS Textract·Google Document AI | Textract는 표 제목·머리글·병합 셀·요약·바닥글을 다른 요소로 표현한다. Google은 상위 제목과 표 머리글을 검색 단위에 포함한다. IS에서 행을 떼어도 업무 제목과 열 의미가 따라가게 하고, 합계·각주를 일반 데이터 행과 구분하는 데 적용한다. [Textract 표 구조](https://docs.aws.amazon.com/textract/latest/dg/how-it-works-tables.html) · [Google 레이아웃 파서](https://docs.cloud.google.com/document-ai/docs/layout-parse-chunk) |
| Azure AI Search·W3C PROV | Azure는 검색 단위와 부모 문서를 일대다로 연결한다. PROV는 파생물·인용과 원천의 관계를 표현한다. ADEN에서는 검색 단위에서 원본 쪽과 원본 범위로 돌아갈 수 있게 한다. [Azure 인덱스 프로젝션](https://learn.microsoft.com/en-us/azure/search/search-how-to-define-index-projections) · [W3C PROV-O](https://www.w3.org/TR/prov-o/) |
| Anthropic Contextual Retrieval | 해당 절을 설명하는 짧은 문맥을 임베딩과 BM25 입력 앞에 붙였다. 보고된 이득은 자체 평가 조건에서 얻었다. ADEN에서는 PDF에 이미 적힌 업무 제목·역할·표 머리글을 붙이는 결정론적 방법부터 비교한다. [Contextual Retrieval](https://www.anthropic.com/research/contextual-retrieval) |
| Dense X Retrieval·시맨틱 청킹 비교 | Dense X는 독립적인 사실 단위가 검색에 도움이 된 사례를 보였고, Qu 등은 시맨틱 분할이 고정 분할보다 일관되게 우세하지 않았다고 보고했다. ADEN에서는 사실을 잘게 나눌 때 조건이나 표 열의 의미가 사라지는지 검사한다. [Dense X Retrieval](https://aclanthology.org/2024.emnlp-main.845/) · [시맨틱 청킹 비교](https://aclanthology.org/2025.findings-naacl.114/) |
| TableRAG·질문 분해 | TableRAG는 서술 검색과 SQL 표 연산을 결합한다. Ammann 등은 하위 질문별 검색 뒤 후보를 합쳐 재정렬했다. ADEN에서는 확인된 사건과 기록 역할은 SQL에서 찾고, 규정과 예외는 텍스트 검색으로 가져오는 데 참고한다. 요청이 둘이면 두 요청의 근거가 각각 최종 문맥에 남는지 평가한다. [TableRAG](https://aclanthology.org/2025.emnlp-main.710/) · [질문 분해](https://aclanthology.org/2025.acl-srw.32/) · [MultiHop-RAG](https://arxiv.org/abs/2401.15391) |
| AWS Entity Resolution·Kimball | 매칭에 사용한 규칙을 기록하고, 각 행이 나타내는 업무 사건을 먼저 정의한다. ADEN에서는 같은 장비나 업체가 보인다는 이유로 사건을 합치지 않는다. 청구·수납·정정은 각각의 원본 기록과 역할을 보존하고, 사건 연결에 사용한 키와 규칙을 남긴다. [규칙 기반 매칭](https://docs.aws.amazon.com/entityresolution/latest/userguide/creating-matching-workflow-rule-based.html) · [사실 테이블의 행 단위](https://www.kimballgroup.com/2008/11/fact-tables/) |

## 3. SQLite는 어디에 쓰면 좋은가

SQLite는 **검증된 사실과 관계를 조회하는 로컬 스냅샷**으로 적합하다. 기존 정제 산출물에서 사건·역할·원본 쪽을 연결한 뒤 정확 키와 복합 조건을 쿼리할 수 있다. 현재 개인 실험도 파이썬 메모리 맵에서 관계를 조회한다. SQLite에서 기대할 이점은 관계 제약을 한곳에서 검사하고, 같은 자료를 여러 실험에서 바로 열어 같은 결과를 얻기 쉬워지는 것이다. 조회 지연과 메모리 이득은 기존 방식과 비교해 판단한다. [현행 대상 조회](advanced_target_lookup.py#L111) · [SQLite 적용 범위](https://www.sqlite.org/whentouse.html) · [파이썬 sqlite3](https://docs.python.org/3/library/sqlite3.html)

### 첫 스키마와 확장 기준

| 관계 | 행의 단위와 주요 제약 | 기존 입력 |
| --- | --- | --- |
| `source_pages` | 원본 한 쪽. `source_id`가 기본 키이며 한 스냅샷에서 `(book_id, page)`는 유일하다. | `sources.jsonl` |
| `cases` | 검증된 사건 하나. `(family, case_id)`가 기본 키다. | HG·HA 검증 결과 |
| `case_records` | 사건의 기록 역할 한 개. 검증된 HG·HA에서는 `(family, case_id, role)`마다 원본 쪽이 하나다. 외래 키로 사건과 쪽을 연결한다. | `case_sources.jsonl`, `ha_pages.jsonl` |
| `handbook_records` | IS 원본 행 하나와 확인된 키. `key_type`과 `key_value`로 조회하며 `record_id`와 `source_id`로 출처를 찾는다. 새 데이터에서 키가 중복되면 후보 복수로 드러낸다. | 검증된 `기록번호`·`처리 기록` |

원본 쪽에 파일 해시를, 스냅샷에는 입력·추출기·색인 버전을 기록한다. 1차 구현은 HG의 사건→역할→쪽과 기존 조회 일치에 집중한다. HA와 IS는 같은 출처 모델을 쓸 수 있으며 검색에 연결하는 효과는 따로 측정한다. 규칙 검색을 실험할 때 `rules`와 `rule_occurrences`를 추가한다. 복합 사건 조회를 옮길 때는 이미 검증된 프로필·현장·날짜 필드와 날짜 유형을 사용하고, 복합 인덱스는 실제 필터와 쿼리 플랜을 보고 정한다. [SQLite 외래 키](https://www.sqlite.org/foreignkeys.html) · [복합 인덱스](https://www.sqlite.org/queryplanner.html) · [쿼리 플랜](https://www.sqlite.org/eqp.html)

```sql
SELECT cr.role, sp.book_id, sp.page
FROM case_records AS cr
JOIN source_pages AS sp ON sp.source_id = cr.source_id
WHERE cr.family = ? AND cr.case_id = ?
ORDER BY cr.role;
```

질문에서 예약 키와 요청 기록이 확인되면 SQL로 해당 역할의 원본 쪽을 가져온다. 두 사건을 비교한다면 사건별로 조회하고, 한쪽 후보가 여러 개면 모호 상태를 보존한다. 텍스트 검색에서 발견한 사건 키로 조회를 보강하려면 현재 대상 검증과 역할 조건을 통과해야 한다. SQL은 확인된 키와 관계를 실행한다. 자유 표현의 업무 의미는 BM25·벡터 검색과 기존 대상 해석기가 처리한다. SQL 문장과 매개변수는 애플리케이션에서 정의한다. 숫자 계산으로 확장하는 경우에는 같은 통화·단위, 효력 시점, 정정 상태가 확인된 레코드만 계산 대상으로 삼는다. [기존 비용 계산과 보류 조건](records/advanced-curation/charge-audit-20261109/report.md)

### 로컬에서 확인한 것

Python `sqlite3`가 연결한 SQLite **3.53.1**의 `:memory:`에 원본 쪽 **18,000개**를 적재했다. HG **2,730건**과 HA **198건**을 사건별로 모두 조회해 역할과 원본 쪽이 기존 매핑과 일치했다. IS의 `(키 종류, 키 값)` **11,073개**도 각각 조회해 원본 행과 쪽이 일치했고 키 중복은 0개였다. 외래 키 오류는 0건이고 무결성 검사는 `ok`였다. `EXPLAIN QUERY PLAN`에서는 사건 복합 기본 키 탐색과 원본 쪽 인덱스 탐색을 확인했다. 검증 범위는 **관계 적재와 조회 결과 일치**다. 파일 기반 지연, 메모리, 검색 정확도는 별도 비교 항목이다.

### FTS5와 벡터의 경계

FTS5는 별도 텍스트 검색 후보로 비교할 수 있다. 기본 `unicode61`은 유니코드 문자 분류에 따라 토큰을 나눈다. 로컬 예제에서 `보관료`는 `보관료를 검토한다`를 `unicode61`로 찾지 못했고 `trigram`으로는 찾았다. `운임`처럼 2글자인 질의는 `trigram`의 전체 텍스트 `MATCH`에서 잡히지 않았다. `unicode61`에서 따옴표로 감싼 `AB-12344`는 하이픈이 있는 행과 공백으로 갈린 행을 둘 다 찾았다. 첫 비교는 **식별자 정확 일치에 키 열을, 자유 표현 검색에 기존 Bigram-ID·Snowflake-ko를 사용**한다. FTS5를 별도로 평가할 때는 토큰화·제목 가중치·순위 변화까지 측정한다. [FTS5 토큰화](https://www.sqlite.org/fts5.html#tokenizers) · [FTS5 BM25](https://www.sqlite.org/fts5.html#the_bm25_function) · [현재 RRF 구현](hybrid.py#L23)

로컬 읽기 중심이라면 같은 정제 입력에서 새 DB를 오프라인으로 만들고, 검증이 끝난 파일을 읽기 전용으로 연다. DB와 검색 색인의 입력 버전을 맞추고, 빌드 시 연결별로 외래 키 검사를 켜고 무결성·참조를 검사한다. 평가 결과에도 입력 버전을 남긴다. 여러 실행이 공유 파일에 쓰는 경우나 쓰기가 잦은 서비스로 넓힐 경우에는 배포 방식을 다시 판단한다. SQLite는 같은 파일에서 한 번에 한 쓰기 트랜잭션을 지원한다. [읽기 전용 URI](https://www.sqlite.org/uri.html) · [SQLite 격리와 동시성](https://sqlite.org/isolation.html)

## 4. 다음 실험과 채택 조건

필드·키·원본 줄 연결, 스냅샷 생성 순서, 실패 처리와 단계별 검증은 [Advanced 정제와 관계 조회 구현 계획](advanced-curation-implementation.md)에 정의했다.

1. **IS 벡터 입력만 변경한다.** 18,000쪽에서 IS 3,750쪽만 바꾼다. A는 현재 5:5 하이브리드, B는 공통 머리말과 숫자 바닥글을 IS 벡터 입력에서 덜어낸다. 후속 C는 B에 페이지별 업무·절 제목과 표 열·행을 명시적으로 렌더링해 추가 효과를 본다. BM25, Snowflake-ko 모델, 5:5 RRF, KURE nano, 원문 조회와 최종 선택을 고정한다. 후보 20쪽, 최종 8쪽·8,000토큰으로 비교한다.
2. **관계 저장 방식만 비교한다.** 같은 HG 검증 자료를 기존 메모리 맵과 SQLite 읽기 전용 파일로 조회한다. 명시 키, 간접 단서, 모호 후보, 두 사건에서 반환한 사건·역할·원본 쪽과 순서를 비교한다. 같은 기기에서 빌드 시간, 첫 로딩, 워밍업 후 조회 p50·p95, 메모리와 파일 크기를 기록한다. 기존 경로와 같은 쪽을 반환하는 데서 시작한다.
3. **HG 반복 규칙 분리는 별도 비교로 둔다.** 다섯 역할의 사건별 값은 보존하고, 범위가 같은 반복 규칙을 별도 색인한 조건을 원본과 비교한다. 예외, 정정, 미결, 무료일과 실제·예정일 구분을 우선 검사한다. 적용 범위·버전이 확인되지 않은 문구는 해당 원본 쪽에 두고 검토한다.

| 평가 묶음 | 우선 확인할 지표 |
| --- | --- |
| 일상 표현 40·업무 규정 20 | 후보와 최종의 필수 근거 충족, 한 문서·여러 문서 분리, 문항별 개선·하락 |
| 공식 60·상황별 140 | 동일 `(book_id, page)`의 Recall@20·@8, 최종 전체 충족, 번호·금액·예외 문항 회귀 |
| 범위 밖 20·모호 사례 | 근거 없는 사건을 유일하게 선택하거나 서로 다른 사건 기록을 섞는지 확인 |
| 별도 검수 표본 | 인용 위치와 표 행·각주 보존, 정제 전후 해석 동일성, 여러 요청의 근거가 각각 남는지 확인 |

정제 규칙은 PDF와 기존 정제본에서 정하고 평가 참고 근거는 검색 후 채점에 사용한다. 데이터 지문과 출처 연결을 확인한 뒤 후보와 최종 문맥을 따로 채점한다. 개선이 한 문서에 집중되면 그 범위로 기록한다. 여러 문서 효과는 개별 근거와 전체 충족을 함께 보고, 정제 규칙을 정하는 데 쓰지 않은 표본에서 재현을 확인한다. [카테고리별 평가](records/advanced-category-comparison-20261109/report.md) · [채점의 원본 쪽·인용 규칙](advanced_full_metrics.py#L77)
