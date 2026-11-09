# Advanced 상황별 검색 평가 · 개정판

[최초 검색 실행](../../records/advanced-scenarios-benchmark-20261109/report.md)의 160문항과 참고 근거를 보존한다. 7번 20문항을 검색 본 평가에 추가해 1~7번은 140문항, 8번은 범위 밖 대조 20문항이다. 각 상황의 문항 수는 20개다. [평가 범위와 파일 지문](manifest.json)

번호 없이 안내와 규정을 찾는 질문은 [업무 절차·규정 20문항](work_rules/README.md)에서 별도로 평가한다. [일상 표현 검색 40문항](semantic_queries/README.md)은 문서 제목과 코드를 모르는 사용자가 업무 상황을 풀어 말하는 경우를 다룬다. 이 중 20문항은 여러 PDF에서 근거를 찾아야 한다.

| 상황 | 지금 평가하는 검색 범위 |
| --- | --- |
| 1. 직접 조회 | 질문이 요구한 한 PDF의 행이나 필드가 검색되는지 확인한다. |
| 2. 번호 없이 한 사건 식별 | 질문의 단서에 맞는 사건과 필수 기록을 찾는지 확인한다. |
| 3. 한 사건의 여러 기록 연결 | 약정·현장·승인·청구·수납에서 필요한 기록이 함께 들어오는지 확인한다. |
| 4. 서로 다른 사건 비교 | 두 사건의 요청 기록을 각각 찾는지 확인한다. |
| 5. 적용되는 기록·조건 판정 | 실제일·승인·수납처럼 질문에 필요한 원문을 찾는지 확인한다. |
| 6. 대상이 모호해 추가 확인 필요 | 단서에 맞는 두 후보 사건의 반출과 청구 근거를 모두 찾는지 확인한다. |
| 7. 대상은 명확하지만 확정 근거 부족 | 지정 반납 증빙이 없거나 다른 장소에 반납한 기록과 반납지연료가 미확정임을 보여주는 기록을 찾는지 확인한다. |
| 8. 자료 범위 밖 | 정답 근거가 없는 별도 대조군이다. Recall 및 전체 근거 충족률에서 분리한다. |

6번의 되묻기와 7번의 날짜·금액 확정 보류는 LLM 연결 후 답변 단계에서 평가한다. 참고 파일의 `answer_evaluation_stage`와 `expected_action`에 이 구분을 저장했다.

| 범위 | 검색 입력 | 채점 입력 |
| --- | --- | --- |
| 상황별 검색 140문항 | [retrieval_questions.json](retrieval_questions.json) | [retrieval_references.json](retrieval_references.json) |
| 업무 절차·규정 20문항 | [questions.json](work_rules/questions.json) | [references.json](work_rules/references.json) |
| 일상 표현 검색 40문항 | [questions.json](semantic_queries/questions.json) | [references.json](semantic_queries/references.json) |
| 범위 밖 대조 | [negative_control_questions.json](negative_control_questions.json) | [negative_control_references.json](negative_control_references.json) |

## 다음 비교의 데이터와 집계

아래 180문항 비교는 상황별 검색 140문항, 업무 절차·규정 20문항, 범위 밖 대조 20문항을 사용한다. 새 일상 표현 40문항까지 평가 목록은 총 220문항이다. 일상 표현 묶음은 후보 20쪽과 최종 최대 8쪽·8,000토큰에서 별도 집계한다. 각 비교에서 검색에는 `id`와 `question`을 전달하고, 결과를 저장한 뒤 참고 파일을 읽는다.

| 평가 묶음 | 문항 | 집계 기준 |
| --- | ---: | --- |
| 상황별 검색 1~7번 | 140 | 후보와 최종 문맥 각각에서 필수 근거가 모두 들어온 문항을 유형별로 센다. 6번은 두 후보 사건의 근거, 7번은 증빙 누락이나 미확정 상태를 보여주는 근거를 확인한다. |
| 업무 절차·규정 | 20 | 절차·필요 서류 10개와 적용 조건·예외 10개로 나눠 같은 근거 충족 기준으로 센다. |
| 일상 표현 검색 | 40 | 한 문서 20문항과 여러 문서 20문항을 별도 집계한다. 기존 180문항 비교와 실행·결과를 구분한다. |
| 범위 밖 대조 | 20 | 완전히 무관한 질문 10개와 물류 업무와 유사한 질문 10개를 각각 집계한다. 범위 밖 문항은 정답 근거를 찾는 검색 지표의 분모에서 제외한다. |

### 범위 밖 대조군

현재 설정은 관련도 순으로 문맥을 채운다. 앞선 비교에서는 개인 기준선과 동료 설정 모두 범위 밖 20문항 전부에 8쪽씩 반환했다. 다음 비교에서는 반환 쪽 수와 함께 **상위 결과가 요청과 무관한지, 주제는 비슷하지만 요청한 사실을 뒷받침할 수 없는지**를 기록한다. 예를 들어 현재 게이트 통제 상태를 묻는 질문에서 반입 절차 안내는 관련 주제이지만 실시간 통제 상태를 입증할 수 없다. 검색 점수를 비교할 때는 같은 설정의 같은 단계 안에서 근거가 있는 질문과 대조한다. [기존 범위 밖 결과](../../records/advanced-scenarios-benchmark-20261109-v2/report.md#최종-문맥에-필수-근거가-모두-들어온-문항)

문맥을 걸러내는 규칙을 실험할 때는 기준을 맞추는 문항과 최종 검증 문항을 분리한다. **범위 밖 통과율**은 범위 밖 문항 중 필터를 거쳐 문맥이 전달된 비율이다. **유효 문맥 손실률**은 필터 적용 전 모든 필수 근거를 확보한 문항 중 적용 후 일부 또는 전체 근거를 잃은 비율이다. 두 수치와 필터 적용 후 본 평가의 전체 근거 충족률을 함께 기록한다. 6·7번은 자료 안에서 필요한 근거를 찾아야 하는 질문으로 계속 채점한다. 실제로 되묻거나 보류하는지는 답변 평가에서 확인한다.

## 개인 기준선의 카테고리별 220문항 실행

기본 5:5 하이브리드를 상황별 140문항, 업무 규정 20문항, 일상 표현 40문항, 범위 밖 대조 20문항에 실행한다. `rankings.jsonl`에 후보 순위와 최종 문맥 지문을 저장한 뒤 `results.json`의 `categories`에 유형별 점수를 기록한다. 근거형 문항은 필수 근거의 전체 충족을 집계하고, 범위 밖 대조는 반환한 페이지 수를 따로 집계한다. 이전 BM25 기준선을 함께 실행하려면 `--compare`를 추가한다. [모델과 인덱스 준비](../../README.md#현재-advanced-구성) · [카테고리별 비교 결과](../../records/advanced-category-comparison-20261109/report.md)

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false \
  uv run --no-sync python -m experimental.u_10o0o.advanced_search \
  --suite categories --output-dir artifacts/u_10o0o/advanced-hybrid-categories-check
```

## 이전 BM25 기준선과 동료 무 LLM 설정의 180문항 비교

이전 BM25 기준선의 원문 조회와 동료 `team_graph`를 세 질문 파일의 모든 문항에 실행한다. Advanced 복원 페이지, 개인 HG 원문 색인, 동료 p800 색인과 각 모델의 로컬 캐시가 필요하다. 후보 순위와 문맥을 먼저 저장하고 참고 근거를 채점한다. [동료 색인](../../records/advanced-workflow-20261109/report.md) · [실행 결과와 대조군 판정](../../records/advanced-scenarios-180-20261109/report.md)

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false \
  uv run --no-sync python -m experimental.u_10o0o.evaluation.advanced_scenarios.compare_180 \
  --output artifacts/u_10o0o/advanced-scenarios-180-check
```

## 기존 160문항의 로컬 검색 재실행

현재 [비교 실행 코드](compare.py)는 상황별 검색 140문항과 범위 밖 대조 20문항의 기존 결과를 재현한다. Advanced 복원 페이지·HG 원본 색인과 [동료 p800 색인](../../records/advanced-workflow-20261109/report.md)을 준비한다. 개인 기준선과 동료 설정의 로컬 모델을 사용하며 마지막 출력 경로는 새 폴더로 지정한다.

```bash
scenario_data=artifacts/u_10o0o/advanced-scenarios-20261109-v2
mkdir -p "$scenario_data"
cp experimental/u_10o0o/evaluation/advanced_scenarios/{manifest.json,*_questions.json,*_references.json,compare.py} "$scenario_data/"
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false PYTHONPATH=. \
  uv run --no-sync python -B "$scenario_data/compare.py" \
  --output artifacts/u_10o0o/advanced-scenarios-benchmark-20261109-v2
```

[생성 코드](build.py)는 최초 세트를 원본에 연결한 기록이다. 개정판은 최초 세트의 7번 질문과 근거를 검색용 파일로 옮겼다. [개정판 비교 결과](../../records/advanced-scenarios-benchmark-20261109-v2/report.md)
