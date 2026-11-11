# Broad-01 독립 집계·진단

2026-11-10. broad-01의 `summary`, `scores`, `changes`, `traces`, `workload`, `config`에서 질문 원문이나 인용을 열지 않고 다시 집계했다. 저장된 식별자는 회귀 문항을 추적하는 데만 썼다. 결과는 검색 근거의 단계별 포함 여부이며, 답변 정확도나 운영 품질 측정은 아니다.

## 범위와 집계 기준

- 344문항: 동료 304문항(`peer_false_premise` 20, `peer_two_doc` 114, `peer_multi_doc` 170)과 일상 표현 40문항(`semantic_queries`).
- baseline은 현재 개인 `advanced_search`이며 Luna HG router를 포함하지 않는다. 나머지 네 arm은 같은 보관 계획을 사용한 `requests20`, `requests40`, `requests20_pack`, `requests40_pack`이다. 검색기별 후보는 고정 Top20, 통합 후보는 20쪽, 최종 문맥은 최대 8쪽·8,000토큰이다.
- 개선·하락은 baseline과 문항 ID·split을 맞춰 `final_complete`가 0→1 또는 1→0으로 바뀐 수다. `candidate_complete`, `expanded_complete`, `final_complete`는 각 단계에서 모든 필수 근거 사실을 포함했는지를 나타낸다. 표의 개선·하락은 한 그룹이라도 선택된 수가 아니라 전체 필수 사실의 완전 충족이다.
- `changes`의 단계 delta는 완전도 0/1 변화다. 회귀 단계는 baseline이 완전 충족한 문항에서 해당 arm이 처음 미충족된 단계로 분류했다. 필수 사실 개수 합은 별도 표에 둔다.

## 최종 완전 충족: 전체·분할별 paired 변화

`baseline` 열은 완전 충족 문항 수다. 각 arm 열은 `완전 충족 수 (개선/하락)`이며, 변경이 없는 수는 `분모 - 개선 - 하락`이다.

| 분할 | n | baseline | requests20 | requests40 | requests20_pack | requests40_pack |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 전체 | 344 | 139 | 162 (32/9) | **164 (33/8)** | 159 (32/12) | 161 (33/11) |
| 동료 전체 | 304 | 126 | 144 (26/8) | **145 (27/8)** | 141 (26/11) | 142 (27/11) |
| 일상 표현 | 40 | 13 | 18 (6/1) | **19 (6/0)** | 18 (6/1) | **19 (6/0)** |
| peer_false_premise | 20 | 18 | 18 (1/1) | 18 (1/1) | 17 (1/2) | 17 (1/2) |
| peer_two_doc | 114 | 51 | 59 (11/3) | 59 (11/3) | 59 (11/3) | 59 (11/3) |
| peer_multi_doc | 170 | 57 | 67 (14/4) | **68 (15/4)** | 65 (14/6) | 66 (15/6) |

비율로는 전체 baseline 40.4%에서 requests20 47.1%, requests40 47.7%, requests20_pack 46.2%, requests40_pack 46.8%였다. 동료 304에서는 41.4% → requests20 47.4%, requests40 47.7%, pack arm 46.4%·46.7%였다. 일상 40에서는 32.5% → 45.0%·47.5%·45.0%·47.5%였다.

## 후보·확장·최종 단계

아래는 각 단계의 완전 충족 문항 수와, 괄호 안에 전체 필수 사실 중 포함된 개수의 합을 적었다. 총 필수 사실 수가 문항마다 다르므로 완전 충족 수와 사실 합은 다른 지표다.

| arm | 후보 | 확장 후 | 최종 문맥 |
| --- | ---: | ---: | ---: |
| baseline | 171 (574) | 171 (574) | 139 (518) |
| requests20 | 186 (595) | 187 (596) | 162 (551) |
| requests40 | 190 (601) | 191 (602) | **164 (555)** |
| requests20_pack | 186 (595) | 187 (596) | 159 (535) |
| requests40_pack | 190 (601) | 191 (602) | 161 (540) |

Baseline에서 완전 충족한 문항이 각 arm에서 처음 빠진 단계와 ID는 다음과 같다. `—`는 해당 단계에서 완전 충족 회귀가 없다는 뜻이다.

| arm | 후보 단계 | 확장 단계 | 최종 문맥 단계 |
| --- | --- | --- | --- |
| requests20 | AS-SEM-09 | — | FPA-15, SY3-adv-029, SY4-adv-003, SY4-adv-028, SYF-adv-047, SYF-adv-053, SYF-adv-055, SYF4-adv-028 |
| requests40 | — | — | FPA-15, SY3-adv-029, SY4-adv-003, SY4-adv-028, SYF-adv-047, SYF-adv-053, SYF-adv-055, SYF4-adv-028 |
| requests20_pack | AS-SEM-09, FPA-12 | — | FPA-15, SY3-adv-006, SY3-adv-029, SY3-adv-033, SY3-adv-037, SY4-adv-003, SY4-adv-028, SYF-adv-047, SYF-adv-053, SYF-adv-055 |
| requests40_pack | FPA-12 | — | FPA-15, SY3-adv-006, SY3-adv-029, SY3-adv-033, SY3-adv-037, SY4-adv-003, SY4-adv-028, SYF-adv-047, SYF-adv-053, SYF-adv-055 |

후보 깊이만 비교하면 requests40은 requests20 대비 후보·확장 단계의 완전 충족이 각각 4문항 늘었고, 최종 단계에서는 3문항 개선·1문항 하락이었다. 최종 필수 사실 합은 4 늘었다. requests20의 후보 단계 회귀 AS-SEM-09는 requests40에서 회복됐다. requests40의 8개 baseline 회귀는 모두 후보·확장 단계가 아니라 최종 선택 단계에서 처음 빠졌다.

요청별 교대 배치는 같은 깊이의 비-pack arm과 후보·확장 단계 완전도가 같았다. 최종 단계에서는 두 pack arm 모두 1개를 새로 완전 충족했지만 4개를 잃었다. 두 깊이에서 공통으로 완전 충족을 잃은 ID는 FPA-12, SY3-adv-006, SY3-adv-033, SY3-adv-037이고, 얻은 ID는 SYF4-adv-028이었다. pack 간 필수 사실 합은 requests20 대비 16, requests40 대비 15 감소했다. FPA-12는 후보 단계에서 baseline 대비 한 사실이 빠져 있었으나 비-pack 확장·최종 단계에서 복구된 사례다. pack에서의 추가 손실은 그 복구 사실을 최종 문맥에서 다시 잃은 것이다.

pack 영향은 분할에 따라 달랐다. peer_two_doc에서는 page Recall@8이 requests20의 0.6579에서 requests20_pack 0.6667로, requests40의 0.6623에서 requests40_pack 0.6711로 올랐지만 완전 충족 수는 59로 같았다. peer_multi_doc에서는 requests20 0.5833 → pack 0.5544, requests40 0.5868 → pack 0.5598로 내려갔고 완전 충족도 각각 67→65, 68→66으로 감소했다. 이 broad 표본에서는 요청별 교대 배치가 안정적인 개선으로 이어지지 않았다.

## 요청 그룹과 119 fallback 분모

`workload.request_groups`의 비어 있지 않은 그룹 수는 0개 119문항, 1개 71문항, 2개 90문항, 3개 27문항, 4개 37문항이었다. 계획 상태는 지원 가능 225, 조건부·종속 유형이라 미지원 100, anchor 검증 실패 19였다. 따라서 **119/344는 요청 그룹이 없는 fallback 분모**이고, 지원 가능 요청 그룹은 225/344다. 네 요청별 arm 모두 119문항에서 후보·확장·최종 필수 사실 수와 완전 충족이 baseline과 같았다. pack 경로의 교대 배치는 2개 이상 그룹이 유효한 154문항에서만 실행됐고, 한 그룹인 71문항과 그룹 없는 119문항은 기존 선택으로 남았다.

`traces.route`의 `/fallback/` 표시는 119건 요청 그룹 부재 판정과 별도 경로 라벨이며 344문항 모두에 나타난다. 이를 119개 계획 fallback의 분모로 읽으면 안 된다.

## 논리 검색·KURE 쌍 수

각 arm에 대해 문항별 workload의 `logical_searches`와 `logical_kure_pairs`를 집계했다. pack은 같은 깊이의 후보 arm과 동일한 검색·리랭킹 workload다.

| arm | logical_searches 합계; 문항 수(1/2/3/4/5회) | KURE pairs 합계 | 문항별 mean / p50 / p90 / max |
| --- | --- | ---: | --- |
| baseline | 344; 344/0/0/0/0 | 6,880 | 20.00 / 20 / 20 / 20 |
| requests20, requests20_pack | 650; 174/80/61/12/17 | 14,980 | 43.55 / 28 / 78 / 116 |
| requests40, requests40_pack | 650; 174/80/61/12/17 | 20,087 | 58.39 / 29 / 114 / 193 |

requests40은 requests20보다 논리 KURE 쌍이 5,107개, 34.1% 많았다. KURE 쌍 p90은 78에서 114로, 최대는 116에서 193으로 증가했다. `config.json`의 실제 공유 집계는 검색 650회, KURE pairs 20,216, 외부 API 0회다. rerank 호출 1,233은 config 필드가 아니라 `workload.jsonl`의 `actual_rerank_calls_shared` 합계다. 같은 query-page KURE 점수를 arm 간 공유했다. config의 296초는 공유 계산을 사용하는 로컬 증분 실행 시간이며, 독립 arm 또는 종단간 사용자 지연으로 주장하지 않는다.

## fresh 검증 후보와 위험

fresh 검증 후보는 **requests40, pack 없이**로 둔다. 전체 완전 충족 164/344로 비교 arm 중 가장 높고, 동료 145/304 및 일상 19/40에서도 최고였다. requests20보다 순증 완전 충족 2문항, 필수 사실 합 4개가 많았고 후보·확장 단계의 완전 충족도 각각 4문항 더 컸다. 요청별 pack은 같은 후보 깊이에서 최종 회귀가 순증 이득을 상쇄했으므로 후보에서 제외한다.

이 선택은 채택이 아니라 fresh 검증 대상 고정이다. requests40은 requests20보다 KURE 쌍을 34.1% 더 쓰고 최대 문항 부담도 193쌍이다. requests20과 비교해 3문항을 얻고 1문항을 잃었으며, baseline 대비 최종 문맥 회귀 8건도 남아 있다. broad-01은 같은 문항과 보관 계획을 쓴 평가이고 KURE 점수도 비교군 사이에 공유됐다. 119개 미지원·anchor 실패 fallback은 새 요청 처리를 검증하지 않으며, 이 점수만으로 답변 정확도나 새 데이터 일반화를 주장할 수 없다. baseline은 유지한다.

## 근거 파일

- [실행 설정](../../../../artifacts/u_10o0o/advanced-evidence-preservation-20261110/broad-01/config.json), [집계](../../../../artifacts/u_10o0o/advanced-evidence-preservation-20261110/broad-01/summary.json)
- [문항별 단계 점수](../../../../artifacts/u_10o0o/advanced-evidence-preservation-20261110/broad-01/scores.jsonl), [paired 변화](../../../../artifacts/u_10o0o/advanced-evidence-preservation-20261110/broad-01/changes.jsonl)
- [검색·문맥 trace](../../../../artifacts/u_10o0o/advanced-evidence-preservation-20261110/broad-01/traces.jsonl), [논리 workload](../../../../artifacts/u_10o0o/advanced-evidence-preservation-20261110/broad-01/workload.jsonl)


## requests40의 8개 최종 회귀: 인용 순위와 단계

저장된 broad trace와 허용된 기존 참조의 인용을 대조했다. 인용은 정규화된 복원 전체 페이지에서 일치하는지 확인했고, 원문 질문이나 인용문은 기록하지 않았다. 표의 `base pool→expanded→final`과 `requests40 pool→expanded→final`은 해당 필수 사실의 인용 일치 페이지 순위다. `—`는 최종 문맥에 그 페이지가 없다는 뜻이다. request group 위치는 그 저장 그룹 안의 1기준 순위이며, `role`은 해당 요청의 검증 HG 역할 페이지 목록에도 포함됐음을 뜻한다.

| ID / 누락 사실 그룹 | baseline pool→expanded→final | requests40 pool→expanded→final | 저장 request group 위치 |
| --- | --- | --- | --- |
| FPA-15 / g2 | 6→7→7 | 14→14→— | 1:29, 일반 KURE |
| SY3-adv-029 / g3 | 3→4→4 | 14→14→— | 3:4(role), 4:7 |
| SY4-adv-003 / g1 | 4→6→6 | 9→9→— | 1:3(role) |
| SY4-adv-028 / g1 | 4→7→7 | 12→12→— | 4:3(role) |
| SYF-adv-047 / g1 | 4→3→3 | 13→14→— | 1:12, 일반 KURE |
| SYF-adv-053 / g1 | 3→5→5 | 12→12→— | 2:8, 일반 KURE |
| SYF-adv-055 / g1 | 7→8→8 | 9→9→— | 1:6, 일반 KURE |
| SYF4-adv-028 / g4 | 4→6→6 | 7→9→— | 3:2(role) |

여덟 인용 일치 페이지 모두 baseline의 RRF 후보 pool에 이미 있었고 baseline 최종 8쪽에도 들어갔다. requests40의 합성 pool에서는 순위가 7–14위로 밀렸으며, 원질문 KURE 확장 후에는 9–14위였다. 따라서 손실은 후보 부재나 요청 그룹 개수 미보존이 아니라 requests-first 그룹 합성으로 생긴 순위 이동 뒤 최종 8쪽 절단에서 발생했다. 표에 표시한 네 `role` 출처는 검증 요청 역할 목록에도 남아 있었지만, 다른 요청 그룹 후보와 함께 합성된 pool에서 최종 순위가 8위 밖이었다. 나머지 네 건은 일반 KURE 요청 그룹 경로였다.

누락 사실마다 복원 전체 페이지에서 인용이 검증된 참조 페이지는 하나였고, 그 페이지가 최종 문맥에는 없었다. FPA-15의 해당 참조 그룹에는 인용 후보가 여럿 기록되어 있지만 전체 페이지 인용 일치 검사는 하나만 통과했다. 따라서 최종 문맥에 다른 페이지가 들어간 것을 주석된 동등 대체근거 선택으로 볼 증거는 없다. 여덟 trace의 route는 `unsupported_constraint` 또는 `unverified_hg`의 fallback이며 `explicit_hg`가 아니다. 기존 정확 HG 매칭의 5쪽 강제 앞삽입은 이 8건의 원인이 아니다.
