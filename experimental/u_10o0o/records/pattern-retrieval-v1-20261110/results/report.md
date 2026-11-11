# 패턴 검색 평가 보고서

이 보고서에는 문항 ID와 집계 지표만 있습니다. 질문 원문, 정답 인용, 사건 ID, 출처 ID, 검색된 필드 값은 포함하지 않습니다.

일반 검색 지표는 `ambiguous` category를 제외합니다. requirements가 비어 있으면 N/A로 보고합니다. Recall@20은 candidate pool의 고유 페이지, Recall@8은 최종 페이지를 사용합니다. MRR/nDCG@8은 리랭킹·중복 제거·문맥 예산을 적용한 실제 최종 페이지 순서를 사용합니다.

## 검색 품질

### 전체 (ambiguous 제외)

| Arm | N | Recall@20 | Recall@8 | MRR@8 | nDCG@8 | Candidate fact coverage | Candidate complete | Final fact coverage | Final complete |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 100 | 99.0% | 99.0% | 0.886 | 0.902 | 98.3% | 98.0% | 98.3% | 98.0% |
| B | 100 | 95.0% | 91.5% | 0.830 | 0.844 | 93.3% | 94.0% | 90.0% | 90.0% |
| C | 100 | 99.0% | 95.5% | 0.852 | 0.874 | 98.3% | 99.0% | 95.0% | 95.0% |

### 개발

| Arm | N | Recall@20 | Recall@8 | MRR@8 | nDCG@8 | Candidate fact coverage | Candidate complete | Final fact coverage | Final complete |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 50 | 98.0% | 98.0% | 0.801 | 0.832 | 96.7% | 96.0% | 96.7% | 96.0% |
| B | 50 | 96.0% | 89.0% | 0.774 | 0.787 | 95.0% | 94.0% | 88.3% | 86.0% |
| C | 50 | 100.0% | 93.0% | 0.777 | 0.806 | 100.0% | 100.0% | 93.3% | 92.0% |

### 검증

| Arm | N | Recall@20 | Recall@8 | MRR@8 | nDCG@8 | Candidate fact coverage | Candidate complete | Final fact coverage | Final complete |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 50 | 100.0% | 100.0% | 0.970 | 0.971 | 100.0% | 100.0% | 100.0% | 100.0% |
| B | 50 | 94.0% | 94.0% | 0.887 | 0.902 | 91.7% | 94.0% | 91.7% | 94.0% |
| C | 50 | 98.0% | 98.0% | 0.927 | 0.942 | 96.7% | 98.0% | 96.7% | 98.0% |

### development / exact

| Arm | N | Recall@20 | Recall@8 | MRR@8 | nDCG@8 | Candidate fact coverage | Candidate complete | Final fact coverage | Final complete |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 10 | 100.0% | 100.0% | 0.800 | 0.852 | 100.0% | 100.0% | 100.0% | 100.0% |
| B | 10 | 90.0% | 90.0% | 0.800 | 0.826 | 90.0% | 90.0% | 90.0% | 90.0% |
| C | 10 | 100.0% | 100.0% | 0.850 | 0.889 | 100.0% | 100.0% | 100.0% | 100.0% |

### development / variant

| Arm | N | Recall@20 | Recall@8 | MRR@8 | nDCG@8 | Candidate fact coverage | Candidate complete | Final fact coverage | Final complete |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 10 | 100.0% | 100.0% | 0.307 | 0.474 | 100.0% | 100.0% | 100.0% | 100.0% |
| B | 10 | 100.0% | 70.0% | 0.273 | 0.374 | 100.0% | 100.0% | 70.0% | 70.0% |
| C | 10 | 100.0% | 70.0% | 0.271 | 0.372 | 100.0% | 100.0% | 70.0% | 70.0% |

### development / paraphrase

| Arm | N | Recall@20 | Recall@8 | MRR@8 | nDCG@8 | Candidate fact coverage | Candidate complete | Final fact coverage | Final complete |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 10 | 100.0% | 100.0% | 1.000 | 1.000 | 100.0% | 100.0% | 100.0% | 100.0% |
| B | 10 | 100.0% | 100.0% | 1.000 | 1.000 | 100.0% | 100.0% | 100.0% | 100.0% |
| C | 10 | 100.0% | 100.0% | 1.000 | 1.000 | 100.0% | 100.0% | 100.0% | 100.0% |

### development / comparison

| Arm | N | Recall@20 | Recall@8 | MRR@8 | nDCG@8 | Candidate fact coverage | Candidate complete | Final fact coverage | Final complete |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 10 | 100.0% | 100.0% | 1.000 | 1.000 | 100.0% | 100.0% | 100.0% | 100.0% |
| B | 10 | 100.0% | 95.0% | 0.848 | 0.869 | 100.0% | 100.0% | 90.0% | 90.0% |
| C | 10 | 100.0% | 100.0% | 0.867 | 0.904 | 100.0% | 100.0% | 100.0% | 100.0% |

### development / mixed

| Arm | N | Recall@20 | Recall@8 | MRR@8 | nDCG@8 | Candidate fact coverage | Candidate complete | Final fact coverage | Final complete |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 10 | 90.0% | 90.0% | 0.900 | 0.833 | 90.0% | 80.0% | 90.0% | 80.0% |
| B | 10 | 90.0% | 90.0% | 0.950 | 0.864 | 90.0% | 80.0% | 90.0% | 80.0% |
| C | 10 | 100.0% | 95.0% | 0.900 | 0.863 | 100.0% | 100.0% | 95.0% | 90.0% |

### development / ambiguous

| Arm | N | Recall@20 | Recall@8 | MRR@8 | nDCG@8 | Candidate fact coverage | Candidate complete | Final fact coverage | Final complete |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 10 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| B | 10 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| C | 10 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |

### validation / exact

| Arm | N | Recall@20 | Recall@8 | MRR@8 | nDCG@8 | Candidate fact coverage | Candidate complete | Final fact coverage | Final complete |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 10 | 100.0% | 100.0% | 1.000 | 1.000 | 100.0% | 100.0% | 100.0% | 100.0% |
| B | 10 | 90.0% | 90.0% | 0.900 | 0.900 | 90.0% | 90.0% | 90.0% | 90.0% |
| C | 10 | 100.0% | 100.0% | 1.000 | 1.000 | 100.0% | 100.0% | 100.0% | 100.0% |

### validation / variant

| Arm | N | Recall@20 | Recall@8 | MRR@8 | nDCG@8 | Candidate fact coverage | Candidate complete | Final fact coverage | Final complete |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 10 | 100.0% | 100.0% | 0.900 | 0.926 | 100.0% | 100.0% | 100.0% | 100.0% |
| B | 10 | 100.0% | 100.0% | 0.783 | 0.839 | 100.0% | 100.0% | 100.0% | 100.0% |
| C | 10 | 100.0% | 100.0% | 0.783 | 0.839 | 100.0% | 100.0% | 100.0% | 100.0% |

### validation / paraphrase

| Arm | N | Recall@20 | Recall@8 | MRR@8 | nDCG@8 | Candidate fact coverage | Candidate complete | Final fact coverage | Final complete |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 10 | 100.0% | 100.0% | 0.950 | 0.963 | 100.0% | 100.0% | 100.0% | 100.0% |
| B | 10 | 100.0% | 100.0% | 1.000 | 1.000 | 100.0% | 100.0% | 100.0% | 100.0% |
| C | 10 | 100.0% | 100.0% | 1.000 | 1.000 | 100.0% | 100.0% | 100.0% | 100.0% |

### validation / comparison

| Arm | N | Recall@20 | Recall@8 | MRR@8 | nDCG@8 | Candidate fact coverage | Candidate complete | Final fact coverage | Final complete |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 10 | 100.0% | 100.0% | 1.000 | 1.000 | 100.0% | 100.0% | 100.0% | 100.0% |
| B | 10 | 100.0% | 100.0% | 1.000 | 1.000 | 100.0% | 100.0% | 100.0% | 100.0% |
| C | 10 | 100.0% | 100.0% | 1.000 | 1.000 | 100.0% | 100.0% | 100.0% | 100.0% |

### validation / mixed

| Arm | N | Recall@20 | Recall@8 | MRR@8 | nDCG@8 | Candidate fact coverage | Candidate complete | Final fact coverage | Final complete |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 10 | 100.0% | 100.0% | 1.000 | 0.967 | 100.0% | 100.0% | 100.0% | 100.0% |
| B | 10 | 80.0% | 80.0% | 0.750 | 0.769 | 80.0% | 80.0% | 80.0% | 80.0% |
| C | 10 | 90.0% | 90.0% | 0.850 | 0.869 | 90.0% | 90.0% | 90.0% | 90.0% |

### validation / ambiguous

| Arm | N | Recall@20 | Recall@8 | MRR@8 | nDCG@8 | Candidate fact coverage | Candidate complete | Final fact coverage | Final complete |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 10 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| B | 10 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| C | 10 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |

### 모호성 문항 (별도 집계)

| Arm | N | Recall@20 | Recall@8 | MRR@8 | nDCG@8 | Candidate fact coverage | Candidate complete | Final fact coverage | Final complete |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 20 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| B | 20 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| C | 20 | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |

## 검색 index 및 임베딩

출처 문서 수: 120; 원문 페이지: 18000; occurrences: 78844; code links: 38992.
Units: A=18000, B=60844; corpus.sqlite3=212,729,856 bytes; vectors.npy=322,945,152 bytes; vector dimension=1024.
Embedding time=1816.82s; raw input tokens=18,714,089; unit input tokens (identical-text reuse included)=18,714,089; truncated units=0.

## 조건별 검색량

워밍업을 제외한 측정 요청 기준이다. 후보 단위 수가 같아도 고유 원본 페이지 수와 입력 토큰 수가 같지는 않다. 모든 외부 API 호출은 0회다.

| 세트 | 조건 | 요청/리랭크 호출 | 리랭크 쌍 | 리랭크 토큰 | 절단 쌍 | 후보 쪽 평균 | 최종 쪽 평균 | 문맥 토큰 평균 | 관계 발동 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| development | A | 60 | 1200 | 598,948 | 0 | 20.00 | 8.00 | 6031 | 0 |
| development | B | 60 | 1200 | 484,518 | 0 | 19.67 | 7.97 | 6108 | 0 |
| development | C | 60 | 1200 | 488,847 | 0 | 19.68 | 7.98 | 6114 | 31 |
| validation | A | 60 | 1200 | 586,398 | 0 | 20.00 | 8.00 | 5874 | 0 |
| validation | B | 60 | 1200 | 459,201 | 0 | 19.60 | 8.00 | 5933 | 0 |
| validation | C | 60 | 1200 | 460,026 | 0 | 19.60 | 8.00 | 5914 | 28 |

## C target 및 오확정 점검

Target accuracy는 unique 또는 multiple target이 기대된 문항에서 case 집합까지 정확히 일치해야 합니다. Exact status match와 `none`/`unknown_code`/`unsupported_exclusion`/`too_many_targets` 등을 모호성·충돌·비적용에 대한 안전한 보류로 해석한 비율을 분리했습니다. False confirmation은 잘못된 단일 target 확정, false positive는 잘못된 target 주장을 뜻합니다. Abstain은 target이 기대됐지만 보류 status로 응답한 비율입니다. ambiguous 전용 false-confirmation/false-positive는 별도 집계합니다.

| Arm | Target accuracy (N) | Status accuracy (N) | Exact status match (N) | Safe abstention mapping (N) | False confirmation (N) | False positive (N) | Abstain (N) | Ambiguous false confirmation (N) | Ambiguous false positive (N) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| B | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| C | 57.0% (100) | 57.0% (100) | 57.0% (100) | N/A (0) | 0.0% (100) | 0.0% (100) | 43.0% (100) | N/A (0) | N/A (0) |

| Arm | Target accuracy (N) | Status accuracy (N) | Exact status match (N) | Safe abstention mapping (N) | False confirmation (N) | False positive (N) | Abstain (N) | Ambiguous false confirmation (N) | Ambiguous false positive (N) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| B | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |
| C | N/A (0) | 90.0% (20) | 10.0% (20) | 90.0% (20) | 10.0% (20) | 10.0% (20) | N/A (0) | 0.0% (16) | 0.0% (16) |

## CPU 지연 시간

각 수치는 고정된 검증 문항 30개, 3회 반복, CPU 스레드 4개 기준입니다.

| Arm | Stage | N | p50 (ms) | p95 (ms) |
|---|---|---:|---:|---:|
| A | query_embedding | 90 | 70.52 | 84.58 |
| A | bm25 | 90 | 27.35 | 37.23 |
| A | dense | 90 | 2.22 | 2.34 |
| A | relation | 90 | 0.04 | 0.05 |
| A | rerank | 90 | 6135.98 | 8703.62 |
| A | restore | 90 | 2.18 | 2.96 |
| A | total | 90 | 6215.91 | 8811.82 |
| B | query_embedding | 90 | 71.29 | 83.82 |
| B | bm25 | 90 | 55.41 | 80.87 |
| B | dense | 90 | 6.84 | 7.34 |
| B | relation | 90 | 0.05 | 0.06 |
| B | rerank | 90 | 5771.13 | 8769.37 |
| B | restore | 90 | 2.20 | 2.83 |
| B | total | 90 | 5906.31 | 8900.64 |
| C | query_embedding | 90 | 71.03 | 88.34 |
| C | bm25 | 90 | 55.79 | 80.64 |
| C | dense | 90 | 6.90 | 7.42 |
| C | relation | 90 | 0.11 | 0.17 |
| C | rerank | 90 | 5847.92 | 8759.27 |
| C | restore | 90 | 2.17 | 2.84 |
| C | total | 90 | 5963.57 | 8890.73 |

GPU/CPU context hash 차이: 0건; 최종 출처 순서 동일: 0건; 상이: 0건.

## 문항별 A→B 및 B→C 변화

`final facts`는 충족한 reference requirement 수입니다. `first missing`은 arm별 가장 먼저 생략된 검색 단계입니다. requirement가 N/A면 complete로 세지 않습니다.

| ID | Split | Category | Final facts A/B/C | A→B | B→C | C class expected/observed | C correct | Exact match | Safe abstention | False confirmation | False positive | Abstain | First missing A/B/C |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| PF-D-001 | development | exact | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-D-002 | development | exact | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-D-003 | development | exact | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unknown_code | false | false | false | false | false | true | —/—/— |
| PF-D-004 | development | exact | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-D-005 | development | exact | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-D-006 | development | exact | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-D-007 | development | exact | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-D-008 | development | exact | 1/0/1 | 하락 (-1) | 개선 (+1) | unique/unique | true | true | false | false | false | false | —/candidate_evidence/— |
| PF-D-009 | development | exact | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-D-010 | development | exact | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-V-001 | validation | exact | 1/0/1 | 하락 (-1) | 개선 (+1) | unique/unique | true | true | false | false | false | false | —/candidate_evidence/— |
| PF-V-002 | validation | exact | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-V-003 | validation | exact | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unknown_code | false | false | false | false | false | true | —/—/— |
| PF-V-004 | validation | exact | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-V-005 | validation | exact | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unknown_code | false | false | false | false | false | true | —/—/— |
| PF-V-006 | validation | exact | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-V-007 | validation | exact | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-V-008 | validation | exact | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-V-009 | validation | exact | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-V-010 | validation | exact | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-D-011 | development | variant | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unknown_code | false | false | false | false | false | true | —/—/— |
| PF-D-012 | development | variant | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unknown_code | false | false | false | false | false | true | —/—/— |
| PF-D-013 | development | variant | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-D-014 | development | variant | 1/0/0 | 하락 (-1) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/final_selection/final_selection |
| PF-D-015 | development | variant | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-D-016 | development | variant | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-D-017 | development | variant | 1/0/0 | 하락 (-1) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/final_selection/final_selection |
| PF-D-018 | development | variant | 1/0/0 | 하락 (-1) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/final_selection/final_selection |
| PF-D-019 | development | variant | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-D-020 | development | variant | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-V-011 | validation | variant | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unknown_code | false | false | false | false | false | true | —/—/— |
| PF-V-012 | validation | variant | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-V-013 | validation | variant | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-V-014 | validation | variant | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-V-015 | validation | variant | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-V-016 | validation | variant | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-V-017 | validation | variant | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-V-018 | validation | variant | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-V-019 | validation | variant | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unknown_code | false | false | false | false | false | true | —/—/— |
| PF-V-020 | validation | variant | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-D-021 | development | paraphrase | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/none | false | false | false | false | false | true | —/—/— |
| PF-D-022 | development | paraphrase | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/none | false | false | false | false | false | true | —/—/— |
| PF-D-023 | development | paraphrase | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/none | false | false | false | false | false | true | —/—/— |
| PF-D-024 | development | paraphrase | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/none | false | false | false | false | false | true | —/—/— |
| PF-D-025 | development | paraphrase | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/none | false | false | false | false | false | true | —/—/— |
| PF-D-026 | development | paraphrase | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/none | false | false | false | false | false | true | —/—/— |
| PF-D-027 | development | paraphrase | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/none | false | false | false | false | false | true | —/—/— |
| PF-D-028 | development | paraphrase | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/none | false | false | false | false | false | true | —/—/— |
| PF-D-029 | development | paraphrase | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/none | false | false | false | false | false | true | —/—/— |
| PF-D-030 | development | paraphrase | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/none | false | false | false | false | false | true | —/—/— |
| PF-V-021 | validation | paraphrase | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/none | false | false | false | false | false | true | —/—/— |
| PF-V-022 | validation | paraphrase | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/none | false | false | false | false | false | true | —/—/— |
| PF-V-023 | validation | paraphrase | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/none | false | false | false | false | false | true | —/—/— |
| PF-V-024 | validation | paraphrase | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/none | false | false | false | false | false | true | —/—/— |
| PF-V-025 | validation | paraphrase | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/none | false | false | false | false | false | true | —/—/— |
| PF-V-026 | validation | paraphrase | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/none | false | false | false | false | false | true | —/—/— |
| PF-V-027 | validation | paraphrase | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/none | false | false | false | false | false | true | —/—/— |
| PF-V-028 | validation | paraphrase | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/none | false | false | false | false | false | true | —/—/— |
| PF-V-029 | validation | paraphrase | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/none | false | false | false | false | false | true | —/—/— |
| PF-V-030 | validation | paraphrase | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/none | false | false | false | false | false | true | —/—/— |
| PF-D-031 | development | comparison | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | multiple_targets/conflict | false | false | false | false | false | true | —/—/— |
| PF-D-032 | development | comparison | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | multiple_targets/conflict | false | false | false | false | false | true | —/—/— |
| PF-D-033 | development | comparison | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | multiple_targets/multiple_targets | true | true | false | false | false | false | —/—/— |
| PF-D-034 | development | comparison | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | multiple_targets/conflict | false | false | false | false | false | true | —/—/— |
| PF-D-035 | development | comparison | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | multiple_targets/multiple_targets | true | true | false | false | false | false | —/—/— |
| PF-D-036 | development | comparison | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | multiple_targets/multiple_targets | true | true | false | false | false | false | —/—/— |
| PF-D-037 | development | comparison | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | multiple_targets/conflict | false | false | false | false | false | true | —/—/— |
| PF-D-038 | development | comparison | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | multiple_targets/conflict | false | false | false | false | false | true | —/—/— |
| PF-D-039 | development | comparison | 1/0/1 | 하락 (-1) | 개선 (+1) | multiple_targets/multiple_targets | true | true | false | false | false | false | —/final_selection/— |
| PF-D-040 | development | comparison | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | multiple_targets/multiple_targets | true | true | false | false | false | false | —/—/— |
| PF-V-031 | validation | comparison | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | multiple_targets/conflict | false | false | false | false | false | true | —/—/— |
| PF-V-032 | validation | comparison | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | multiple_targets/conflict | false | false | false | false | false | true | —/—/— |
| PF-V-033 | validation | comparison | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | multiple_targets/conflict | false | false | false | false | false | true | —/—/— |
| PF-V-034 | validation | comparison | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | multiple_targets/conflict | false | false | false | false | false | true | —/—/— |
| PF-V-035 | validation | comparison | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | multiple_targets/multiple_targets | true | true | false | false | false | false | —/—/— |
| PF-V-036 | validation | comparison | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | multiple_targets/conflict | false | false | false | false | false | true | —/—/— |
| PF-V-037 | validation | comparison | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | multiple_targets/conflict | false | false | false | false | false | true | —/—/— |
| PF-V-038 | validation | comparison | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | multiple_targets/multiple_targets | true | true | false | false | false | false | —/—/— |
| PF-V-039 | validation | comparison | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | multiple_targets/conflict | false | false | false | false | false | true | —/—/— |
| PF-V-040 | validation | comparison | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | multiple_targets/conflict | false | false | false | false | false | true | —/—/— |
| PF-D-041 | development | mixed | 2/2/2 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-D-042 | development | mixed | 2/2/2 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-D-043 | development | mixed | 2/2/2 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unknown_code | false | false | false | false | false | true | —/—/— |
| PF-D-044 | development | mixed | 2/2/2 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-D-045 | development | mixed | 2/2/2 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unknown_code | false | false | false | false | false | true | —/—/— |
| PF-D-046 | development | mixed | 1/1/1 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | candidate_evidence/candidate_evidence/final_selection |
| PF-D-047 | development | mixed | 1/1/2 | 변화 없음 (+0) | 개선 (+1) | unique/unique | true | true | false | false | false | false | candidate_evidence/candidate_evidence/— |
| PF-D-048 | development | mixed | 2/2/2 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-D-049 | development | mixed | 2/2/2 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-D-050 | development | mixed | 2/2/2 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-V-041 | validation | mixed | 2/0/0 | 하락 (-2) | 변화 없음 (+0) | unique/unsupported_exclusion | false | false | false | false | false | true | —/candidate_evidence/candidate_evidence |
| PF-V-042 | validation | mixed | 2/2/2 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-V-043 | validation | mixed | 2/2/2 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-V-044 | validation | mixed | 2/2/2 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-V-045 | validation | mixed | 2/2/2 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-V-046 | validation | mixed | 2/2/2 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-V-047 | validation | mixed | 2/0/2 | 하락 (-2) | 개선 (+2) | unique/unique | true | true | false | false | false | false | —/candidate_evidence/— |
| PF-V-048 | validation | mixed | 2/2/2 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-V-049 | validation | mixed | 2/2/2 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-V-050 | validation | mixed | 2/2/2 | 변화 없음 (+0) | 변화 없음 (+0) | unique/unique | true | true | false | false | false | false | —/—/— |
| PF-D-051 | development | ambiguous | 0/0/0 | 해당 없음 (N/A) | 해당 없음 (N/A) | ambiguous/none | true | false | true | false | false | false | —/—/— |
| PF-D-052 | development | ambiguous | 0/0/0 | 해당 없음 (N/A) | 해당 없음 (N/A) | ambiguous/none | true | false | true | false | false | false | —/—/— |
| PF-D-053 | development | ambiguous | 0/0/0 | 해당 없음 (N/A) | 해당 없음 (N/A) | ambiguous/none | true | false | true | false | false | false | —/—/— |
| PF-D-054 | development | ambiguous | 0/0/0 | 해당 없음 (N/A) | 해당 없음 (N/A) | ambiguous/none | true | false | true | false | false | false | —/—/— |
| PF-D-055 | development | ambiguous | 0/0/0 | 해당 없음 (N/A) | 해당 없음 (N/A) | ambiguous/none | true | false | true | false | false | false | —/—/— |
| PF-D-056 | development | ambiguous | 0/0/0 | 해당 없음 (N/A) | 해당 없음 (N/A) | ambiguous/none | true | false | true | false | false | false | —/—/— |
| PF-D-057 | development | ambiguous | 0/0/0 | 해당 없음 (N/A) | 해당 없음 (N/A) | ambiguous/none | true | false | true | false | false | false | —/—/— |
| PF-D-058 | development | ambiguous | 0/0/0 | 해당 없음 (N/A) | 해당 없음 (N/A) | ambiguous/none | true | false | true | false | false | false | —/—/— |
| PF-D-059 | development | ambiguous | 0/0/0 | 해당 없음 (N/A) | 해당 없음 (N/A) | conflict/unique | false | false | false | true | true | false | —/—/— |
| PF-D-060 | development | ambiguous | 0/0/0 | 해당 없음 (N/A) | 해당 없음 (N/A) | conflict/conflict | true | true | true | false | false | false | —/—/— |
| PF-V-051 | validation | ambiguous | 0/0/0 | 해당 없음 (N/A) | 해당 없음 (N/A) | ambiguous/none | true | false | true | false | false | false | —/—/— |
| PF-V-052 | validation | ambiguous | 0/0/0 | 해당 없음 (N/A) | 해당 없음 (N/A) | ambiguous/none | true | false | true | false | false | false | —/—/— |
| PF-V-053 | validation | ambiguous | 0/0/0 | 해당 없음 (N/A) | 해당 없음 (N/A) | ambiguous/none | true | false | true | false | false | false | —/—/— |
| PF-V-054 | validation | ambiguous | 0/0/0 | 해당 없음 (N/A) | 해당 없음 (N/A) | ambiguous/none | true | false | true | false | false | false | —/—/— |
| PF-V-055 | validation | ambiguous | 0/0/0 | 해당 없음 (N/A) | 해당 없음 (N/A) | ambiguous/none | true | false | true | false | false | false | —/—/— |
| PF-V-056 | validation | ambiguous | 0/0/0 | 해당 없음 (N/A) | 해당 없음 (N/A) | ambiguous/none | true | false | true | false | false | false | —/—/— |
| PF-V-057 | validation | ambiguous | 0/0/0 | 해당 없음 (N/A) | 해당 없음 (N/A) | ambiguous/none | true | false | true | false | false | false | —/—/— |
| PF-V-058 | validation | ambiguous | 0/0/0 | 해당 없음 (N/A) | 해당 없음 (N/A) | ambiguous/none | true | false | true | false | false | false | —/—/— |
| PF-V-059 | validation | ambiguous | 0/0/0 | 해당 없음 (N/A) | 해당 없음 (N/A) | conflict/unique | false | false | false | true | true | false | —/—/— |
| PF-V-060 | validation | ambiguous | 0/0/0 | 해당 없음 (N/A) | 해당 없음 (N/A) | conflict/conflict | true | true | true | false | false | false | —/—/— |
