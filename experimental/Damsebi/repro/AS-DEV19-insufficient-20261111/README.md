# AS-DEV19 근거 부족 재현 자료 (B 전달용)

PR #52의 B 요청사항 1을 재현하는 실제 입력과 실제 출력이다. 실패 입력은 원본 고정 근거 파일(`artifacts/bc-fixed-evidence-20261111/insufficient.jsonl`)의 AS-DEV19 줄을 **바이트 그대로** 옮겼다. 문맥을 다시 구성하거나 내용을 바꾸지 않았다.

- 참고 정답(development.json)과 C Judge의 판정 이유는 넣지 않았다. 생성 쪽 수정이 평가 정답에 맞춰지지 않게 하려는 것이다. C 판정은 상태만 `run/summary.json`에 있다.
- `input/evidence.jsonl`과 `run/run_record.jsonl`에는 Advanced 원문 청크 본문이 들어 있다. 원본 근거 파일이 Git 제외 대상이라 B가 재현할 수 있도록 이 자료만 PR #52에 함께 올렸다(2026-11-12). 저장소의 `experimental/** -text` 규칙으로 줄바꿈 변환 없이 보관되므로 `hashes.json`의 바이트 해시로 대조할 수 있다.

## 무엇이 문제인가

| 항목 | 내용 |
| --- | --- |
| 조건 | 고정 근거, 최종 문맥 7청크. 이 예약·장비의 "비용 조건 약정"(AS076 p.33, 산입 시작·종료 규칙·무료일수·요율) 페이지가 없다. 사건 기록(AS077), 계약 대조 회신(AS078), 비용 청구 명세(AS079)는 있다 |
| 실제 출력 | `answered`. 보관료 "산입 2029-08-05~2029-08-16, 총 9일", 장비반납지연료 "산입 2029-08-16~2029-08-24, 총 8일"로 단정했다(사건일을 산입 시작에 넣은 경계). 청구 산식을 "확정액 USD 205/145"로 제시했고 reason에 "산입 방식·일수·구간 요율·확정액을 확인했습니다". "검산 미완료" 표기는 없다 |
| 기대 동작 (PR #49 생성 규칙) | 산입 규칙·무료일수·요율 원문을 확인하지 못한 두 비용은 answer에 "검산 미완료"와 확인하지 못한 항목(산입 시작·종료일 포함 여부, 무료일수, 적용 요율)을 적는다. USD 205·145는 "청구서상 금액"으로 출처와 함께 답한다. 확인되지 않은 산입 경계를 추정하지 않는다 |
| 같은 조건의 다른 문항 | M19는 같은 부족 조건에서 기대대로 "검산 미완료"와 "청구서상 금액"을 구분했다 |
| 횟수 | 1회 생성 |

## 파일

| 파일 | 내용 |
| --- | --- |
| `input/evidence.jsonl` | 실패 입력(질문·검색 방식·후보 20개·최종 청크 7개). 원본 줄 그대로 |
| `input/question.json` | 실행 질문(`id`·`question`만) |
| `run/run_record.jsonl` | 실제 실행 기록 원본 줄(시도별 실제 모델 입력·출력, 최종 문맥, 해시, 시간, trace ID) |
| `run/summary.json` | 답변·reason·인용·request_id·trace ID·시도 요약과 C 판정 상태 |
| `run/manifest.json` | 실험 ID, 실행 커밋, 설정 파일 해시, 버전 |
| `config/advanced.yaml`, `config/shipping_qa.txt` | 실행 커밋의 설정·생성 프롬프트 원본 |
| `hashes.json` | 커밋, 입력·기록 줄 SHA-256, 설정·프롬프트 SHA-256, 최종 문맥 해시 |

## 실행 정보

| 항목 | 값 |
| --- | --- |
| 실험 ID | `bc-fixed-insufficient-20261111` |
| request_id / trace ID | `run/summary.json` 참고(Langfuse에 저장 확인 `verified`) |
| 실행 커밋 | `793e86bb07287475cd3ea628c69144574b788c14`(이후 main까지 `app/generation`·`app/service/query.py`·`configs/advanced.yaml` 변경 없음) |
| 생성 설정 | `configs/advanced.yaml` `0221d3af…`: `gpt-6-luna`, reasoning_effort low, max_output_tokens 4096, model_timeout_seconds 60, generation_max_attempts 2, k 8, 문맥 8,000토큰 |
| 생성 프롬프트 | `shipping_qa.txt` `c2ec0c3b…`(PR #49) |
| 최종 문맥 | `final_context_hash` `sha256:4f88bef2…`, `model_context_hash` `368a18f3…`, 7청크 |

실행 기록의 `config_hash`는 설정 JSON(`documents_dir` 절대 경로 포함)과 프롬프트 해시로 계산해 PC마다 다르다. PC 사이에는 위의 커밋·프롬프트 해시·문맥 해시와 설정 값을 직접 비교한다.

## 재현

```bash
export ADEN_CONFIG=configs/advanced.yaml
export ADEN_EVIDENCE_PROVIDER=app.service.evidence:stored_evidence
export ADEN_EVIDENCE_RUNS=<이 폴더>/input/evidence.jsonl
export ADEN_EVIDENCE_CONTEXT=1

# 평가 CLI(채점 없이 생성만): 실행 기록은 artifacts/experiments/<ID>/runs.jsonl
python -m app.evaluation.scripts.run_benchmark --adapter app.service.bootstrap:query_adapter \
    --config configs/advanced.yaml --questions <이 폴더>/input/question.json --modes hybrid_rerank \
    --experiment-id as-dev19-repro-<날짜>

# 또는 같은 환경변수로 API를 띄워 POST /query {"question": <input/question.json의 질문>, "retrieval_mode": "hybrid_rerank"}
```

입력이 같은지는 새 실행 기록의 `final_context_hash`·`model_context_hash`가 `hashes.json`과 같은지로 확인한다(이 자료를 만들 때 공통 공급기 `app.service.evidence`로 다시 만든 문맥의 해시·토큰 수가 실행 때와 같음을 확인했다). 모델 출력 문장은 실행마다 다를 수 있으므로, 기대 동작(검산 미완료 표기, 청구서상 금액 구분, 산입 경계 비추정)을 기준으로 본다.
