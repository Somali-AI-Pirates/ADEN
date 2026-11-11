# Advanced KURE-v2 검색 비교

같은 18,000쪽·280문항에서 KURE-v2 토큰 벡터의 전수 MaxSim을 기존 하이브리드 검색에 연결했다. BM25, 5:5 RRF, KURE nano 리랭킹, 원문 조회, 최종 8쪽·8,000토큰은 고정했다. 모델 리비전과 측정 방법은 [실험 조건](protocol.md), 문항별 변화와 원시 지연은 [결과 기록](results.json)에 있다. [기존 세 모델 비교](../nemotron/report.md)

| 최종 필수 근거 전체 충족 | Snowflake-ko | KURE-v3 | Nemotron 8B | KURE-v2 |
|---|---:|---:|---:|---:|
| 공식 | 32/58 | 33/58 | 34/58 | 36/58 |
| 카테고리 유효 문항 | 170/200 | 173/200 | 177/200 | 173/200 |
| 일상 표현 | 13/40 | 15/40 | 19/40 | 15/40 |
| 일상 표현 중 다문서 | 3/20 | 2/20 | 4/20 | 2/20 |
| 공식 문항의 정확한 페이지 Recall@8 | 77.01% | 79.17% | 79.45% | 81.18% |

280문항을 모두 실행했다. 참고 근거가 있는 공식 58개와 범위 내 카테고리 200개를 채점했다. 일상 표현은 카테고리의 부분집합이다. 전체 필수 근거 충족은 Nemotron 211/258, KURE-v2 209/258이며 4개 개선·6개 하락·248개 동일이다.

| 필수 근거 충족 단계 | 평가 묶음 | Nemotron 8B | KURE-v2 |
|---|---|---:|---:|
| 벡터 후보 20개 | 공식 58문항 | 13 | 23 |
| 하이브리드 후보 20개 | 공식 58문항 | 41 | 43 |
| 최종 문맥 | 공식 58문항 | 34 | 36 |
| 벡터 후보 20개 | 카테고리 200문항 | 98 | 121 |
| 하이브리드 후보 20개 | 카테고리 200문항 | 141 | 142 |
| 최종 문맥 | 카테고리 200문항 | 177 | 173 |
| 벡터 후보 20개 | 일상 표현 40문항 | 19 | 18 |
| 하이브리드 후보 20개 | 일상 표현 40문항 | 21 | 16 |
| 최종 문맥 | 일상 표현 40문항 | 19 | 15 |

공식 문항의 개선은 AS-DEV20, AS-ADD05, AS-ADD13이다. AS-ADD27은 후보에 필요한 근거가 있었으나 최종 문맥에서 하나가 빠졌다. 일상 표현에서 하락한 5문항은 후보·조회 단계부터 필수 근거가 부족했다. 이 중 AS-SEM-40은 KURE-v2의 질문 입력 한도도 넘겼다. 64토큰 입력 한도를 넘긴 질문은 총 29개였고, 가장 긴 페이지는 1,018토큰으로 문서 입력 한도 안에 있었다.

| 현재 PC에서 측정 | Nemotron 8B | KURE-v2 |
|---|---:|---:|
| 질문 인코딩 + 벡터 후보 20개 p50 | 1,516.3ms | 26.7ms |
| 질문 인코딩 + 벡터 후보 20개 p95 | 1,682.1ms | 34.9ms |
| 벡터 검색만 p50 | 4.5ms | 17.0ms |
| 18,000쪽 문서 벡터 생성 기록 | 149.6분 | 3.1분 |
| 문서 벡터 저장 공간 | 281.3MiB | 1.69GiB |

RTX 5060 Laptop 8GB에서 30문항을 배치 1로 측정했다. Nemotron은 기존 CPU/GPU 분산 BF16 인코딩과 CPU 코사인 검색을 썼다. KURE-v2는 GPU BF16 인코딩과 GPU에 상주시킨 FP16 토큰 벡터의 전수 MaxSim을 썼다. MaxSim 검색 단계의 PyTorch 최대 할당량은 1.82GiB였다. 두 측정은 모델과 인덱스를 미리 올리고 워밍업 3회 후 수행했다. 지연 표의 범위는 벡터 단계까지다. KURE-v2의 문서 벡터 생성 기록에는 저장 시간이 포함되고, 이전 Nemotron의 기록은 저장에 성공한 배치의 계산 시간 합계다. 압축형 PLAID 인덱스는 이번 비교에서 측정하지 않았다.

현재 8GB 장비에서 KURE-v2는 질문 인코딩이 훨씬 짧아 전체 벡터 단계가 빨랐다. 검색 계산 자체는 페이지당 토큰 벡터를 여러 개 비교하는 KURE-v2가 길었으며, 무압축 저장 공간도 약 6.2배 컸다. 공식 세트는 KURE-v2의 결과가 가장 높았고, 일상 표현과 다문서 질문은 Nemotron의 결과가 더 높았다.

검색 결과를 먼저 저장한 후 고정 참고 근거로 채점했다. 기존 세 모델의 문항별 점수와 집계가 재채점에서 일치했다. Ruff, pytest 291개 및 subtest 15개, CLI 실행, 패딩을 제외하는 MaxSim 점수 검사를 통과했다.

로컬에 고정 리비전 모델 가중치를 받은 뒤 아래 순서로 실행한다. 새로 재현할 경우 공통 입력과 앞선 세 모델이 준비된 별도 실행 경로를 `--run`으로 전달한다. 각 timing 프로세스에 `OPENBLAS_NUM_THREADS=8 OMP_NUM_THREADS=8 MKL_NUM_THREADS=8`을 적용했다.

```bash
.venv/bin/python -m experimental.u_10o0o.embedding_ablation.kure_v2 build
.venv/bin/python -m experimental.u_10o0o.embedding_ablation.kure_v2 retrieve
.venv/bin/python -m experimental.u_10o0o.embedding_ablation.kure_v2 score
.venv/bin/python -m experimental.u_10o0o.embedding_ablation.kure_v2 timing kure_v2
.venv/bin/python -m experimental.u_10o0o.embedding_ablation.kure_v2 timing nemotron
```
