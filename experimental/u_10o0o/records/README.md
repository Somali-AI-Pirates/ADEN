# 검색 실험 기록

실험 결과 요약과 채택 근거를 조회하는 시작점이다. 모델 선택 및 채택 결정은 [선택 기록](decisions.md)에 기록한다.

현재 Advanced 개인 검색은 **Bigram-ID BM25 후보 20쪽 → KURE nano 상위 3쪽 고정 → BM25 순서로 최대 8쪽**을 사용한다. [현재 구성과 동료 재벤치](advanced-factor-ablation-20261108/report.md)

## 보관 범위

- 기록은 Basic·Advanced PDF 페이지 검색, 임베딩 모델 비교, BM25 토크나이저 비교, RRF 하이브리드 실험 순서로 정리했다.
- `retrieval_evaluations/` 아래 보고서와 문항별 검색 결과, 설정·입력 해시·실행 시간은 기존 실험 산출물에서 복사했다. 원래 산출물은 로컬 `artifacts/u_10o0o/`에 보존했다.
- JSON 사본에는 문항 ID, 검색된 문서·쪽·순위·점수, 집계 지표, 실행 설정과 입력 SHA-256 지문이 있다. 원 질문·답변 텍스트와 정답 근거·매칭 쪽 목록은 평가 정답을 배포하지 않도록 제외했다. 집계 보고서의 수치는 기존 실행 결과 그대로 보존했다.
- PDF 캐시와 벡터 산출물 `.npy`·`.npz`는 로컬 실행 자원이다. 실험을 다시 실행할 때는 [실행 안내](../README.md#실행)에 따라 준비한다.

## Basic 페이지 검색과 임베딩 비교

처음 Basic PDF 360쪽으로 검색을 실험하고 CPU 임베딩 모델을 비교했다.

- [초기 CPU 비교 보고서](retrieval_evaluations/cpu-comparison-20261108-171530-824848/report.md)는 BAAI BGE-M3, KURE-v1, GTE multilingual base 결과를 정리한다.
- [후보 모델 비교 보고서](retrieval_evaluations/cpu-candidates-20261108-173451/report.md)는 Snowflake-ko, KURE-v2·v3, EmbeddingGemma, Kanana, Bekko를 비교한다. KURE-v2의 token-vector 검색 결과도 포함한다.
- [Basic BGE-M3 실행](retrieval_evaluations/BAAI-bge-m3-page-top3-20261108-170506-582126/report.md)과 [첫 Snowflake-ko 실행](retrieval_evaluations/dragonkue-snowflake-arctic-embed-l-v2.0-ko-page-top3-20261108-182440-625847/report.md)에는 문항별 결과가 있다.

## Advanced 페이지 검색 기준선

Advanced 원본 PDF 120개에서 복원한 18,000쪽과 일반 평가 60문항으로 비교했다. 근거 페이지가 있는 58문항을 채점했다.

- [기준선 비교 보고서](retrieval_evaluations/advanced-baselines.md)는 Snowflake-ko, BM25, Bigram·Kiwi RRF 하이브리드 결과를 한 표에 모았다.
- [Snowflake-ko 실행](retrieval_evaluations/advanced-dragonkue-snowflake-arctic-embed-l-v2.0-ko-page-top3-20261108-185720-335334/report.md)과 [실행 자원·시간 기록](retrieval_evaluations/advanced-dragonkue-snowflake-arctic-embed-l-v2.0-ko-page-top3-20261108-185720-335334/runtime.json)에서 조건·결과를 확인한다.
- [BM25 토크나이저 비교](retrieval_evaluations/advanced-bm25-tokenizers.md)에는 Bigram과 Kiwi 단독 결과가 있다. 세부 실행은 [Bigram BM25](retrieval_evaluations/advanced-bm25-bigram-page-top3-20261108-194056-559597/report.md), [Kiwi BM25](retrieval_evaluations/advanced-bm25-kiwi-page-top3-20261108-194207-443549/report.md)에 저장했다.
- 하이브리드의 가중치별 보고서와 JSON 결과는 [초기 Bigram RRF](retrieval_evaluations/advanced-hybrid-rrf-page-top3-20261108-191947-192465/bm25-7-dense-3/report.md), [Bigram 재실행](retrieval_evaluations/advanced-hybrid-rrf-bigram-page-top3-20261108-195536-027821/bm25-7-dense-3/report.md), [Kiwi RRF](retrieval_evaluations/advanced-hybrid-rrf-kiwi-page-top3-20261108-195419-857080/bm25-6-dense-4/report.md)에서 시작한다. 각 실행 폴더에는 다섯 비율의 하위 폴더가 있다.

## Advanced 리랭커 비교

- [CPU 리랭커 비교](advanced-reranking-20261108-201548-983299/report.md)는 같은 RRF Top 20으로 BAAI BGE, KURE nano, Horizon small을 평가한 결과다. 기준선과 후보 커버리지, 최종 Top 3 지표, 지연, 입력 길이, 설정과 진행 로그를 확인할 수 있다.
- [최종 결과 수 비교](advanced-reranking-cutoffs-20261108/report.md)는 Kiwi 단독, RRF, 리랭커 세 모델의 Hit·Recall·Precision을 @3·5·8·10·15·20에서 비교한다.

## Advanced 소제목·문단 청킹 비교

- [페이지와 구조 청크 비교](advanced-structure-chunking-20261108-215143-955031/report.md)는 같은 후보 20개·최종 3개에서 원본 출처 페이지 지표와 4,000토큰 문맥의 실제 인용문 포함률을 비교한다.

## Advanced Kiwi 식별자 토큰 비교

- [식별자 토큰 비교](advanced-kiwi-identifiers-20261108-220437-379358/report.md)는 기존 Kiwi와 하이픈 식별자를 한 토큰으로 보존한 변형을 비교한다. 일반 평가 60문항과 식별자 단독 BM25 진단 30개를 구분해 기록했다.

## Advanced 문맥 확장 답변 비교

- [KURE Top 3와 RRF 2쪽 추가 비교](advanced-context-expansion-20261108-223239-683500/report.md)는 같은 4,000토큰 상한에서 실제 답변의 사실·조건·근거·판단, 생성 입력 토큰과 p95 지연을 비교한다. 생성과 채점에 `gpt-6-luna`를 사용했다.

## Advanced 변수 분리와 동료 재벤치

- [변수별 결과와 선택 근거](advanced-factor-ablation-20261108/report.md)는 후보 1,812개 조합, 리랭킹과 문맥 예산 비교, 최종 순서 10개 변형, 추가 280·공식 58문항의 개인·동료 비교를 정리한다.

## 결과 해석

각 실행 결과에 데이터셋, 검색 단위, 평가 순위 범위와 실행 조건을 기록했다. 수치를 비교할 때는 해당 조건이 일치하는지 확인한다.
