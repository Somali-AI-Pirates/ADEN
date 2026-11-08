# Advanced 페이지 검색 평가

검색 방식 `nlpai-lab/KURE-Reranker-nano` · 페이지 단위 · CrossEncoder 관련도 점수 · 상위 3쪽

| 묶음 | 전체 | 채점 | 제외 | Hit@3 | Recall@3 | Precision@3 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| overall | 60 | 58 | 2 | 0.9483 | 0.6020 | 0.5632 |
| development | 30 | 29 | 1 | 0.9310 | 0.6006 | 0.5632 |
| additional | 30 | 29 | 1 | 0.9655 | 0.6034 | 0.5632 |

근거는 `evidence_groups`에서 중복을 제거한 (문서 ID, 쪽 번호) 집합이다.
Hit은 일치 근거가 하나라도 있으면 1이다.
Recall은 일치 수를 근거 쪽 수로 나눈 값이다.
Precision은 상위 3쪽 중 근거와 일치한 비율이다.
근거 페이지가 있는 문항에 대해 지표별 단순 평균을 계산했다.

문항별 검색 순위와 점수, 지표, 입력 파일 해시는 [results.json](results.json)에 있다.
