# Advanced 벤치마크 유형별 집계·실패 분석 초안

> **잠정 집계 · 2026-11-13T14:05:48+09:00 · 채점 결과 172/280문항**

완료된 문항은 처리 순서와 지연의 영향을 받습니다. 부분 평균을 전체 성능이나 유형 간 우열로 해석하지 않습니다. 본 문서는 별도 초안이며, 진행 중인 채점과 기존 최종 보고서를 변경하지 않습니다.

- 답변 생성: 280/280 완료. 이번 갱신에서 신규 수집 172개.
- 페이지 정답: 258문항. 미보유 22문항은 페이지 지표의 실패로 계산하지 않습니다.
- 답변 모델: Luna medium. 저장된 채점 조건: Luna max, advanced Faithfulness, Judge 및 실패 진단.
- 교정·보안·멀티턴 결과는 이 280문항 품질 평균에 합치지 않습니다.
- 수집 검증 이슈: 0건. 이전 스냅샷에서만 유지한 결과: 0개.

## 1. 전체 잠정 지표

평균은 해당 지표의 평가 가능 문항만 사용합니다. ‘대기’는 문항 채점 미완료, ‘항목 누락’은 완료 문항에 해당 지표가 없는 경우입니다. 평가 불가·미적용은 0점이 아닙니다. Judge 네 축은 저장된 원 척도의 평균이며 정답률로 변환하지 않습니다.

| 지표 | 평균 | 평가 가능 | 평가 불가 | 미적용 | 대기 | 항목 누락 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 후보: 전체 페이지 근거 | 0.875 | 168 | 0 | 4 | 108 | 0 |
| 최종: 전체 페이지 근거 | 0.851 | 168 | 0 | 4 | 108 | 0 |
| 후보 페이지 Recall | 0.943 | 168 | 0 | 4 | 108 | 0 |
| 최종 페이지 Recall | 0.929 | 168 | 0 | 4 | 108 | 0 |
| 후보 MRR | 0.964 | 168 | 0 | 4 | 108 | 0 |
| 최종 MRR | 0.963 | 168 | 0 | 4 | 108 | 0 |
| 후보 nDCG | 0.927 | 168 | 0 | 4 | 108 | 0 |
| 최종 nDCG | 0.922 | 168 | 0 | 4 | 108 | 0 |
| 최종 문맥: 필수 사실 전체 지지 | 0.968 | 158 | 12 | 2 | 108 | 0 |
| 답변: 필수 사실 전체 정확 | 0.927 | 165 | 5 | 2 | 108 | 0 |
| Faithfulness (advanced) | 0.931 | 147 | 25 | 0 | 108 | 0 |
| Judge: 사실 | 0.929 | 170 | 0 | 2 | 108 | 0 |
| Judge: 조건 | 0.959 | 171 | 0 | 1 | 108 | 0 |
| Judge: 근거 | 0.942 | 172 | 0 | 0 | 108 | 0 |
| Judge: 판단 | 0.965 | 172 | 0 | 0 | 108 | 0 |

Faithfulness는 최종 근거에 대한 답변의 지지 정도입니다. 필요한 답을 모두 했는지, 정답인지와는 별도입니다.

## 2. 유형별 현황 — 14개 그룹

아래 각 점수의 n은 해당 지표의 평가 가능 분모입니다. 그룹별 전체 지표 상태는 다음 절에 제공합니다.

| 유형 | 전체 | 채점 완료 | 대기 | 최종 페이지 전체 | 필수 사실 최종 지지 | 답변 필수 사실 정확 | Faithfulness |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Additional | 30 | 29 | 1 | 0.536 / n=28 | 0.920 / n=25 | 0.786 / n=28 | 0.936 / n=27 |
| Custom M | 20 | 0 | 20 | — | — | — | — |
| Custom S | 20 | 0 | 20 | — | — | — | — |
| Development | 30 | 30 | 0 | 0.655 / n=29 | 0.960 / n=25 | 0.929 / n=28 | 0.903 / n=24 |
| 8 범위 밖 질문 | 20 | 2 | 18 | — | — | — | 0.525 / n=2 |
| 1 직접 조회 | 20 | 19 | 1 | 1.000 / n=19 | 1.000 / n=17 | 1.000 / n=17 | 0.961 / n=16 |
| 2 번호 없이 사건 식별 | 20 | 20 | 0 | 0.950 / n=20 | 0.950 / n=20 | 0.950 / n=20 | 0.968 / n=19 |
| 3 한 사건의 여러 기록 | 20 | 17 | 3 | 1.000 / n=17 | 1.000 / n=17 | 1.000 / n=17 | 0.941 / n=13 |
| 4 여러 사건 비교 | 20 | 18 | 2 | 1.000 / n=18 | 1.000 / n=18 | 1.000 / n=18 | 0.984 / n=15 |
| 5 기록·조건 판정 | 20 | 14 | 6 | 1.000 / n=14 | 1.000 / n=13 | 1.000 / n=14 | 0.929 / n=13 |
| 6 대상 모호·추가 확인 | 20 | 10 | 10 | 0.900 / n=10 | 0.900 / n=10 | 0.700 / n=10 | 0.861 / n=9 |
| 7 확정 근거 부족 | 20 | 9 | 11 | 1.000 / n=9 | 1.000 / n=9 | 1.000 / n=9 | 0.987 / n=5 |
| 업무 규정: 적용 | 10 | 1 | 9 | 1.000 / n=1 | 1.000 / n=1 | 1.000 / n=1 | 0.857 / n=1 |
| 업무 규정: 절차 | 10 | 3 | 7 | 1.000 / n=3 | 1.000 / n=3 | 1.000 / n=3 | 0.833 / n=3 |

### 유형별 지표 분모

#### Additional — 29/30 채점 완료

| 지표 | 평균 | 평가 가능 | 평가 불가 | 미적용 | 대기 | 항목 누락 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 후보: 전체 페이지 근거 | 0.607 | 28 | 0 | 1 | 1 | 0 |
| 최종: 전체 페이지 근거 | 0.536 | 28 | 0 | 1 | 1 | 0 |
| 후보 페이지 Recall | 0.807 | 28 | 0 | 1 | 1 | 0 |
| 최종 페이지 Recall | 0.768 | 28 | 0 | 1 | 1 | 0 |
| 후보 MRR | 0.911 | 28 | 0 | 1 | 1 | 0 |
| 최종 MRR | 0.911 | 28 | 0 | 1 | 1 | 0 |
| 후보 nDCG | 0.806 | 28 | 0 | 1 | 1 | 0 |
| 최종 nDCG | 0.791 | 28 | 0 | 1 | 1 | 0 |
| 최종 문맥: 필수 사실 전체 지지 | 0.920 | 25 | 4 | 0 | 1 | 0 |
| 답변: 필수 사실 전체 정확 | 0.786 | 28 | 1 | 0 | 1 | 0 |
| Faithfulness (advanced) | 0.936 | 27 | 2 | 0 | 1 | 0 |
| Judge: 사실 | 0.828 | 29 | 0 | 0 | 1 | 0 |
| Judge: 조건 | 0.931 | 29 | 0 | 0 | 1 | 0 |
| Judge: 근거 | 0.966 | 29 | 0 | 0 | 1 | 0 |
| Judge: 판단 | 0.966 | 29 | 0 | 0 | 1 | 0 |

#### Custom M — 0/20 채점 완료

| 지표 | 평균 | 평가 가능 | 평가 불가 | 미적용 | 대기 | 항목 누락 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 후보: 전체 페이지 근거 | — | 0 | 0 | 0 | 20 | 0 |
| 최종: 전체 페이지 근거 | — | 0 | 0 | 0 | 20 | 0 |
| 후보 페이지 Recall | — | 0 | 0 | 0 | 20 | 0 |
| 최종 페이지 Recall | — | 0 | 0 | 0 | 20 | 0 |
| 후보 MRR | — | 0 | 0 | 0 | 20 | 0 |
| 최종 MRR | — | 0 | 0 | 0 | 20 | 0 |
| 후보 nDCG | — | 0 | 0 | 0 | 20 | 0 |
| 최종 nDCG | — | 0 | 0 | 0 | 20 | 0 |
| 최종 문맥: 필수 사실 전체 지지 | — | 0 | 0 | 0 | 20 | 0 |
| 답변: 필수 사실 전체 정확 | — | 0 | 0 | 0 | 20 | 0 |
| Faithfulness (advanced) | — | 0 | 0 | 0 | 20 | 0 |
| Judge: 사실 | — | 0 | 0 | 0 | 20 | 0 |
| Judge: 조건 | — | 0 | 0 | 0 | 20 | 0 |
| Judge: 근거 | — | 0 | 0 | 0 | 20 | 0 |
| Judge: 판단 | — | 0 | 0 | 0 | 20 | 0 |

#### Custom S — 0/20 채점 완료

| 지표 | 평균 | 평가 가능 | 평가 불가 | 미적용 | 대기 | 항목 누락 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 후보: 전체 페이지 근거 | — | 0 | 0 | 0 | 20 | 0 |
| 최종: 전체 페이지 근거 | — | 0 | 0 | 0 | 20 | 0 |
| 후보 페이지 Recall | — | 0 | 0 | 0 | 20 | 0 |
| 최종 페이지 Recall | — | 0 | 0 | 0 | 20 | 0 |
| 후보 MRR | — | 0 | 0 | 0 | 20 | 0 |
| 최종 MRR | — | 0 | 0 | 0 | 20 | 0 |
| 후보 nDCG | — | 0 | 0 | 0 | 20 | 0 |
| 최종 nDCG | — | 0 | 0 | 0 | 20 | 0 |
| 최종 문맥: 필수 사실 전체 지지 | — | 0 | 0 | 0 | 20 | 0 |
| 답변: 필수 사실 전체 정확 | — | 0 | 0 | 0 | 20 | 0 |
| Faithfulness (advanced) | — | 0 | 0 | 0 | 20 | 0 |
| Judge: 사실 | — | 0 | 0 | 0 | 20 | 0 |
| Judge: 조건 | — | 0 | 0 | 0 | 20 | 0 |
| Judge: 근거 | — | 0 | 0 | 0 | 20 | 0 |
| Judge: 판단 | — | 0 | 0 | 0 | 20 | 0 |

#### Development — 30/30 채점 완료

| 지표 | 평균 | 평가 가능 | 평가 불가 | 미적용 | 대기 | 항목 누락 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 후보: 전체 페이지 근거 | 0.690 | 29 | 0 | 1 | 0 | 0 |
| 최종: 전체 페이지 근거 | 0.655 | 29 | 0 | 1 | 0 | 0 |
| 후보 페이지 Recall | 0.874 | 29 | 0 | 1 | 0 | 0 |
| 최종 페이지 Recall | 0.862 | 29 | 0 | 1 | 0 | 0 |
| 후보 MRR | 1.000 | 29 | 0 | 1 | 0 | 0 |
| 최종 MRR | 1.000 | 29 | 0 | 1 | 0 | 0 |
| 후보 nDCG | 0.891 | 29 | 0 | 1 | 0 | 0 |
| 최종 nDCG | 0.886 | 29 | 0 | 1 | 0 | 0 |
| 최종 문맥: 필수 사실 전체 지지 | 0.960 | 25 | 5 | 0 | 0 | 0 |
| 답변: 필수 사실 전체 정확 | 0.929 | 28 | 2 | 0 | 0 | 0 |
| Faithfulness (advanced) | 0.903 | 24 | 6 | 0 | 0 | 0 |
| Judge: 사실 | 0.900 | 30 | 0 | 0 | 0 | 0 |
| Judge: 조건 | 0.967 | 30 | 0 | 0 | 0 | 0 |
| Judge: 근거 | 0.933 | 30 | 0 | 0 | 0 | 0 |
| Judge: 판단 | 0.967 | 30 | 0 | 0 | 0 | 0 |

#### 8 범위 밖 질문 — 2/20 채점 완료

| 지표 | 평균 | 평가 가능 | 평가 불가 | 미적용 | 대기 | 항목 누락 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 후보: 전체 페이지 근거 | — | 0 | 0 | 2 | 18 | 0 |
| 최종: 전체 페이지 근거 | — | 0 | 0 | 2 | 18 | 0 |
| 후보 페이지 Recall | — | 0 | 0 | 2 | 18 | 0 |
| 최종 페이지 Recall | — | 0 | 0 | 2 | 18 | 0 |
| 후보 MRR | — | 0 | 0 | 2 | 18 | 0 |
| 최종 MRR | — | 0 | 0 | 2 | 18 | 0 |
| 후보 nDCG | — | 0 | 0 | 2 | 18 | 0 |
| 최종 nDCG | — | 0 | 0 | 2 | 18 | 0 |
| 최종 문맥: 필수 사실 전체 지지 | — | 0 | 0 | 2 | 18 | 0 |
| 답변: 필수 사실 전체 정확 | — | 0 | 0 | 2 | 18 | 0 |
| Faithfulness (advanced) | 0.525 | 2 | 0 | 0 | 18 | 0 |
| Judge: 사실 | — | 0 | 0 | 2 | 18 | 0 |
| Judge: 조건 | 1.000 | 1 | 0 | 1 | 18 | 0 |
| Judge: 근거 | 0.000 | 2 | 0 | 0 | 18 | 0 |
| Judge: 판단 | 1.000 | 2 | 0 | 0 | 18 | 0 |

#### 1 직접 조회 — 19/20 채점 완료

| 지표 | 평균 | 평가 가능 | 평가 불가 | 미적용 | 대기 | 항목 누락 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 후보: 전체 페이지 근거 | 1.000 | 19 | 0 | 0 | 1 | 0 |
| 최종: 전체 페이지 근거 | 1.000 | 19 | 0 | 0 | 1 | 0 |
| 후보 페이지 Recall | 1.000 | 19 | 0 | 0 | 1 | 0 |
| 최종 페이지 Recall | 1.000 | 19 | 0 | 0 | 1 | 0 |
| 후보 MRR | 0.974 | 19 | 0 | 0 | 1 | 0 |
| 최종 MRR | 0.974 | 19 | 0 | 0 | 1 | 0 |
| 후보 nDCG | 0.981 | 19 | 0 | 0 | 1 | 0 |
| 최종 nDCG | 0.981 | 19 | 0 | 0 | 1 | 0 |
| 최종 문맥: 필수 사실 전체 지지 | 1.000 | 17 | 2 | 0 | 1 | 0 |
| 답변: 필수 사실 전체 정확 | 1.000 | 17 | 2 | 0 | 1 | 0 |
| Faithfulness (advanced) | 0.961 | 16 | 3 | 0 | 1 | 0 |
| Judge: 사실 | 1.000 | 19 | 0 | 0 | 1 | 0 |
| Judge: 조건 | 1.000 | 19 | 0 | 0 | 1 | 0 |
| Judge: 근거 | 1.000 | 19 | 0 | 0 | 1 | 0 |
| Judge: 판단 | 1.000 | 19 | 0 | 0 | 1 | 0 |

#### 2 번호 없이 사건 식별 — 20/20 채점 완료

| 지표 | 평균 | 평가 가능 | 평가 불가 | 미적용 | 대기 | 항목 누락 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 후보: 전체 페이지 근거 | 1.000 | 20 | 0 | 0 | 0 | 0 |
| 최종: 전체 페이지 근거 | 0.950 | 20 | 0 | 0 | 0 | 0 |
| 후보 페이지 Recall | 1.000 | 20 | 0 | 0 | 0 | 0 |
| 최종 페이지 Recall | 0.950 | 20 | 0 | 0 | 0 | 0 |
| 후보 MRR | 0.955 | 20 | 0 | 0 | 0 | 0 |
| 최종 MRR | 0.950 | 20 | 0 | 0 | 0 | 0 |
| 후보 nDCG | 0.967 | 20 | 0 | 0 | 0 | 0 |
| 최종 nDCG | 0.950 | 20 | 0 | 0 | 0 | 0 |
| 최종 문맥: 필수 사실 전체 지지 | 0.950 | 20 | 0 | 0 | 0 | 0 |
| 답변: 필수 사실 전체 정확 | 0.950 | 20 | 0 | 0 | 0 | 0 |
| Faithfulness (advanced) | 0.968 | 19 | 1 | 0 | 0 | 0 |
| Judge: 사실 | 0.950 | 20 | 0 | 0 | 0 | 0 |
| Judge: 조건 | 0.950 | 20 | 0 | 0 | 0 | 0 |
| Judge: 근거 | 0.950 | 20 | 0 | 0 | 0 | 0 |
| Judge: 판단 | 0.950 | 20 | 0 | 0 | 0 | 0 |

#### 3 한 사건의 여러 기록 — 17/20 채점 완료

| 지표 | 평균 | 평가 가능 | 평가 불가 | 미적용 | 대기 | 항목 누락 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 후보: 전체 페이지 근거 | 1.000 | 17 | 0 | 0 | 3 | 0 |
| 최종: 전체 페이지 근거 | 1.000 | 17 | 0 | 0 | 3 | 0 |
| 후보 페이지 Recall | 1.000 | 17 | 0 | 0 | 3 | 0 |
| 최종 페이지 Recall | 1.000 | 17 | 0 | 0 | 3 | 0 |
| 후보 MRR | 1.000 | 17 | 0 | 0 | 3 | 0 |
| 최종 MRR | 1.000 | 17 | 0 | 0 | 3 | 0 |
| 후보 nDCG | 0.994 | 17 | 0 | 0 | 3 | 0 |
| 최종 nDCG | 0.994 | 17 | 0 | 0 | 3 | 0 |
| 최종 문맥: 필수 사실 전체 지지 | 1.000 | 17 | 0 | 0 | 3 | 0 |
| 답변: 필수 사실 전체 정확 | 1.000 | 17 | 0 | 0 | 3 | 0 |
| Faithfulness (advanced) | 0.941 | 13 | 4 | 0 | 3 | 0 |
| Judge: 사실 | 1.000 | 17 | 0 | 0 | 3 | 0 |
| Judge: 조건 | 1.000 | 17 | 0 | 0 | 3 | 0 |
| Judge: 근거 | 1.000 | 17 | 0 | 0 | 3 | 0 |
| Judge: 판단 | 1.000 | 17 | 0 | 0 | 3 | 0 |

#### 4 여러 사건 비교 — 18/20 채점 완료

| 지표 | 평균 | 평가 가능 | 평가 불가 | 미적용 | 대기 | 항목 누락 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 후보: 전체 페이지 근거 | 1.000 | 18 | 0 | 0 | 2 | 0 |
| 최종: 전체 페이지 근거 | 1.000 | 18 | 0 | 0 | 2 | 0 |
| 후보 페이지 Recall | 1.000 | 18 | 0 | 0 | 2 | 0 |
| 최종 페이지 Recall | 1.000 | 18 | 0 | 0 | 2 | 0 |
| 후보 MRR | 0.963 | 18 | 0 | 0 | 2 | 0 |
| 최종 MRR | 0.963 | 18 | 0 | 0 | 2 | 0 |
| 후보 nDCG | 0.968 | 18 | 0 | 0 | 2 | 0 |
| 최종 nDCG | 0.968 | 18 | 0 | 0 | 2 | 0 |
| 최종 문맥: 필수 사실 전체 지지 | 1.000 | 18 | 0 | 0 | 2 | 0 |
| 답변: 필수 사실 전체 정확 | 1.000 | 18 | 0 | 0 | 2 | 0 |
| Faithfulness (advanced) | 0.984 | 15 | 3 | 0 | 2 | 0 |
| Judge: 사실 | 1.000 | 18 | 0 | 0 | 2 | 0 |
| Judge: 조건 | 1.000 | 18 | 0 | 0 | 2 | 0 |
| Judge: 근거 | 1.000 | 18 | 0 | 0 | 2 | 0 |
| Judge: 판단 | 1.000 | 18 | 0 | 0 | 2 | 0 |

#### 5 기록·조건 판정 — 14/20 채점 완료

| 지표 | 평균 | 평가 가능 | 평가 불가 | 미적용 | 대기 | 항목 누락 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 후보: 전체 페이지 근거 | 1.000 | 14 | 0 | 0 | 6 | 0 |
| 최종: 전체 페이지 근거 | 1.000 | 14 | 0 | 0 | 6 | 0 |
| 후보 페이지 Recall | 1.000 | 14 | 0 | 0 | 6 | 0 |
| 최종 페이지 Recall | 1.000 | 14 | 0 | 0 | 6 | 0 |
| 후보 MRR | 1.000 | 14 | 0 | 0 | 6 | 0 |
| 최종 MRR | 1.000 | 14 | 0 | 0 | 6 | 0 |
| 후보 nDCG | 1.000 | 14 | 0 | 0 | 6 | 0 |
| 최종 nDCG | 1.000 | 14 | 0 | 0 | 6 | 0 |
| 최종 문맥: 필수 사실 전체 지지 | 1.000 | 13 | 1 | 0 | 6 | 0 |
| 답변: 필수 사실 전체 정확 | 1.000 | 14 | 0 | 0 | 6 | 0 |
| Faithfulness (advanced) | 0.929 | 13 | 1 | 0 | 6 | 0 |
| Judge: 사실 | 1.000 | 14 | 0 | 0 | 6 | 0 |
| Judge: 조건 | 1.000 | 14 | 0 | 0 | 6 | 0 |
| Judge: 근거 | 1.000 | 14 | 0 | 0 | 6 | 0 |
| Judge: 판단 | 1.000 | 14 | 0 | 0 | 6 | 0 |

#### 6 대상 모호·추가 확인 — 10/20 채점 완료

| 지표 | 평균 | 평가 가능 | 평가 불가 | 미적용 | 대기 | 항목 누락 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 후보: 전체 페이지 근거 | 0.900 | 10 | 0 | 0 | 10 | 0 |
| 최종: 전체 페이지 근거 | 0.900 | 10 | 0 | 0 | 10 | 0 |
| 후보 페이지 Recall | 0.950 | 10 | 0 | 0 | 10 | 0 |
| 최종 페이지 Recall | 0.950 | 10 | 0 | 0 | 10 | 0 |
| 후보 MRR | 1.000 | 10 | 0 | 0 | 10 | 0 |
| 최종 MRR | 1.000 | 10 | 0 | 0 | 10 | 0 |
| 후보 nDCG | 0.950 | 10 | 0 | 0 | 10 | 0 |
| 최종 nDCG | 0.950 | 10 | 0 | 0 | 10 | 0 |
| 최종 문맥: 필수 사실 전체 지지 | 0.900 | 10 | 0 | 0 | 10 | 0 |
| 답변: 필수 사실 전체 정확 | 0.700 | 10 | 0 | 0 | 10 | 0 |
| Faithfulness (advanced) | 0.861 | 9 | 1 | 0 | 10 | 0 |
| Judge: 사실 | 0.700 | 10 | 0 | 0 | 10 | 0 |
| Judge: 조건 | 0.700 | 10 | 0 | 0 | 10 | 0 |
| Judge: 근거 | 0.600 | 10 | 0 | 0 | 10 | 0 |
| Judge: 판단 | 0.700 | 10 | 0 | 0 | 10 | 0 |

#### 7 확정 근거 부족 — 9/20 채점 완료

| 지표 | 평균 | 평가 가능 | 평가 불가 | 미적용 | 대기 | 항목 누락 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 후보: 전체 페이지 근거 | 1.000 | 9 | 0 | 0 | 11 | 0 |
| 최종: 전체 페이지 근거 | 1.000 | 9 | 0 | 0 | 11 | 0 |
| 후보 페이지 Recall | 1.000 | 9 | 0 | 0 | 11 | 0 |
| 최종 페이지 Recall | 1.000 | 9 | 0 | 0 | 11 | 0 |
| 후보 MRR | 0.889 | 9 | 0 | 0 | 11 | 0 |
| 최종 MRR | 0.889 | 9 | 0 | 0 | 11 | 0 |
| 후보 nDCG | 0.882 | 9 | 0 | 0 | 11 | 0 |
| 최종 nDCG | 0.882 | 9 | 0 | 0 | 11 | 0 |
| 최종 문맥: 필수 사실 전체 지지 | 1.000 | 9 | 0 | 0 | 11 | 0 |
| 답변: 필수 사실 전체 정확 | 1.000 | 9 | 0 | 0 | 11 | 0 |
| Faithfulness (advanced) | 0.987 | 5 | 4 | 0 | 11 | 0 |
| Judge: 사실 | 1.000 | 9 | 0 | 0 | 11 | 0 |
| Judge: 조건 | 1.000 | 9 | 0 | 0 | 11 | 0 |
| Judge: 근거 | 1.000 | 9 | 0 | 0 | 11 | 0 |
| Judge: 판단 | 1.000 | 9 | 0 | 0 | 11 | 0 |

#### 업무 규정: 적용 — 1/10 채점 완료

| 지표 | 평균 | 평가 가능 | 평가 불가 | 미적용 | 대기 | 항목 누락 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 후보: 전체 페이지 근거 | 1.000 | 1 | 0 | 0 | 9 | 0 |
| 최종: 전체 페이지 근거 | 1.000 | 1 | 0 | 0 | 9 | 0 |
| 후보 페이지 Recall | 1.000 | 1 | 0 | 0 | 9 | 0 |
| 최종 페이지 Recall | 1.000 | 1 | 0 | 0 | 9 | 0 |
| 후보 MRR | 1.000 | 1 | 0 | 0 | 9 | 0 |
| 최종 MRR | 1.000 | 1 | 0 | 0 | 9 | 0 |
| 후보 nDCG | 1.000 | 1 | 0 | 0 | 9 | 0 |
| 최종 nDCG | 1.000 | 1 | 0 | 0 | 9 | 0 |
| 최종 문맥: 필수 사실 전체 지지 | 1.000 | 1 | 0 | 0 | 9 | 0 |
| 답변: 필수 사실 전체 정확 | 1.000 | 1 | 0 | 0 | 9 | 0 |
| Faithfulness (advanced) | 0.857 | 1 | 0 | 0 | 9 | 0 |
| Judge: 사실 | 1.000 | 1 | 0 | 0 | 9 | 0 |
| Judge: 조건 | 1.000 | 1 | 0 | 0 | 9 | 0 |
| Judge: 근거 | 1.000 | 1 | 0 | 0 | 9 | 0 |
| Judge: 판단 | 1.000 | 1 | 0 | 0 | 9 | 0 |

#### 업무 규정: 절차 — 3/10 채점 완료

| 지표 | 평균 | 평가 가능 | 평가 불가 | 미적용 | 대기 | 항목 누락 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 후보: 전체 페이지 근거 | 1.000 | 3 | 0 | 0 | 7 | 0 |
| 최종: 전체 페이지 근거 | 1.000 | 3 | 0 | 0 | 7 | 0 |
| 후보 페이지 Recall | 1.000 | 3 | 0 | 0 | 7 | 0 |
| 최종 페이지 Recall | 1.000 | 3 | 0 | 0 | 7 | 0 |
| 후보 MRR | 0.833 | 3 | 0 | 0 | 7 | 0 |
| 최종 MRR | 0.833 | 3 | 0 | 0 | 7 | 0 |
| 후보 nDCG | 0.877 | 3 | 0 | 0 | 7 | 0 |
| 최종 nDCG | 0.877 | 3 | 0 | 0 | 7 | 0 |
| 최종 문맥: 필수 사실 전체 지지 | 1.000 | 3 | 0 | 0 | 7 | 0 |
| 답변: 필수 사실 전체 정확 | 1.000 | 3 | 0 | 0 | 7 | 0 |
| Faithfulness (advanced) | 0.833 | 3 | 0 | 0 | 7 | 0 |
| Judge: 사실 | 1.000 | 3 | 0 | 0 | 7 | 0 |
| Judge: 조건 | 1.000 | 3 | 0 | 0 | 7 | 0 |
| Judge: 근거 | 1.000 | 3 | 0 | 0 | 7 | 0 |
| Judge: 판단 | 1.000 | 3 | 0 | 0 | 7 | 0 |

## 3. 카테고리 180문항 — 9개 그룹

앞의 14개 그룹 중 해당 문항을 다시 보여주는 부분집합입니다. 전체 280에 중복 합산하지 않습니다.

| 유형 | 전체 | 채점 완료 | 대기 | 최종 페이지 전체 | 필수 사실 최종 지지 | 답변 필수 사실 정확 | Faithfulness |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 직접 조회 | 20 | 19 | 1 | 1.000 / n=19 | 1.000 / n=17 | 1.000 / n=17 | 0.961 / n=16 |
| 2 번호 없이 사건 식별 | 20 | 20 | 0 | 0.950 / n=20 | 0.950 / n=20 | 0.950 / n=20 | 0.968 / n=19 |
| 3 한 사건의 여러 기록 | 20 | 17 | 3 | 1.000 / n=17 | 1.000 / n=17 | 1.000 / n=17 | 0.941 / n=13 |
| 4 여러 사건 비교 | 20 | 18 | 2 | 1.000 / n=18 | 1.000 / n=18 | 1.000 / n=18 | 0.984 / n=15 |
| 5 기록·조건 판정 | 20 | 14 | 6 | 1.000 / n=14 | 1.000 / n=13 | 1.000 / n=14 | 0.929 / n=13 |
| 6 대상 모호·추가 확인 | 20 | 10 | 10 | 0.900 / n=10 | 0.900 / n=10 | 0.700 / n=10 | 0.861 / n=9 |
| 7 확정 근거 부족 | 20 | 9 | 11 | 1.000 / n=9 | 1.000 / n=9 | 1.000 / n=9 | 0.987 / n=5 |
| 8 범위 밖 질문 | 20 | 2 | 18 | — | — | — | 0.525 / n=2 |
| 업무 규정 | 20 | 4 | 16 | 1.000 / n=4 | 1.000 / n=4 | 1.000 / n=4 | 0.839 / n=4 |

전체 문항 평균(micro)과 그룹별 동일 가중 평균(macro)은 집계 JSON에 따로 저장합니다. 유형별 완료율이 다르므로 현재 macro 또한 최종 비교에 사용하지 않습니다.

## 4. 실패 위치 — 페이지 존재와 의미 판정 구분

### 정답 페이지의 최초 누락

페이지 지표는 book_id+page 및 허용 대체 출처에 근거합니다. 페이지 부재만으로 답변 오류를 확정하지 않습니다.

| 저장 진단 | 문항 수 |
| --- | ---: |
| 정답 페이지가 후보에 없음 | 21 |
| 최종 문맥에 정답 페이지 존재 | 143 |
| 후보에는 있지만 최종 문맥에서 빠짐 | 4 |
| 페이지 정답 없음 | 4 |

### 의미상 필수 사실의 누락

아래 수는 문항 단위이며 여러 단계가 한 문항에 공존할 수 있습니다. 합계를 전체 실패 문항 수로 사용하지 않습니다. 필수 사실별 자동 판정이며 사람 검수 완료를 의미하지 않습니다.

| 최초 손실 단계 | 해당 문항 수 |
| --- | ---: |
| 최종 문맥에 있지만 답변에서 누락·오답 | 5 |
| 후보에는 있지만 최종 문맥에서 빠짐 | 1 |
| 검색기에서 근거를 찾지 못함 | 2 |
| 검색 상태 판정이 답변을 막음 | 1 |
| 최초 누락 단계 불명확 | 19 |

### 현재 자료에서 먼저 확인할 차이

- 페이지 진단은 누락이지만 의미 판정상 필수 사실이 최종 문맥·답변에 모두 있는 문항: **12개**. 대체 근거나 페이지 정답의 범위를 검토해야 하며 단순 검색 실패로 합치지 않습니다.
- 최종 문맥이 지지하지만 답변에서 빠지거나 틀렸다고 판정한 문항: **6개**. 검색 개선과 구분해 최종 답변 반영을 점검할 대상입니다.
- 평가 불가·불확실 판정은 별도 검토 대상으로 유지합니다. 미완료 표본으로 실패율이나 원인의 우선순위를 확정하지 않습니다.

## 5. 문항별 점검 목록

원문과 모델의 자유 서술 판정은 복사하지 않았습니다. 링크의 체크포인트에서 score.evaluation_details.attribution.fact_checks 및 score.evaluation_details.faithfulness를 확인할 수 있습니다. 필수 사실 번호는 items의 0부터 시작하는 인덱스입니다.

| ID | 유형 | 페이지 진단 | 의미 판정 / 누락·오답 사실 | 답변 단계 손실 사실 | 평가 불가 지표 수 | 확인 자료 |
| --- | --- | --- | --- | --- | ---: | --- |
| AS-DEV02 | Development | 정답 페이지가 후보에 없음 | scored / 없음; 불확실 2 | — | 2 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-02/grades/luna-max-128k-advanced/checkpoints/47a6e242bb222b4f29a45e9b7b6712c5b95361646015e8563357221524d0d96f.json) |
| AS-DEV03 | Development | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-03/grades/luna-max-128k-advanced/checkpoints/26c2de454137417bc551fa8a37fb0fe290e1028876b1fccd2e4f7e25f2530ed3.json) |
| AS-DEV04 | Development | 정답 페이지가 후보에 없음 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-04/grades/luna-max-128k-advanced/checkpoints/13b5e8e9b61d3eab65a87d92e671ba5b9a8f0a28b2f77318b08c6c821385f38f.json) |
| AS-DEV06 | Development | 정답 페이지가 후보에 없음 | scored / 없음; 불확실 2 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-06/grades/luna-max-128k-advanced/checkpoints/2b0b9b7357a1fc99e4161e9fbac19d95d1aece08fbd2165881291c004ceee0ed.json) |
| AS-DEV07 | Development | 최종 문맥에 정답 페이지 존재 | scored / 없음; 불확실 1 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-07/grades/luna-max-128k-advanced/checkpoints/946211138a81c3da7a18f20fff605f1f147abef4f5ffbec9e6ba0ab612bea749.json) |
| AS-DEV08 | Development | 정답 페이지가 후보에 없음 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-08/grades/luna-max-128k-advanced/checkpoints/0ed9c447738ca3e6a3ff21d819eea5b79dead78c524db2ba3855eeed59e10d23.json) |
| AS-DEV09 | Development | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-09/grades/luna-max-128k-advanced/checkpoints/a761acd5a140dbfc3856766ce837b6192d19acf1ee07c50d38eea62c6f9a7840.json) |
| AS-DEV10 | Development | 최종 문맥에 정답 페이지 존재 | scored / 없음; 불확실 0,1 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-10/grades/luna-max-128k-advanced/checkpoints/1e7c1b9231f7e9895c27abeb7d98efa294992ab016ca7d918d178067ac92e690.json) |
| AS-DEV11 | Development | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-11/grades/luna-max-128k-advanced/checkpoints/57b168b73a39fda203a083a9b54ac528793165e750eddc807ffc4e6ef2abbe4b.json) |
| AS-DEV12 | Development | 페이지 정답 없음 | scored / 0; 불확실 0 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-12/grades/luna-max-128k-advanced/checkpoints/f649b629c3e69d155fe61f05218a387edf0fa4a4aef7a80703f683236f63ad8c.json) |
| AS-DEV13 | Development | 후보에는 있지만 최종 문맥에서 빠짐 | scored / 0,1 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-13/grades/luna-max-128k-advanced/checkpoints/8d16e67843ee639f89da015075ffd3f859f739c2803e41cb1906d0ebb400368d.json) |
| AS-DEV14 | Development | 최종 문맥에 정답 페이지 존재 | scored / 없음; 불확실 0,1,2 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-14/grades/luna-max-128k-advanced/checkpoints/e07a75cd1631ca9bc82241ed2e843628e9d6b912c88bc9809e2aac36a12a845c.json) |
| AS-DEV15 | Development | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-15/grades/luna-max-128k-advanced/checkpoints/e04af54949766c4230e8b4da1348591513027e82576e922575562dd899dffdfd.json) |
| AS-DEV16 | Development | 정답 페이지가 후보에 없음 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-16/grades/luna-max-128k-advanced/checkpoints/85feba663424321ebbbe734264dc42d64797b340932010972b4d014c372e67f4.json) |
| AS-DEV17 | Development | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-17/grades/luna-max-128k-advanced/checkpoints/c6ee973a1d737fcc47f3b884e1b71af60b6b60738e933689c15fac29bb4eb365.json) |
| AS-DEV18 | Development | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-18/grades/luna-max-128k-advanced/checkpoints/97fbcf6280b3a16decda429a51ab5cca30e30c624cb7dc0617f5e99f81ada52d.json) |
| AS-DEV19 | Development | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-19/grades/luna-max-128k-advanced/checkpoints/2a0a7f65a10400f72e56daf91f7c4f627bb293493b3ea9f2540477cbdf53a2d0.json) |
| AS-DEV22 | Development | 정답 페이지가 후보에 없음 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-22/grades/luna-max-128k-advanced/checkpoints/ba0b31078df16e6eb1a6fbf5c9159b81bd49c0e9e1e78dafb4c629ae20e23b0b.json) |
| AS-DEV24 | Development | 정답 페이지가 후보에 없음 | scored / 없음; 불확실 0 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-24/grades/luna-max-128k-advanced/checkpoints/a30ac69da1e229a0a6d9bb4bddc163b0f98a335879b03a56687f3ada2aaeb280.json) |
| AS-DEV26 | Development | 정답 페이지가 후보에 없음 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-26/grades/luna-max-128k-advanced/checkpoints/7c6ffbab61919af574149fb9fa77d4ef400edd74e142eb5c5175cac295002997.json) |
| AS-DEV28 | Development | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-28/grades/luna-max-128k-advanced/checkpoints/cde160935fa74672ddbc61762d71596d2277ef8ae502999c1e1b9af7bfbb21d9.json) |
| AS-DEV30 | Development | 정답 페이지가 후보에 없음 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-30/grades/luna-max-128k-advanced/checkpoints/59766f815d0dedc1ca8cd438a5f493e0298680c6c5c95a2d5bc93ca3a636c4f0.json) |
| AS-ADD01 | Additional | 정답 페이지가 후보에 없음 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-31/grades/luna-max-128k-advanced/checkpoints/f417564e99a1aec432534968743bfee2d0dea00f6ef696c4a7d0594a89110449.json) |
| AS-ADD02 | Additional | 최종 문맥에 정답 페이지 존재 | scored / 없음; 불확실 1 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-32/grades/luna-max-128k-advanced/checkpoints/6995fdb1ee149303553d7ca179ff54d03aed993db6e89723fdc0c34a5b528742.json) |
| AS-ADD03 | Additional | 정답 페이지가 후보에 없음 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-01/grades/luna-max-128k-advanced/checkpoints/45db2821da76e914ac12892dddf64d31d05e3256da1f8a59030291f742539e8f.json) |
| AS-ADD05 | Additional | 정답 페이지가 후보에 없음 | scored / 없음; 불확실 1 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-03/grades/luna-max-128k-advanced/checkpoints/a543b6ebacb874e2b02cb5f6c8264766ffa6ce02a1c0e8cbb4f3f04982c53211.json) |
| AS-ADD06 | Additional | 정답 페이지가 후보에 없음 | scored / 없음; 불확실 2 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-04/grades/luna-max-128k-advanced/checkpoints/050645597798b1ba515d7c4067985e00551002a4c7abfaadb91da166648844a8.json) |
| AS-ADD07 | Additional | 정답 페이지가 후보에 없음 | scored / 없음 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-05/grades/luna-max-128k-advanced/checkpoints/ba53ff74e15f7d134a0409c4f0ed6596c48e3171cdb92697fdc9a8f7d7431326.json) |
| AS-ADD09 | Additional | 정답 페이지가 후보에 없음 | scored / 0 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-07/grades/luna-max-128k-advanced/checkpoints/4dcdb496bcb9056449f1c8385d203d9a9546c71caabc6d5fd376cef1ca0628b9.json) |
| AS-ADD10 | Additional | 정답 페이지가 후보에 없음 | scored / 0; 불확실 2 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-08/grades/luna-max-128k-advanced/checkpoints/5375423112bed9d34a024b423db953ec358b3e4836daa2682d4513643a1a1455.json) |
| AS-ADD12 | Additional | 페이지 정답 없음 | scored / 1 | 1 | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-10/grades/luna-max-128k-advanced/checkpoints/7d5b603dbf513263f593877d69624b561295f18162b045b1dcb7b653149f3c79.json) |
| AS-ADD13 | Additional | 최종 문맥에 정답 페이지 존재 | scored / 2 | 2 | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-11/grades/luna-max-128k-advanced/checkpoints/4bbfc1356b19fa12ffb3077c8c96d41efd5432d37d06b112c330a17950637ad3.json) |
| AS-ADD14 | Additional | 후보에는 있지만 최종 문맥에서 빠짐 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-12/grades/luna-max-128k-advanced/checkpoints/5892ee9e2693892aafa1d6ce4f689133d38c0f8b22044d47027ad456fd01d87e.json) |
| AS-ADD18 | Additional | 최종 문맥에 정답 페이지 존재 | scored / 0 | 0 | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-16/grades/luna-max-128k-advanced/checkpoints/a9282e2223e9a35aed118369e98795432dfcec82aeb9254b659878f81b33c5fb.json) |
| AS-ADD19 | Additional | 정답 페이지가 후보에 없음 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-17/grades/luna-max-128k-advanced/checkpoints/3e65d02fa9b7ce19eedc60c639bbfe06ff503eee07eee13d1e9d6cdc7a951474.json) |
| AS-ADD21 | Additional | 정답 페이지가 후보에 없음 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-19/grades/luna-max-128k-advanced/checkpoints/e72b591b34c1619383e6b359bfa00b6fb33c1b215c22c5a8ffa6c9fe78148883.json) |
| AS-ADD23 | Additional | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-21/grades/luna-max-128k-advanced/checkpoints/3ab8830d6135ba74a2ffd852e647d669b7f83902111a2b4abb6893b19a58fa59.json) |
| AS-ADD25 | Additional | 정답 페이지가 후보에 없음 | scored / 없음; 불확실 0 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-23/grades/luna-max-128k-advanced/checkpoints/4ac790918c524a65ac36f7693ad325b47ff12c1faf3d5205562f9c44d9c671e4.json) |
| AS-ADD27 | Additional | 후보에는 있지만 최종 문맥에서 빠짐 | scored / 없음; 불확실 0,1 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-25/grades/luna-max-128k-advanced/checkpoints/8d5cf3937adedea0550b03d4e53777a492137bfacdc94124bea82f1f9fd7421b.json) |
| AS-ADD29 | Additional | 정답 페이지가 후보에 없음 | scored / 0 | 0 | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-27/grades/luna-max-128k-advanced/checkpoints/4d088bf3392d1a1c823bae1d4c450145db8d0db1ce57da4d6e1e8ac91bd37a16.json) |
| AS-S01-03 | 1 직접 조회 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-31/grades/luna-max-128k-advanced/checkpoints/d996794d375f28d1357f3cb92741ceacd46ad555e18d3856cb1226abb67ccbfb.json) |
| AS-S01-05 | 1 직접 조회 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-01/grades/luna-max-128k-advanced/checkpoints/9ac4ecb7af43a01754295de6c18e54cf6f24dab932d8e8baac8b3ce3d276aa02.json) |
| AS-S01-07 | 1 직접 조회 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-03/grades/luna-max-128k-advanced/checkpoints/449b13bed1b51f3c01370d9c4607a44755d8ada8da214472fca141ad33560177.json) |
| AS-S01-09 | 1 직접 조회 | 최종 문맥에 정답 페이지 존재 | scored / 없음; 불확실 0 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-05/grades/luna-max-128k-advanced/checkpoints/de33027b42c8352b044aec298eb31d8369e56baa7b1dfe0b69a0e1d96df7023c.json) |
| AS-S01-11 | 1 직접 조회 | 최종 문맥에 정답 페이지 존재 | scored / 없음; 불확실 0 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-07/grades/luna-max-128k-advanced/checkpoints/e4c63d91c1b0e90604989264d7612710e11311027834ef45ebadc0f29d0669e3.json) |
| AS-S01-12 | 1 직접 조회 | 최종 문맥에 정답 페이지 존재 | scored / 없음; 불확실 0 | — | 2 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-08/grades/luna-max-128k-advanced/checkpoints/71f5d050cc56fd106d81183f80f20685205e474b6997c61649022d3209736f27.json) |
| AS-S01-15 | 1 직접 조회 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-11/grades/luna-max-128k-advanced/checkpoints/974bfca032388f3d655382a596b6b1d9ff730a7db3461c3e1986194545bec36e.json) |
| AS-S02-09 | 2 번호 없이 사건 식별 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-25/grades/luna-max-128k-advanced/checkpoints/a4bf87df3a6aa76635e6c5f3cfd95e8e860b92d8e7cdf2c43c9f3fee1a741393.json) |
| AS-S02-12 | 2 번호 없이 사건 식별 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-28/grades/luna-max-128k-advanced/checkpoints/9dfcbaa866ef886d06a7dd2cd309435466ccdff4cbd827280b0445d532dfb683.json) |
| AS-S02-13 | 2 번호 없이 사건 식별 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-29/grades/luna-max-128k-advanced/checkpoints/a8266af1b962f2dac6defa32e5549d67a59daca2a052e527a606b6fde44e6cac.json) |
| AS-S02-15 | 2 번호 없이 사건 식별 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-31/grades/luna-max-128k-advanced/checkpoints/7d655a36fee3d2572db4b750174aa7e734a276ab8c2d0d050a2410679b3ec18d.json) |
| AS-S02-17 | 2 번호 없이 사건 식별 | 후보에는 있지만 최종 문맥에서 빠짐 | scored / 0,1 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-01/grades/luna-max-128k-advanced/checkpoints/79d8d0cc7aeec806a987f090031171d8979271dedfd47f4533a1a20854299257.json) |
| AS-S02-19 | 2 번호 없이 사건 식별 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-03/grades/luna-max-128k-advanced/checkpoints/c5b60e10f0191791a47535687143b5603e2546f5c4ea9418f0d5215193f4db5b.json) |
| AS-S03-01 | 3 한 사건의 여러 기록 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-05/grades/luna-max-128k-advanced/checkpoints/f260cc4498f842faf7a6f94b63635a84170c4410cf98796313fefd3e4de3b41d.json) |
| AS-S03-02 | 3 한 사건의 여러 기록 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-06/grades/luna-max-128k-advanced/checkpoints/8d69435d5ef04c0b0a6d092ea18623e86b751957fa7c6f65abdc4d6816c276d3.json) |
| AS-S03-08 | 3 한 사건의 여러 기록 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-12/grades/luna-max-128k-advanced/checkpoints/99d4730b320118454186939632335529186844535ba3a8f51b33f19855ae5363.json) |
| AS-S03-10 | 3 한 사건의 여러 기록 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-14/grades/luna-max-128k-advanced/checkpoints/9af81c9af5fba84ab84beb0b0ae9e3077f1e4fa7a737352a5d20e3362782dd75.json) |
| AS-S03-11 | 3 한 사건의 여러 기록 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-15/grades/luna-max-128k-advanced/checkpoints/c6dac620a1d0caa9d19cbfb6459136a7c5df836d880c55363b61ca65d3830270.json) |
| AS-S03-14 | 3 한 사건의 여러 기록 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-18/grades/luna-max-128k-advanced/checkpoints/0c52c077295ca26ab2e2489628aab8eb843ed233e7a3a3fedd2c572a4ceff47c.json) |
| AS-S03-16 | 3 한 사건의 여러 기록 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-20/grades/luna-max-128k-advanced/checkpoints/1a08beccb1919f2132da6231559d02db0d857235ab3774429c65fc0eb77c270c.json) |
| AS-S03-18 | 3 한 사건의 여러 기록 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-22/grades/luna-max-128k-advanced/checkpoints/a684f9089fecbd1d6162427ce83cacc27fbe3c466144175783e76118e873ee2d.json) |
| AS-S04-05 | 4 여러 사건 비교 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-29/grades/luna-max-128k-advanced/checkpoints/910511753a6405663b64a057f42e5f300a93de5cd035bf1446062437eebb1e23.json) |
| AS-S04-08 | 4 여러 사건 비교 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-32/grades/luna-max-128k-advanced/checkpoints/b10935c541540a6667493a38324c4f51dfc52d22c5cf888bed36550ec9f361ad.json) |
| AS-S04-09 | 4 여러 사건 비교 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-01/grades/luna-max-128k-advanced/checkpoints/a491c3bcd3c05667cbcb8d8a1d643f9ae601eefc3ac956246d49c43f4ac9ff68.json) |
| AS-S04-18 | 4 여러 사건 비교 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-10/grades/luna-max-128k-advanced/checkpoints/60c48c60278ed786b1647a2c78b68ebf7e667072451b7da7bd991bd3ceee31bc.json) |
| AS-S04-20 | 4 여러 사건 비교 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-12/grades/luna-max-128k-advanced/checkpoints/35f0384200779a85747718f91f0219998af3ab417960c3df5c22393553c41e4b.json) |
| AS-S05-02 | 5 기록·조건 판정 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-14/grades/luna-max-128k-advanced/checkpoints/aa84f95f313c3fdcbc530c410c75a2d658a389c460aef3151c8fd0fd391dcaec.json) |
| AS-S05-03 | 5 기록·조건 판정 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-15/grades/luna-max-128k-advanced/checkpoints/82ab55cfca3213ff45f966e6de65dd27b48f87ee5cc14f58013df4710c1cd09e.json) |
| AS-S05-04 | 5 기록·조건 판정 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-16/grades/luna-max-128k-advanced/checkpoints/4f288b7dc410daac5a46166087b31bbb1b3a44a1f1a406fdf022c647ef28f191.json) |
| AS-S05-14 | 5 기록·조건 판정 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-26/grades/luna-max-128k-advanced/checkpoints/00424486a7be9bb719d3561691acaa8200b32b2dfcb30bf100d8204f46eb250b.json) |
| AS-S05-17 | 5 기록·조건 판정 | 최종 문맥에 정답 페이지 존재 | scored / 없음; 불확실 0 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-29/grades/luna-max-128k-advanced/checkpoints/d162d60ca13a8dd63d7aea5635ca311161b24b702bbac4c9c04ab7b0ce0e64c8.json) |
| AS-S06-02 | 6 대상 모호·추가 확인 | 정답 페이지가 후보에 없음 | scored / 0,1 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-02/grades/luna-max-128k-advanced/checkpoints/a4f9c4d0df6eaae7bfd2a87f48efc9071887e31fd667e99c85351752699d78c2.json) |
| AS-S06-05 | 6 대상 모호·추가 확인 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-05/grades/luna-max-128k-advanced/checkpoints/bbd471538544955ed212302e6f3d329cd74b988c466b422a733da97333e6d4cc.json) |
| AS-S06-09 | 6 대상 모호·추가 확인 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-09/grades/luna-max-128k-advanced/checkpoints/35ffae9b6c1fcff104a4c8101e4dd343ce9fefadda628fd990bf1f03d06f8020.json) |
| AS-S06-11 | 6 대상 모호·추가 확인 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-11/grades/luna-max-128k-advanced/checkpoints/4b6ff1757c265b920add5d0f010a2fbc2444363430412bfae69a697b40bdece1.json) |
| AS-S06-15 | 6 대상 모호·추가 확인 | 최종 문맥에 정답 페이지 존재 | scored / 0,1 | 0,1 | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-15/grades/luna-max-128k-advanced/checkpoints/9cfd9cc0671a82aea48a4ff61bce89e16a3347e5f4fc1256c7d9929990608f18.json) |
| AS-S06-16 | 6 대상 모호·추가 확인 | 최종 문맥에 정답 페이지 존재 | scored / 0,1 | 0,1 | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-16/grades/luna-max-128k-advanced/checkpoints/6026d8e37b53368848af745b1158c63308f727e94cec1bc6bb6b7f1c6b680e4c.json) |
| AS-S07-03 | 7 확정 근거 부족 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-23/grades/luna-max-128k-advanced/checkpoints/ebf5526ac9708f4591a2fbdea76b3d5a7c2e2b05c165ff701f87bf46ad24010e.json) |
| AS-S07-06 | 7 확정 근거 부족 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-26/grades/luna-max-128k-advanced/checkpoints/84fabef3e6395d7b8628cc5ff730463cf879316983b89d829ed07fb37039a950.json) |
| AS-S07-08 | 7 확정 근거 부족 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-28/grades/luna-max-128k-advanced/checkpoints/31d71b6ac726e35bfa27044fb9ddd9067d922c4184b9bfe2b296087877ae8039.json) |
| AS-S07-10 | 7 확정 근거 부족 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-30/grades/luna-max-128k-advanced/checkpoints/55bb226d936c67b67d8a38d27c016a3d845b053d0fcfbf4cbccdc7ecf2963dc2.json) |
| AS-S07-16 | 7 확정 근거 부족 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 1 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-04/grades/luna-max-128k-advanced/checkpoints/7cbb8a0fa9984cd40d02c61de294c892f1b0f70676ee2ef3138632947cab35a1.json) |
| AS-S08-06 | 8 범위 밖 질문 | 페이지 정답 없음 | not_applicable / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-14/grades/luna-max-128k-advanced/checkpoints/7e11cd2317b55eade5acf64e23988fb9be34bd189aa53eb35cdee2a0df31d0c2.json) |
| AS-S08-20 | 8 범위 밖 질문 | 페이지 정답 없음 | not_applicable / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-28/grades/luna-max-128k-advanced/checkpoints/48d6176bafe9933fa6dafbf57c5a342c3401314b6ce066c3e10fef99e63b10a8.json) |
| AS-WR-03 | 업무 규정: 절차 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-31/grades/luna-max-128k-advanced/checkpoints/a704aeb69852b7511fcdf38da0e923ef719a326612c0ac35fa9e9db25ec2aef7.json) |
| AS-WR-08 | 업무 규정: 절차 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-04/grades/luna-max-128k-advanced/checkpoints/13bf5b7d8b32203e000c3e57cd9f81046f554fa0bacee97c95e47e12d2f31d3c.json) |
| AS-WR-18 | 업무 규정: 적용 | 최종 문맥에 정답 페이지 존재 | scored / 없음 | — | 0 | [저장 판정](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-280-20261113-grade-views/part-14/grades/luna-max-128k-advanced/checkpoints/a001166aa0dd4518b69132b30d6ab32af623713fe243e1cf81f2ad96d5434908.json) |

## 6. 재개와 최종 합치기

저장된 완료 결과만 읽는 명령입니다. 새 채점·답변 생성·원격 점수 게시를 실행하지 않습니다.

```bash
cd /home/jake/workspace/projects/kant/ADEN
.venv/bin/python artifacts/experiments/advanced-final-benchmark-20261113-report-draft/refresh_report.py
```

- 동일 결과는 한 번만 집계합니다. 새 완료 문항을 더한 후 모든 표를 기존 집계 함수로 다시 계산합니다.
- 기존 결과나 채점 조건이 달라지면 충돌로 기록하고 자동 교체하지 않습니다.
- 입력 고정 재현은 `--replay <snapshot 디렉터리>`를 사용합니다. 기존 스냅샷은 수정하지 않습니다.
- 280개 고유 결과 수집 후에도 평가 불가·누락·판정 충돌을 확인해야 합니다. 기존 최종 보고서에 자동 게시하지 않습니다.

스냅샷: [20261113T050548759516Z-172](/home/jake/workspace/projects/kant/ADEN/artifacts/experiments/advanced-final-benchmark-20261113-report-draft/snapshots/20261113T050548759516Z-172) · 집계 입력/조건과 문항별 출처는 manifest.json에 기록했습니다.
공유 보고서의 기술적 출처: 기존 app/evaluation/benchmark.py의 summarize_benchmark 및 질문별 추적 함수.
