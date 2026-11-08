# Intermediate 실패 유형 대응 리서치: "기록 문서 + 규칙/요율/계약 문서" 동시 회수

작성일: 2026-11-07 / 범위: 웹 리서치(2024–2026 논문·블로그), 구현 1시간 내외, Python, 로컬 GPU 8GB, gpt-6-luna($0.10/M in, $0.50/M out)

---

## 0. 먼저 10분 진단 (어떤 처방이 맞는지 결정)

실패한 Intermediate 문항마다, 정답 book_id 중 **규칙형 문서(규정·요율표·약관·계약)**에 대해 아래 세 순위를 기록한다.

| 측정 | 의미 | 결과가 이렇다면 → 처방 |
|---|---|---|
| (a) RRF 후보 풀(top-50~100) 안 최고 순위 | 후보 풀 회수 여부 | 풀에 없음 → **회수 문제**: R1(규칙 서브쿼리), R3(문서 prefix), R4(카탈로그 라우팅) |
| (b) 리랭크 후 최고 순위 | 리랭커가 밀어냈는지 | 풀엔 있는데 9위 이하 → **선택 문제**: R1의 슬롯 보장, R2(셋 선택) |
| (c) 서브쿼리별 리랭크 리스트 각각의 최고 순위 | 어떤 서브쿼리가 규칙 문서를 잡는지 | 특정 서브쿼리에서만 상위 → RRF가 희석 중 → **R1의 xQuAD/PM2 집계**로 교체 |

이미 "후보 풀 확대는 효과 없음"이 확인됐으므로, (a)에서 규칙 문서가 풀에 아예 없을 가능성이 크다. 이 경우 리랭커/선택 단계만 바꿔서는 해결되지 않고, **질문에서 식별자를 제거한 규칙 지향 쿼리**로 별도 후보를 만들어야 한다(R1, R4).

---

## 1. 추천 순위 (기대효과/노력 순)

| 순위 | 기법 | 노력 | 쿼리당 비용 | 기대효과(추정) | 근거 강도 |
|---|---|---|---|---|---|
| **R1** | 유형화된 요구사항 계획(JSON) + **식별자 제거 규칙 서브쿼리(step-back)** + 요구사항별 슬롯 보장(xQuAD/PM2식 탐욕 집계로 RRF 대체) + 규칙형 문서 최소 쿼터 | 40–60분 | ≈$0.0002 | **큼**. 실패 원인(ID 없는 규칙 문서가 후보/슬롯 경쟁에서 탈락)을 정면으로 다룸 | 중상 |
| **R2** | LLM 셋 선택(SetR식 "정보 요건 식별 → 요건별 문단 매핑 → 커버리지 최대 집합 선택") over top-30, 문서별 그룹·유형 라벨 표시 | 45–60분 | ≈$0.002 (2회 순열 시 ≈$0.004) | **중~큼**. 단, 규칙 문서가 top-30 안에 있어야 함 → R1과 결합 시 효과 극대 | 중상 |
| **R3** | 문서 단위 요약·유형 prefix(Summary-Augmented Chunking): 문서당 LLM 1회로 150–300자 카드 생성 → 모든 청크 앞에 붙여 임베딩·BM25·**리랭커 입력**까지 반영 | 45분 + 재인덱싱 | 인덱싱 1회, 문서당 ≈$0.0005 | **중**. 표만 있는 요율 청크에 "보관료/보관일수/요율" 어휘가 생김 | 중 |
| **R4** | LLM 카탈로그 라우팅: 문서 수가 수백 개 이하라면 (제목+카드) 목록 전체를 LLM에 주고 "이 계산에 적용되는 규칙/요율/계약 문서"를 고르게 한 뒤, 그 문서 안에서 규칙 쿼리로 최고 청크 1개씩 확보 | 30분 | ≈$0.001 | **중~큼**(문서 수가 적을수록). BM25 문서카드 라우팅 실패와 달리 LLM이 의미로 고름 | 중하 |
| **R5** | 표 처리: (저비용) 표형 청크에 "표 제목/직전 소제목/문서 제목" prefix (15분) → (고비용) pdfplumber 단어 좌표 클러스터링 또는 `pdftotext -layout`으로 행 복원 후 "헤더: 값" 직렬화 | 15분 / 1–2시간 | 0 | 저비용판은 **소~중**, 풀버전은 생성 정확도에 더 큼 | 중 |
| **R6** | 충분성 검사 루프: 최종 8개에 대해 "커버되지 않은 요건"을 LLM이 지목 → 그 요건으로 타깃 검색 → 최하위 슬롯 교체 | 30분 | ≈$0.002 | 소~중 (R1/R2 후 잔여 실패 처리) | 중하 |

권장 실행 순서: **0(진단) → R1 → R2 → R3(+R5 저비용판 동시 적용) → R4/R6는 잔여 실패 보고 결정.**

---

## 2. 기법별 상세

### R1. 유형화된 요구사항 계획 + 규칙 지향 서브쿼리 + 요구사항별 슬롯 보장

**핵심 아이디어**
- 현재 LLM 분해는 서브쿼리에 장비 ID를 그대로 남기기 쉽고, ID가 있는 쿼리는 BM25/리랭커 모두 기록 페이지를 압도적으로 선호한다(ID 토큰 일치).
- 해결: 분해 시 **요건 유형을 명시적으로 라벨링**하고, rule/rate/contract 유형 요건은 **식별자·날짜·수량을 제거한 추상 쿼리**(step-back)로 만든다. 그리고 최종 선택에서 **요건마다 최소 1슬롯**을 보장한다.

**근거**
- Step-Back Prompting(ICLR 2024): 구체 질문을 상위 원리 질문으로 추상화 후 그 질문으로도 검색. TimeQA에서 PaLM-2L 41.5% → Step-Back+RAG 68.7%, MuSiQue +7%. 일반 RAG가 거의 개선하지 못한 하드 서브셋에서 효과가 큼. https://arxiv.org/abs/2310.06117
- BRIGHT(ICLR 2025, 추론 집약 검색 — 질문과 관련 문서가 어휘를 공유하지 않는 "정리·원리 검색"이 포함되어 본 문제와 구조가 같음): LLM이 쓴 추론을 쿼리로 쓰면 최대 +12.2 nDCG@10, BM25에서 22.5 → 27.0으로 가장 큰 개선. https://arxiv.org/abs/2407.12883
- 질문 분해 + cross-encoder(2025): MultiHop-RAG Hits@4 0.611(naive) → 0.687(리랭커만) → **0.763(분해+리랭커)**. 분해와 리랭커는 상호보완. https://arxiv.org/html/2507.00355v1
- RuleRAG(2024–25): "어떤 규칙을 찾아야 하는지"를 검색에 명시하면 학습 없이 Recall@10 평균 +89.2%(자체 벤치마크, 독립 재현 없음). https://arxiv.org/abs/2410.22353
- LANCER(ECIR 2026): 서브질문을 생성하고 후보마다 서브질문별 커버 여부를 판정해 탐욕적으로 커버리지를 최대화하는 리랭킹이 pointwise/listwise/setwise LLM 리랭커보다 nugget coverage가 높음(NeuCLIR α-nDCG 72.4 vs listwise 71.2, CovCov 77.3 vs 74.9). 서브질문 품질이 병목이며 2–3개가 적당. https://arxiv.org/html/2601.22008v1
- xQuAD(WWW 2010)/PM2(SIGIR 2012): 서브쿼리(=aspect)별 관련도로 "아직 커버되지 않은 aspect"에 가중치를 주는 탐욕 다변화. MMR(암시적 다양성)과 달리 aspect를 명시적으로 커버. https://doi.org/10.1145/1772690.1772780 , https://doi.org/10.1145/2348283.2348296
- ORDER(2026): 여러 소스가 필요할 때 전역 top-k는 한 소스가 독식하므로 선택된 소스마다 검색 예산을 균등 배분(Uniform Multi-source Sampler). https://arxiv.org/abs/2609.17012

**프롬프트(쿼리당 1회, ≈600 in / 300 out 토큰 ≈ $0.0002)**

```
당신은 해운·물류 업무 매뉴얼 검색 계획자입니다.
질문에 답하려면 어떤 정보가 필요한지 요건으로 나누고, 각 요건의 문서 유형을 지정하세요.
- type: record(장비/부킹 기록·원장), rule(산정 규칙·절차), rate(요율표·타리프), contract(계약·약관), other
- record 요건의 query에는 질문의 식별자를 그대로 넣으세요.
- rule/rate/contract 요건의 query에는 식별자·특정 날짜·수량을 절대 넣지 말고,
  "어떤 일반 규정이 이 계산/판단을 지배하는가"를 묻는 일반 검색어로 쓰세요(문서에 쓰일 법한 용어 사용).
- 요건은 2~6개.
출력(JSON만): {"intent": "calculation|judgment|lookup|procedure",
 "requirements":[{"id":"r1","type":"record","need":"...","query":"..."}, ...]}
질문: {question}
```
- 예: "N1-40-F09104 보관료 계산" → r1 record "N1-40-F09104 반입일 반출일 보관 기록", r2 rule "장비 보관일수 산정 기준 기산일 종료일 포함 여부", r3 rate "컨테이너 장비 보관료 요율표 일당 요금 규격별", r4 contract "보관료 면제 기간 프리타임 계약 조건".
- 추가 안전장치: rule/rate/contract 쿼리에 대해 **정규식으로 ID 패턴을 강제로 제거**(이미 identifier-graph용 정규식이 있으므로 재사용).
- (선택) 코퍼스 어휘 유도: 프롬프트에 문서 제목 목록(또는 자주 나오는 규칙 용어 50개)을 넣어 LLM이 코퍼스 용어로 쿼리를 쓰게 함. CSQE(EACL 2024)는 코퍼스에서 뽑은 문장으로 확장하면 LLM 환각을 줄이고 BM25 대비 큰 개선을 보고. https://arxiv.org/abs/2402.18031

**최종 선택: RRF 대신 xQuAD식 문서 단위 탐욕 집계 (코드 스케치)**

```python
import math
def sig(x): return 1/(1+math.exp(-x))

# rel[q][doc] = 해당 쿼리(원질문 q0 또는 요건 r_i)에 대한 bge-reranker 점수의 문서별 max (sigmoid)
# w[r_i] = 요건 가중치 (기본 균등; rule/rate/contract는 1.0, record는 1.0)
def xquad_select(docs, rel, reqs, w, k_docs=7, lam=0.8):
    S, uncovered = [], {r: 1.0 for r in reqs}
    while len(S) < k_docs:
        best, best_s = None, -1
        for d in docs:
            if d in S: continue
            div = sum(w[r] * rel[r].get(d, 0) * uncovered[r] for r in reqs)
            s = (1-lam) * rel['q0'].get(d, 0) + lam * div
            if s > best_s: best, best_s = d, s
        S.append(best)
        for r in reqs:
            uncovered[r] *= (1 - rel[r].get(best, 0))
    return S
```
- 문서가 선택되면 그 문서가 커버하는 요건의 "미커버 가중치"가 줄어, 다음 슬롯은 아직 커버 안 된 규칙/요율 요건을 가진 문서로 간다. RRF는 이 상호작용이 없어 기록 문서 다수가 슬롯을 독식한다.
- 문서 7개 선택 후, 각 문서의 최고 청크 1개 + 남는 슬롯은 기존 순서로 채움(Recall@8은 문서 단위이므로 청크보다 문서 슬롯이 중요).
- **유형 쿼터(간단판)**: intent가 calculation/judgment이면 rule∪rate∪contract 유형 문서를 최소 2–3개 보장. 문서 유형 라벨은 R3의 문서 카드 생성 시 함께 얻는다(없으면 제목 키워드 규칙: "요율|타리프|약관|규정|기준|계약|지침" → rule계열).
- λ는 0.7–0.9 그리드, 쿼터는 {0,2,3} 그리드로 검증셋에서 결정.

---

### R2. LLM 셋 선택(set selection) over top-30

**근거**
- SetR(ACL 2025 oral): "정보 요건 식별(IRI) → 요건별 문단 찾기 → 다양·명확한 정보를 최대한 커버하는 집합 선택" 프롬프트. top-20 후보에서 평균 2.9개를 고름.
  - GPT-4o 교사(=제로샷 프롬프트) 기준, RankGPT식 순위 프롬프트 대비 HotpotQA EM 33.85 → 39.16, MuSiQue F1 13.25 → 16.91.
  - MultiHopRAG Hit@k 48.87% → 69.90%, 정보 커버리지 19.33% → 36.49%. 순위 지표(MRR/nDCG)는 오히려 낮아짐 → **문서 단위 recall 목표에 맞는 방식**.
  - https://arxiv.org/html/2507.06838v2 , 코드: https://github.com/LGAI-Research/SetR
- DPS(2025): 순위 대신 "필요 최소 집합" 선택이 bge-reranker-v2-m3 대비 HotpotQA F1 58.2 → 66.3, MuSiQue 19.1 → 28.9(단, 파인튜닝 모델; 제로샷 아님). https://arxiv.org/html/2508.09497v1
- 위치 편향: 리스트 순서를 섞어 여러 번 돌리고 합치면 listwise 랭킹이 GPT-3.5에서 7–18% 개선(Permutation Self-Consistency, NAACL 2024). https://arxiv.org/abs/2310.07712
- 비용/지연 참고: RankGPT 슬라이딩윈도(20개, gpt-4o-mini) 1회 호출 p50 ≈1.8초, 쿼리당 ≈$0.0015, Recall@10 0.50 → 0.77(소규모 실무 벤치, 361쿼리). https://pypi.org/project/ranksmith/0.3.2/
- 주의: 2025 LLM 리랭커 대규모 비교에서 새로운(학습에 없던) 질의에서는 LLM 리랭커 일반화가 들쭉날쭉하고 cross-encoder가 경쟁력 있음 → **완전 대체 말고 "보정/보충"으로 쓰기**. https://arxiv.org/abs/2508.16757

**프롬프트 형식(권장)** — 후보를 **문서별로 묶고** 유형 라벨·문서 제목을 보여주면 LLM이 "규칙 문서가 하나도 없다"를 인지하기 쉽다.

```
질문: {question}
후보 문단(문서별 묶음, 번호는 문단 ID):
=== 문서 D3 | 유형: rate | 제목: 컨테이너 장비 보관료 요율표 ===
[12] ...(최대 300토큰으로 자름)
[13] ...
=== 문서 D7 | 유형: record | 제목: ... ===
[4] ...
...
1단계: 이 질문에 답하는 데 필요한 정보 요건을 나열하라(기록 사실, 산정 규칙, 요율, 계약 조건 등).
2단계: 각 요건을 담은 문단 ID를 찾아라. 요건을 담은 문단이 없으면 "없음"이라고 써라.
3단계: 요건을 가장 많이 커버하도록, 가능한 한 서로 다른 문서에서 최대 8개 문단을 고르라.
      같은 문서에서 2개 이상 고르는 것은 그 문서만 담은 요건이 있을 때만 허용.
JSON만 출력: {"requirements":[{"need":"...","type":"...","ids":[..]}],"selection":[..]}
```
- 비용: 30문단 × ~400토큰 + 지시문 ≈ 13k in / 400 out → **≈$0.0015/쿼리**, 2회 순열(원순서 + 역순) 합집합/투표 시 ≈$0.003. 지연 2–6초(병렬 가능).
- 결합 방식(안전): `final = LLM.selection` 다음 기존 파이프라인 순서로 8개까지 채움. 변형 A/B: (i) 리랭커 top-3 문서는 고정 보호, (ii) 비보호.
- 후보 30개 구성: 원질문 리랭크 top-15 + **R1의 rule/rate/contract 서브쿼리 각각의 top-5** (중복 제거). 이렇게 해야 규칙 문서가 선택 대상에 들어온다.
- "없음"으로 나온 요건은 R6(충분성 루프) 트리거로 재사용 가능.

---

### R3. 문서 단위 요약·유형 prefix (저비용 contextual retrieval)

**근거**
- Summary-Augmented Chunking(NLLP@EMNLP 2025): 구조가 비슷한 문서가 많을 때 "엉뚱한 문서에서 검색"(Document-Level Retrieval Mismatch)이 핵심 실패. 문서당 gpt-4o-mini로 ~150자 요약을 만들어 모든 청크 앞에 붙이면 DRM이 대략 절반. 최적 설정은 청크 500자 + 요약 150자(Recall 41.8%, DRM 19.3%); 요약 300자는 오히려 약간 나쁨. **일반 요약이 도메인 전문가 지향 요약보다 나음.** https://arxiv.org/html/2510.06999v1
- Snowflake(2025, 금융 RAG): 문서 단위 전역 메타데이터(회사명·문서종류·날짜 등 key-value 줄)를 청크마다 붙이면 정답률 50–60% → 72–75%. **청크별 LLM 컨텍스트보다 문서 단위 컨텍스트가 더 낫고 훨씬 저렴**(청크별은 Llama3에서 -5.8pt). 문서당 1회 호출, 문서 앞 4,000자만 입력. https://www.snowflake.com/en/engineering-blog/impact-retrieval-chunking-finance-rag/
- Anthropic Contextual Retrieval(2024): 청크별 컨텍스트로 top-20 실패율 5.7% → 3.7%(임베딩) → 2.9%(+BM25) → 1.9%(+리랭크). 단 **일반 문서 요약을 붙이는 것은 자체 실험에서 효과가 제한적**이었다고 명시 → 요약은 "검색에 유용한 판별 정보(문서 유형, 다루는 계산·용어)" 위주로 쓸 것. https://www.anthropic.com/news/contextual-retrieval
- dsRAG Contextual Chunk Headers: 문서 제목 + 1–2문장 문서 요약 + 섹션 제목을 청크 앞에 붙여 임베딩과 **리랭커** 모두에 맥락 제공. https://d-star-ai.github.io/dsRAG
- Metadata-Driven RAG(FinanceBench, 2025): "가장 큰 개선은 청크 메타데이터를 텍스트에 직접 임베딩한 것". https://arxiv.org/abs/2510.24402

**구현**
- 문서당 1회 호출(앞 4,000자 + 목차/소제목 목록 입력, ≈3k in / 200 out ≈ $0.0004/문서):
```
다음 문서의 검색용 카드를 만드세요. JSON만:
{"doc_type":"record|rule|rate|contract|manual",
 "title":"...", 
 "covers":"이 문서가 규정/제공하는 계산·판단 (예: 장비 보관료 산정, 보관일수 기산 규칙)",
 "keywords":["보관료","보관일수","프리타임",...최대 8개]}
식별자(장비번호·부킹번호)는 넣지 마세요.
```
- prefix 형식(150–250자): `[문서: {title} | 유형: 요율표 | 다룸: 장비 보관료 산정 | 키워드: 보관료, 보관일수, 일당요율]\n` + 청크 본문.
- **임베딩(E5-small, 512토큰 한도)·BM25·bge-reranker 입력 모두**에 동일 prefix 적용. 리랭커가 "보관료 계산" 질문과 숫자만 있는 요율표 청크를 연결할 수 있게 되는 것이 핵심.
- 기존 "문서카드 BM25 라우팅"이 실패한 것과의 차이: 라우팅은 문서카드끼리 경쟁(질문의 ID 토큰이 기록 문서 카드에 유리)했지만, prefix는 **청크 점수 자체**에 문서 맥락을 주입하고 R1의 ID-제거 규칙 쿼리와 결합된다.
- 같은 호출에서 얻은 `doc_type`은 R1 쿼터/R2 라벨에 재사용.

---

### R4. LLM 카탈로그 라우팅 (문서 수가 적을 때)

- 전체 문서 수가 ~300 이하라면 (제목 + R3 카드의 covers 1줄) 목록 ≈ 300 × 30토큰 ≈ 9k 토큰 → 쿼리당 ≈$0.001.
- 프롬프트: "질문의 계산/판단에 적용될 규칙·요율·계약 문서를 최대 5개 고르시오(기록 문서는 제외). JSON: {doc_ids:[...]}."
- 선택된 문서 각각에서 R1 규칙 쿼리로 리랭크한 최고 청크 1개를 R2 후보 풀에 넣는다(최종 8에 직접 넣지 말고 후보로).
- 근거는 상대적으로 약함: 소스 라우터 후 소스별 예산 배분(ORDER, https://arxiv.org/abs/2609.17012), "지식베이스가 작으면 전체를 프롬프트에 넣는 편이 단순"(Anthropic), 계층형 문서→청크 검색에서 evidence recall 개선(HiChunk 74.1% → 81.0%, https://arxiv.org/abs/2509.11552). 그래도 "ID를 공유하지 않는 규칙 문서"를 찾는 데는 의미 기반 LLM 선택이 BM25 카드 매칭보다 구조적으로 유리하다.

---

### R5. 표 처리 (구조가 사라진 표)

**근거**
- 행 단위 key-value 직렬화(STC, 2026 WIP): 각 행을 "열이름: 값" 블록으로 만들고 헤더를 값 옆에 유지 → BM25 Recall@1 0.366 → 0.754, 하이브리드 MRR 0.358 → 0.595(MAUD). https://arxiv.org/abs/2605.00318
- 실무 사례(Runbear): 마크다운 표 → "열: 값" 자기서술 행 + 큰 청크로 정답률 0.771 → 0.929(51문항, 두 변경 동시 적용이라 기여 분리 불가). https://runbear.io/posts/your-ai-can-read-your-docs-but-not-your-tables
- 표 검색은 제목 같은 메타데이터 유무에 민감(TARGET, 2025). https://arxiv.org/abs/2505.11545
- 범용 검색기도 적절히 선형화된 표에서 표 전용 모델과 동급(Wang et al. 2022). https://arxiv.org/abs/2205.09843
- PDF 파서 비교(금융 QA, 2026): 표 중심 벤치(TableQuest)에서 pdfplumber가 MRR 0.807로 1위. https://arxiv.org/abs/2604.12047

**저비용판(15분, 먼저 할 것)**
- "표형 청크" 탐지: 줄 평균 길이 < 15자 & 숫자/단위 비율 높음.
- 해당 청크 앞에 `[표: {직전 소제목 또는 '표 n' 캡션} | 문서: {제목}]`과, 표의 첫 헤더 줄들(첫 3~8개 짧은 줄)을 다시 붙임 → 헤더가 모든 표 청크에 따라다니게 함.
- 표가 청크 경계에서 잘릴 때 헤더 재삽입. R3 prefix와 함께 적용.

**풀버전(1–2시간, 리스크 있음)**
- 텍스트 레이어가 완벽하므로 좌표로 열 복원 가능:
  - `pdfplumber.Page.extract_words()` → `top` 기준 행 그룹핑(y_tolerance 2–3pt) → `x0`(숫자열은 `x1`) 클러스터링으로 열 경계 → 셀 배정. 괘선 있는 표는 `extract_tables()`(lines 전략), 없는 표는 text 전략.
  - 또는 poppler `pdftotext -layout`(공백으로 열 정렬 보존) 후 2칸 이상 공백으로 분할.
  - PyMuPDF `page.find_tables()`도 가능하나 AGPL 라이선스.
- 행 직렬화: `"{표제목} | 규격: 20ft | 기간: 1–7일 | 요율: 0원/일"`. 표가 짧으면(≤30행) 표 전체 1청크 + 행 청크 병행.
- 이 작업은 **검색보다 생성 정확도(요율 대조)**에 더 크게 기여할 가능성. Recall 목표만 보면 저비용판 + R3로 대부분 확보 가능.

---

### R6. 충분성 검사 루프 (선택)

- 최종 8개(청크 300토큰 절단) + R1 요건 목록을 주고 "각 요건이 커버되었는가, 미커버 요건의 검색어는?"을 JSON으로 받음 → 미커버 요건마다 타깃 검색(top-3) → 가장 기여도 낮은 슬롯(같은 문서 중복 청크 우선)과 교체. 1회만.
- 근거: Retrieve-Verify-Retrieve, QAMPARI에서 complete recall 상대 +10%/절대 +3% 이상(2026). https://arxiv.org/html/2602.18425 ; MMU-RAG(NeurIPS 2025) 우승권 시스템의 LLM 커버리지 신호 기반 반복 검색. https://arxiv.org/pdf/2602.20735
- 비용 ≈ $0.001–0.002/쿼리, 지연 +2–4초.

---

## 3. 하지 말 것 / 주의할 것

- **식별자를 포함한 HyDE**: 가상 답변이 가짜 요율·날짜·장비번호를 만들어 기록 문서 쪽으로 더 끌려감. 규칙용 HyDE를 쓰려면 "식별자·숫자 없이 규정 조문체로 2–3문장"으로 제한. Query2doc는 BM25에서 3–15% 개선이지만, 강한 dense 검색기에서는 이득이 줄고 일부 도메인(NFCorpus, SciFact)에서는 오히려 하락. https://arxiv.org/abs/2303.07678
- **청크별 contextual retrieval(Anthropic 원형)**: 청크 수만큼 호출. 문서 단위 prefix가 더 싸고 Snowflake 실험에서 더 나았음.
- **LANCER식 pointwise 판정(후보 × 서브질문)**: 후보 100 × 2 서브질문 ≈ 200회 호출, 이득은 통계적 유의성 약함. 아이디어(서브질문별 커버리지 탐욕 집계)만 R1의 xQuAD 집계로 가져오면 충분.
- **SetR/DPS 파인튜닝**: 1시간 범위 밖. 제로샷 프롬프트(GPT-4o 교사 수준)만으로도 순위 프롬프트보다 나았다는 점만 활용.
- **LLM 셋 선택 단독 대체**: 새로운 질의에서 LLM 리랭커 일반화가 불안정(2025 비교 연구) → 항상 기존 순서로 백필하고 A/B 확인.
- prefix가 길면 E5-small(512토큰)·bge-reranker 입력에서 본문이 잘림 → prefix 250자 이내.

---

## 4. 실험 계획 (검증셋 기준, 각 단계 독립 ablation)

1. 진단(0) 결과 표 작성: 규칙형 정답 문서의 (a)/(b)/(c) 순위 분포.
2. R1-a: 유형 라벨 + ID 제거 규칙 쿼리만 추가(집계는 기존 RRF) → R1-b: xQuAD 집계(λ 그리드) → R1-c: 유형 쿼터.
3. R2: 후보 30 = 원질문 top-15 + 규칙 서브쿼리 top-5씩. 보호 top-3 유무, 순열 1회/2회.
4. R3 + R5 저비용판: 재인덱싱 후 R1/R2 재측정(상호작용 확인).
5. 잔여 실패에 R4 또는 R6.
- 보고 지표: 문서 Recall@8(전체/Intermediate), **규칙형 정답 문서 Recall@8 별도**, 필요한 문서 수(≤4, 5–7)별 Recall, 쿼리당 LLM 비용·지연.

---

## 5. 참고 문헌 / 링크

- SetR — Shifting from Ranking to Set Selection for RAG (ACL 2025): https://arxiv.org/html/2507.06838v2 , https://aclanthology.org/2025.acl-long.861 , https://github.com/LGAI-Research/SetR
- DPS — From Ranking to Selection (2025): https://arxiv.org/html/2508.09497v1
- LANCER — LLM Reranking for Nugget Coverage (ECIR 2026): https://arxiv.org/html/2601.22008v1
- Setwise zero-shot ranking (SIGIR 2024): https://arxiv.org/abs/2310.09497
- Permutation Self-Consistency (NAACL 2024): https://arxiv.org/abs/2310.07712
- How Good are LLM-based Rerankers? (2025): https://arxiv.org/abs/2508.16757
- ranksmith(RankGPT 슬라이딩윈도 비용·지연 실무 벤치): https://pypi.org/project/ranksmith/0.3.2/
- Question Decomposition for RAG (2025): https://arxiv.org/html/2507.00355v1
- Step-Back Prompting (ICLR 2024): https://arxiv.org/abs/2310.06117
- BRIGHT (ICLR 2025): https://arxiv.org/abs/2407.12883
- RuleRAG (2024–25): https://arxiv.org/abs/2410.22353
- CSQE — Corpus-Steered Query Expansion (EACL 2024): https://arxiv.org/abs/2402.18031
- Query2doc (EMNLP 2023): https://arxiv.org/abs/2303.07678
- xQuAD (WWW 2010): https://doi.org/10.1145/1772690.1772780 ; PM2 (SIGIR 2012): https://doi.org/10.1145/2348283.2348296
- ORDER — Task-Conditioned Routing for RAG (2026): https://arxiv.org/abs/2609.17012
- Retrieve-Verify-Retrieve (2026): https://arxiv.org/html/2602.18425
- RMIT-ADM+S at MMU-RAG (NeurIPS 2025 competition): https://arxiv.org/pdf/2602.20735
- Summary-Augmented Chunking (NLLP 2025): https://arxiv.org/html/2510.06999v1
- Snowflake — Impact of Retrieval and Chunking on Finance RAG (2025): https://www.snowflake.com/en/engineering-blog/impact-retrieval-chunking-finance-rag/
- Anthropic — Contextual Retrieval (2024): https://www.anthropic.com/news/contextual-retrieval
- dsRAG (Contextual Chunk Headers): https://d-star-ai.github.io/dsRAG
- Metadata-Driven RAG for Financial QA (2025): https://arxiv.org/abs/2510.24402
- HiChunk (2025): https://arxiv.org/abs/2509.11552
- STC — Structure-Aware Chunking for Tabular Data (2026): https://arxiv.org/abs/2605.00318
- Runbear 표 직렬화 사례: https://runbear.io/posts/your-ai-can-read-your-docs-but-not-your-tables
- TARGET — Table Retrieval Benchmark (2025): https://arxiv.org/abs/2505.11545
- Table Retrieval May Not Necessitate Table-specific Model Design (2022): https://arxiv.org/abs/2205.09843
- PDF Parsing & Chunking for Financial QA (2026): https://arxiv.org/abs/2604.12047

※ 표의 "기대효과"는 위 근거와 본 파이프라인 구조에 기반한 추정이며, 해당 데이터셋에서 측정된 값이 아님. 일부 수치(RuleRAG, STC, Runbear, ranksmith)는 저자 자체 평가·소규모·비동료심사 자료임.
