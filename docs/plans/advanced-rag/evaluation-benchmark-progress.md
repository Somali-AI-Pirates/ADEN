# Advanced 최종 평가 진행 기록

기준일: 2026-11-13. 이 문서는 `data/advanced/evaluation/`의 확정 데이터 평가 준비와 실행 현황을 기록한다. **2026-11-13 13:27 KST에 고정 280문항 답변 생성 280/280 완료를 확인했다. 저장 답변만 쓰는 advanced 채점은 13:33 KST에 시작했으며, 2026-11-13 13:38:43 KST 확인 시 14/280 문항이 완료되고 32개 작업이 실행 중이다. 최종 평가 점수나 결론은 아직 없다.**

## 평가 범위와 고정 경계

- 평가 모델은 GPT-6 Luna의 `max` effort를 우선 요청한다. 실제 요청에서 계정·API가 지원하지 않는다는 명시적 거절을 받은 경우에만 `xhigh`로 전환한다. 일반 오류나 타임아웃을 근거로 effort를 낮추지 않는다.
- 현재 Advanced 실행 설정은 [`configs/advanced.yaml`](../../../configs/advanced.yaml)의 `evaluation` 항목을 따른다: 출력 상한 128,000 토큰, 요청 제한 시간 600초, SDK 재시도 최대 2회다. Responses API에서 `max` 호출이 성공했으며, 이번 교정 실행 manifest에서도 유효 effort가 `max`로 기록됐다.
- `default`와 `advanced` Faithfulness 비교는 20개 교정 문항에서만 수행한다. 전체 280문항 본평가에서는 **advanced 프로필만** 실행한다. 전체 280에 대한 `default` 프로필 비교는 계획하거나 실행하지 않는다.
- 제품 답변 생성은 기존 GPT-6 Luna `medium` 설정으로 유지한다. 생성 effort와 평가 effort는 별도 설정·기록으로 다룬다.
- 정답·필수 사실·보안 라벨은 검색과 답변 생성 입력에 넣지 않는다. 채점 단계에서만 읽는다.
- 원본 문항·참고답·모델 입력과 출력은 로컬 `artifacts/`에 둔다. Git 문서·보고서에는 원문, 인용문, 비밀값을 복사하지 않는다.
- 재개는 실행 지문이 같은 완료 결과만 재사용한다. 완료 행이 있는 generation은 재사용한다. 요청을 보낸 뒤 저장 전에 끊긴 `generation-inflight.json`이 있으면 자동 재호출하지 않고 기존 저장분을 먼저 확인한다. 채점은 문항별 완료 결과를 재사용하지만, 한 문항의 중간 API 호출이 끝난 뒤 체크포인트 전에 끊기면 해당 호출이 다시 나갈 수 있다. 따라서 정확히 한 번의 과금은 보장하지 않는다.

## 데이터 분모와 집계 기준

합본 원본 질문·참고답을 단일 기준으로 사용한다. 이번 고정 inventory는 **280개 고유 문항**이며, 그중 **258개는 페이지 정답을 가진 문항**, **22개는 페이지 정답이 없는 문항**이다. 페이지 정답이 없는 문항은 DEV 1개, ADD 1개, scenario 8 대조군 20개로 구분한다.

| 묶음 | 문항 수 | 집계 기준 |
| --- | ---: | --- |
| Development | 30 | 합본에 한 번 포함 |
| Additional | 30 | 합본에 한 번 포함 |
| Scenario 1–7 | 140 | 유형별 20문항 |
| Scenario 8 대조군 | 20 | 페이지 정답 없음 |
| Work rules | 20 | 두 하위 유형 각 10문항을 합친 묶음 |
| Custom S / Custom M | 40 | 각 20문항, 합본에 한 번 포함 |
| **전체** | **280** | 고유 ID 기준, 구성 파일을 다시 더하지 않음 |

`category_180`은 Scenario 1–8과 Work rules만 포함하는 별도 집계다. 전체 280에서 Development·Additional·Custom을 제외한 180문항이며, Scenario 1–8의 8개 그룹과 Work rules 1개 그룹, 총 9개 그룹을 같은 가중치로 macro 집계한다. 전체 14개 primary group의 동일 가중치 평균은 `all_group_macro`로 따로 표기한다. 두 macro는 섞지 않는다.

최종 평가에는 페이지 기반 지표와 의미상 필수 사실 평가를 별도로 둔다. 페이지 진단은 `book_id + page` 정답의 후보·최종 문맥 포함만 본다. semantic 표는 검수된 필수 사실마다 최종 문맥의 인용 지지, 답변의 정확성, 주 귀속, 최초 손실 단계, 다중 failure tags를 집계한다. 같은 페이지가 있다는 이유만으로 의미상 사실을 충족했다고 판정하지 않는다. 사실 목록이 비어 있는 항목과 평가 불가·누락도 점수 분모와 분리한다. Ragas Faithfulness는 Judge의 사실 판정과 독립적으로 평가하되, 전체 본평가에서는 `advanced` 프로필만 실행한다. 프로필 간 비교는 20개 교정 문항에만 한정한다.

보안 평가는 `security_tasks.json`에 지정된 DEV14·ADD13의 기존 질문을 그대로 사용해 별도 결과로 남긴다. 보안 질문을 새로 생성하거나 본 평가의 문항별 점수에 합치지 않는다.

## 구현·오프라인 검증

2026-11-13 확인된 평가 구현과 준비 검사는 다음과 같다. 이 검사는 실제 280문항 답변·채점 성능을 뜻하지 않는다.

| 검사 | 결과 | 증거 |
| --- | --- | --- |
| 저장소 전체 Ruff | 통과 | `/tmp/aden-evaluation-implementation-20261113/ruff-v3.json` |
| 전체 pytest | 796 passed, 11 subtests passed, 경고 1건 | `/tmp/aden-evaluation-implementation-20261113/pytest-v3.json` |
| Faithfulness metadata 집중 검사 | 16 passed; 전체 796 테스트도 통과 | `/tmp/aden-evaluation-implementation-20261113/faithfulness-metadata-validation.json`, `/tmp/aden-evaluation-implementation-20261113/pytest-v3.json` |
| CLI 도움말 | 12개 진입점 통과 | `/tmp/aden-evaluation-implementation-20261113/cli-v3.json` |
| 일반 평가·보안 평가 샘플 | 두 모의 실행 통과; 실제 모델 호출 아님 | `/tmp/aden-evaluation-implementation-20261113/sample-v3.json` |
| uv lock·sdist·wheel | 통과 | `/tmp/aden-evaluation-implementation-20261113/build-v3.json` |
| 프롬프트 리소스 | 9개 소스 바이트와 wheel 포함 항목 일치, 누락·금지 리소스 0개 | `/tmp/aden-evaluation-implementation-20261113/resources-v3.json` |
| 문서 링크 | 이전 검사에서 23개, 끊어진 링크 0개; 이번 문서 변경의 링크 검사 별도 수행 | `/tmp/aden-evaluation-implementation-20261113/resource-doc-check.json` |

평가 연결은 OpenAI Responses API를 사용한다. 이전 Chat Completions 경로의 `max` 거절 기록은 현행 실행 설정을 나타내지 않는다. 본평가가 끝난 뒤 실제 요청 manifest와 저장된 사용량을 다시 확인하고, 모의 실행·metadata·리소스 검증을 포함한 최종 전체 검사를 진행한다. 지금 단계에서는 280문항 채점이 완료됐다고 표시하지 않는다.

## Faithfulness 교정 실행 이력과 한계

교정 실행은 설정·프롬프트 revision이 다른 별도 run이다. 아래 결과를 서로 합산하거나 본평가 결과로 인용하지 않는다. 비교 가능한 프로필 쌍은 같은 20문항 교정 세트 안에서만 다룬다.

| 실행 | 출력 상한 | 프로필·완료량 | 용도와 구분 |
| --- | ---: | --- | --- |
| `advanced-final-20261113` | 32,768 | default 20/20, advanced 20/20 | 최초 프로필 비교. 1건은 advanced에서 평가 불가로 남음. |
| `advanced-final-20261113-v2` | 32,768 | advanced 20/20 | 보강 프롬프트 v2 확인. default 대조군을 실행하지 않은 별도 비교다. |
| `advanced-final-128k-20261113` | 128,000 | default 20/20, advanced 20/20 | 새 YAML 출력 상한에서 프로필 비교. advanced 1건은 평가 불가로 남음. |
| `explicit-interval-128k-20261113` | 128,000 | advanced 3/3 | 날짜 구간 판정에 대한 별도 표적 확인; 20문항 교정 통계와 분리한다. |

이 표본에는 채점 기준의 한계가 있다. `SYN-FTH-05`의 gold label은 기대 판정이 한 가지로 고정되지 않는 모호성이 확인됐다. `SYN-FTH-10`은 v2에서 산술 근거가 지원한다고 잘못 긍정 판정한 확정 오탐 사례다. 원문·인용은 공개 문서에 옮기지 않는다. 두 사례는 gold와 판정 경계를 확인하는 제한 사항으로 기록하며, 이를 근거로 프롬프트의 일반 성능을 과장하지 않는다. unlabeled historical 8건은 설명적 참고만 가능하고 정확도·정밀도·재현율 분모에 넣지 않는다.

이 표본은 평가 프롬프트 교정 자료이며 최종 280문항 평가 결과와 합산하지 않는다. 실행 manifest·상세 결과는 로컬 `artifacts/experiments/faithfulness-calibration/`에 보관한다.

## 확정 프롬프트와 280문항 실행

사용자가 완료한 최종 답변·계획 프롬프트를 `/tmp/aden-evaluation-implementation-20261113/prompt-freeze-latest.json`에 고정했다. SHA 두 종류는 기록 방식이 다르므로 함께 적는다.

| 파일 | 원본 파일 바이트 SHA-256 | 런타임 로드 문자열 SHA-256 |
| --- | --- | --- |
| `app/rag/prompts/answer.txt` | `b9d9ce53bbe6ce67ca773d061c6f480cc0997b4a0d7bd671909e853becd34c97` | `3b0c949c0f3fe699ef6f01415e332188def07f1bc53c72562e1de18acaa4be43` |
| `app/rag/prompts/plan.txt` | `12247a325615c313f923582a01f8fd578db75aaa816252abb250dae6f2d6d592` | `3b2f80ea400476bc48f7722232c63e24765800b5ca324e748decababae06b409` |

원본 파일 SHA에는 마지막 줄바꿈이 포함된다. 런타임 SHA는 모델에 넣는 문자열에 대한 값이며, 두 값의 차이는 끝 공백·줄바꿈 제거 때문이다. 새 실행 manifest와 첫 모델 입력의 시스템 문자열 지문은 런타임 SHA와 일치함을 확인했다. 이 둘을 서로 다른 프롬프트 버전으로 해석하지 않는다.

- 초기 실행 ID `advanced-final-280-20261113`은 고정 입력 검사에서 답변 프롬프트 변경을 감지해 **0/280 응답 저장 시 중단**했다. 이 실행은 재개·채점하지 않는다.
- 현재 고정 실행 ID는 `advanced-final-280-20261113-frozen`이다. 2026-11-13 13:27 KST에 manifest의 `run_status=complete`, `run_count=280`과 `runs.jsonl`의 280개 고유 행을 대조했다. 상태별 저장 행은 answered 213, abstained 47, needs_clarification 20이다. 이는 생성 상태의 집계이며 정확도·검색 성능 결과가 아니다.
- 저장 답변 채점 `luna-max-128k-advanced`는 2026-11-13 13:33 KST 시작했다. 13:38:43 KST 확인 시 280문항 중 14개 결과만 저장됐으며 32개 작업이 실행 중이었다. 이 부분 집계는 최종 결과로 인용하지 않는다.
- 고정 실행 manifest는 당시 기준 커밋 `1c99006`과 dirty worktree, 설정·모듈·데이터 fingerprint를 기록한다. 출력·원문 및 상세 모델 입력은 로컬 `artifacts/experiments/advanced-final-280-20261113-frozen/`에만 둔다.
- 답변 생성과 입력 지문 검증은 완료됐다. 저장 답변 기반 채점이 진행 중이며, 완료 수·호출 수·토큰·지연은 저장된 grading view와 manifest를 대조해 최종 집계한다.

## 완료와 미완료 구분

| 단계 | 상태 | 다음 확인 |
| --- | --- | --- |
| Canonical 280/258 inventory, subgroup·macro 분모 및 보고서 집계 구현 | 완료 | 확정 inventory preflight 유지 |
| Ruff·pytest·CLI·샘플·lock·build·문서 링크 검사 | 완료 | 이후 코드·설정 변경이 있으면 영향 범위 재검사 |
| `max` 모델 API 적합성 확인 | 완료(연결 방식 기준) | Responses API의 parse/create에서 max 호출 성공, 교정 실행도 effective effort=max 기록. 본평가의 실제 채점·사용량은 아직 대기 |
| Faithfulness 보정 표본 | 완료, 한계 별도 기록 | 32K 최초 비교, 32K v2 advanced-only, 128K 비교와 128K 표적 probe를 서로 다른 실행으로 보존 |
| 확정 프롬프트의 280문항 답변 생성 | 완료: 280/280 | 2026-11-13 13:27 KST 상태 확인; 고정 입력·실행 지문과 행 수 재확인 |
| 280문항 채점·semantic/page 단계별 실패 집계 | 진행 중: 14/280 (2026-11-13 13:38:43 KST) | 결과가 280개 모두 모이고 exact-ID 재검증을 마친 뒤 집계 |
| 별도 보안 질문 실행·채점 | 완료, 결과 검토 대기 | DEV14·ADD13 동일 문맥 재생·방어 생성·Judge 결과를 본평가와 분리해 기록 |
| 최종 보고서·결론 | 대기 | advanced Faithfulness만 본평가에 사용; profile 비교는 calibration만. 결과는 채점·metadata 검증 뒤 실제 저장값으로 작성 |

## 재개 방법

1. `/tmp/aden-evaluation-implementation-20261113/prompt-freeze-latest.json`의 원본 SHA와 현행 파일을 확인한다.
2. 실행은 `advanced-final-280-20261113-frozen`의 고정 입력 manifest와 같은 설정·지문으로만 이어간다. 입력이나 런타임 prompt SHA가 달라졌다면 기존 run을 덮어쓰지 말고 중단 사유를 별도 기록한다.
3. `runs.jsonl`과 `generation-inflight.json`을 먼저 확인한다. 완료 결과는 재사용하고, inflight는 호출 결과가 저장됐는지 확인되기 전 자동 재호출하지 않는다.
4. 모든 답변 저장 후 동일한 질문 ID 정렬·reference ID 매칭을 검증하고 `--resume --diagnose-failures --final-benchmark-report`를 사용해 채점한다. Custom S/M에는 독립 검수 완료된 `--derived-facts`만 연결한다.
5. 보안 비교는 DEV14·ADD13의 동일한 질문 텍스트를 사용하고 일반 평가 결과와 별도 보고한다.
6. 생성은 완료됐다. `default`는 20문항 교정 비교에만 남기고, 본평가 채점에서는 advanced만 실행한다. 전체 채점과 최종 metadata/리소스 검증이 끝나기 전에 점수 표나 채택 결론을 채우지 않는다.

실행 코드와 고정 데이터 위치: [TableRAG 평가 CLI](../../../app/evaluation/scripts/run_table_rag.py), [모델 프롬프트 지문](../../../app/rag/model.py), [Advanced 평가 데이터](../../../data/advanced/evaluation/). 보고서 틀은 [Advanced 최종 벤치마크 보고서](../../../reports/advanced-final-benchmark-2026-11-13/report.md)에 둔다.
