# Advanced 페이지 검색 기준선 비교

같은 Advanced 원본 PDF 120개, 복원 페이지 18,000쪽, 일반 평가 60문항을 사용했다. 문항마다 상위 3쪽을 검색하고, 근거 페이지가 있는 58문항을 채점했다. 페이지와 질문·근거 파일의 해시가 일치한다.

| 검색 방식 | Hit@3 | Recall@3 | Precision@3 | CPU 검색 p50 | CPU 검색 p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| [Snowflake-ko 벡터 검색](advanced-dragonkue-snowflake-arctic-embed-l-v2.0-ko-page-top3-20261108-185720-335334/report.md) | 67.24% | 33.05% | 27.01% | 100.4ms | 113.7ms |
| [BM25](advanced-bm25-page-top3-20261108-191419-360553/report.md) | 87.93% | 49.57% | 47.70% | 46.9ms | 67.3ms |

근거를 찾은 문항은 벡터 검색 39개, BM25 51개였다. 두 방식 모두 적중한 문항은 37개, 벡터 검색만 적중한 문항은 2개, BM25만 적중한 문항은 14개였다.

## 평가 묶음별 결과

| 평가 묶음 | 검색 방식 | 채점 | Hit@3 | Recall@3 | Precision@3 |
| --- | --- | ---: | ---: | ---: | ---: |
| development | Snowflake-ko 벡터 검색 | 29 | 72.41% | 34.20% | 27.59% |
| development | BM25 | 29 | 89.66% | 49.43% | 48.28% |
| additional | Snowflake-ko 벡터 검색 | 29 | 62.07% | 31.90% | 26.44% |
| additional | BM25 | 29 | 86.21% | 49.71% | 47.13% |

## 실행 조건

BM25는 페이지 본문에서 한글 두 글자 토큰과 영문·숫자 덩어리를 추출했다. k1은 1.5, b는 0.75로 고정했다.

BM25 색인 생성 시간은 1.33초였다. 지연 시간은 각 실행에서 색인을 준비하고 3문항으로 예열한 뒤 60문항을 한 번씩 측정했다. 벡터 검색은 CPU 8스레드로 질문 임베딩과 순위를 계산했고, BM25는 단일 Python 스레드로 토큰화와 순위 계산을 수행했다.

## 가중 하이브리드 검색

가중치 표기는 BM25:Snowflake-ko 순이다. 각 검색의 상위 20쪽에서 가중치 / (60 + 순위)를 더해 페이지별 RRF 점수를 계산하고 최종 상위 3쪽을 채점했다. 다섯 비율은 같은 페이지와 평가 문항을 사용했다.

| BM25:Snowflake-ko | Hit@3 | Recall@3 | Precision@3 | CPU 검색 p50 | CPU 검색 p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| [5:5](advanced-hybrid-rrf-page-top3-20261108-191947-192465/bm25-5-dense-5/report.md) | 91.38% | 43.53% | 38.51% | 154.9ms | 193.3ms |
| [4:6](advanced-hybrid-rrf-page-top3-20261108-191947-192465/bm25-4-dense-6/report.md) | 82.76% | 38.94% | 33.33% | 168.4ms | 194.4ms |
| [6:4](advanced-hybrid-rrf-page-top3-20261108-191947-192465/bm25-6-dense-4/report.md) | 91.38% | 45.98% | 42.53% | 174.8ms | 207.0ms |
| [3:7](advanced-hybrid-rrf-page-top3-20261108-191947-192465/bm25-3-dense-7/report.md) | 81.03% | 37.21% | 31.61% | 171.7ms | 209.6ms |
| [7:3](advanced-hybrid-rrf-page-top3-20261108-191947-192465/bm25-7-dense-3/report.md) | 91.38% | 46.41% | 43.10% | 172.1ms | 197.5ms |

7:3은 하이브리드에서 가장 높은 Recall@3와 Precision@3를 기록했다. 근거를 하나 이상 찾은 문항은 53개로 BM25의 51개보다 많았다. 7:3만 적중한 문항은 2개, BM25만 적중한 문항은 0개였다. Recall@3와 Precision@3는 BM25 단독이 각각 3.16%p, 4.60%p 높았다.

하이브리드 지연 시간은 각 비율에서 3문항 예열 후 60문항을 한 번씩 실행해 측정했다. BM25 검색, CPU 질문 임베딩과 벡터 검색, RRF 결합을 포함한다. 각 비율의 개별 보고서에 Development와 Additional 결과가 있다.

## Kiwi BM25 + Snowflake-ko 하이브리드

위 하이브리드와 같은 페이지·문항·근거·문서 벡터를 사용했다. BM25 토크나이저만 Kiwi로 바꾸고, 검색기별 후보 20쪽, RRF 상수 60, 최종 Top 3를 유지했다. Advanced 60문항 중 근거가 있는 58문항을 채점했다. [Bigram 재실행 결과](advanced-hybrid-rrf-bigram-page-top3-20261108-195536-027821)는 기존 다섯 비율의 문항별 순위와 지표를 모두 재현했다.

| BM25:Snowflake-ko | Kiwi Hit@3 | Kiwi Recall@3 | Kiwi Precision@3 | CPU p50 Bigram / Kiwi | CPU p95 Bigram / Kiwi |
| --- | ---: | ---: | ---: | ---: | ---: |
| [5:5](advanced-hybrid-rrf-kiwi-page-top3-20261108-195419-857080/bm25-5-dense-5/report.md) | 89.66% | 46.84% | 41.95% | 207.6 / 187.6ms | 241.0 / 223.7ms |
| [4:6](advanced-hybrid-rrf-kiwi-page-top3-20261108-195419-857080/bm25-4-dense-6/report.md) | 82.76% | 41.95% | 36.21% | 207.8 / 201.9ms | 237.4 / 240.4ms |
| [6:4](advanced-hybrid-rrf-kiwi-page-top3-20261108-195419-857080/bm25-6-dense-4/report.md) | 87.93% | 51.44% | 48.28% | 204.1 / 204.1ms | 244.2 / 240.4ms |
| [3:7](advanced-hybrid-rrf-kiwi-page-top3-20261108-195419-857080/bm25-3-dense-7/report.md) | 82.76% | 41.52% | 35.63% | 212.0 / 204.8ms | 246.1 / 248.5ms |
| [7:3](advanced-hybrid-rrf-kiwi-page-top3-20261108-195419-857080/bm25-7-dense-3/report.md) | 87.93% | 51.44% | 48.28% | 205.3 / 211.4ms | 250.8 / 256.1ms |

같은 가중치에서 Kiwi의 Recall@3와 Precision@3가 다섯 비율 모두 높았다. 하이브리드에서 근거를 찾은 문항 수는 Kiwi 5:5가 52개, Bigram 최고가 53개였다. Kiwi 6:4와 7:3은 51문항에서 적중했고, Kiwi 하이브리드 중 Recall@3와 Precision@3가 가장 높았다.

[Kiwi BM25 단독](advanced-bm25-kiwi-page-top3-20261108-194207-443549/report.md)은 50문항에서 적중했다. Recall@3 59.05%와 Precision@3 59.77%는 이번 하이브리드 다섯 비율보다 높았다.

Kiwi는 `kiwipiepy` 0.24.1과 모델 0.24.0을 사용했다. 로컬 CPU에서 토크나이저 초기화를 포함한 BM25 색인 생성에 Bigram 1.56초, Kiwi 68.44초가 걸렸다. 검색 지연은 색인 생성 후 각 비율에 3문항을 예열하고 60문항을 한 번씩 검색한 값이다.
