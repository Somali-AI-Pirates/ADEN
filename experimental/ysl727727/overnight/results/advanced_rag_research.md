# 한국어 물류 매뉴얼 RAG: 검색 개선 리서치 (2026-11 기준)

대상 파이프라인: PDF → 페이지 텍스트 → 문단 청크(~800자 + 제목) → `intfloat/multilingual-e5-small`(Qdrant) + BM25 → RRF(20+20) → `BAAI/bge-reranker-v2-m3` → top-k≈8 → OpenAI LLM.
평가 지표: 문서 단위 Hit@k / Recall@k(필요한 book_id 합집합), k = 최종 컨텍스트 청크 수.
제약: RTX 5060 Laptop 8GB, 로컬 Qdrant, OpenAI API는 쓸 수 있으나 비용은 적당히, 실험은 몇 시간 안에, 정답 키를 검색이나 인덱싱에 쓰지 않음.

> 표기: **[근거]** 는 출처에 있는 수치·주장이고, **[추정]** 은 출처 수치를 바탕으로 이 과제에 맞춰 계산하거나 판단한 값이다.

---

## 0. 요약

1. **지표 구조부터 활용한다.** Recall@k가 "문서 합집합"이라서, 같은 문서의 청크가 top-8을 여러 칸 차지하면 지표에 기여하지 않는다. 후보 풀을 넓히고(예: 100+100 → rerank) 최종 선택을 문서 단위로 다양화하면(문서당 상한, MMR, 문서 점수 집계) LLM 비용 없이 가장 큰 개선을 기대할 수 있다. **[추정]**
2. **질문 분해(sub-query) + 슬롯 배분**은 multi-hop 검색에서 근거가 가장 일관된 질의 측 기법이다. 분해 + 리랭커 조합으로 MRR@10 +36.7%가 보고됐다. HyDE와 multi-query 확장은 정밀한 질의에서 효과가 제한적이었다.
3. **청크 문맥화**: 문서 제목, 장·절 경로, 문서 요약 prefix를 붙이는 방식은 LLM 없이 할 수 있다. 그다음 단계인 Anthropic식 Contextual Retrieval은 실패율 -35%(임베딩), -49%(+BM25), -67%(+리랭커)가 보고됐고, 2026년 금융 벤치마크에서도 "일관된 개선"으로 나왔다.
4. **한국어 임베딩 교체**: MTEB(kor) 검색에서 `nlpai-lab/KURE-v2`(154M, late-interaction, Apache-2.0)가 평균 nDCG@10 0.816, 장문 MLDR 0.716으로 단일벡터 모델들(bge-m3 MLDR 0.43)을 크게 앞선다. 단일벡터로는 `KURE-v1`, `dragonkue/snowflake-arctic-embed-l-v2.0-ko`, `bge-m3`(모두 568M)가 8GB에 들어간다. BM25는 Kiwi 형태소 토크나이저를 권장한다.
5. **GraphRAG**: MS GraphRAG Standard와 LightRAG는 18,000페이지 기준 인덱싱 토큰이 수억 개 규모라 몇 시간 안에는 비현실적이다. 대신 LinearRAG 방식(LLM 없이 NER로 만든 엔티티-문장-패시지 그래프 + PPR)이나 Kiwi 고유명사 공출현 그래프를 RRF의 세 번째 신호로 넣는 정도가 적당하다.
6. **ANN**: 청크 수가 수만~30만 개면 exact search도 충분히 빠르다. `QdrantClient(path=...)` 로컬 모드는 원래 brute-force exact search이므로 HNSW 튜닝이 효과가 없다. 서버 모드라면 exact 결과와 비교해 ANN recall@후보수 ≥0.99인지 확인한다.

---

## 1. 최신 고급 RAG 검색 기법 (2024–2026)

### 1.1 기법별 정리

| 기법 | 핵심 | 근거(출처) | 이 과제에 대한 판단 |
|---|---|---|---|
| **Contextual Retrieval** (Anthropic) | LLM이 청크마다 50–100토큰짜리 문서 내 맥락을 써서 청크 앞에 붙인 뒤 임베딩과 BM25에 둘 다 반영 | top-20 실패율 5.7% → 3.7%(-35%), +Contextual BM25 2.9%(-49%), +리랭크 1.9%(-67%). 원문은 top-150을 rerank해 20개를 썼고, 비용은 문서 토큰 100만 개당 약 $1.02(캐싱 가정) [Anthropic](https://www.anthropic.com/engineering/contextual-retrieval). 2026년 금융 QA(23,088 질의) 벤치마크에서도 "consistent gains" [arXiv 2604.01733](https://arxiv.org/abs/2604.01733) | **유망.** "조건이나 예외가 긴 문서 속에 묻혀 있는" 청크가 어느 업무의 어느 절인지 드러나게 해 준다. Advanced 문서(약 150p)는 문서 전체를 넣으면 비싸므로 **절 단위 창 + 문서 요약**으로 바꿔 쓴다(2.4절 비용) |
| **Zero-LLM 문맥 헤더**(title-chain prefix) | 문서 제목 > 장 > 절 경로를 청크 앞에 붙임 | header-split + semantic merge + title-chain 파이프라인으로 MRR@5 0.374 → 0.463(+23.8%) [arXiv 2608.00824](https://arxiv.org/abs/2608.00824). 기법 설명 [r/LangChain](https://redlib.hbubli.cc/r/LangChain/comments/1e5le9h/solving_the_outofcontext_chunk_problem_for_rag) | **가장 싸고 먼저 할 일.** 지금은 heading만 붙이고 있으므로 문서명, 장·절 경로, 페이지를 더하고 BM25에도 반영한다 |
| **Late chunking** (Jina) | 긴 문맥을 한 번에 인코딩한 뒤 토큰 임베딩을 청크 단위로 pooling | 문서가 길수록 이득이 크고 짧은 텍스트에서는 차이가 없다 [Jina](https://jina.ai/news/late-chunking-in-long-context-embedding-models/), [arXiv 2409.04701](https://arxiv.org/abs/2409.04701). 비교 연구에서는 Contextual Retrieval이 의미 보존은 낫고 비용이 크며, late chunking은 효율적이지만 관련성·완결성이 떨어졌다 [arXiv 2504.19754](https://arxiv.org/abs/2504.19754) | **보통.** 장문 컨텍스트 모델(bge-m3, KURE-v1은 8192)이 필요하다. 구현 부담에 비해 이득이 불확실하다 |
| **Parent-document / small-to-big** | 작은 청크로 매칭하고 큰 부모(절, 페이지)를 반환 | [LangChain ParentDocumentRetriever 설명](https://hub.athina.ai/blogs/how-to-implement-a-parent-document-retriever) | 문서 단위 Recall에는 직접 효과가 없다. 조건·예외를 답변에 담는 데는 도움이 되므로 **생성 단계용**으로 쓴다 |
| **RAPTOR** | 청크를 클러스터링하고 요약하는 과정을 재귀적으로 반복해 트리를 만듦(collapsed tree 검색) | QuALITY에서 GPT-4와 함께 쓸 때 정확도 +20%p [ICLR 2024](https://proceedings.iclr.cc/paper_files/paper/2024/hash/8a2acd174940dbca361a6398a4f9df91-Abstract-Conference.html), [arXiv 2401.18059](https://arxiv.org/abs/2401.18059) | 요약 노드는 여러 문서에 걸칠 수 있어 **문서 귀속(book_id)이 흐려진다**. 대신 문서별 요약(1.1의 다음 행)이 지표와 더 잘 맞는다 |
| **문서 요약 라우팅**(doc → chunk 2단계) | 문서마다 LLM 요약을 만들고, 요약으로 문서를 고른 뒤 그 문서 안에서 청크 검색 | LlamaIndex Document Summary Index(임베딩 또는 LLM 기반 선택) [LlamaIndex](https://www.llamaindex.ai/blog/a-new-document-summary-index-for-llm-powered-qa-systems-9a32ece2f9ec). 1단계에서 놓친 문서는 2단계에서 복구할 수 없다는 주의점 [levelup](https://levelup.gitconnected.com/hierarchical-rag-document-summaries-to-chunk-retrieval-dc2a1dd62d7f). 다중 입도 요약 트리 HTSIR [AAAI](https://ojs.aaai.org/index.php/AAAI/article/view/40283) | **유망(문서 수가 적다).** 120개 문서 카탈로그는 질의당 약 2만 토큰이라 LLM 선택도 저렴하다. 단, **하드 필터가 아니라 가산점이나 슬롯 보장(soft boost)**으로 써야 recall이 떨어지지 않는다 |
| **HyDE** | 가상 답변을 생성해 그것으로 검색 | [arXiv 2212.10496](https://arxiv.org/abs/2212.10496). 정밀하거나 수치를 묻는 질의에서 효과가 제한적 [arXiv 2604.01733](https://arxiv.org/abs/2604.01733) | **낮음.** 도메인 용어를 지어낼 위험이 있다 |
| **Multi-query / RAG-Fusion** | 질의를 여러 개로 바꿔 쓴 뒤 RRF | [arXiv 2402.03367](https://arxiv.org/abs/2402.03367). 위 금융 벤치마크에서는 효과 제한적 | 패러프레이즈만으로는 약하다. 아래 분해형이 낫다 |
| **질문 분해**(Query Decomposition) | 하위 질문마다 검색하고, 합친 풀을 cross-encoder로 rerank | MultiHop-RAG와 HotpotQA에서 MRR@10 +36.7%, F1 +11.6% [arXiv 2507.00355](https://arxiv.org/abs/2507.00355). 분해가 개선은 크지만 질문 유형에 따라 불안정하다는 보고 [JTIE 2025](https://garuda.kemdiktisaintek.go.id/documents/detail/6386053) | **최우선 후보.** 근거 그룹이 여러 문서에 걸치는 질문 구조와 정확히 맞는다. 원 질의를 항상 같이 fusion에 넣어 단일 사실 질문에서 손해를 막는다 |
| **HyPE**(인덱싱 시 가상 질문 생성) | 청크마다 LLM이 예상 질문을 만들어 그것을 임베딩(질문-질문 매칭) | context precision 최대 +42%p, recall 최대 +45%p(저자 보고) [arXiv 2607.29402](https://arxiv.org/abs/2607.29402) | 중간. 청크당 LLM 1회라 비용은 Contextual Retrieval과 비슷하다. 다국어 검증은 아직 없다 |
| **다양성 선택**(MMR, 문서당 상한) | 비슷하거나 같은 출처 청크가 반복되는 것을 억제 | DF-RAG multi-hop 최대 +9.5% [Capital One](https://capitalone.science/publications/df-rag-enhancing-rag-for-question-answering/), Vendi-RAG [arXiv 2502.11228](https://arxiv.org/html/2502.11228v2). 문서당 1–2개 상한 권고(실험 없음) [besthub](https://besthub.dev/articles/mmr-for-rag-low-cost-chunk-limits-balance-relevance-and-diversity-5e88a91163a4) | **최우선(무료).** 문서 단위 Recall에서는 같은 문서의 두 번째 청크가 지표상 가치가 0이다 |
| **문서 점수 집계**(MaxP 등) | 청크 점수를 문서 단위로 집계(max/sum)해 문서 순위를 먼저 정함 | BERT 문서 랭킹의 MaxP [arXiv 1905.09217](https://arxiv.org/abs/1905.09217) | 위의 다양성 선택을 구현하는 가장 단순한 방법이다 |
| **Late interaction**(ColBERT류) | 토큰마다 벡터를 두고 MaxSim | KURE-v2와 bge-m3 multi-vector는 1.3절 참고. Qdrant 1.10+는 multivector(MaxSim)와 prefetch 재정렬을 지원 [Qdrant](https://qdrant.tech/documentation/advanced-tutorials/reranking-hybrid-search/), [Qdrant multivector](https://qdrant.tech/documentation/tutorials-search-engineering/using-multivector-representations/) | **유망.** 한국어 장문 근거가 가장 강하다(MLDR) |
| **Matryoshka**(MRL) | 앞쪽 차원만 잘라 써도 품질이 크게 떨어지지 않게 학습 | [arXiv 2205.13147](https://arxiv.org/abs/2205.13147). 50%로 자르면 보통 1–4점 하락 [mindstudio](https://www.mindstudio.ai/blog/what-is-matryoshka-representation-learning) | **불필요.** 이 규모에서는 메모리가 병목이 아니고, 자르면 recall만 손해다 |
| **한국어 BM25 토큰화** | 공백 대신 Kiwi 형태소(명사, 어근) | AutoRAG는 `ko_kiwi`를 "highly recommend" [AutoRAG docs](https://marker-inc-korea.github.io/AutoRAG/nodes/retrieval/bm25.html). 공백 토큰화의 한계 예시 [wikidocs](https://wikidocs.net/251980) | 현재 BM25가 공백 기반이면 **반드시 먼저 확인할 것** |

### 1.2 임베딩 모델 (8GB VRAM에 들어가는 것 위주)

MTEB(kor, v2) 검색 9종 평균 nDCG@10 (KURE-v2 카드) [HF KURE-v2](https://huggingface.co/nlpai-lab/KURE-v2):

| 모델(HF id) | 크기 | 차원 / 최대 길이 | 라이선스 | 한국어 근거 |
|---|---|---|---|---|
| `nlpai-lab/KURE-v2` | 154M | 토큰당 128d(multi-vector) / 8192 | Apache-2.0 | **평균 0.8160**, AutoRAG 0.9718, MLDR(장문) **0.7159**, Ko-StrategyQA 0.8070. PyLate나 Sentence-Transformers `MultiVectorEncoder`로 사용 [HF](https://huggingface.co/nlpai-lab/KURE-v2) |
| `nlpai-lab/KURE-v1` | 568M(bge-m3 기반) | 1024 / 8192 | MIT | 평균 0.7616, AutoRAG 0.8708, MLDR 0.4637. 자체 벤치마크 Recall@10 0.797(bge-m3 0.792, e5-large 0.759, e5-large-instruct 0.749, jina-v3 0.763) [HF](https://huggingface.co/nlpai-lab/KURE-v1) |
| `dragonkue/snowflake-arctic-embed-l-v2.0-ko` | 568M | 1024 | Apache-2.0 | 평균 0.7653, AutoRAG **0.9093**(단일벡터 중 최고), LawIRKo 0.7735 [HF KURE-v2 표](https://huggingface.co/nlpai-lab/KURE-v2) |
| `BAAI/bge-m3` | 568M | 1024 / 8192, dense·sparse·ColBERT 3모드 | MIT | 평균 0.7509, MIRACL 0.7015(최고), MrTidy 0.6471. `compute_score` 가중치 예시 [0.4, 0.2, 0.4] [HF](https://huggingface.co/BAAI/bge-m3) |
| `Snowflake/snowflake-arctic-embed-l-v2.0` | 568M | 1024, MRL | Apache-2.0 | KURE-v1 표 Recall@10 0.787 [HF KURE-v1](https://huggingface.co/nlpai-lab/KURE-v1) |
| `Qwen/Qwen3-Embedding-0.6B` | 0.6B | 32–1024(MRL) / 32K, instruction-aware | Apache-2.0 | MMTEB 검색 64.64(e5-large-instruct 57.12, bge-m3 54.60) [HF](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B). **한국어 전용 점수는 공개 표에서 찾지 못함**(4B 0.7737, 8B 0.7826만 있음). 질의 instruction이 없으면 1–5% 하락 |
| `intfloat/multilingual-e5-large-instruct` | 560M | 1024 / 512 | MIT | MMTEB 검색 57.12. 한국어 표에서는 e5-large(비instruct)보다 약간 낮음 [HF KURE-v1](https://huggingface.co/nlpai-lab/KURE-v1) |
| `intfloat/multilingual-e5-large` | 560M | 1024 / 512 | MIT | 평균 0.7333, 그러나 **MLDR 0.2708**(512토큰 한계) [HF comsat](https://huggingface.co/sionic-ai/comsat-embed-ko-8b-preview) |
| `dragonkue/multilingual-e5-small-ko` (v2 있음) | 118M | 384 / 512 | (카드 확인 필요) | 저자 표 기준 평균 약 0.689(e5-small 0.671). **현재 모델을 그대로 바꿔 끼울 수 있는 후보** [HF](https://huggingface-proxy-6ci.pages.dev/dragonkue/multilingual-e5-small-ko) |
| `jinaai/jina-embeddings-v3` | 570M | 1024(MRL) / 8192, late chunking 지원 | **CC BY-NC 4.0** | KURE-v1 표 Recall@10 0.763 [HF](https://huggingface.co/jinaai/jina-embeddings-v3) |
| `google/embeddinggemma-300m` | 308M | 768(MRL) | Gemma 약관 | MMTEB(v2) 500M 이하에서 SOTA라는 보고. **한국어 점수 없음** [HF](https://huggingface.co/unsloth/embeddinggemma-300m/blob/main/README.md) |
| 참고(대형): `Qwen/Qwen3-Embedding-4B` | 4B | – | Apache-2.0 | 0.7737. fp16 약 8GB라 8GB GPU에는 빠듯하다 **[추정]** |

**판단 [추정]:**
- 단일벡터를 교체한다면 **KURE-v1**이나 **snowflake-arctic-ko**(AutoRAG는 PDF에서 파싱한 문서 기반이라 이 과제와 성격이 가장 비슷함)를 먼저 본다.
- 장문 매뉴얼에서 recall을 끌어올리려면 **KURE-v2**를 세 번째 retriever나 재점수 신호로 쓴다. 토큰당 128d라서 30만 청크 × 약 400토큰을 fp16으로 저장하면 약 30GB가 된다. 그래서 1단계 인덱스로 쓰기보다는 후보 100–200개를 그때그때 인코딩해서 MaxSim으로 재점수하는 쪽이 현실적이다. PyLate의 `rank.rerank`는 인덱스 없이도 동작한다 [PyLate](https://lightonai.github.io/pylate/).
- `multilingual-e5-*` 계열은 최대 길이가 512토큰이다. 800자 + 헤더 + 문맥 prefix를 붙이면 잘릴 수 있으니 토큰 길이를 확인한다.

### 1.3 리랭커 (bge-reranker-v2-m3 이후)

| 모델(HF id) | 크기 | 라이선스 | 근거 |
|---|---|---|---|
| `BAAI/bge-reranker-v2-m3` (현재) | 568M | Apache-2.0 | **MIRACL(한국어 포함 18개 언어) 69.32로 비교 대상 중 최고**(jina-v3 66.83, Qwen3-R-0.6B 57.70, Qwen3-R-4B 67.52) [HF jina-reranker-v3](https://huggingface.co/jinaai/jina-reranker-v3) |
| `dragonkue/bge-reranker-v2-m3-ko` | 568M | Apache-2.0 | 한국어 금융 AutoRAG 벤치마크 top-1 0.9123(원본 0.8772, jina-v2 0.8070), top-3 recall 0.9649(원본 0.9561) [HF](https://huggingface.co/dragonkue/bge-reranker-v2-m3-ko) |
| `Qwen/Qwen3-Reranker-0.6B` | 0.6B | Apache-2.0 | MMTEB-R 66.36(bge-v2-m3 58.36), MLDR 67.28(59.51). 32K 문맥, instruction 사용 [HF](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B). MIRACL에서는 bge보다 낮음 |
| `Qwen/Qwen3-Reranker-4B` | 4B | Apache-2.0 | MMTEB-R 72.74. 8GB에서는 4/8bit 양자화가 필요 **[추정]** |
| `jinaai/jina-reranker-v3` | 0.6B(Qwen3 기반, listwise) | **CC BY-NC 4.0** | BEIR 61.94, MIRACL 66.83, 문서 64개를 한 번에 처리 [HF](https://huggingface.co/jinaai/jina-reranker-v3) |
| `mixedbread-ai/mxbai-rerank-base-v2` / `large-v2` | 0.5B / 1.5B | Apache-2.0 | BEIR 서브셋 55.57 / 57.49, Mr.TyDi 28.56 / 29.79. MIRACL 55.32(base) [mixedbread](https://mixedbread.com/blog/mxbai-rerank-v2) |
| `nvidia/llama-nemotron-rerank-1b-v2` | 1B | NVIDIA OpenMDW + Llama 3.2 | 한국어 포함 26개 언어, 8192 토큰. 자사 임베더와 함께 MIRACL Recall@5 65.8% [HF](https://huggingface.co/nvidia/llama-nemotron-rerank-1b-v2) |

**판단 [추정]:** 한국어에서는 bge-reranker-v2-m3가 이미 강하다(MIRACL). 리랭커 교체보다는 **후보 깊이(20+20 → 100+100)**와 **입력 max_length**(헤더와 prefix 포함)를 먼저 점검하는 편이 효과가 크다. 교체를 시험한다면 `dragonkue/bge-reranker-v2-m3-ko`와 `Qwen3-Reranker-0.6B`(instruction 포함)를 A/B로 비교하고, 기존 리랭커와 점수를 융합하는 것도 고려한다.

---

## 2. GraphRAG 옵션

### 2.1 무엇이 실제로 multi-document recall에 도움이 되나

- **RAG vs GraphRAG 체계적 비교**: 단일 홉이나 세부 사실 질의는 일반 RAG가 낫고, multi-hop 추론은 GraphRAG가 낫다. 둘은 상호보완적이다 [arXiv 2502.11371](https://arxiv.org/abs/2502.11371). 같은 토큰 예산을 주면 차이가 거의 사라진다는 2차 보고도 있다(검증 안 됨) [futureagi](https://futureagi.com/blog/graphrag-vs-rag/).
- **GraphRAG-Bench (ICLR 2026)**: 단순 사실 질의에서는 vanilla RAG가 충분하다. 복잡한 추론에서는 그래프가 유리하고, 그중 **HippoRAG2**가 가장 좋다. 프롬프트 길이는 LightRAG 약 1만 토큰, HippoRAG2 약 1천 토큰이다 [arXiv 2506.05690](https://arxiv.org/abs/2506.05690), [요약](https://aiexpjourney.substack.com/p/do-you-really-need-graphrag-ai-innovations).
- **HippoRAG 2**: OpenIE 트리플, 동의어 엣지(유사도 0.8 이상), 패시지 노드로 그래프를 만들고 PPR로 검색한다. Recall@5가 NV-Embed-v2 대비 MuSiQue +5.0, 2Wiki +13.9 [arXiv 2502.14802](https://arxiv.org/abs/2502.14802).
- **LinearRAG (ICLR 2026)**: **spaCy NER만으로**(LLM 0 토큰) 엔티티-문장-패시지 Tri-Graph를 만들고 PPR(damping 0.85)로 검색한다. 2Wiki 인덱싱 250초 / 0 토큰이고, HippoRAG2는 1147초 / 4.98M+1.22M 토큰이었다. 정확도도 HippoRAG2보다 높다(2Wiki 70.2 vs 62.7) [arXiv 2510.10114](https://arxiv.org/html/2510.10114v4).
- **MS GraphRAG Global / community report**는 요약형 질의용이다. 여기서 필요한 "근거 문서 recall"에는 적합하지 않다. FastGraphRAG 모드는 명사구 + 공출현으로 그래프를 만들고 LLM은 커뮤니티 리포트에만 쓴다. 그래프가 "상당히 노이즈가 많다" [GraphRAG docs](https://microsoft.github.io/graphrag/index/methods/).
- **LazyGraphRAG**: NLP 명사구와 공출현으로 인덱싱하므로 비용은 벡터 RAG와 같고 GraphRAG의 0.1%다. LLM은 질의 시점에만 쓴다. **오픈소스 graphrag 라이브러리에 포함됐다는 근거는 찾지 못했다**(Azure 쪽에 통합) [MSR blog](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/).
- 기타: KET-RAG(핵심 청크에만 KG를 만들고 나머지는 텍스트-키워드 이분 그래프, 인덱싱 비용 10배 이상 절감) [arXiv 2502.09304](https://arxiv.org/abs/2502.09304), fast-graphrag(PPR 기반, GraphRAG 대비 6배 저렴하다는 자체 주장) [GitHub](https://github.com/circlemind-ai/fast-graphrag), nano-graphrag(GraphRAG 경량 재구현) [GitHub](https://github.com/gusye1234/nano-graphrag), LightRAG [GitHub](https://github.com/HKUDS/LightRAG).

### 2.2 인덱싱 비용 근거 (MuSiQue 11,656 패시지, HippoRAG2 Appendix F) [arXiv 2502.14802](https://arxiv.org/html/2502.14802)

| 방법 | 입력 토큰 | 출력 토큰 | 인덱싱 시간 |
|---|---|---|---|
| RAPTOR | 1.7M | 0.2M | 100.5분 |
| HippoRAG 2 | 9.2M | 3.0M | 99.5분 |
| LightRAG | 68.5M | 18.3M | 235분 |
| GraphRAG | 115.5M | 36.1M | 277분 |

### 2.3 Advanced(약 18,000페이지) 비용 추정 **[추정]**

가정:
- 페이지당 한국어 1,000–2,000자 → 1,800–3,600만 자.
- o200k 토크나이저에서 한국어는 약 0.41 토큰/자 [측정 블로그](https://jangwook.net/en/blog/en/multilingual-llm-token-tax-experiment/) → **코퍼스 약 7–15M 토큰**, 800자 청크 기준 **약 2.3만–4.5만 청크**(중앙값 4만 청크, 11M 토큰으로 계산).
- 단가($/1M, 입력/출력): gpt-5-nano 0.05/0.40, gpt-4o-mini 0.15/0.60, gpt-5-mini 0.25/2.00, gpt-5.4-nano 0.20/1.25. Batch는 50% 할인이지만 **완료 창이 24h**라 "몇 시간 안"을 보장하지 못한다 [OpenAI pricing](https://developers.openai.com/api/docs/pricing), [Batch](https://developers.openai.com/api/docs/guides/batch).
- GPT-5 계열은 추론 토큰도 출력으로 과금되므로 reasoning effort를 최소로 둔다. 1,024토큰 이상의 공통 prefix는 자동 캐싱된다 [prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching).

| 인덱싱 방식 | 입력 / 출력 토큰 | gpt-5-nano | gpt-4o-mini | gpt-5-mini | 몇 시간 안에 가능한가 |
|---|---|---|---|---|---|
| 문서별 요약(190개 문서 map-reduce) | 약 13M / 1M | **약 $1** | 약 $3 | 약 $5 | 가능(수십 분) |
| Contextual Retrieval(절 단위 창 약 4k + 청크, 출력 80) | 약 180M(대부분 캐시) / 3M | $2–10 | $15–30 | $10–50 | 가능하지만 TPM 한도에 좌우됨(약 1h 이상) |
| 위와 같되 **문서 전체(약 90k 토큰)를 문맥으로** | 약 3.7B / 3M | 캐시해도 $20+ | $280+ | – | **비권장**(TPM, 시간) |
| HyPE(청크당 질문 3–5개) | 약 16M / 6M | 약 $3 | 약 $6 | 약 $16 | 가능 |
| HippoRAG2식 OpenIE(청크당 약 1.3k 입력, 0.35k 출력) | 약 52M / 14M | 약 $8 | 약 $16 | 약 $41 | 빠듯함(4만 회 호출, 1–3h) + 한국어 프롬프트 적응 필요 |
| LightRAG(HippoRAG2 대비 입력 약 7배, 출력 약 6배) | 약 380M / 85M | 약 $50 | 약 $110 | 약 $260 | **불가**(수 시간 이상) |
| MS GraphRAG Standard(약 12배) | 약 620M / 170M | 약 $100 | 약 $195 | 약 $500 | **불가** |
| LinearRAG / LazyGraphRAG / Kiwi 공출현 그래프 | 0 | $0 | $0 | $0 | 가능(CPU로 수 분) |

청크 수가 10만–30만 개라면 청크 비례 항목은 2.5–7.5배로 늘어난다.

### 2.4 권장하는 저가 변형 **[추정]**

1. **KG-light(LinearRAG 방식)**:
   - Kiwi로 NNP/NNG 복합명사를 뽑고, 정규식으로 도메인 엔티티를 잡는다. 예: B/L, 서식 번호, 항만·터미널 코드, Incoterms, 조항 번호, 화물·컨테이너 유형.
   - 문장-엔티티와 패시지-엔티티 희소 행렬을 만든다.
   - 질의 엔티티를 seed로 PPR을 돌려 패시지 점수를 내고, 이를 RRF의 세 번째 리스트로 넣는다.
   - 엔티티가 여러 문서를 이어 주므로(예: 같은 서식명, 같은 절차명) 다문서 근거 확장에 직접 도움이 된다. 비용은 0이다.
2. **문서·절 요약 그래프**: 190개 문서와 그 장·절 요약(LLM 약 $1–5)만 노드로 두고, 엔티티 공유와 요약 임베딩 유사도로 엣지를 만든다. 검색에서 상위 문서의 이웃 문서를 후보에 추가한다(질의당 1–2 hop).
3. **BM25 용어 기반 엔티티 링크 확장**: 1차 top 청크에서 IDF가 높은 용어를 뽑아, 그 용어를 포함한 **다른 문서**의 청크로 2차 BM25를 돌린다(pseudo-relevance feedback을 문서 간으로 제한).

---

## 3. ANN (Qdrant HNSW, 양자화, exact search)

### 3.1 먼저 확인할 것
- **`QdrantClient(path=...)` 또는 `":memory:"` 로컬 모드는 brute-force exact search다.** `search_params`(hnsw_ef 등)는 효과가 없다. 컬렉션이 2만 포인트를 넘으면 로드할 때 "Local mode is not recommended…"라는 경고가 뜬다(`LARGE_DATA_THRESHOLD = 20_000`) [qdrant-client 소스](https://raw.githubusercontent.com/qdrant/qdrant-client/master/qdrant_client/local/qdrant_local.py). 즉 로컬 모드라면 **ANN 오차는 0이고 튜닝할 대상이 없다.** 속도만 문제가 된다.
- **규모 [추정]:** 30만 × 1024d × 4B ≈ 1.2GB(384d면 약 0.46GB). 질의 하나에 약 3억 회 곱셈-덧셈이라 CPU BLAS나 GPU matmul로 수십 ms 이하다. 평가할 때는 **exact search(또는 numpy/torch 행렬곱, FAISS `IndexFlatIP`)로 ANN 변수를 없애는 것**이 가장 깔끔하다. FAISS 문서에서도 정확한 결과를 보장하는 것은 Flat 인덱스뿐이라고 한다 [FAISS wiki](https://github.com/facebookresearch/faiss/wiki/Guidelines-to-choose-an-index).

### 3.2 Qdrant 서버(Docker) 모드 파라미터
- 기본값: `m=16`, `ef_construct=100`, `full_scan_threshold=10000`(**KB 단위**, 1KB는 256d 벡터 1개). 검색 시 `ef`의 기본값은 ef_construct다 [Qdrant indexing](https://qdrant.tech/documentation/manage-data/indexing/).
- 고정밀 예시는 `m=64`, `ef_construct=512`다. exact 결과를 ground truth로 놓고 `hnsw_ef`를 sweep하라고 권한다(예: `{"hnsw_ef":128,"exact":false}`) [Qdrant optimize](https://qdrant.tech/documentation/ops-optimization/optimize/).
- **권장 [추정]:** 10만–30만 청크에서는 `m=32`, `ef_construct=256–512`, 검색 시 `hnsw_ef`를 후보 수(limit)의 2–4배로 둔다(예: limit=100 → 256). 빌드는 수 분이면 끝나므로 아끼지 않는다.
- 인덱싱이 끝났는지(`indexed_vectors_count == points_count`, status green) 확인한 뒤 측정한다. 실험할 때는 한 번에 한 설정만 바꾸고, 질의 절반으로 고른 설정을 나머지 절반으로 검증한다 [Qdrant tuning guide](https://qdrant.tech/documentation/search-tuning/before-tuning-a-qdrant-collection/).
- **doc → chunk 2단계에서 `book_id` 필터를 쓸 경우:** HNSW를 빌드하기 **전에** keyword payload index를 만들어야 필터 인지 엣지가 생긴다. 필터에 걸리는 점이 full_scan_threshold보다 적으면 자동으로 full scan(=exact)이 된다 [Qdrant indexing](https://qdrant.tech/documentation/manage-data/indexing/).

### 3.3 양자화
- **Scalar int8**: 4배 압축, 오차는 대개 1% 미만이다. 기본으로 rescore하지 않는다.
- **Binary (1bit)**: 32배 압축이고 **약 1000차원 미만에서는 정보 손실이 크다**. 1.5bit와 2bit(v1.15+), 비대칭 양자화도 있다.
- **PQ**: 최대 64배 압축이지만 정확도 손실이 있어 메모리가 최우선일 때만 권장한다.
- **oversampling**: 2.4 × limit 100이면 240개를 미리 고른 뒤 rescore한다. binary는 3배로 시작하라고 권한다.
- 출처: [Qdrant quantization](https://qdrant.tech/documentation/guides/quantization/).
- **판단:** 이 규모에서는 **양자화를 쓰지 않는다**. 쓴다면 int8 + `rescore=True` + `oversampling=2`로 한다. 384d e5-small에 binary는 쓰지 않는다.

### 3.4 ANN recall 측정법
- 정의: ANN top-K와 exact top-K의 교집합 / K. 이 값은 정답 라벨과 무관하다 [Qdrant 튜토리얼](https://qdrant.tech/documentation/beginner-tutorials/retrieval-quality/), [FAISS 논문](https://arxiv.org/pdf/2401.08281).
- K는 **최종 k(8)가 아니라 RRF에 넣는 후보 깊이(20–100)**로 잡는다.
- 최종 지표(문서 Recall@8)도 exact와 ANN 두 조건에서 따로 비교한다. ANN recall이 낮아도 최종 품질은 거의 같을 수 있다는 지적도 있다 [arXiv 2606.04522](https://arxiv.org/pdf/2606.04522).

```python
from qdrant_client import models
def ann_recall(client, col, qvecs, K=100, ef=256):
    tot = 0.0
    for q in qvecs:
        ex = client.query_points(col, query=q, limit=K,
              search_params=models.SearchParams(exact=True)).points
        an = client.query_points(col, query=q, limit=K,
              search_params=models.SearchParams(hnsw_ef=ef)).points
        tot += len({p.id for p in ex} & {p.id for p in an}) / K
    return tot / len(qvecs)   # 목표 ≥ 0.99 (서버 모드에서만 의미 있음)
```

### 3.5 대안
- **FAISS**: `IndexFlatIP`(exact). `IndexHNSWFlat`은 M을 4–64 사이에서 고르고, 메모리는 벡터당 약 d×4 + M×2×4 바이트다 [FAISS wiki](https://github.com/facebookresearch/faiss/wiki/Guidelines-to-choose-an-index).
- **hnswlib** [GitHub](https://github.com/nmslib/hnswlib): GloVe 1.2M에서 M=16, efC=200일 때 ef=200이면 R@10 97.6%, ef=400이면 99.1% [stoolap](https://stoolap.io/docs/performance/ann-benchmarks/).
- 이 과제에서는 exact(행렬곱)로 충분하다 **[추정]**.

---

## 4. 우선순위 Top 5 실험 (문서 단위 Recall@k 향상 가능성순)

| # | 실험 | 내용 | 기대 효과 / 근거 | 노력 |
|---|---|---|---|---|
| **1** | **후보 확대 + 문서 다양화 선택** | 아래 세 단계 + 같은 조건을 exact search로 고정 | 지표가 book_id 합집합이므로 직접적이다. 같은 문서의 청크가 겹치는 것을 막는다. MMR과 다양성에 대한 근거(DF-RAG, Vendi-RAG) | **1–2h, LLM 비용 0** |
| **2** | **질문 분해 + 슬롯 배분** | 아래 세 단계 | MRR@10 +36.7% [arXiv 2507.00355](https://arxiv.org/abs/2507.00355). 근거 그룹이 여러 문서에 걸치는 질문에 직결 | 2–3h, 질의당 약 $0.0002(gpt-5-nano) |
| **3** | **청크 문맥화**(헤더 → 요약 → 선택적 LLM 문맥) | 아래 세 단계 | -35% ~ -49% 실패율 [Anthropic](https://www.anthropic.com/engineering/contextual-retrieval), title-chain +23.8% MRR@5 [arXiv 2608.00824](https://arxiv.org/abs/2608.00824) | (a)(b) 1–2h, (c) +1–2h. 비용 약 $1–10 |
| **4** | **한국어 검색기 업그레이드** | 아래 세 단계 | MTEB(kor) 0.75–0.82 수준, 장문 MLDR에서 큰 격차 [KURE-v2](https://huggingface.co/nlpai-lab/KURE-v2) | 2–3h(재임베딩: 568M 모델로 수만~30만 청크, 수십 분 **[추정]**) |
| **5** | **문서 라우팅(soft) 또는 KG-light 그래프** | 아래 세 단계 | Document Summary Index [LlamaIndex](https://www.llamaindex.ai/blog/a-new-document-summary-index-for-llm-powered-qa-systems-9a32ece2f9ec), LinearRAG(0 토큰으로 HippoRAG2 이상) [arXiv 2510.10114](https://arxiv.org/html/2510.10114v4) | 2–4h, 약 $1–5 |

실험별 상세 절차:

**1. 후보 확대 + 문서 다양화 선택**
- (a) dense와 BM25 후보를 20+20에서 100+100(또는 50+50)으로 늘리고 top-100–150을 rerank한다. Anthropic은 150개를 rerank해 20개를 썼다.
- (b) 최종 8칸을 채울 때 문서당 상한을 1–2개로 두거나, 문서 점수(MaxP)로 문서 순위를 먼저 정한 뒤 문서마다 최고 청크를 고른다. 남는 칸은 점수순으로 채운다.
- (c) 위 조건을 모두 exact search로 고정해서 측정한다.

**2. 질문 분해 + 슬롯 배분**
- 기존 "일반 규칙 rewrite"에 더해, LLM이 2–4개의 하위 질의를 만든다(근거 측면별, 예: "요율 규정", "예외 조건", "서류 요건").
- 하위 질의마다 hybrid 검색과 rerank를 돌린 뒤 RRF로 합친다. **원 질의는 항상 포함**한다.
- 최종 k를 하위 질의별로 round-robin 배분한다.

**3. 청크 문맥화**
- (a) 문서명, 장·절 경로, 페이지 헤더를 붙인다(LLM 0).
- (b) 문서별 LLM 요약 1–2문장을 prefix로 붙인다(190회 호출).
- (c) Intermediate와 Advanced에 절 단위 창 Contextual Retrieval(gpt-5-nano)을 적용한다.
- dense와 BM25를 **둘 다** 재색인한다.

**4. 한국어 검색기 업그레이드**
- BM25를 Kiwi 형태소 토큰화로 바꾸고, dense를 e5-small에서 KURE-v1 또는 snowflake-arctic-ko로 교체한다(A/B).
- 여력이 있으면 KURE-v2 MaxSim 재점수를 RRF의 세 번째 신호로 추가한다.
- 리랭커 `bge-reranker-v2-m3-ko`와 `Qwen3-Reranker-0.6B`도 같은 틀로 A/B한다.

**5. 문서 라우팅(soft) 또는 KG-light 그래프**
- (a) 문서 요약, 제목, 목차로 문서 단위 인덱스를 만든다(dense + BM25). 상위 N개 문서의 청크에 가산점을 주거나 1칸을 보장한다. 하드 필터는 쓰지 않는다.
- 또는 (b) Kiwi 엔티티 공출현 + PPR 리스트를 RRF에 추가한다.

**운영 팁 [추정]:**
- Basic에서 개선이 Intermediate와 Advanced로 그대로 옮겨 가지 않을 수 있다. 문서가 길고 많을수록 #1, #3, #4의 효과가 커질 것으로 본다. 난이도별로 따로 보고한다.
- 하이퍼파라미터(상한, 깊이, RRF 가중치)는 질의 절반에서 고르고 나머지 절반에서 확인한다. 질의 수가 적으면 bootstrap 95% CI를 함께 본다 [Qdrant tuning guide](https://qdrant.tech/documentation/search-tuning/before-tuning-a-qdrant-collection/).
- 요약, 문맥 prefix, HyPE 질문, 엔티티 그래프는 모두 **문서 내용만으로** 만든다. 정답 키는 쓰지 않는다.
- GraphRAG 풀버전(MS GraphRAG, LightRAG)과 RAPTOR, HyDE, Matryoshka, 양자화는 이번 제약 아래에서는 **우선순위가 낮다**.

---

## 5. 주요 출처
- Anthropic Contextual Retrieval: https://www.anthropic.com/engineering/contextual-retrieval
- Retrieval strategy benchmark (2026): https://arxiv.org/abs/2604.01733
- Title-chain prefix (2026): https://arxiv.org/abs/2608.00824
- Late chunking: https://arxiv.org/abs/2409.04701 · Chunking 비교: https://arxiv.org/abs/2504.19754
- Question Decomposition (ACL SRW 2025): https://arxiv.org/abs/2507.00355
- HyPE: https://arxiv.org/abs/2607.29402 · HyDE: https://arxiv.org/abs/2212.10496 · RAPTOR: https://arxiv.org/abs/2401.18059
- KURE-v2 / v1: https://huggingface.co/nlpai-lab/KURE-v2 , https://huggingface.co/nlpai-lab/KURE-v1
- comsat-ko 표: https://huggingface.co/sionic-ai/comsat-embed-ko-8b-preview
- Qwen3-Embedding / Reranker: https://huggingface.co/Qwen/Qwen3-Embedding-0.6B , https://huggingface.co/Qwen/Qwen3-Reranker-0.6B
- jina-reranker-v3: https://huggingface.co/jinaai/jina-reranker-v3 · bge-reranker-v2-m3-ko: https://huggingface.co/dragonkue/bge-reranker-v2-m3-ko
- Nemotron rerank: https://huggingface.co/nvidia/llama-nemotron-rerank-1b-v2 · mxbai v2: https://mixedbread.com/blog/mxbai-rerank-v2
- bge-m3: https://huggingface.co/BAAI/bge-m3 · jina-colbert-v2(CC BY-NC, MIRACL-ko 0.671): https://huggingface.co/jinaai/jina-colbert-v2
- RAG vs GraphRAG: https://arxiv.org/abs/2502.11371 · GraphRAG-Bench: https://arxiv.org/abs/2506.05690
- HippoRAG 2: https://arxiv.org/abs/2502.14802 · LinearRAG: https://arxiv.org/abs/2510.10114 · KET-RAG: https://arxiv.org/abs/2502.09304
- LazyGraphRAG: https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/ · GraphRAG methods: https://microsoft.github.io/graphrag/index/methods/
- OpenAI pricing: https://developers.openai.com/api/docs/pricing · Batch: https://developers.openai.com/api/docs/guides/batch
- Qdrant: quantization https://qdrant.tech/documentation/guides/quantization/ , optimize https://qdrant.tech/documentation/ops-optimization/optimize/ , indexing https://qdrant.tech/documentation/manage-data/indexing/ , local mode source https://raw.githubusercontent.com/qdrant/qdrant-client/master/qdrant_client/local/qdrant_local.py
- FAISS index guide: https://github.com/facebookresearch/faiss/wiki/Guidelines-to-choose-an-index
- AutoRAG BM25 tokenizer: https://marker-inc-korea.github.io/AutoRAG/nodes/retrieval/bm25.html
