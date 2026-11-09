# Advanced 상황별 검색 평가 · 개정판

[최초 검색 실행](../../records/advanced-scenarios-benchmark-20261109/report.md)의 160문항과 참고 근거를 보존한다. 7번 20문항을 검색 본 평가에 추가해 1~7번은 140문항, 8번은 범위 밖 대조 20문항이다. 각 상황의 문항 수는 20개다. [평가 범위와 파일 지문](manifest.json)

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
| 본 평가 | [retrieval_questions.json](retrieval_questions.json) | [retrieval_references.json](retrieval_references.json) |
| 범위 밖 대조 | [negative_control_questions.json](negative_control_questions.json) | [negative_control_references.json](negative_control_references.json) |

검색 입력은 `id`와 `question`이다. 검색 결과를 저장한 뒤 참고 파일을 열어 필수 근거가 최종 8쪽·8,000토큰에 남았는지 채점한다. [두 설정 비교 실행 코드](compare.py)

## 로컬 검색 재실행

Advanced 복원 페이지·HG 원본 색인과 [동료 p800 색인](../../records/advanced-workflow-20261109/report.md)을 준비한다. 아래 명령은 개인 기준선과 동료 설정의 로컬 모델을 쓰고 외부 API를 호출하지 않는다. 마지막 출력 경로는 새 폴더로 지정한다.

```bash
scenario_data=artifacts/u_10o0o/advanced-scenarios-20261109-v2
mkdir -p "$scenario_data"
cp experimental/u_10o0o/evaluation/advanced_scenarios/{manifest.json,*_questions.json,*_references.json,compare.py} "$scenario_data/"
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false PYTHONPATH=. \
  uv run --no-sync python -B "$scenario_data/compare.py" \
  --output artifacts/u_10o0o/advanced-scenarios-benchmark-20261109-v2
```

[생성 코드](build.py)는 최초 세트를 원본에 연결한 기록이다. 개정판은 최초 세트의 7번 질문과 근거를 검색용 파일로 옮겼다. [개정판 비교 결과](../../records/advanced-scenarios-benchmark-20261109-v2/report.md)
