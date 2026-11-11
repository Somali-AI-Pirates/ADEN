# 원본에서 새로 만드는 Advanced A/B/C

- A: 원문 페이지 하이브리드.
- B: 기록/표 행과 설명 단위 하이브리드.
- C: B에 출처가 확인된 명시 코드 관계로 최대 8개 새 후보 추가.

기존 개인 기준선이나 운영 API를 가져오지 않는 별도 실험이다. 이전 결과·정제본·질문·색인을 입력으로 사용하지 않는다. 모델 가중치만 고정된 로컬 스냅샷을 쓴다. 자세한 설정·실행 과정·결과는 [실험 기록](../records/advanced-fresh-record-hybrid-20261111/plan.md)을 따른다.

진입점은 아래 모듈이다. 각 명령은 `--help`를 지원한다.

```bash
python -m experimental.u_10o0o.fresh_record_hybrid.extract --output NEW_DIRECTORY
python -m experimental.u_10o0o.fresh_record_hybrid.units --pages PAGES_JSONL --output-dir NEW_DIRECTORY
python -m experimental.u_10o0o.fresh_record_hybrid.retrieval build-vectors --device cuda
python -m experimental.u_10o0o.fresh_record_hybrid.retrieval retrieve --device cuda
python -m experimental.u_10o0o.fresh_record_hybrid.scoring --help
python -m experimental.u_10o0o.fresh_record_hybrid.reporting --help
```

검색기는 질문 ID/문장만 읽는다. 참고 근거는 모든 조건의 검색 결과 저장을 확인한 뒤 별도 채점 단계에서 읽는다. 후보는 최대 20개 단위, 최종은 원문 최대 8쪽/8,000토큰이다. 기록 단위와 원문 페이지의 크기가 다르므로 후보 수가 같다고 비용까지 같다고 해석하지 않는다.

결과는 [전체 보고서](../records/advanced-fresh-record-hybrid-20261111/report.md), [결론](../records/advanced-fresh-record-hybrid-20261111/conclusions.md), [누락 단계](../records/advanced-fresh-record-hybrid-20261111/stage-diagnosis.md), [실행 과정](../records/advanced-fresh-record-hybrid-20261111/process.md)에 있다. 원문과 질문/참고 근거는 로컬 artifacts에, 공유 가능한 설정·지문·수치·문항 ID 변화는 records에 저장했다.

## D 후속 비교

D는 A의 원문 페이지 색인을 그대로 쓰면서 C와 같은 명시 코드 관계를 원문 페이지로 연결한다. 관계의 기록 역할 순서는 유지하며, 원문 페이지 중복을 제거한 뒤 기존 후보에 없는 새 페이지를 최대 8개 보충한다. 기본 실행은 여전히 A/B/C다. D만 실행하려면 새 결과 경로에 저장한다.

```bash
python -m experimental.u_10o0o.fresh_record_hybrid.retrieval retrieve   --conditions D --device cuda --results NEW_DIRECTORY/results.jsonl
```

채점의 `--conditions A B C D`는 네 조건 모두 저장된 비교 파일을 요구한다. 이전 A/B/C와 새 D의 입력·모델 설정이 일치하는지 먼저 확인해야 한다. [D 계획과 결과](../records/advanced-fresh-page-relation-20261111/report.md)에 재현 명령과 문항별 변화가 있다. D에서는 기존 하이브리드 후보를 관계 목록에서 제외하는 C의 병합 정책도 유지한다.

## E: D를 고정한 관계 중복 처리 비교

저장된 D의 초기 검색 순위와 새 관계 페이지 목록을 고정하고, 원문 관계가 확인된 기존 검색 페이지도 관계 순위에 포함한다. 같은 페이지의 RRF 점수는 두 목록에서 합산하고 최종 후보는 중복 없이 20개로 제한한다. D에서 선택하지 않은 새 페이지는 추가하지 않는다. E는 아래 재생 명령으로만 실행하며 일반 retrieve의 기본 ABC 실행은 그대로다.

```bash
python -m experimental.u_10o0o.fresh_record_hybrid.relation_overlap --device cuda --results NEW_E_RUN/results.jsonl
```

D 원문/질문/관계 지문과 리랭커 설정이 같아야 한다. 질문 임베딩·BM25·벡터 검색은 재계산하지 않고 E 후보만 원질문으로 한 번 리랭킹한다. 완료된 D/E 결과를 합친 파일은 `scoring --conditions D E`로 채점한다. 채점에는 각 필수 사실의 적중 여부와 회복·유실된 사실 번호도 남긴다. [E 계획 및 결과](../records/advanced-fresh-relation-overlap-20261111/report.md)에 고정 조건과 재현 방법을 기록한다.

## F: 초기 검색기별 Top10 후보 확보

F는 저장된 E와 같은 BM25/벡터 Top50에서 각 검색기의 Top10 합집합을 먼저 고른다. 20개까지 빈자리를 기존 RRF 순서로 보충하고 선정된20개도 기존 RRF 순서로 나열한다. 그 이후에는 E와 같은 관계 정책과 단일 KURE·최종8쪽/8000토큰을 적용한다.

```bash
python -m experimental.u_10o0o.fresh_record_hybrid.relation_overlap --condition F --device cuda --results NEW_F_RUN/results.jsonl
```

기본 condition은 E로 유지된다. 원천은 기존에 검증된 D 저장 Top50이며 E의 Top50과 동일하다. F의 초기후보 변경에 따라 새 관계 페이지도 달라질 수 있다. Top10 보장은 초기20에만 적용하며, 관계 병합 후 유실 여부를 따로 남긴다. E/F 전체 결과를 저장한 파일은 `scoring --conditions E F`로 채점한다. [F 실험 기록](../records/advanced-fresh-initial-quota-20261111/report.md)을 참고한다.

## E/F의 같은 후보에서 최종 기록 선택

저장된 E/F의 후보20과 KURE 순위·점수를 고정하고, 명시 대상의 요청 기록을 보존하는 선택기를 각각 비교한다. E_coverage/F_coverage는 같은 후보 안에서만 원문 최대8쪽·8,000토큰을 고른다. 지원하는 HG 기록 종류와 대상·역할의 연결이 명확하지 않으면 기존 최종 문맥을 그대로 사용한다.

```bash
python -m experimental.u_10o0o.fresh_record_hybrid.final_coverage --results NEW_COVERAGE_RUN/results.jsonl
python -m experimental.u_10o0o.fresh_record_hybrid.scoring --conditions E E_coverage F F_coverage --help
```

첫 명령은 네 조건의 전체 결과를 저장하며, 저장 완료 후 기존 scoring 명령에 결과·원문·질문·참고근거·출력 경로를 지정한다. 새 검색·임베딩·KURE·LLM 호출은 없고, 해석·선택·원문 복원만 실행한다. 기본 검색이나 E/F 실행은 바꾸지 않는다. [고정 정책](../records/advanced-fresh-final-coverage-20261111/policy.md), [실험 결과](../records/advanced-fresh-final-coverage-20261111/report.md), [재현 명령](../records/advanced-fresh-final-coverage-20261111/reproduce.md)을 참고한다.

## 종료 상태와 기존 평가

이 실험은 여기서 종료한다. 기존 개인 기준선과 운영 API는 변경하지 않았다. [종료 기록](../records/advanced-fresh-record-hybrid-20261111/closeout.md)에 시작 이유, A~F·기록 선택의 변화, 기존 평가에서의 한계와 참고 자료를 모았다.

공식 60문항·카테고리 220문항 재검증은 아래 진입점을 사용한다. 실제 검색 전 질문을 준비·동결하고, 모든 검색 결과 저장 후 별도 score를 실행한다. 필수 로컬 산출물·질문 지문과 전체 명령은 [기존 평가 보고서](../records/advanced-fresh-existing-evaluation-20261111/report.md#재현)에 있다.

```bash
python -m experimental.u_10o0o.fresh_record_hybrid.existing_evaluation prepare --help
python -m experimental.u_10o0o.fresh_record_hybrid.coverage_holdout --help
python -m experimental.u_10o0o.fresh_record_hybrid.existing_evaluation score --help
```

`coverage_holdout`라는 모듈명과 달리 완료된 실행은 `existing_regression`이다. [새 미사용 평가 계획](../records/advanced-fresh-holdout-20261111/plan.md)은 사용자 요청으로 중단했으며, 해당 폴더의 정책 동결 파일만 기존 평가에 재사용했다. 새 미사용 세트 결과가 있는 것으로 해석하지 않는다. [점수 차이·누락 단계 분석](../records/advanced-fresh-existing-evaluation-20261111/generalization-diagnosis.md)을 함께 읽는다.
