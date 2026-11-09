# Advanced 문서에 적용할 검색·답변 패턴

## 범위

개인 Advanced 오프라인 실험에서 원본 PDF 120개와 복원 페이지 18,000쪽을 사용한다. 실험 당시 기준선은 Bigram-ID BM25 후보 20쪽, KURE nano 상위 3쪽 고정, BM25 순서로 최대 8쪽, 문맥 상한 4,000토큰이다. 공통 문맥 조립과 페이지·인용문 채점을 재사용한다. 각 변형에서 검색·선택·렌더링 중 한 단계의 영향을 분리한다. [실험 당시 기준선](../decisions.md#2026-11-08--advanced-개인-기본값을-bigram-id와-kure-상위-3쪽-고정으로-변경)

기존 추가 280문항과 공식 60문항은 비교에 사용한 개발용 세트다. 근거 범위 확장 세트에서는 파일럿 40문항을 별도 집계한다. 블라인드 후보 410문항의 사용 시점은 해당 세트의 검수·설정 고정 절차를 따른다. 검색과 문맥 구성의 입력은 질문·원본 파생 자료이며, 정답과 근거 라벨은 결과 확정 후 채점에 사용한다. [파일럿과 블라인드 세트](../../evaluation/advanced_coverage_v2/README.md)

전 페이지 증강 6,500문항은 별도 검색·문맥 평가다. 기본 근거 기준 페이지·문서 Recall과 인용문 Recall에 더해, 한 사실에 필요한 기본 또는 검증된 대체 인용문 묶음 전체가 문맥에 들어왔는지 집계한다. 블라인드 후보와 사건 키·기록 번호·출처가 겹치는 397문항은 전체 범위 점검에 포함하고 나머지 6,103문항과 구분한다. 질문은 통제된 문장 패턴으로 생성됐으며 사람의 문항별 검수가 남아 있다. [전 페이지 증강 세트](../../evaluation/advanced_full_coverage/README.md)

## 후보와 근거

| 변형 | 공개 레퍼런스 | 이 문서에 맞춘 실험 |
|---|---|---|
| 필드별 검색 | Elasticsearch의 [Combined fields](https://www.elastic.co/docs/reference/query-languages/query-dsl/query-dsl-combined-fields-query)는 BM25F에 기반해 필드 통계와 가중치를 결합한다. | 페이지 본문 점수에 제목·절 필드를 별도 검색 신호로 추가한다. 기존 p800 실험에서 머리말을 제거하자 추가 세트의 후보 문서 Recall@20이 86.61%에서 37.05%로 낮아졌다. |
| 표 행과 부모 문맥 | [Azure Document Intelligence](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout?view=doc-intel-4.0.0#tables)는 행·열·셀·원문 위치를 추출한다. [Bedrock 계층 청킹](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-chunking.html#hierarchical-chunking)은 작은 단위로 검색한 뒤 부모 문맥을 전달한다. | 동일 페이지 순위에서 질문에 관련된 표 행과 열 이름, 해당 절·조건을 원본 페이지 출처와 함께 전달한다. 로컬 정제 도구로 만든 표 행을 사용한다. |
| 근거 역할과 조건부 추가 검색 | [Weaviate MMR](https://docs.weaviate.io/weaviate/search/hybrid#diversity-selection-mmr)은 결과 집합의 중복을 낮춘다. [xQuAD](https://theses.gla.ac.uk/4106/)는 커버되지 않은 질의 측면을 고려한다. [Azure agentic retrieval](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)은 복합 질문에 하위 질의를 사용할 수 있다. | 계약 조건·실제 사건일·승인·청구처럼 질문에서 요구된 역할을 기존 후보 안에서 우선 확보한다. 부족한 역할만 로컬 어휘 질의로 제한적으로 보강한다. |
| 업무 유효 시점과 계산 | [Oracle Temporal Validity](https://docs.oracle.com/en/database/oracle/oracle-database/19/adfns/design_basics.html)에서는 업무상 유효한 기간을 조회 조건으로 쓴다. [TableRAG 연구](https://aclanthology.org/2025.emnlp-main.710/)는 검색과 표 연산을 분리한다. | 기준 시점과 적용 조건은 명시 근거로 제한한다. 구조화된 값으로 계산 조건을 모두 확인할 수 있는 범위에서만 결정론적으로 계산하고, 근거가 부족하면 확인 대상만 전달한다. |

현재 복원 페이지에서 대조 가능한 인용문은 추가 세트 614개, 공식 세트 176개였다. 후보 20쪽에서 각각 548개와 152개, 최종 8쪽에서 534개와 145개, 실제 4,000토큰 문맥에서 534개와 144개가 남았다. 현재 기준선에서는 후보 검색 단계의 개선 여지가 가장 컸다. 표의 셀 결합과 유효 시점 적용은 인용문 포함량 외에 답변 점수로도 확인할 필요가 있다. [기존 순위 기록](../advanced-factor-ablation-20261108/selected-results.json) · [로컬 표 스키마](../../../../app/ingestion/curated_schema.py)

검색 벤치는 전체 개발 세트와 파일럿에서 후보 문서·페이지 Recall, 최종 인용문·근거 그룹 커버리지, 실제 문맥 토큰과 워밍업 후 p50·p95를 기록한다. 답변 표본에서는 기존과 동일한 `gpt-6-luna`로 생성과 채점을 수행하고 사실·조건·근거·판단을 분리한다. 같은 질문에서 최종 문맥이 동일하면 한 결과를 공유한다. 실행 로그와 집계는 문항 ID, 출처 위치, 점수와 사용량으로 구성한다.

개인 실험 답변 표본은 파일럿의 답변 유형 세 가지에서 6개씩, 추가 확인·보류에서 3개씩 고정 추출한 24문항이다. 여섯 조건으로 최대 생성 144회와 채점 144회를 계획했고, 이전 실행의 토큰 사용량을 기준으로 비용을 약 $0.16으로 추정했다. 실제 호출 수는 문맥 중복 제거와 생성 성공 여부에 따라 달라진다. 첫 문항에서 생성·채점과 사용량을 점검하고 API 접근·할당량 오류가 반복될 수 있는 경우 부분 기록을 보존한다. [GPT-6 Luna 표준 요금](https://developers.openai.com/api/docs/models/gpt-6-luna)
