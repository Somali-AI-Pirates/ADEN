# Basic 임베딩 모델 CPU 비교

같은 Basic 360쪽과 일반 평가 72문항 · 페이지 검색 · 상위 3쪽
CPU 8스레드 · float32 · 문서 배치 8 · 실행 1회

| 모델 | 채점 | Hit@3 | Recall@3 | Precision@3 | 문서 360쪽 | 질문 p50 | 질문 p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| [alibaba-nlp-community/gte-multilingual-base](../cpu-comparison-20261108-171530-824848/alibaba-nlp-community-gte-multilingual-base/report.md) | 68 | 60.29% | 35.33% | 20.59% | 75.9초 | 28.9ms | 43.9ms |
| [hotchpotch/bekko-embedding-v1-a25m](hotchpotch-bekko-embedding-v1-a25m/report.md) | 68 | 44.12% | 27.62% | 16.18% | 24.2초 | 11.4ms | 14.9ms |
| [nlpai-lab/KURE-v3](nlpai-lab-KURE-v3/report.md) | 68 | 61.76% | 37.78% | 23.04% | 521.1초 | 184.3ms | 281.9ms |
| [dragonkue/snowflake-arctic-embed-l-v2.0-ko](dragonkue-snowflake-arctic-embed-l-v2.0-ko/report.md) | 68 | 63.24% | 38.21% | 23.53% | 192.2초 | 69.3ms | 111.7ms |
| [google/embeddinggemma-2](google-embeddinggemma-2/report.md) | 68 | 51.47% | 30.63% | 19.12% | 134.9초 | 54.5ms | 72.4ms |
| [kakaocorp/kanana-2-1.3b-embedding](kakaocorp-kanana-2-1.3b-embedding/report.md) | 68 | 54.41% | 33.37% | 19.12% | 718.5초 | 176.3ms | 312.8ms |

## 토큰 벡터 검색

| 모델 | 채점 | Hit@3 | Recall@3 | Precision@3 | 문서 360쪽 | 질문 p50 | 질문 p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| [nlpai-lab/KURE-v2](nlpai-lab-KURE-v2/report.md) | 68 | 51.47% | 35.17% | 19.12% | 97.6초 | 50.5ms | 62.7ms |

KURE-v2는 페이지마다 토큰 벡터를 만들고 전체 페이지를 MaxSim으로 검색했다.
질문 임베딩과 순위 계산을 합친 p50은 92.9ms, p95는 142.6ms다.

시간은 모델을 불러오고 예열한 뒤 임베딩에 걸린 시간이다.
문서 시간은 360쪽 전체, 질문 시간은 한 문항씩 임베딩한 72회의 분포다.
근거 페이지가 없는 4문항은 검색에 포함하고 점수 평균에서 제외했다.
원 실행 폴더에는 모델별 문서 벡터도 저장됐지만, Git 보관본에는 묶음별 보고서, 문항별 지표와 검색 결과, 입력 해시를 담았다.

GTE 기준선은 같은 입력과 CPU 설정으로 측정한 기존 실행에서 가져왔다.
KURE-v2의 공식 질의 길이는 64토큰이며, 16/72문항이 한도를 초과했다.
EmbeddingGemma 2는 텍스트 인코더만 사용했고, Kanana 2는 CPU eager 양방향 어텐션으로 실행했다.
