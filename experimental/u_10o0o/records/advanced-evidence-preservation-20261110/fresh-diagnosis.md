# Fresh-01 requests40 실패 단계 진단

2026-11-10. 저장된 `fresh-01`의 점수·trace·문맥과 허용된 `fresh-validation` 참조를 대조했다. 원문 질문·인용문은 기록하지 않았다. 24건 모두 후보 단계 완전 충족, 23건 확장 단계, 21건 최종 문맥에서 완전 충족했다. 최종 미충족은 FV-002, FV-003, FV-006이다. 세 건 모두 baseline도 같은 필수 사실 그룹을 놓쳤으므로 requests40의 신규 회귀는 아니다.

| ID | 누락 근거 페이지 / fact | 후보 순위 | expanded 순위 | 최종 문맥 | 최종 토큰 / 한도 | 판정 |
| --- | --- | ---: | ---: | --- | ---: | --- |
| FV-002 | AS017 p1 / 요청2 / 전체 요건4 | 3 | 9 | 8쪽 밖 | 4,070 / 8,000 | 확장 순위 9위가 8쪽 선택 상한에서 제외 |
| FV-003 | AS054 p1 / 요청2 / 전체 요건2 | 20 | 없음 | 없음 | 4,134 / 8,000 | HG 원문 보강 후 후보20쪽 제한에서 탈락 |
| FV-006 | AS037 p1 / 요청2 / 전체 요건2 | 5 | 9 | 8쪽 밖 | 4,339 / 8,000 | 확장 순위 9위가 8쪽 선택 상한에서 제외 |

세 trace 모두 `explicit_hg/fallback/no_verified_pair` route다. explicit-HG 경로는 검증된 다섯 HG 역할 페이지를 앞에 둔 뒤 20쪽으로 제한한다. FV-002와 FV-006 근거는 expanded 9위라 최종 8쪽 상한에서 빠졌다. 최종 문맥은 각 8쪽이지만 4,070·4,339토큰으로 8,000 한도보다 낮아 토큰 제한은 원인이 아니다. FV-003의 AS054 p1은 후보 20위에서 explicit-HG 확장 후 사라져 20쪽 컷 단계에서 탈락했다. 저장 trace는 pre-HG KURE 순위를 담지 않아 이 한 건에서 5쪽 prepend와 KURE 재정렬의 개별 기여는 나눌 수 없다. 이 셋 중 확장 누락은 FV-003뿐이다.

세 누락 fact에는 각각 주석된 대체 bundle이 없었다. 따라서 같은 사실을 대신할 주석 근거가 골라졌다고 볼 근거는 없다. 결과는 저장 산출물 기준 진단이며, 추가 검색·모델·API 호출은 하지 않았다.

근거: [Fresh-01 점수](../../../../artifacts/u_10o0o/advanced-evidence-preservation-20261110/fresh-01/scores.jsonl), [trace](../../../../artifacts/u_10o0o/advanced-evidence-preservation-20261110/fresh-01/traces.jsonl), [최종 문맥](../../../../artifacts/u_10o0o/advanced-evidence-preservation-20261110/fresh-01/contexts.jsonl).
