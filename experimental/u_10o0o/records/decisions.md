# 검색 실험 선택 기록

## 2026-11-08 · Snowflake-ko를 기준 임베딩 모델로 선택

Basic 원본 PDF 360쪽과 일반 평가 72문항에서 문서 페이지 단위 Top 3를 비교했다. 근거가 있는 68문항 기준 Snowflake-ko는 Hit@3 63.24%, Recall@3 38.21%, Precision@3 23.53%로 CPU 후보 중 세 지표가 가장 높았다. GTE multilingual base보다 두 문항에서 근거를 더 찾았고, 질문 임베딩 p50은 69.3ms로 GTE의 28.9ms보다 길었다. 이 품질 차이를 근거로 개인 실험의 기준 모델을 Snowflake-ko로 정했다. [CPU 임베딩 모델 비교](retrieval_evaluations/cpu-candidates-20261108-173451/report.md)

## 2026-11-08 · Advanced에서 검색 방식과 토크나이저 비교

같은 Advanced 원본 PDF의 복원 페이지 18,000쪽과 일반 평가 60문항으로 Snowflake-ko 단독, BM25 단독, 가중 RRF 하이브리드를 비교했다. Advanced 안의 Development와 Additional 묶음을 사용하고, 근거가 있는 58문항을 채점했다. [Advanced 기준선 비교](retrieval_evaluations/advanced-baselines.md)

- Kiwi BM25 단독은 Bigram보다 적중 문항이 1개 적었지만 Recall@3가 49.57%에서 59.05%, Precision@3가 47.70%에서 59.77%로 올랐다. 이 비교에서는 Kiwi 단독을 근거 커버리지 기준으로 사용했다. [BM25 토크나이저 비교](retrieval_evaluations/advanced-bm25-tokenizers.md)
- Kiwi 하이브리드의 6:4와 7:3은 Recall@3 51.44%, Precision@3 48.28%로 공동 최고였다. 리랭커 비교에는 6:4를 고정하고, 각 검색기의 Top 20을 RRF로 합친 뒤 상위 20쪽을 모델에 제공한다. Bigram 7:3의 Hit@3 91.38%와 Kiwi 단독의 근거 커버리지를 함께 참고한다. [가중치 비교](retrieval_evaluations/advanced-baselines.md#kiwi-bm25--snowflake-ko-하이브리드)

## 2026-11-08 · 리랭커 선택 기준

기존 ADEN 리랭커인 BAAI BGE를 비교 기준으로 두고, 한국어·영어 경량 후보 KURE nano와 다국어 경량 후보 Horizon small을 같은 CPU PyTorch 설정으로 비교한다. 세 모델의 비교는 같은 RRF 후보로 수행하며 Recall@3를 먼저 보고, Hit@3와 Precision@3 및 적중을 잃은 문항도 확인한다. 채택 판단에는 Kiwi BM25 단독과의 품질 차이, p95 지연, 새 문항에서의 재현 여부를 반영한다. 현재 Advanced 60문항은 이미 검색 가중치 선택에도 사용했다.

## 2026-11-08 · KURE nano를 후속 검증 후보로 선정

RRF 후보 Top 20에서 근거가 있었던 55문항을 KURE와 Horizon이 모두 Top 3에서 적중시켰다. KURE는 Recall@3 60.20%, Precision@3 56.32%로 리랭커 중 가장 높았고, Kiwi BM25 단독보다 5문항을 더 적중시켰다. 두 묶음에서 Kiwi 단독의 적중을 유지했다. 추가 리랭킹 p95는 KURE 5.79초, Horizon 2.37초였다. [리랭커 비교 보고서](advanced-reranking-20261108-201548-983299/report.md)

이 비교에서는 Kiwi BM25 단독을 빠른 기준선으로 삼았다. KURE의 Recall 증가는 1.15%p였고, Precision은 Kiwi 단독이 3.45%p 높았다. 당시 서버 적용을 보류하고, 새 문항의 답변 품질과 서버 요청 지연으로 검증할 후보를 KURE로 정했다. Horizon은 CPU 속도 비교 후보였다.

## 2026-11-08 · Advanced Top 3 검색 품질 기준선 확정

이전 Advanced 페이지 검색 실험의 기준선은 **Kiwi BM25와 Snowflake-ko 벡터 검색 각각 20쪽 → BM25:벡터 6:4 및 RRF 상수 60 → 결합 Top 20을 `nlpai-lab/KURE-Reranker-nano`로 재정렬 → 최종 Top 3**였다. 현재 개인 기본값은 아래 Bigram-ID 구성이다.

Advanced 60문항 중 근거가 있는 58문항에서 55문항을 적중했다. Hit@3 94.83%로 공동 최고였고 Recall@3 60.20%로 가장 높았다. 이번 선택에서는 3쪽 안에 근거를 확보하는 문항 수와 근거 커버리지를 우선했다. Kiwi BM25 단독은 Precision@3 59.77%와 Top 5·8에서의 높은 Recall을 보여, 더 넓은 문맥과 CPU 지연을 비교할 때 참고한다. [k별 비교 결과](advanced-reranking-cutoffs-20261108/report.md)

## 2026-11-08 · Advanced 개인 기본값을 Bigram-ID와 KURE 상위 3쪽 고정으로 변경

복원한 페이지에서 Bigram-ID BM25로 20쪽을 찾고, KURE nano 512토큰 설정으로 상위 3쪽을 고른다. 뒤는 BM25 순서로 중복 없이 채워 8쪽까지 사용한다. Bigram-ID와 Snowflake를 6:4로 결합해도 340문항의 후보 20쪽이 같아 기본값에서 벡터 검색을 뺐다.

순수 KURE는 Top 3, BM25 단독은 Top 8의 문서 커버리지가 높았다. KURE 상위 3쪽을 고정한 구성은 추가 세트에서 순수 KURE의 Top 3를 유지하면서 비교한 순위 조합 중 문서 Recall@8이 가장 높았다. 공식 문서 Recall@8은 BM25 단독보다 0.43%p 낮고 페이지 Recall@8은 1.29%p 높았다. 새 기본값의 문서 Recall@8은 기존 개인 구성의 추가 71.67%·공식 78.45%에서 89.11%·86.64%로 올랐다. 동료의 식별자 연결 구성은 97.86%·96.84%로 가장 높았다. [변수 비교와 전체 재벤치](advanced-factor-ablation-20261108/report.md)
