# Nemotron과 KURE-v2 동일 조건 비교

공식 60문항·카테고리 220문항을 실행하고 공식 58개·카테고리 200개를 근거 채점했다. 원래 채점과 동일 검수 패킷의 민감도를 구분한다. 기존에 본 문항의 개발·회귀 결과다. [조건](protocol.md) · [과정](process.md) · [문항별 차이](changes.md) · [결과 JSON](results.json)

| 모델 | 조건 | 공식 | 카테고리 유효 | 일상 표현 | 업무 규정 | 전체 |
|---|---|---:|---:|---:|---:|---:|
| KURE-v2 | 기존 20쪽 | 36/58 | 173/200 | 15/40 | 18/20 | 209/258 |
| KURE-v2 | 합집합 최대40 | 36/58 | 173/200 | 15/40 | 18/20 | 209/258 |
| KURE-v2 | 범위 필터+20 | 36/58 | 175/200 | 17/40 | 18/20 | 211/258 |
| KURE-v2 | 범위 필터+최대40 | 36/58 | 176/200 | 18/40 | 18/20 | 212/258 |
| Nemotron | 기존 20쪽 | 34/58 | 177/200 | 19/40 | 18/20 | 211/258 |
| Nemotron | 합집합 최대40 | 33/58 | 178/200 | 20/40 | 18/20 | 211/258 |
| Nemotron | 범위 필터+20 | 35/58 | 178/200 | 20/40 | 18/20 | 213/258 |
| Nemotron | 범위 필터+최대40 | 34/58 | 179/200 | 21/40 | 18/20 | 213/258 |

| 후보 전체 근거 충족 | 조건 | 공식 | 일상 표현 |
|---|---|---:|---:|
| KURE-v2 | 기존 20쪽 | 43/58 | 16/40 |
| KURE-v2 | 합집합 최대40 | 46/58 | 20/40 |
| KURE-v2 | 범위 필터+20 | 43/58 | 18/40 |
| KURE-v2 | 범위 필터+최대40 | 47/58 | 21/40 |
| Nemotron | 기존 20쪽 | 41/58 | 21/40 |
| Nemotron | 합집합 최대40 | 44/58 | 25/40 |
| Nemotron | 범위 필터+20 | 41/58 | 24/40 |
| Nemotron | 범위 필터+최대40 | 45/58 | 29/40 |

| 최종 정확한 페이지 Recall@8 | 조건 | 공식 | 일상 표현 |
|---|---|---:|---:|
| KURE-v2 | 기존 20쪽 | 81.18% | 49.58% |
| KURE-v2 | 합집합 최대40 | 82.33% | 48.33% |
| KURE-v2 | 범위 필터+20 | 81.18% | 57.08% |
| KURE-v2 | 범위 필터+최대40 | 82.33% | 59.58% |
| Nemotron | 기존 20쪽 | 79.45% | 60.83% |
| Nemotron | 합집합 최대40 | 79.60% | 65.83% |
| Nemotron | 범위 필터+20 | 80.03% | 63.33% |
| Nemotron | 범위 필터+최대40 | 80.17% | 68.33% |

| 일상 표현의 검수 민감도 | 조건 | 대체 출처 인정 | 범위 모호 문항 제외 |
|---|---|---:|---:|
| KURE-v2 | 기존 20쪽 | 16/40 | 16/39 |
| KURE-v2 | 합집합 최대40 | 16/40 | 16/39 |
| KURE-v2 | 범위 필터+20 | 18/40 | 18/39 |
| KURE-v2 | 범위 필터+최대40 | 19/40 | 19/39 |
| Nemotron | 기존 20쪽 | 19/40 | 18/39 |
| Nemotron | 합집합 최대40 | 20/40 | 19/39 |
| Nemotron | 범위 필터+20 | 21/40 | 20/39 |
| Nemotron | 범위 필터+최대40 | 22/40 | 21/39 |

SEM-40의 대체 출처와 SEM-32의 범위 모호 판정은 앞선 KURE-v2 실험에서 고정한 것을 수정 없이 사용했다. 원래 정답은 변경하지 않았으며 새 모델의 결과에 맞춘 출처 추가는 없다. 독립·사람 검수는 수행하지 않았다.

| 모델 | 조건 | 전체 p50(ms) | 전체 p95(ms) | 인코딩 p50(ms) | 리랭킹 p50(ms) |
|---|---|---:|---:|---:|---:|
| KURE-v2 | 기존 20쪽 | 247.0 | 273.9 | 15.1 | 179.3 |
| KURE-v2 | 합집합 최대40 | 394.1 | 425.8 | 13.0 | 322.0 |
| KURE-v2 | 범위 필터+20 | 270.7 | 616.5 | 12.9 | 179.0 |
| KURE-v2 | 범위 필터+최대40 | 411.2 | 786.3 | 13.1 | 328.9 |
| Nemotron | 기존 20쪽 | 1688.7 | 1972.4 | 1467.5 | 196.4 |
| Nemotron | 합집합 최대40 | 1835.0 | 2304.3 | 1458.1 | 350.2 |
| Nemotron | 범위 필터+20 | 1745.1 | 2255.8 | 1457.0 | 198.6 |
| Nemotron | 범위 필터+최대40 | 1882.7 | 2267.9 | 1464.2 | 341.8 |

지연은 같은 PC의 같은 30문항·3회 반복·배치 1·조건 순환에서 질문 인코딩부터 최종 문맥 조립까지 측정했다. 모델/색인 준비와 답변 생성은 제외했다. KURE-v2는 앞선 측정 기록이며 두 모델을 동시에 교차 실행한 결과는 아니다. Nemotron은 8GB GPU에서 CPU/GPU 분산, KURE-v2는 CUDA 인코딩/MaxSim이다. 이 장비에서의 배포 구성을 비교하며 동일 GPU 상주 조건의 모델 속도로 해석하지 않는다.

기존 Nemotron 재현 차이는 0건이며, KURE-v2와 BM25 순위·범위 판정이 같았던 비교는 1120/1120건이다.

## 실행 검증

온라인 배치 1과 품질 배치의 문맥 지문 차이: `{'KURE-v2': {'base20': {'runs': 3, 'ids': ['negative_control:AS-S08-01']}, 'union40': {'runs': 0, 'ids': []}, 'scope20': {'runs': 3, 'ids': ['negative_control:AS-S08-01']}, 'scope40': {'runs': 0, 'ids': []}}, 'Nemotron': {'base20': {'runs': 9, 'ids': ['negative_control:AS-S08-01', 'scenarios:AS-S01-04', 'semantic_queries:AS-SEM-01']}, 'union40': {'runs': 9, 'ids': ['negative_control:AS-S08-01', 'scenarios:AS-S01-04', 'semantic_queries:AS-SEM-01']}, 'scope20': {'runs': 9, 'ids': ['negative_control:AS-S08-03', 'scenarios:AS-S01-04', 'semantic_queries:AS-SEM-01']}, 'scope40': {'runs': 12, 'ids': ['negative_control:AS-S08-02', 'negative_control:AS-S08-03', 'scenarios:AS-S01-04', 'semantic_queries:AS-SEM-01']}}}`. 상세 출처와 반복별 기록은 로컬 timing.json에 저장했다.

## 재현

```bash
.venv/bin/python -m experimental.u_10o0o.embedding_ablation.nemotron_followup prepare
.venv/bin/python -m experimental.u_10o0o.embedding_ablation.nemotron_followup retrieve
.venv/bin/python -m experimental.u_10o0o.embedding_ablation.nemotron_followup score
.venv/bin/python -m experimental.u_10o0o.embedding_ablation.nemotron_followup timing
.venv/bin/python -m experimental.u_10o0o.embedding_ablation.nemotron_followup report
```

다른 출력 경로는 --output으로 지정한다. 기존 경로를 덮어쓰지 않는다. 원문·질문·참고 인용은 artifacts 아래에만 보관한다.
