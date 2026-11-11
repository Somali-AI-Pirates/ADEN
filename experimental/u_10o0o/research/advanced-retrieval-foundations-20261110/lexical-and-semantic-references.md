# Advanced RAG 검색 기반: 식별자·규정 혼합 데이터

- 조사일: 2026-11-10
- 목적: 계약·장비·증빙·예약 코드, 약칭, 일상 업무 규정이 섞인 문서에서 검색 선택지와 실패 조건을 정리한다.
- 범위: 공개 공식 문서와 1차 논문을 확인했다. 제품 기능은 링크된 자료의 주장이고, ADEN 적용 순위는 별도로 표시한 적용 제안이다.
- 현재 기준선(세부 실행 경로는 [prior-evidence.md](prior-evidence.md)): 개인 기본 실행은 제목 보너스 Bigram-ID BM25 + Snowflake-ko dense 각 상위 20쪽을 5:5 RRF로 합치고, KURE nano top-3 learned reranker와 후보 최대 20쪽, 원문으로 검증한 HG/쌍 출처 보강을 거쳐 최종 최대 8쪽을 구성한다. 현재 개인 기본 실행과 Luna/SQLite 실험 경로를 구분한다. 이 조사는 기준선 변경, 설치, 모델 호출을 포함하지 않는다.

## 요약

우선순위는 엔진 교체나 새 모델 도입이 아니라 **정확한 식별자 조회와 자연어 규정 검색을 분리하는 것**이다. 전체 ID 원문과 정규화 값을 보존하고, 장비 유형, ID 구성요소, 별칭, 제목/절, 본문, 검증된 메타데이터를 별도 검색 신호로 둔다. 전체 식별자가 유일하게 매핑될 때만 exact lookup 또는 hard filter를 사용하고, 일상 규정은 현재 dense + lexical 기준선으로 찾는다. 원문과 대조해 검증된 HG 조회/쌍 출처 보강은 사건·역할 출처를 확정하는 경로로 둔다. KURE 점수는 learned reranker의 관련성 신호일 뿐 값이나 사건을 검증하지 않는다.

요청 기준선과 별도의 로컬 snapshot 감사 결과는 [data-audit.md](data-audit.md)에서 참조한다. 감사에 따르면 이 snapshot의 full CT/HG/AP/INV/RCPT 코드 2,730개와 HL 코드 2,821개는 각각 한 사건에만 대응하지만, 91개 사건은 HL 코드가 두 개다. 반대로 HG 프로필의 20GP는 1,350개 사건, 40GP는 1,380개 사건에 나타난다. 따라서 이 corpus에서는 20GP가 고유 예약/사건 ID가 아니며, 20GP를 full HL 값의 synonym으로 매핑하면 안 된다. CT가 이 snapshot에서 고유하지만, 이를 다른 corpus에도 일반화하지 않는다. 이 숫자는 audit snapshot 범위의 관찰이다.

BM25는 **어휘가 일치하는 후보의 순위를 매긴다**. field 분리는 문서 구조를 반영하고, tokenizer는 match term을 바꾸며, synonyms/trigrams/fuzzy는 후보 매칭을 넓힌다. SPLADE/BGE-M3 sparse는 학습된 용어 가중치·확장을 쓰는 모델 축이고, ColBERT는 토큰 벡터 상호작용 축이다. 이 중 어느 것도 exact metadata와 검증조회를 대신하지 않는다. BM25F는 BM25의 field-aware 확장이지 의미 검색은 아니다. [R1–R5, R10–R13]

## 무엇이 BM25와 다른 축인가

| 축 | 하는 일 | BM25와의 관계 | 이 데이터에서의 쓰임 |
| --- | --- | --- | --- |
| Exact field / filter | 정규화된 전체 값·구조 필드의 동등성으로 후보를 찾거나 제한 | BM25 점수화와 별개. hard filter는 후보 집합을 결정 | 예약/계약/증빙 ID, 장비 유형, 문서 버전 |
| BM25 / BM25F / fielded query | 용어 일치, 빈도, IDF, 길이, field 중요도로 순위 계산 | lexical scorer. BM25F는 field structure를 반영한 BM25 확장 | 제목·절·ID·본문 가중치 |
| Tokenizer / analyzer | 원문을 검색 term으로 분리/정규화 | scorer가 아니라 BM25에 들어가는 일치 term 결정 | 한국어 형태소·합성어, 숫자-문자/구분자 변형 |
| Alias / synonym graph | 승인된 별칭을 질의에 확장 | BM25 자체가 아님. 확장 용어를 BM25가 점수화 가능 | 일상 업무어 ↔ 검증된 약칭/영문 명칭 |
| Character n-gram / fuzzy | 부분문자열·편집거리로 후보 생성 | 별도 후보 생성/유사도 축 | 오타/OCR/구분자 누락 복구 |
| Learned sparse | 모델이 vocab 차원의 query/document 가중치를 내고 역색인 검색 | BM25와 다른 학습된 sparse 표현/점수. 일부 lexical 특성 유지 | 한국어 도메인 용어의 learned expansion 후보 |
| Late interaction | 토큰별 query/document vector 상호작용으로 순위 계산 | BM25·단일 dense vector와 다른 모델/점수 축 | 일반 규정의 세밀한 표현 대응 후보 |

### BM25와 필드 점수

BM25는 용어 빈도, 전체 문서 집합에서의 용어 희소성, 문서 길이 등을 반영한다. BM25F는 제목·본문 등 문서 stream별 길이 정규화와 field weight를 반영해 결합한다. 이는 문서 구조를 이용하는 lexical 개선이지, query와 document의 어휘 불일치를 직접 해결하는 의미 검색은 아니다. [R1]

제품의 multi-field query를 모두 BM25F라고 부를 수 없다. Elasticsearch의 most_fields는 필드별 match 점수를 합산하고, cross_fields는 같은 analyzer를 쓰는 필드를 큰 필드처럼 다룬다. 유용한 fielded BM25 query 패턴이지만 BM25F의 field-normalized term frequency 결합과 같은 뜻은 아니다. 실제 엔진이 BM25F인지, 독립 field 점수 합인지, term 통계 blending인지 확인해야 한다. [R2]

## 식별자 표현: exact, parts, aliases를 분리

모든 field는 신뢰 가능한 추출/정제 결과에서 채워지는 경우에만 hard filter로 쓴다.

- **id_raw**: 원문 값과 문서/페이지 provenance
- **id_exact_norm**: ID 종류에 한정해 대소문자/구분자를 정규화한 전체 값. raw 원문은 보존
- **id_parts**: HL, 20GP, 0066 같은 부분 토큰을 찾는 보조 lexical field
- **id_alias**: 검증된 구형 코드/별칭 ↔ canonical ID 매핑
- **entity_type**, **equipment_type**, **evidence_type**, **document_version**: 검증된 metadata filter 후보
- **title/section**, **body_ko**: 규정·절차 자연어 검색 field

OpenSearch의 keyword field는 전체 값을 하나의 term으로 색인하며, exact equality에 맞는다. 정규화가 설정되지 않으면 대소문자를 구분한다. 범위 계산 목적이 아닌 숫자형 식별자도 keyword field를 쓰라고 안내한다. 전체 값 equality와 구성 토큰 검색을 같은 field에 맡기지 않는다. raw 값을 정규화해 덮어쓰지 말고 canonical ID 및 aliases의 출처/종류를 별도 관리한다. [R3]

현재 토큰화 스냅샷 [identifier-tokenization.json](identifier-tokenization.json)은 20GP 입력을 20gp 하나로, 20 GP를 20와 gp 두 토큰으로 기록한다. 대소문자/구분자 변형의 출력도 다르다. 이 파일은 tokenizer 출력만 확인하고 retrieval, routing, 최종 context 정확도를 측정하지 않았으므로 정확도 근거로 쓰지 않는다. 질의와 색인 변형을 동일 전처리 규칙으로 확인할 필요를 보여주는 국소 관찰이다.

Elasticsearch word_delimiter_graph는 XL500을 XL과 500 등으로 분해할 수 있고 preserve_original은 원래 토큰을 추가한다. 문서는 이 filter를 keyword/whitespace tokenizer와 같이 쓰라고 권고하며, punctuation을 먼저 지우는 tokenizer는 split, catenation, preserve_original 동작을 방해할 수 있다고 경고한다. preserve_original/catenation은 multi-position token을 만들어 index와 phrase query에 제약을 줄 수 있다. 실무에서는 raw exact field와 parts field를 따로 두고 분석 결과를 확인한다. [R4]

한국어 업무 문장의 형태소·합성어 분석은 식별자 분해와 별개다. Elastic Nori는 mecab-ko-dic 기반 분석과 사용자 사전을 제공하고, mixed 모드는 합성어 원형과 성분을 함께 출력한다. 도메인 합성어를 보완할 근거지만, 영어 analyzer나 SQLite unicode61만으로 한국어 규정 검색이 충분하다는 보장은 없다. Nori는 Elastic plugin 기능이므로 현재 backend에 기능이 있다고 가정하거나 설치를 제안하는 것이 아니다. [R6, R7]

## 별칭과 부분 코드의 안전 경계

모든 약칭을 상호교환 가능한 synonym으로 가정하지 않는다. 한쪽 방향으로만 치환되어야 할 약칭/정식명은 directed mapping으로, 의미 동등성이 확인된 용어만 equivalent group으로 둔다. Elasticsearch synonym_graph는 다어절 동의 표현을 검색 시점에 graph로 처리하며, Elastic은 multi-word synonym에 search analyzer를 권한다. 여기서 graph는 analyzer token-position DAG이며 지식 그래프/GraphRAG가 아니다. 이 방식은 synonym set을 관리하고 색인을 바꾸지 않고 규칙을 갱신하는 데 유리하다. 다만 업무 담당자가 매핑 방향을 확인하고, 질의 확장 내용을 추적할 수 있어야 한다. [R5]

20GP는 snapshot에서 여러 사건에 반복되는 장비 프로필 값이다. 따라서 **정확한 문자열 일치가 정확한 사건 식별을 뜻하지 않는다**. 20GP 단독 입력은 reservation/HL ID의 hard filter로 쓰지 말고 equipment_type/HG 프로필 field 문맥 후보로 검색한다. 20GP를 HL 전체 코드의 synonym으로 대체하지 않는다. Full HL 값은 이번 audit snapshot에서 각 하나의 사건에 연결되지만, 한 사건이 HL 코드 두 개를 가질 수 있다. 즉, 코드→사건의 exact 조회는 가능해도 사건→단일 코드 관계는 아니다. 일반화는 [data-audit.md](data-audit.md)의 audit 범위를 넘어가지 않는다.

Character trigram은 형태소 분석과 달리 문자 부분일치를 제공한다. SQLite FTS5 trigram은 substring search에 쓸 수 있지만 3 Unicode 문자 미만은 full-text query에서 match하지 않고, 조건이 너무 짧은 LIKE/GLOB 패턴은 전체 스캔을 할 수 있다. PostgreSQL pg_trgm은 threshold 기반 유사도와 GIN/GiST index를 제공하며, trigram 추출 시 non-word 문자를 무시한다. 구분자가 의미 있는 코드에서 이 유사도 경로는 exact equality를 대체하지 못한다. 이는 BM25 relevance나 entity uniqueness와도 다르다. 짧은 코드에서는 후보 충돌이 커지므로 긴 오타/OCR 문자열의 복구 fallback으로 제한한다. [R7, R8]

Fuzzy는 편집 거리 안의 term 변형을 만들어 여러 exact query로 검색하는 방식이다. Elasticsearch는 expansion 수가 커지고 prefix 길이가 없을 때 성능이 저하될 수 있다고 경고한다. 그러므로 긴 ID의 오타/ OCR 변형에만 제한적으로 쓰고 20, GP, 20GP 같은 짧은 토큰에는 전역 fuzzy를 적용하지 않는다. fuzzy/n-gram 유사도만으로 예약/계약을 확정하지 않는다. [R9]

## 엔진 선택은 기능/운영 경계의 선택

엔진 교체 자체가 accuracy를 높이지 않는다. 동일한 corpus, analyzer, fields, query, 평가 없이 결과가 향상됐다고 말할 수 없다.

| 선택지 | 공식 문서상 기능 | 이 데이터에서의 역할과 한계 |
| --- | --- | --- |
| SQLite FTS5 | phrase/prefix/NEAR, tokenizer 선택, BM25와 column weights, trigram substring | 작은 로컬/실험 lexical 비교에 적합. 기본 unicode61은 한국어 형태소 분석기가 아니며, trigram은 short ID에 한계가 있고 BM25 score 부호가 일반 구현과 반대다. |
| PostgreSQL FTS + pg_trgm | ts_rank/ts_rank_cd lexical·proximity ranking 및 weights, trigram similarity/substring index | 이미 구조 데이터가 있는 DB에서 FTS와 ID를 같이 쓰기 좋다. ts_rank_cd는 BM25가 아니며 pg_trgm은 fuzzy/substring 도구다. |
| Elasticsearch / OpenSearch | keyword exact, term query, analyzer, fielded multi-match, BM25, fuzzy, synonym graph. Elastic에는 Nori 문서가 있음 | analyzer와 fields를 세밀하게 만들 수 있다. query DSL의 multi-field가 진짜 BM25F인지 확인하고, mapping/tokenizer 변경의 결과는 corpus 평가로 판단한다. |
| Vespa | string field BM25, weakAnd sparse lexical 및 nearestNeighbor, configurable rank profile | retrieval과 ranking을 분리해 hybrid 조합 가능. 공식 예시에서 BM25는 0 이상, dense closeness는 0–1 점수라 raw 5:5를 엔진 간 이식할 수 없다. |

SQLite FTS5는 BM25에 column별 weight를 줄 수 있지만 내장 tokenizer와 scorer를 다른 엔진과 동일하다고 보면 안 된다. PostgreSQL ts_rank_cd는 위치 근접도를 쓰는 cover-density ranking이지 표준 BM25가 아니다. Vespa는 per-field BM25 및 dense 점수 조합을 제공하나 score 범위가 다르다. 따라서 현재 5:5 RRF는 rank-based 기준선으로 유지하되, 이를 다른 모델/엔진의 raw score sum과 혼동하지 않고 새 점수 결합을 따로 평가한다. [R7, R8, R3, R10]

## 학습 sparse와 late interaction은 후순위

**SPLADE**는 query/document를 vocab-level sparse vector로 표현하고 term weighting과 learned expansion을 사용해 역색인으로 검색한다. BM25와 다른 학습된 sparse scoring 방식이며, lexical match 특성을 유지하면서 어휘 불일치를 줄이려는 축이다. SPLADE 논문은 MS MARCO와 BEIR에서 효과/효율을 보고했지만 이는 한국어 해운 corpus나 ID collision에 대한 보장이 아니다. 한국어 tokenizer/vocab과 희귀 코드의 expansion 동작을 별도 확인해야 한다. [R11]

**BGE-M3**는 dense, sparse, multi-vector 표현을 함께 제공하고 100개 이상 언어 지원을 주장한다. 저자 평가에는 한국어 MIRACL과 한국어 MultiLongDoc 데이터가 포함된다. 논문 자체는 실제 도메인 일반화와 언어별 성능 편차를 한계로 적으므로 ADEN의 계약/예약 코드 성능으로 등치할 수 없다. Snowflake-ko dense를 유지한 채 한국어 sparse/multi-vector variant로 비교할 근거는 있지만, exact ID metadata나 원문 검증 HG 조회를 대체할 근거는 없다. KURE는 관련성 reranker로만 취급한다. [R12]

**ColBERT/ColBERTv2**는 query token마다 document token과 MaxSim을 취해 토큰 수준 의미 차이를 보존하는 late interaction이다. 일반 규정에서 표현의 세밀한 차이가 단일 dense vector에 묻힐 때 비교할 수 있다. 반면 token vectors 저장과 retrieval 운영 비용이 늘어난다. ColBERTv2는 late-interaction footprint를 6–10배 줄였으나, late-interaction index는 single-vector보다 큰 편이라고 논문이 설명한다. 이 연구는 한국어 해운 식별자의 유일성이나 exact filter 성능을 증명하지 않는다. [R13]

## 적용 우선순위와 평가 slice

1. **Exact-ID/evidence 경로:** 코드 종류를 구분하고 raw·normalized 값과 canonical 관계를 보존한다. snapshot에서 full HL은 각 한 사건에 연결되지만 한 사건은 HL이 두 개일 수 있으므로 mapping cardinality를 유지한다. 20GP와 40GP는 해당 HG 프로필 값으로 처리하고 사건/예약 ID와 동의어 처리하지 않는다. 원문으로 검증된 HG 조회는 사건/역할 출처 확정에 사용하고, KURE 점수는 순위 관련성 신호로만 취급한다.
2. **현재 lexical의 field 경계:** 가능하면 ID raw/exact, ID parts/aliases, title/section, body를 분리한다. 정규 ID는 exact path에서, parts는 낮은 가중치의 recall 보조로 쓴다. backend가 BM25F를 지원하지 않으면 별도 field query와 rank fusion이 가능한지 확인한다.
3. **업무 aliases와 한국어 analyzer:** 승인된 구형 코드와 약칭을 원장화한다. 자연어 규정만 형태소/합성어 analyzer와 domain dictionary로 실험하고 ID exact field는 분해하지 않는다.
4. **n-gram/fuzzy:** 긴 문자열의 오타/OCR/구분자 누락 fallback에 한정한다. 20GP처럼 여러 사건에 반복되는 short value는 자동 확장이나 entity resolution에 쓰지 않는다.
5. **Learned sparse → late interaction:** 앞 단계 후에도 규정 질의 recall/ranking 문제가 남을 때, 한국어 규정·혼합 코드 질의를 분리해 후보로 평가한다. 논문 benchmark 점수로 도입을 결정하지 않는다.

평가는 (a) full ID 대소문자/구분자 변형, (b) short equipment type와 여러 가능한 사건, (c) 약칭/업무어/다어절 한국어 규정, (d) 코드+절차 혼합 질문, (e) 문서 버전·증빙 조건의 예외를 별도 slice로 분리한다. ID exact top-1/precision과 규정 retrieval recall/ranking을 하나의 평균점수로 섞지 않는다. 변경은 한 축씩 비교하고 증빙 인용/HG lookup 정확도를 따로 확인한다.

## 자료와 적용 범위

공식 문서는 변경 가능한 페이지이므로 2026-11-10에 확인했다. 논문은 공개일/판본을 표기한다.

- **[R1] Robertson & Zaragoza, “The Probabilistic Relevance Framework: BM25 and Beyond”** — 2009. [저자 공개 PDF](https://www.staff.city.ac.uk/~sbrp622/papers/foundations_bm25_review.pdf), DOI 10.1561/1500000019. BM25와 BM25F를 구분하고 field structure를 다루는 근거다. 적용: field가 분명한 corpus의 lexical ranking.
- **[R2] Elasticsearch Reference, Multi-match / Similarity settings** — 공식 문서, 2026-11-10 확인. [multi-match](https://www.elastic.co/docs/reference/query-languages/query-dsl/query-dsl-multi-match-query), [BM25](https://www.elastic.co/docs/reference/elasticsearch/index-settings/similarity). most_fields 점수 합과 cross_fields의 동일 analyzer field blending을 명시한다. 한계: 둘 모두 BM25F와 자동 동치가 아니다.
- **[R3] OpenSearch Documentation, Keyword field / Keyword search / Term-level queries** — 공식 문서, 2026-11-10 확인. [keyword field](https://docs.opensearch.org/latest/mappings/supported-field-types/keyword/), [BM25](https://docs.opensearch.org/latest/search-plugins/keyword-search/), [term queries](https://docs.opensearch.org/latest/opensearch/query-dsl/term/). exact whole-value field와 BM25 lexical scorer를 구분한다. 한계: exact 문자열이 entity unique를 보장하지 않는다.
- **[R4] Elasticsearch Reference, Word delimiter graph / Token graphs** — 공식 문서, 2026-11-10 확인. [word delimiter graph](https://www.elastic.co/docs/reference/text-analysis/analysis-word-delimiter-graph-tokenfilter), [token graphs](https://www.elastic.co/docs/manage-data/data-store/text-analysis/token-graphs). ID의 원형·부분 토큰, graph 위치, index/phrase 제약을 설명한다.
- **[R5] Elasticsearch Docs, Synonym graph / Configure synonyms** — 공식 문서, 2026-11-10 확인. [synonym graph setup](https://www.elastic.co/docs/solutions/search/full-text/search-with-synonyms), [filter reference](https://www.elastic.co/guide/en/elasticsearch/reference/current/analysis-synonym-graph-tokenfilter.html). 다어절 synonym graph와 search-time 사용을 권장한다. 한계: rule 방향과 analyzer chain 관리 필요.
- **[R6] Elasticsearch Reference, Korean Nori / nori_tokenizer** — 공식 문서, 2026-11-10 확인. [Nori plugin](https://www.elastic.co/docs/reference/elasticsearch/plugins/analysis-nori), [tokenizer](https://www.elastic.co/docs/reference/elasticsearch/plugins/analysis-nori-tokenizer). mecab-ko-dic, mixed 합성어, user dictionary 동작을 설명한다. 한계: 현재 backend 기능을 뜻하지 않으며 코드는 별도 exact field가 필요하다.
- **[R7] SQLite Documentation, FTS5** — 공식 문서, 2026-11-10 확인. [FTS5](https://www.sqlite.org/fts5.html). phrase/prefix/NEAR, 내장 tokenizer, BM25/column weights, trigram을 제공한다. 한계: trigram의 3자 미만 쿼리 제한 및 다른 BM25와 score sign 차이.
- **[R8] PostgreSQL Documentation, Text Search Controls / pg_trgm** — 공식 문서, 2026-11-10 확인. [ranking](https://www.postgresql.org/docs/current/textsearch-controls.html), [pg_trgm](https://www.postgresql.org/docs/current/pgtrgm.html). ts_rank_cd cover-density와 trigram index similarity를 설명하며 trigram 추출에서 non-word 문자를 무시한다. 한계: BM25 및 semantic/unique key 판정과 다르다.
- **[R9] Elasticsearch Reference, Fuzzy query** — 공식 문서, 2026-11-10 확인. [fuzzy query](https://www.elastic.co/docs/reference/query-languages/query-dsl/query-dsl-fuzzy-query). edit-distance expansion과 max expansion에 따른 성능 경고를 확인했다. 적용: 제한된 긴 문자열 오류 복구.
- **[R10] Vespa Documentation, BM25 / Hybrid text search** — 공식 문서, 2026-11-10 확인. [BM25 feature](https://docs.vespa.ai/en/ranking/bm25.html), [hybrid tutorial](https://docs.vespa.ai/en/learn/tutorials/hybrid-search). BM25, nearestNeighbor, weakAnd, custom rank profile과 점수 범위 차이를 설명한다. 한계: framework 기능이 corpus 정확도 향상을 보장하지 않는다.
- **[R11] Formal et al., “SPLADE” / “SPLADE v2”** — arXiv 2021-07-12 및 2021-09-21. [SPLADE](https://arxiv.org/abs/2107.05720), [SPLADE v2](https://arxiv.org/abs/2109.10086). learned sparse weighting/expansion, inverted index, 효율 trade-off를 실험했다. 한계: 대상 benchmark가 ADEN 한국어/코드 corpus가 아니다.
- **[R12] Chen et al., “BGE M3-Embedding”** — arXiv 2024-02-05. [논문](https://arxiv.org/abs/2402.03216). dense/sparse/multi-vector와 다국어 지원, 한국어 benchmark를 포함한다. 한계: 저자가 도메인 일반화와 언어별 편차를 적는다.
- **[R13] Khattab & Zaharia, “ColBERT” / Santhanam et al., “ColBERTv2”** — SIGIR 2020, arXiv 2020-04-27; NAACL 2022, arXiv v3 2022-07-10. [ColBERT](https://arxiv.org/html/2004.12832), [ColBERTv2](https://arxiv.org/html/2112.01488). token-level MaxSim late interaction과 multi-vector 저장 비용을 설명한다. 한계: 한국어 코드 유일성/exact filter 성능의 증거가 아니다.
- **로컬 실행·실험 구분:** [prior-evidence.md](prior-evidence.md)는 개인 기본 실행과 Luna/SQLite 실험 경로를 분리한다. SQLite 간 결과 동등성은 저장 방식 차이에 검색 정확도 이득이 없음을 뜻한다.
- **로컬 snapshot 근거:** [data-audit.md](data-audit.md) 및 [data-audit.json](data-audit.json). 위 코드 종류와 사건 cardinality, 20GP/40GP 관찰의 scope는 해당 감사 파일에 한정한다. 논문/공개 자료와 섞어 일반화하지 않는다.
