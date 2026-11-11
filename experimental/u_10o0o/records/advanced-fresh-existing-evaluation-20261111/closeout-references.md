# 이번 실험 종료 기록: 참고자료와 적용 범위

이 자료는 report.md의 측정값을 대신하거나 외부 문헌으로 결과를 정당화하지 않는다. **실제 구현·비교에 사용한 원리**, **아이디어의 문헌상 배경**, **이번에 적용하지 않은 구조**를 구분한다. 이번에 비교한 페이지 단위와 기록 단위는 이 데이터에서의 실험 변수다. 아래 문헌은 어느 쪽이 보편적으로 더 낫다고 정하지 않는다.

| 1차 자료 | 이번 실험과의 연결 | 이 자료만으로 증명할 수 없는 것 |
|---|---|---|
| Patrick Lewis et al., [“Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks” (NeurIPS 2020)](https://arxiv.org/abs/2005.11401) | 검색된 텍스트 단위를 외부 지식으로 사용해 생성 모델에 제공하는 RAG의 대표 설계 배경이다. 원문 페이지(A/D/E/F)와 필드·값을 펼친 기록 단위(B/C)를 같은 검색·리랭킹 흐름에서 따로 비교한 이유와 연결된다. | 논문은 우리 PDF에서 페이지와 기록 중 어느 단위가 더 좋은지, 또는 이번 정제·전개 규칙이 맞는지 증명하지 않는다. 이번 비교의 결론은 로컬 실험 결과만으로 판단한다. |
| Stephen Robertson, Hugo Zaragoza, [“The Probabilistic Relevance Framework: BM25 and Beyond” (2009)](https://doi.org/10.1561/1500000019) | A–F 공통의 키워드 검색기에서 사용한 BM25의 이론적 배경이다. 문서 내 용어 빈도와 길이를 고려하는 어휘 검색을 벡터 검색과 결합해 평가했다. | 이번 한국어 토큰화, 완전 코드 보존, k1=1.5, b=0.75의 최적성이나 우리 질문의 성능을 증명하지 않는다. 이 값들은 비교 조건으로 고정한 설정이다. |
| Gordon V. Cormack, Charles L. A. Clarke, Stefan Büttcher, [“Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods” (SIGIR 2009)](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf) | BM25와 벡터의 순위를 합친 5:5 RRF, 그리고 D→E에서 검색·관계 순위를 함께 반영한 병합을 이해하는 기준 자료다. E/F 관계 중복 처리 비교는 점수 원천별 순위 결합이 결과에 미치는 영향을 시험했다. | 논문이 이번 데이터에서 k=60, 5:5 가중치, Top 20이 최적임을 보이지 않는다. 특정 순위 결합의 일반 결과를 우리 필수 근거 충족률로 옮겨 해석할 수 없다. |
| Rodrigo Nogueira, Kyunghyun Cho, [“Passage Re-ranking with BERT” (2019)](https://arxiv.org/abs/1901.04085) 및 [KURE-Reranker-nano 모델 카드](https://huggingface.co/nlpai-lab/KURE-Reranker-nano) | 하이브리드 후보를 만든 뒤 질문–후보 쌍을 교차 인코더로 다시 점수화하는 마지막 순위 단계와 연결된다. 이번에는 KURE를 후보 최대 20개에 한 번 적용했고, 기록 선택 비교에서는 같은 후보와 KURE 순위를 고정했다. | BERT 논문의 다른 데이터셋 결과나 KURE 카드의 공개 벤치마크는 이 Advanced 문항에서의 정답 근거 회수나 CPU 지연을 보장하지 않는다. 리랭커 점수만으로 여러 요청의 근거가 모두 포함된다고도 할 수 없다. |
| Xiangrong Zhu et al., [“Knowledge Graph-Guided Retrieval Augmented Generation” (NAACL 2025)](https://aclanthology.org/2025.naacl-long.449/) | 관계를 이용해 초기 검색 결과에서 관련 단위를 확장하고 구성한다는 발상과 연결된다. C/D/E에서는 원문으로 확인한 완전 코드→사건→기록 연결을 제한적으로 사용했다. | 이번 구현은 논문의 그래프 추출·검색·조직화 파이프라인 재현이 아니다. 관계 보충은 검증된 명시 연결만 사용했으며, 논문 결과가 우리 관계 데이터나 성능을 검증하지 않는다. |
| Microsoft Research, [GraphRAG Global Search 공식 문서](https://microsoft.github.io/graphrag/query/global_search/) | 구현 범위를 구분하기 위한 공식 설명이다. Microsoft GraphRAG Global Search는 LLM이 만든 지식 그래프 커뮤니티 요약을 map-reduce로 검색·종합한다. 이번에는 그 색인·커뮤니티 보고서·요약 질의를 구현하지 않았다. | 원문에 기록된 코드 관계를 따라 후보를 보충하는 로컬 규칙을 “GraphRAG를 적용했다”고 부를 근거가 아니다. 이 문서는 본 실험 방식의 품질 비교 자료도 아니다. |
| Jaime Carbonell, Jade Goldstein, [“The Use of MMR, Diversity-Based Reranking for Reordering Documents and Producing Summaries” (SIGIR 1998)](https://www.cs.cmu.edu/~jgc/publication/MMR_DiversityBased_Reranking_SIGIR_1998.pdf) | 관련성과 중복 감소를 함께 고려하는 검색 결과 다양화의 고전적 배경이다. 마지막 8쪽에서 같은 사건의 반복 기록만 남지 않도록 요청된 기록 역할을 보존한다는 선택 문제와 개념적으로 연결된다. | 이번 기록 선택기는 MMR이나 학습된 다양화가 아니다. 지원되는 대상·요청 역할을 우선하는 결정적 선택 규칙이다. MMR 논문은 이 규칙의 정확도나 적용 범위를 뒷받침하지 않는다. |
| [BEIR: “A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models” (NeurIPS 2021)](https://arxiv.org/abs/2104.08663), [HERB: “Benchmarking Deep Search over Heterogeneous Enterprise Data” (EMNLP Industry 2025)](https://aclanthology.org/2025.emnlp-industry.34/), Nick Craswell et al., [“TREC Deep Learning Track: Reusable Test Collections in the Large Data Regime” (2021)](https://arxiv.org/abs/2104.09399) | BEIR와 HERB는 검색 평가가 여러 데이터·질문 유형과 실제 연결된 업무 자료를 포함해야 한다는 배경을 제공한다. TREC DL 자료는 기존 평가 컬렉션을 재사용할 때 반복 조정에 따른 선택 편향을 고려해야 함을 다룬다. 공식 60·카테고리 220은 이번에 **기존 세트 재검증·회귀 확인**으로 사용했으며, 미사용 일반화 검증 세트로 취급하지 않았다. | 외부 벤치마크의 문항·점수는 이번 평가에 포함되지 않았다. 기존 문항 재검증은 독립적인 새 검증이 아니고, 문항·정답의 포괄성이나 사람 검수 완료를 보장하지 않는다. |

고정한 임베딩 모델의 출처는 [Snowflake-ko 모델 카드(리비전 55ec6e9)](https://huggingface.co/dragonkue/snowflake-arctic-embed-l-v2.0-ko/blob/55ec6e9358a56d56af759bc8372e970caf8c305f/README.md)에서 확인할 수 있다. 이 링크는 실제 사용한 모델 산출물을 식별하기 위한 것이며, 카드의 공개 벤치마크를 ADEN Advanced 성능의 근거로 쓰지 않는다.

## 해석 시 고정할 구분

- **실제 비교:** 원문 페이지 검색과 기록 단위 검색, 명시 관계 보충, E의 관계 중복 순위 반영, F의 초기 후보 할당, 동일 후보에서의 기록 선택을 각각 보고서에 기록했다. 선택기 적용 전후로 기존 공식·카테고리 세트의 전체 근거 충족 변화는 없었다.
- **문헌에서 빌린 것은 원리와 평가 관점:** BM25, RRF, 교차 인코더, 관계 기반 확장, 결과 다양화의 배경을 확인했다. 공개 자료가 이번 데이터의 점수나 고정 파라미터를 대신 정해주지는 않는다.
- **적용하지 않은 것:** 전면적인 GraphRAG 인덱스, LLM 질의 계획, 두 번째 리랭킹, 응답 생성은 이번 종료 범위에 들어있지 않다.
- **결과의 지위:** 기존 Advanced 문항을 다시 사용한 결과는 회귀 진단이다. 최초의 새 검증 세트는 후속 D/E/F·선택기 조정에 재사용했고, 기존 공식·카테고리 재검증에서는 동결한 규칙을 바꾸지 않았다. 두 결과 모두 독립적인 미사용 업무 질문에 대한 성능으로 해석하지 않는다.
