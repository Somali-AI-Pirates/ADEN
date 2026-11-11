# Advanced 검색의 그래프·구조화 검색 원점 자료

2026-11-10 확인. 공개된 공식 문서와 논문 원문, 저장소의 현재 Advanced 문서·실험 기록을 대조했다. 목적은 새 제품이나 실험을 정하는 일이 아니라, 이미 있는 HG 조회·SQLite 관계 조회·그래프 확장·복합 질문 검색의 경계와 그래프 방식이 추가로 필요한 조건을 구분하는 것이다. 모델 호출, 설치, 검색 엔진 변경, corpus 전송은 하지 않았다.

## 핵심 판단

현재 HG의 확인된 사건 ID와 다섯 역할별 원본 위치는 고정된 관계다. 한 사건의 역할 쪽을 찾거나 확인된 필드로 사건을 거르는 경로에는 기존 검증 인덱스와 SQL join이 잘 맞는다. 원문 관계가 다섯 문서에 걸쳐 있다는 사실만으로 GraphRAG가 필요한 것은 아니다. 조회 경로와 식별 키가 이미 정해져 있기 때문이다.

GraphRAG 계열은 다른 문제를 푼다. Microsoft GraphRAG는 비정형 문서에서 LLM으로 엔터티·관계와 커뮤니티 요약을 만들고, query-time에는 지역 엔터티 탐색(Local), corpus 전체 요약(Global), 두 방식의 단계적 확장(DRIFT)을 수행한다. HippoRAG·LightRAG·PathRAG는 엔터티를 잇는 그래프에서 여러 문서 사이의 연관 경로를 찾거나 검색 결과를 다듬는다. 이들은 정확한 사건·역할 관계를 보관하는 정규화 DB와 같은 종류의 구성요소가 아니다. GraphRAG의 Local/Global/DRIFT는 질의 방식이고, 검증된 노드·관계와 SQL은 사실을 저장하고 꺼내는 방식이다. 한 시스템에서 검색 seed는 벡터·키워드로 찾고, 확정된 노드의 주변 문서는 그래프/SQL로 가져오는 조합도 가능하다.

ADEN에서 별도 그래프 탐색이 추가 가치를 낼 후보는 다음과 같이 한정된다.

- 질문의 단서가 일부 문서에 흩어져 있고, 다음 관계 유형과 경로 길이를 미리 고정하기 어려운 다중 hop 질의다. 관계는 원문 출처와 함께 검증돼야 한다.
- 정규화된 필드에 아직 담기지 않은 반복 주제나 정책 간 공통점을 corpus 전체에서 폭넓게 찾아야 한다. 명시적으로 정해진 집계는 SQL로 표현할 수 있는지 먼저 본다.
- 그래프 확장이 기존 근거를 밀어내지 않고 최종 답에 필요한 새 원문 쪽을 실제로 회복하는지 분리해 측정할 수 있다.

## ADEN 현재 자료와 구현 경계

| 자료 | 현재 확인된 단위 | 검색상 의미 |
| --- | --- | --- |
| 복원 corpus | 120개 PDF, 18,000쪽 | HG 인계철, IS 업무 자료집, HA 사례와 소수 기타 자료가 PDF 안에서 혼합된다. 출처 기준은 `(book_id, page)`와 `source_id`다. |
| HG 사건 기록 | 2,730건 × 다섯 역할 = 13,650쪽 | 사건마다 약정·현장 사건 수신·계약 조건 대조·청구·수납/미결 기록이 있고, 역할별 쪽은 서로 다른 PDF에 있다. 확인된 사건 ID와 역할 연결은 이미 구축돼 있다. |
| IS 자료 | 3,750쪽 | 업무 설명과 표·원장이 섞여 있다. 표 머리글은 반복되지만 문단은 페이지마다 달라지는 경우가 많다. 조건·예외가 든 설명은 원문 검색이 필요하다. |
| HA와 기타 | 594쪽과 6쪽 | HA는 별도 사건과 세 역할을 가지며, 기타는 원본 페이지 검색 경로가 적절하다. |
| 장비 규격 `20GP` | HG 프로필 기반 관계에서 1,350사건이 공유. `40GP`는 1,380사건. [전체 재집계](data-audit.md) | 장비 **종류**를 나타내는 단서로 다뤄야 한다. 장비 인스턴스나 사건의 고유 ID처럼 쓰면 안 된다. |

채택된 개인 기본 실행은 [advanced_search.py](../../advanced_search.py)의 제목 보너스 Bigram-ID BM25와 Snowflake-ko 각 20쪽을 5:5 RRF로 결합하고, KURE nano 상위 3쪽 재정렬 뒤 결정론적 로컬 조회와 기존 선택을 적용한다. 후보는 최대 20쪽, 최종 문맥은 최대 8쪽·8,000토큰이다. 여기서 HG 사건·출처 보강은 `complete_sources()`가 기존 결정론적 조회기를 호출하는 경로다. 이 기본 실행은 Luna `lookup_hg_sources` 라우터나 HG 관계 SQLite 도구를 직접 호출하지 않는다. SQLite 관계 저장소와 Luna 도구를 결합한 조회는 [advanced_experiment](../../advanced_experiment.py) 및 최근 비교·그래프 확장 실험의 별도 경로이며, 개인 기본 실행과 기준선·동작을 혼동하지 않는다. 기존 역할별 정규화 필드·SQL 실험과 원문 검색의 경계는 [Advanced 데이터 패턴과 SQLite 검토](../../advanced-data-patterns-and-search.md), [정제·검색 계획](../../advanced-curation-plan.md), [선행근거 감사](prior-evidence.md)에 정리돼 있다. 개인 기본값은 [실행 안내](../../README.md#현재-advanced-구성)를 따른다.

기존 그래프 확장 기록도 설계 판단에 포함한다. 질문 코드와 5:5 상위 10쪽에서 확인된 사건을 seed로 삼아 SQLite 관계를 따라 최대 여섯 쪽을 보충한 설정은 공식 인용형 58문항에서 전체 근거 충족이 기준선과 같은 43/58이었다. 동료 304문항에서는 기준선 126/304, 그래프 확장 121/304, 절별 검색을 더한 설정 109/304였고, 동료 Recall@8은 기준선 59.73%에서 그래프 확장 58.94%로 내려갔다. 그래프 확장은 일부 누락 역할을 보충했지만 후보가 이미 충분한 질문에서 최종 선택 단계가 근거를 밀어내는 경우도 있었다. 해당 실험은 seed가 검색 후보에서 발견된 사건에 한정됐으므로, 찾지 못한 사건을 새 경로로 연결하는 그래프의 가능성을 평가한 것은 아니다. 따라서 이 기록은 “그래프가 쓸모없다”는 결론이 아니라 seed 회수, 출처 검증, 후보 예산을 분리해서 봐야 한다는 근거다. [그래프 확장 결과](../../records/advanced-graph-expansion-20261109/report.md)

복합 질문 기록에서는 SQLite 추가가 조건에 맞는 사건 집합을 찾는 작은 원문 대조 묶음에서 후보 확보를 2/6에서 6/6으로 높였고, 최종 충족을 2/6에서 5/6으로 높였다. 해당 설정의 동료 표본에서는 기준 대비 하락도 생겨, 요청별 근거 선택 단계에서 회복했다. 이는 이미 구조화된 조건 조회에 SQL이 맞는다는 근거이지, 전체 검색을 바꾸는 근거는 아니다. [복합 검색 계획 비교](../../records/advanced-planned-retrieval-20261109/report.md)

현재 감사의 완전 CT 계약 코드 2,730개는 각기 사건 하나에만 대응한다. HL 2,821개와 EV 7,917개도 각각 한 사건에 대응한다. 공유 계약의 다중 사건 연결은 이 스냅샷에서 관측된 사실로 전제하지 않는다. 반면 장비 종류는 수천 건이 공유하므로 관계의 선택도를 구별해야 한다. [코드 타입별 관계 통계](data-audit.md)

## GraphRAG 이름 아래 다른 질의 방식

| 방식 | 검색·추론 동작 | ADEN에서 맞는 질문 | 비용과 주의점 |
| --- | --- | --- | --- |
| Microsoft GraphRAG Local | 질의와 관련 있는 엔터티를 찾고, 그 엔터티의 인접 관계·속성·커뮤니티 보고서와 원문 텍스트 조각을 함께 문맥에 넣는다. | 고유한 인물·기관·개념을 중심으로 여러 비정형 문서에 흩어진 주변 사실을 모으는 질문. | seed 엔터티 인식과 entity resolution 품질에 의존한다. 같은 표기·약칭이 여러 대상을 가리키거나 숫자·효력일·조건을 정확히 가려야 하면 원문과 검증된 필드의 직접 조회가 기준이어야 한다. |
| Microsoft GraphRAG Global | 생성된 커뮤니티 보고서를 대상으로 map-reduce 방식으로 corpus 전체에 대한 답을 만든다. 문서는 이를 resource-intensive로 명시한다. | “여러 자료에서 반복되는 업무상 원칙은 무엇인가?” 같은 전체 corpus의 개방형 요약. | 추출 그래프와 커뮤니티 보고서의 생성·갱신 비용이 든다. 수치 집계, 정확한 날짜 상태, 특정 사건의 증빙을 요약 결과만으로 확정할 수 없다. 보고서에서 원문 쪽으로 되돌아갈 연결이 필요하다. |
| Microsoft GraphRAG DRIFT | 관련 커뮤니티 보고서를 시작점으로 넓은 답과 후속 질문을 만들고, 후속 질문마다 local search로 세부화한다. 결과는 관련도에 따라 계층화된다. | 하나의 명확한 질문에서 여러 방향으로 조사 단서를 찾아야 하는 탐색적 질문. | 후속 질의 수와 깊이에 따라 검색·LLM 호출이 늘어난다. 결과 범위가 넓어지는 만큼 후보의 중복·잡음과 비용을 함께 제한해야 한다. |
| Deterministic entities + SQL | 검증된 typed ID와 명시 관계로 정확히 필터링하고 join한다. 질문 해석은 기존 query plan/조회기가 지원하는 단서만 사용한다. | 알려진 사건의 역할별 원본, 확인된 계약·장비·예약 키, 정규화된 날짜·상태·요율 조건. | 추출 범위와 스키마 밖의 사실을 알지 못한다. 모든 필드의 의미·효력·단위가 검증됐다는 조건에서만 답에 쓴다. |

Microsoft GraphRAG의 표준 색인은 텍스트 단위별 엔터티와 관계 추출, 엔터티/관계 요약, 커뮤니티 보고서 생성을 포함한다. Microsoft는 GraphRAG에서 그래프 추출이 색인 비용의 약 75%라고 추정한다. FastGraphRAG는 NLP 기반 명사구와 공출현 관계를 써서 언어모델 비용을 낮추는 대신 그래프가 더 noisy하며, 기본 NLTK/정규식 경로는 주로 영어에 적합하다고 설명한다. 한국어 약어·표 양식이 섞인 ADEN에서 이 수치를 실제 비용으로 간주하거나 FastGraphRAG 정확도를 가정할 수는 없다. GraphRAG를 쓰더라도 프롬프트 튜닝과 대상 corpus에서의 관계 검수가 필요하다. [Microsoft 색인 방식과 비용 설명](https://microsoft.github.io/graphrag/index/methods/) · [쿼리 모드 개요](https://microsoft.github.io/graphrag/query/overview/) · [Local Search](https://microsoft.github.io/graphrag/query/local_search/) · [DRIFT Search](https://github.com/microsoft/graphrag/blob/main/docs/query/drift_search.md)

### Neo4j GraphRAG retriever는 무엇을 더하는가

Neo4j의 Python GraphRAG 라이브러리는 `VectorRetriever`, `HybridRetriever`, `VectorCypherRetriever`, `HybridCypherRetriever`, `Text2Cypher`, `ToolsRetriever` 등을 제공한다. `HybridRetriever`는 vector와 full-text index를 함께 검색한다. `VectorCypherRetriever`/`HybridCypherRetriever`는 검색된 노드를 시작점으로 개발자가 준 Cypher retrieval query를 실행해 관계와 주변 정보를 가져온다. 따라서 그래프 DB로 이전하지 않고도 익숙한 패턴은 다음과 같이 구분해 적용할 수 있다.

1. 질문의 단어가 업무 용어와 표준 용어를 섞거나 예약·계약 코드가 들어 있으면 현재와 같은 lexical + dense seed 검색을 쓴다.
2. 확인된 사건 ID나 검색된 원본 페이지에서 역할·계약·행위자·연결 페이지를 가져올 때는 그 관계를 SQL 또는 Cypher로 명시 조회한다.
3. 자유 질문으로 SQL/Cypher를 생성시키는 경로는 생성 쿼리 오류와 허용 범위 검증이 필요하다. Neo4j 문서도 Text2Cypher 결과가 문법적으로 유효함을 보장하지 않고, 실행할 수 없으면 오류를 낸다고 경고한다. 정확한 필드와 조인 경로가 이미 정해진 ADEN 질의에 자연어→임의 쿼리를 추가하면 안전성보다 복잡성이 먼저 늘 수 있다.

Neo4j 문서는 hybrid retrieval 이후 graph traversal로 추가 문맥을 반환하는 패턴과 Text2Cypher의 제약을 분명히 구분한다. `VectorCypherRetriever`는 기계가 seed를 정하는 방식이며, `retrieval_query`가 실제로 통과할 관계 유형·방향·깊이를 제한한다. 관계가 고정되고 경로가 짧은 업무에서는 같은 결과를 현재 SQLite의 명시 query로 제공할 수 있다. [Neo4j GraphRAG Python RAG User Guide](https://neo4j.com/docs/neo4j-graphrag-python/current/user_guide_rag.html)

## SQL join과 graph traversal의 선택 경계

| 질의 구조 | 현재 적합한 조회 | 그래프가 추가 가치를 낼 때 |
| --- | --- | --- |
| 알려진 사건 ID → 사건 → 다섯 역할 → 원본 쪽 | PK/FK와 role 조건을 가진 SQL join. 페이지는 기존 출처 키로 재구성한다. | 이 고정 경로만으로는 그래프 검색의 추가 이점이 작다. |
| 서로 다른 두 사건의 청구·수납 기록 비교 | 두 사건을 독립 식별하고 대상별 SQL 조회 후 원문 페이지를 보존한다. | 양쪽 사건을 공통 계약 개정·정책 조항·수정 이력 같은 더 긴 연결 경로로 이어야 하고 그 경로가 사전에 고정되지 않은 경우. |
| 예약 키, 계약 ID, 상태, 날짜 구간처럼 스키마가 알려진 조건 필터·집계 | typed field, 유효 시점, 단위, 상태를 검사한 SQL. | 조건이 필드에 없고 문서에서 찾아야 하면 먼저 원문 검색으로 값을 증거와 함께 추출한다. 연결 경로 자체가 여러 관계 단계를 넘을 때 그래프를 검토한다. |
| 3~4개 원문이 필요한 복합 질문 | 질문 계획에서 각 독립 요청과 역할을 구분하고, 확정된 각 대상에 SQL/기존 lookup을 수행한다. 최종 문맥은 요청별 근거를 보존한다. | 검색해야 할 다음 대상이 앞의 근거를 읽고서야 정해지고, 해당 근거가 다른 문서 유형·사건·계약을 반복해서 가로지르는 경우. 문서 개수가 많다는 이유만으로 그래프를 택하지 않는다. |
| “여러 사건에서 공통적으로 어떤 예외가 반복되나?” | 이미 정규화된 규칙 분류·효력 범위는 SQL 집계 후 원문 검증. open-text 표현의 전체 주제를 묻는다면 Global/DRIFT 계열을 후보로 볼 수 있다. | 커뮤니티/관계 요약이 원문 쪽을 회복하고, 필수 조건·예외의 누락과 요약 비용을 통제할 수 있어야 한다. |
| 연결 길이가 미리 정해지지 않은 경로 질의 | 시작 노드를 정확히 식별하고 관계 종류와 provenance를 제한한 recursive SQL도 가능하다. | 여러 종류의 노드를 오가는 반복·가변 길이 연결이 핵심이며 경로 자체의 순위나 이웃 탐색이 반복되는 경우 graph pattern/PPR/path retrieval이 질의 표현과 반복 분석에 유리할 수 있다. |

SQL은 고정 경로만 표현할 수 있는 것은 아니다. SQLite recursive CTE는 tree·graph를 걷는 질의도 지원한다. 관계가 행과 foreign key로 명확하고 조회 경로가 안정적이면 recursive SQL도 가능하다. graph DB의 차이는 단순히 “여러 hop을 할 수 있느냐”가 아니라, 관계 패턴이 핵심 데이터이고 다양한 변수 길이 경로를 자주 질의하는지, 경로·이웃 기반 연산을 반복하는지에 있다. Neo4j 문서도 variable-length path를 지원하는 동시에 길이 상한이 크거나 패턴이 너무 일반적이면 수많은 path를 만들어 느려질 수 있으므로 관계·노드 조건으로 traversal을 제한하라고 안내한다. [SQLite recursive CTE](https://www.sqlite.org/lang_with.html) · [Neo4j variable-length paths](https://neo4j.com/docs/cypher-manual/current/patterns/variable-length-paths/)

## 엔터티 연결, blocking, 모호함

Entity resolution은 이름 비슷한 레코드를 같은 실체로 합치는 기능으로 축약하면 위험하다. 우선 서로 다른 타입의 식별자를 분리해야 한다. 예약 ID, 사건 ID, 계약 ID, 업무 기록 ID, 장비 인스턴스 번호, 장비 종류(`20GP`)는 각각 의미와 유일성 범위가 다르다. 원문에서 같은 사건임을 확인한 `case_id + role + source_id`는 결정론적으로 연결할 수 있다. 띄어쓰기·구두점·전각문자 정규화는 비교를 돕는 표준화일 뿐, 업무상 같은 엔터티임을 입증하지 않는다.

Blocking은 전체 레코드 쌍을 다 비교하지 않도록 공유 키로 후보 블록을 만든다. Peter Christen의 record linkage 조사에서는 오류나 누락이 있는 blocking key가 true match를 다른 블록에 넣어 놓칠 수 있고, 빈번한 값은 큰 블록을 만들어 후보 비교 수를 늘린다고 설명한다. 작은 블록은 연산을 줄이는 대신 true match를 놓칠 수 있으며, candidate pair reduction만으로 품질을 평가할 수 없으므로 pairs completeness(재현율)와 pairs quality(정밀도)를 함께 보아야 한다. `20GP`처럼 여러 사건에 공유되는 규격은 전형적인 낮은 선택도 단서다. 이 값 하나로 사건·장비 개체를 병합하거나 특정 사건을 자동 확정할 근거가 될 수 없다. [Christen, A Survey of Indexing Techniques for Scalable Record Linkage and Deduplication](https://cs.brown.edu/courses/cs227/archives/2016/papers/IndexingDeduplication.pdf)

권고되는 연결 경계는 다음과 같다.

- **정확 ID 조회:** 허용된 필드에서 읽은 값인지 검증하고, 유일한 검증 레코드가 있을 때만 자동 확정한다. 조회 규칙과 원본 출처를 기록한다.
- **간접 후보 검색:** 화주·화물·규격·날짜처럼 이미 지원하는 복수 단서를 기존 lookup과 대조한다. 상충 조건을 조용히 버리지 않는다. 일치 후보가 둘 이상이면 후보 집합과 차이를 남긴다.
- **유사도 기반 매칭:** 후보를 만들거나 재정렬하는 데 쓸 수 있지만 단독 확정 키로 쓰지 않는다. 높은 점수 하나가 업무 식별자의 동등성을 뜻하지 않는다.
- **모호함 처리:** 여러 후보, 불완전한 기록, 서로 다른 의미의 같은 날짜는 모호 상태로 보존하고 필요한 구분 정보를 받는다. 후보가 하나 남았다는 사실만으로 근거가 검증된 것은 아니다.

AWS Entity Resolution의 실무 문서도 exact/fuzzy 조건을 설정할 수 있는 rule-based matching과 match ID뿐 아니라 이를 만든 match rule을 출력한다. 이 설계가 가리키는 요점은 매칭 결과를 근거 없는 상수로 소비하지 않고 어떤 조건이 연결을 만들었는지 추적하는 것이다. 범용 엔터티 병합 제품을 추가해야 한다는 뜻은 아니다. [AWS rule-based matching](https://docs.aws.amazon.com/entityresolution/latest/userguide/creating-matching-workflow-rule-based.html) · [AWS advanced rule options](https://docs.aws.amazon.com/entityresolution/latest/userguide/rule-based-mw-advanced.html)

## 표·정형 레코드와 정책 문서의 혼합 검색

ADEN IS 3,750쪽은 한 표만 있는 데이터베이스가 아니다. 반복 양식의 표와 안내 문단, 업무 설명, 각주·예외가 PDF 안에 섞인다. 표 셀을 평문으로 이어 붙이면 행/열 관계가 흐려지므로, 현재 정제 문서가 정한 대로 표 제목·열 머리글·원문 행/셀 위치·같은 절의 조건을 묶어야 한다. 결과를 인용할 때는 각 정형 행을 PDF의 `source_id`, 페이지, 줄 범위로 되돌려야 한다. [지식 데이터 설계](../../../../docs/design/knowledge-data.md) · [Advanced 데이터 패턴](../../advanced-data-patterns-and-search.md)

질문 유형에 따라 검색 역할을 나누는 것이 자연스럽다.

| 사용자 질문 | 처리 경로 | 답변 근거로 요구할 것 |
| --- | --- | --- |
| 식별된 사건의 청구액, 실제 반납일, 역할 기록 | 정규화 필드/SQL lookup | 값, 역할, 상태·단위, 원본 쪽. 실제/예정/입력 날짜를 섞지 않는다. |
| “왜 이 비용이 적용됐나?”, 적용 예외·개정·효력 시점 | BM25/dense 원문 검색과 필요한 역할 원본 | 적용 조건·예외·개정 효력의 문장과 원문 출처. 기존 정형 필드가 모든 규정 의미를 포괄한다고 가정하지 않는다. |
| 표 행과 업무 설명을 함께 묻는 합성 질문 | 먼저 서술에서 대상/조건을 찾고, 검증된 표에서 필요한 값·계산을 SQL로 조회한 뒤 두 근거를 함께 전달 | 텍스트 단서와 표 원문을 별도로 보존하고 각 중간 결과를 출처와 연결한다. |
| 여러 문서의 값 합계·비율 | 필드 추출이 완전하고 같은 통화·단위·유효 시점·정정 상태를 확인한 경우 SQL 계산 | 입력 행과 적용 범위, 계산 근거. 조건을 확인할 수 없는 사건은 계산에서 빼고 보류한다. |

EMNLP 2025 TableRAG는 이종 문서 질문을 질문 분해 → 텍스트 검색 → SQL 프로그램·실행 → 중간 답 조합으로 처리한다. 논문은 표를 flatten하는 방식에서 표 구조 손실이 생긴다고 지적하며 자체 HeteQA와 공개 benchmark에서 개선을 보고한다. 이는 텍스트와 SQL을 섞는 접근을 뒷받침하지만 ADEN의 추출 정확도나 효과를 보장하지 않는다. ADEN의 적용 단위는 새 TableRAG 엔진 도입이 아니라, 원문 검색이 필요한 정책 문장과 정확히 추출된 행 값의 책임을 분리하는 것이다. [TableRAG: A Retrieval Augmented Generation Framework for Heterogeneous Document Reasoning, EMNLP 2025](https://aclanthology.org/2025.emnlp-main.710/)

## 다중 hop 검색 알고리즘이 해결하는 문제

| 알고리즘 / 연구 | 핵심 생각 | ADEN 데이터에서 유효할 조건 | 비용·실패·적용 한계 |
| --- | --- | --- | --- |
| HippoRAG / Personalized PageRank | 문서에서 LLM OpenIE 관계 그래프와 동의어 연결을 만들고, 질의 엔터티를 seed로 PPR을 계산해 관련 passage를 순위화한다. 질의 한 번의 PPR로 여러 passage 사이 관련성 탐색을 한다. | 관련 단서가 서로 다른 문서에 나뉘고 관계를 미리 모두 지정할 수 없는 path-finding 질문. 예: 계약·업무 기록의 연결을 거쳐 대상 사건 후보를 찾아야 하는 반복 질문. | 색인 시 엔터티·관계·동의어 추출과 질의 entity linking이 필요하다. 논문의 작은 error analysis에서 오류의 48%는 NER, 28%는 OpenIE, 24%는 PPR로 분류됐고, 저자들은 긴 문서에서 OpenIE 일관성과 확장성 검증을 한계로 적었다. 약어·상태·시간 문맥이 중요한 계약문서에서는 누락·잘못된 entity merge가 그대로 잘못된 전파를 만든다. 논문의 “최대 20%” 및 10–20배 저비용 주장은 해당 multi-hop benchmark와 IRCoT 비교 조건의 결과다. ADEN의 성능 예측값이 아니다. |
| LightRAG | 낮은 수준의 엔터티/관계 retrieval과 높은 수준의 포괄적 retrieval을 함께 제공하는 dual-level 검색이며, 그래프와 벡터 표현을 결합한다. 저자들은 증분 데이터 통합 방식을 보고한다. | 신규 자료가 자주 들어오고 그래프 수준의 특정 엔터티 조회와 넓은 주제 조회를 함께 반복하며, 색인 업데이트 비용이 실제 병목인 경우. | “Fast”는 해당 논문 설정과 benchmark에 대한 이름이다. 엔터티·관계 추출 비용과 오류, 대상 corpus 재현성을 검증해야 한다. 증분 업데이트가 기존 레코드의 사실 정확도나 동명이인 분리를 자동 해결하지 않는다. EMNLP 2025 실험 결과를 한국어 표·인계 기록에 일반화할 수 없다. |
| PathRAG | 시작 노드에서 인덱싱 그래프의 후보 관계 경로를 가져오고 flow-based pruning으로 중요 경로를 골라 구조를 보존해 문맥에 넣는다. 이웃 전체를 붙이는 방식의 중복 정보를 줄이는 데 초점을 둔다. | seed 검색은 괜찮지만 이웃/공출현 확장으로 불필요한 후보가 많아지고, 답에 필요한 관계의 순서를 함께 전달해야 하는 경우. | pruning은 누락된 source node나 틀린 entity resolution을 바로잡지 않는다. 경로가 너무 많거나 넓은 관계를 허용하면 확장 자체가 비쌀 수 있다. AAAI 2026 논문 여섯 dataset의 평가 결과는 참고 방법이며, ADEN 후보·최종 문맥 실험을 대체하지 않는다. |
| PropRAG | 개별 관계 triple 대신 조건·맥락을 담은 proposition을 만들고, 미리 만든 표현 그래프에서 LLM-free beam search로 다중 단계 근거 경로를 찾는다. 저자들은 triple의 context collapse가 조건·출처·n-ary 관계를 잃을 수 있다고 문제 삼는다. | 약정 문장에 조건·효력·예외가 함께 있어 관계 triple로만 압축하면 해석이 달라질 위험이 큰 경우. 검토용 연구 패턴으로 참고할 가치가 있다. | proposition 추출은 오프라인 LLM 과정과 별도 provenance가 필요하다. 추출문이 원문을 대체하지 않는다. benchmark에서의 zero-shot Recall/F1은 ADEN 대상 검증이 아니다. 현재 검색 기준에 별도 proposition index가 이미 있다는 뜻도 아니다. |

Graph traversal은 추출 관계 품질을 따라간다. HippoRAG 논문도 엔터티 중심 검색이 쿼리의 문맥 단서를 덜 사용할 수 있고, NER/OpenIE 오류가 retrieval miss의 주 원인이 될 수 있음을 보인다. PathRAG는 인접 노드를 넓게 붙이는 데서 생기는 중복과 noise를 문제 삼는다. 이 결과를 ADEN에 옮기면 “그래프를 추가한다”보다 다음 검증 질문이 중요하다. 이 seed는 질문에서 어떤 출처를 근거로 잡았나? 연결 edge는 어떤 원문과 관계 유형으로 확인됐나? 여러 사건 중 모호한 하나를 그래프 점수가 자동으로 고르지 않았나? 경로 보강이 최종 8쪽 안에서 기존 필수 근거를 밀어내지 않았나?

## 연구 자료로부터 정리한 적용·중단 조건

1. **현재 기준 유지:** 확인된 ID와 역할 매핑에는 기존 HG lookup/SQLite를 사용한다. 정형 사건 관계를 그래프 이름으로 재구현하지 않는다. 자료가 많다는 점만으로 indexing graph를 생성하지 않는다.
2. **텍스트 검색과 SQL 결합:** 단서·정책·예외 문장은 기존 hybrid 검색, 정확히 확인한 값·관계는 명시 SQL, 최종 인용은 원본 페이지로 이어간다. 추출 상태·효력·단위가 미확정이면 SQL 계산에 포함하지 않는다.
3. **새 graph retrieval의 입증 조건:** 고정 경로 lookup이 못 찾는 held-out 다중 hop 질의를 먼저 분류한다. “기존 후보에 있던 다른 역할 추가”와 “초기 후보에 없던 관계 경로를 찾아 새 사건을 찾음”을 별도 분모로 둔다. entity linking/edge 정밀도·재현율, 오연결, 필수 원문 Recall@8 및 전체 충족률, 기존 근거 하락, 최종 token, 온라인 latency·LLM 호출량과 증분 색인 비용을 함께 기록한다.
4. **모호함 보존:** `20GP`와 같이 공유되는 값은 차별력이 낮은 조건이다. 단서가 충돌하거나 여러 사건이 일치하면 목록/모호 상태를 남긴다. fuzzy score나 PPR rank만으로 확정 ID를 만들지 않는다.
5. **Global/DRIFT 채택 범위:** 질문이 corpus 차원의 열려 있는 주제 요약일 때만 평가 후보로 둔다. 주제가 이미 정규화됐거나 사건별 집계·고정된 표를 묻는 질문에는 SQL 집계를 먼저 쓴다. 모든 요약 근거를 원문 page로 추적할 수 있는지를 조건에 포함한다.
6. **비용이 품질을 앞서지 않게 확인:** 외부 연구의 benchmark 우위는 ADEN에 대한 증거가 아니다. 검증 세트에서 새 필수 원문 근거를 늘리지 못하거나 기존 근거를 밀어내는 비용이 개선보다 크면 해당 retrieval 확장을 적용할 근거가 없다.

## 참고 자료 목록

모든 웹 자료는 2026-11-10 확인했다. 문서 페이지가 수시로 바뀔 수 있으므로 링크는 특정 주장 확인용이며, 수치·성능 주장은 각 연구의 자체 설정으로 한정한다.

| 출처 | 출처 성격 | 확인한 주장과 제한 |
| --- | --- | --- |
| [Microsoft GraphRAG Query Overview](https://microsoft.github.io/graphrag/query/overview/) | 공식 프로젝트 문서 | Local은 특정 엔터티 중심으로 그래프와 문서 텍스트를 결합하고, Global은 모든 community report를 map-reduce로 검색해 resource-intensive하며, DRIFT는 community context로 follow-up을 만들어 local search를 세분화한다. |
| [Microsoft GraphRAG Indexing Methods](https://microsoft.github.io/graphrag/index/methods/) | 공식 프로젝트 문서 | 표준 색인의 LLM 기반 entity/relation extraction·summary·community report, FastGraphRAG의 NLP 공출현 대안, 약 75% indexing cost 추정과 noisier graph trade-off. 비용 비율은 Microsoft 설명을 ADEN 실측으로 해석하지 않았다. |
| [Microsoft GraphRAG Local Search](https://microsoft.github.io/graphrag/query/local_search/) 및 [DRIFT Search](https://github.com/microsoft/graphrag/blob/main/docs/query/drift_search.md) | 공식 프로젝트 문서 | Local search가 구조화 graph와 원문 text unit을 결합한다. DRIFT는 community-report primer와 follow-up/local refinement를 쓴다. |
| [Neo4j GraphRAG Python User Guide](https://neo4j.com/docs/neo4j-graphrag-python/current/user_guide_rag.html) | 공식 라이브러리 문서 | Vector/Hybrid, vector 또는 hybrid 후 Cypher graph traversal, Text2Cypher retriever 구분. 문서는 Text2Cypher 생성 쿼리의 문법 유효성을 보장하지 않음을 경고한다. |
| [Neo4j Cypher Variable-length Paths](https://neo4j.com/docs/cypher-manual/current/patterns/variable-length-paths/) | 공식 DB 문서 | 변수 길이 경로 탐색과 함께 과도하게 일반적/긴 패턴의 path 수·지연 위험 및 traversal 제약 필요성을 설명한다. |
| [SQLite WITH Clause](https://www.sqlite.org/lang_with.html) | 공식 DB 문서 | Recursive CTE로 tree/graph를 순회할 수 있다. SQL이 관계 그래프를 전혀 표현할 수 없다는 주장을 반박한다. |
| [Christen, A Survey of Indexing Techniques for Scalable Record Linkage and Deduplication](https://cs.brown.edu/courses/cs227/archives/2016/papers/IndexingDeduplication.pdf) | IEEE TKDE 논문 원문, 2012 | Blocking key 선택, 후보 수와 true-match 누락 trade-off, pairs completeness/quality 측정. 실제 업무 자료와 성능 수치는 ADEN corpus와 다르다. |
| [AWS Entity Resolution Rule-based Matching](https://docs.aws.amazon.com/entityresolution/latest/userguide/creating-matching-workflow-rule-based.html) 및 [Advanced Rules](https://docs.aws.amazon.com/entityresolution/latest/userguide/rule-based-mw-advanced.html) | 공식 제품 문서 | exact/fuzzy 조건과 연결에 적용한 match rule 기록 사례. ADEN에 AWS 서비스를 도입해야 한다는 근거는 아니다. |
| [TableRAG, EMNLP 2025](https://aclanthology.org/2025.emnlp-main.710/) | ACL Anthology 논문 원문 | 이종 텍스트·표 추론에 query decomposition, text retrieval, SQL 실행, 중간 답 조합을 결합한다. 평가 결과는 저자 benchmark와 실험 조건에 한정된다. |
| [HippoRAG, NeurIPS 2024](https://proceedings.neurips.cc/paper_files/paper/2024/hash/6ddc001d07ca4f319af96a3024f6dbd1-Abstract-Conference.html) 및 [arXiv 원문](https://arxiv.org/abs/2405.14831) | NeurIPS 논문·원문 | OpenIE KG와 PPR seed retrieval, 다중 hop QA 평가. 저자들은 benchmark 상대 개선을 보고하며, 원문 error analysis에서 NER/OpenIE/PPR 실패와 scalability 검증 필요성을 적는다. |
| [LightRAG, Findings of EMNLP 2025](https://aclanthology.org/2025.findings-emnlp.568/) | ACL Anthology 논문 원문 | dual-level retrieval, graph+vector, 증분 업데이트를 제안·평가한다. 제목의 “Simple and Fast”와 benchmark 결과는 ADEN 지연/정확도 보장이 아니다. |
| [PathRAG, AAAI 2026](https://ojs.aaai.org/index.php/AAAI/article/view/40268) | AAAI 논문 원문 | flow-based relational path pruning으로 중복 이웃을 줄이고 경로 중심 prompt를 구성한다. 논문 dataset 결과는 ADEN에서의 재현을 대신하지 않는다. |
| [PropRAG, EMNLP 2025](https://aclanthology.org/2025.emnlp-main.317/) | ACL Anthology 논문 원문 | triple보다 맥락을 보존하는 proposition과 LLM-free online beam search를 제안한다. proposition의 오프라인 추출과 출처 검증 비용은 여전히 필요하다. |

### 자체 검토 범위

- 저장한 문서의 링크·상대경로와 수치를 다시 읽어 점검했다. 내부 수치는 이 폴더의 현재 데이터 패턴·기존 실험 보고서에서 가져왔고 외부 성능 주장과 구분했다.
- 기존 실험을 반복하거나 새 테스트를 실행하지 않았다. 여기서 제시한 적용 조건은 문헌과 저장소 기록의 교차 검토이며 새 구현 결정이나 성능 실측은 아니다.
