# HG+IS 혼합 질문의 IS 규정 근거 추적

2026-11-10 · 기존 H-C07–H-C12 보관 실행의 원인 분석 준비 · 신규 검색/API 호출 없음

## 목적과 입력 분리

이 문서는 기존 24문항 holdout에서 HG+IS 혼합 질문 6개만 추려 질문 의미, 기존 Luna 계획, IS 원문 근거, 후보부터 최종 문맥까지의 누락 지점을 연결한다. 검색 결과를 새로 만들거나 기준선 성능을 확정한 보고서가 아니다. 원문 질문은 records에 복사하지 않았다.

- 실행 입력은 [`mixed-inputs.json`](../../../../artifacts/u_10o0o/advanced-identifier-diagnostic-20261110/mixed-inputs.json)이며 각 행에 원 질문, 원문에서 그대로 분리한 IS 절, 저장된 이전 계획의 IS 쿼리와 출처를 둔다.
- 정답 근거는 별도 [`mixed-references.json`](../../../../artifacts/u_10o0o/advanced-identifier-diagnostic-20261110/mixed-references.json)에만 둔다. 원본 `evidence_groups`를 보존하고 평탄화된 필수 근거와 `is_evidence`·`hg_evidence` 목록을 추가했다. 검색 입력 파일에는 gold 출처 ID·페이지·인용문이 없다.
- IS 전용 마스크는 `sources.jsonl`의 페이지별 heading에 `(^|[> ])IS\d+\s*\|`가 맞는 **3,750쪽·50개 문서**만 대상으로 한다. 책 전체를 통째로 포함하는 조건이 아니며 질문별 gold 쪽 목록도 입력에 제공하지 않는다. [분류 원본](../../../../artifacts/u_10o0o/pattern-curation/advanced/sources.jsonl)
- 입력 쿼리는 모두 비오라클이다. full mixed는 고정된 원 질문이고 `literal_is_query`는 독립성 연결구 뒤의 정확한 연속 부분 문자열이며, `planned_is_query`는 기존 Luna 계획 파일에서 복사한 검색 쿼리다. 사람이 만든 정답 주제어·원문 제목·gold quote를 덧붙이지 않았다.

## 저장 기준선의 경로 구분

기존 holdout의 `baseline` arm을 채택된 개인 기본 실행과 같은 경로로 부르면 안 된다. 개인 기본 실행 [`advanced_search.py`](../../advanced_search.py#L367)는 Bigram-ID BM25와 Snowflake-ko 5:5 RRF 후보, KURE nano 순위와 결정론적 `complete_sources()` 조회를 쓴다. 이 경로는 Luna HG 라우터나 `HGRelations` SQLite 도구를 직접 호출하지 않는다고 선행 감사에 기록돼 있다. [실행 기준과 경로 구분](../../research/advanced-retrieval-foundations-20261110/prior-evidence.md)

반면 holdout의 `baseline`은 [`advanced_plan_holdout.py`](../../advanced_plan_holdout.py#L42)에서 질문마다 Luna HG 라우터와 관계 기반 결정을 거친 뒤 계획 검색만 생략한 arm이다. `parallel`은 같은 경로에 저장 계획과 계획별 검색을 더한다. 여섯 문항 모두 두 arm의 라우트 결정은 일치했다. 따라서 아래 표는 기존 Luna/SQLite 비교의 후보·문맥 추적이며, 부모 실행에서 따로 돌릴 개인 기본 실행과 직접 같은 arm 간 비교로 취급하지 않는다. [기존 실행 설정](../../../../artifacts/u_10o0o/advanced-plan-holdout-20261110/live/config.json) · [문항별 실행 추적](../../../../artifacts/u_10o0o/advanced-plan-holdout-20261110/live/live_calls.jsonl)

## IS 요청 의미와 gold 출처 검토

여섯 요청은 모두 HG 사건 질문과 분리된 절차·규정 확인이다. 저장된 Luna 계획은 여섯 건 모두 별도 `kind=rule` 작업을 만들고 `roles=[]`, 독립 실행(`after=0`)으로 뒀다. 기존 검수에서 대상·요청·범위를 모두 보존했고 지적 사항도 없었다. 각 IS source page의 원문 heading이 IS 계열임을 확인했으며, 여섯 gold quote는 모두 지정 복원 페이지 본문 안의 정확한 부분 문자열이다.

| 문항 | 요청 의미의 요약 | 필수 IS 출처 | 의미·인용 적합성 |
| --- | --- | --- | --- |
| H-C07 | 검토 의견만으로 무료기간 변경 승인 효력이 생기는지 확인 | AS041 p.1 | 별도 승인 규정 질문으로 적절. 인용문은 검토 상태만으로 승인이 되지 않는다는 판정 기준을 직접 뒷받침한다. |
| H-C08 | 새 청구 초안이 기존 발행 문서를 자동 대체하는지 확인 | AS047 p.1 | 문서 효력 구분 질문으로 적절. 인용문이 자동 대체가 아님을 명시한다. |
| H-C09 | 금액 기재와 미발행 상태를 발행으로 볼 수 있는지 구분 | AS051 p.2 | 정산 상태 질문으로 적절. gold 검수도 승인했다. 다만 기존 `references.json`의 anchor는 전체 요청보다 짧은 상황 표지이며, 검수자가 전체 요청문을 anchor에 포함하면 판정 의도가 더 분명하다고 기록했다. 새 입력은 질문의 전체 IS 절과 저장 계획 쿼리를 사용하므로 anchor 축약을 요청 축약으로 오인하지 않는다. |
| H-C10 | 원문 확보·참조 식별과 입금 배분 완료를 구분 | AS053 p.1 | 업무 상태 정의 질문으로 적절. 인용문은 해당 필드가 자료 확보·식별을 나타내며 배분 승인·완료 기록은 아님을 뒷받침한다. |
| H-C11 | 재송부 요청·참조 연결 수정과 업무 승인·대체를 구분 | AS056 p.2 | 문서 관리 상태 질문으로 적절. 인용문이 재송부 미수신 상태와 참조 관계 정정의 효력 범위를 각각 다룬다. |
| H-C12 | 확정된 예정 단계와 실제 반출 확인 여부를 따로 인계하는지 확인 | AS055 p.2 | 단계·상태 인계 질문으로 적절. 인용문은 반출 미확인을 앞선 확정 일정과 구분해 전달하도록 뒷받침한다. |

원본 gold에는 각 요청마다 primary 인용이 하나씩 있고 alternative bundle은 여섯 문항 전체에서 **0개**다. 이는 검토된 대체 근거가 등록되지 않았다는 뜻일 뿐, 코퍼스 안에 같은 요청을 유효하게 뒷받침하는 다른 페이지가 없음을 확인한 것은 아니다. 표의 단계별 근거 지표는 지정된 페이지와 인용문 기준의 엄격한 일치 결과이므로, 동등한 다른 출처나 다른 유효 인용이 있다면 실제 답변 가능성을 낮게 셀 수 있다. 여기서 검증한 범위는 등록된 primary 인용이 지정 원문 페이지에 정확히 있고 요청 의미에 맞는다는 점까지다. HG 출처는 각 사건 요청에 따로 연결했다. 구체적인 출처·인용은 실행 입력과 분리된 [`mixed-references.json`](../../../../artifacts/u_10o0o/advanced-identifier-diagnostic-20261110/mixed-references.json)에서 확인한다.

## 기존 후보·확장·최종 문맥에서의 IS 근거

`candidate`는 최초 20쪽 후보 풀, `expanded`는 소스 보강 후 패킹 전 페이지, `final`은 최대 8쪽 문맥이다. 단계 정의는 [기존 후속 검증 기록](../advanced-plan-followup-20261110/stage-2.md)과 같다. 아래 수치는 저장된 `rankings.jsonl` 및 `request-breakdown.json`에서 IS 요청 두 번째 근거만 따로 추적했다.

| 문항 | IS 근거 쪽 | 기존 holdout baseline candidate → expanded → final | parallel candidate → expanded → final | 누락 위치 |
| --- | --- | ---: | ---: | --- |
| H-C07 | AS041 p.1 | 0 → 0 → 0 | 0 → 0 → 0 | 최초 후보 누락 |
| H-C08 | AS047 p.1 | 1 → 1 → 1 | 1 → 1 → 1 | IS 근거 손실 없음 |
| H-C09 | AS051 p.2 | 0 → 0 → 0 | 0 → 0 → 0 | 최초 후보 누락 |
| H-C10 | AS053 p.1 | 1 → 1 → 0 | 1 → 1 → 0 | 최종 패킹에서 누락 |
| H-C11 | AS056 p.2 | 0 → 0 → 0 | 0 → 0 → 0 | 최초 후보 누락 |
| H-C12 | AS055 p.2 | 0 → 0 → 0 | 0 → 0 → 0 | 최초 후보 누락 |

두 기존 arm 모두 IS 규정 근거는 후보에서 **2/6**, 보강 후 **2/6**, 최종 **1/6**이다. 요청 계획만으로 규정 쪽을 추가 회수하지 못한 네 건은 후보 검색 단계에서 이미 빠졌다. H-C10은 AS053 p.1이 후보와 확장 단계에 있었지만 최종 8쪽에 포함되지 않았다. 최종 8쪽에는 그 문서의 p.2–4가 들어가 p.1 규정 근거가 밀려난 상태다. H-C08의 IS 쪽은 기준선에서도 끝까지 있었으므로 parallel의 전체 문항 충족 개선은 IS 규정 회수에서 온 것이 아니다.

기존 전체 문항 점수에서는 이 구분이 중요하다. H-C08은 baseline의 최초 후보에서 HG 계약 쪽이 빠져 전체 후보 완전성이 0이었고, parallel 계획은 해당 HG 근거를 후보 단계에 추가해 1로 올렸다. 그러나 baseline도 확장 단계에서 전체 완전성 1이 됐으며, 최종 완전성은 두 arm 모두 1이었다. 따라서 H-C08의 계획 효과는 후보 완전성 개선이고 확장·최종 문항 충족 개선은 아니다. H-C10은 두 arm 모두 IS 쪽이 확장까지 있었지만 HG/IS 전체 근거는 최종에서 불완전했다. 따라서 원인 분류는 IS 요청마다 따로 봐야 한다. [기존 문항별 점수](../../../../artifacts/u_10o0o/advanced-plan-holdout-20261110/live/scores.jsonl) · [요청별 근거](../../../../artifacts/u_10o0o/advanced-plan-holdout-20261110/live/request-breakdown.json) · [후보·확장·패킹 쪽](../../../../artifacts/u_10o0o/advanced-plan-holdout-20261110/live/rankings.jsonl)

## 현재까지의 원인 판단과 남은 구분

기존 계획 검수에는 IS 요청 누락이나 범위 축소 증거가 없다. 네 사례는 규정 출처가 최초 후보에 올라오지 않았고, 한 사례는 후보 회수 후 최종 선택에서 빠졌다. 현재 증거는 실패 위치를 각각 **검색 후보 생성**과 **최종 문맥 선택**으로 좁힌다. 왜 후보 검색이 네 규정 쪽을 놓쳤는지(원문 표현과 질의 토큰의 불일치, IS/HG 혼재 코퍼스의 후보 경쟁 등)는 부모 실행의 full mixed, literal 절, 저장 계획 쿼리, IS heading 3,750쪽 필터를 비교해야 판별할 수 있다. 여기서는 임의의 정답 주제어를 추가하지 않았다.

문항·출처 표본의 독립성에도 한계가 있다. 기존 holdout manifest상 질문은 앞선 표본과 중복되지 않지만, holdout HG 사건 24/24와 필수 출처 36쪽 중 32쪽이 역사적 전 페이지 패턴 세트와 겹친다. 이번 6개도 새 문서 사건 표본이라고 볼 수 없다. [고정 입력 manifest](../../../../artifacts/u_10o0o/advanced-plan-holdout-20261110/manifest.json)

## 추적 파일

- 입력: [`mixed-inputs.json`](../../../../artifacts/u_10o0o/advanced-identifier-diagnostic-20261110/mixed-inputs.json) — 실행기가 바로 읽는 6행 list. `question`, `literal_is_query`, `planned_is_query`를 보존한다.
- 참조: [`mixed-references.json`](../../../../artifacts/u_10o0o/advanced-identifier-diagnostic-20261110/mixed-references.json) — ID별 dict. `evidence_groups` 원본과 `evidence_requirements`, `is_evidence`, `hg_evidence`, 원문 부분문자열 확인 결과를 제공한다.
- 기존 검증 맥락: [단계별 점수 기록](../advanced-plan-followup-20261110/stage-3.md) · [검수 프로토콜](../advanced-plan-followup-20261110/protocol.md)

검증은 로컬 JSON 구조와 source quote 위치 확인으로 한정했다. 전체 테스트나 새 검색/API 호출은 실행하지 않았다.
