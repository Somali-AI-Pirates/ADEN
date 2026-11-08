# Basic 임베딩 모델 CPU 비교

같은 Basic 360쪽과 일반 평가 72문항 · 페이지 단위 코사인 검색 · 상위 3쪽
CPU 8스레드 · float32 · 문서 배치 8 · 실행 1회

| 모델 | 채점 | Hit@3 | Recall@3 | Precision@3 | 문서 360쪽 | 질문 p50 | 질문 p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| [BAAI/bge-m3](BAAI-bge-m3/report.md) | 68 | 50.00% | 31.05% | 18.14% | 175.8초 | 63.1ms | 98.4ms |
| [nlpai-lab/KURE-v1](nlpai-lab-KURE-v1/report.md) | 68 | 55.88% | 34.98% | 20.59% | 187.4초 | 64.5ms | 107.7ms |
| [alibaba-nlp-community/gte-multilingual-base](alibaba-nlp-community-gte-multilingual-base/report.md) | 68 | 60.29% | 35.33% | 20.59% | 75.9초 | 28.9ms | 43.9ms |

시간은 모델을 불러오고 예열한 뒤 임베딩에 걸린 시간이다.
문서 시간은 360쪽 전체, 질문 시간은 한 문항씩 임베딩한 72회의 분포다.
근거 페이지가 없는 4문항은 검색에 포함하고 점수 평균에서 제외했다.
원 실행 폴더에는 모델별 문서 벡터도 저장됐지만, Git 보관본에는 묶음별 보고서, 문항별 지표와 검색 결과, 입력 해시를 담았다.
