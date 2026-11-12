# TableRAG additional 최종 평가: 생성 1회, 채점 3회 다수결 (2026-11-12)

PR #59 이후 기본 TableRAG 경로의 최종 LLM 답변을 additional 30문항으로 평가했다. 팀 결정에 따라 additional을 최종 평가 세트로 쓰고, 비용 한도는 두지 않았다. 반복 채점은 3회 다수결로 정했다. 같은 답변을 세 번 채점하고, 갈린 항목은 사람이 검수한다. 점수는 덮어쓰지 않는다.

- 문항별 다수결과 사람 검수 대상: [reports/table-rag-additional-20261112/majority-e1-e3.md](reports/table-rag-additional-20261112/majority-e1-e3.md)
- 회차별 채점 기록: `reports/table-rag-additional-20261112/additional-luna-e1~e3/`(README, answers, comparison, manifest, scores, summary)
- 실행 manifest: [reports/table-rag-additional-20261112/manifest.json](reports/table-rag-additional-20261112/manifest.json)
- 요청별 상세 실행 로그(본문 제거본): [reports/table-rag-additional-20261112/runs-no-text.jsonl](reports/table-rag-additional-20261112/runs-no-text.jsonl)
  - 원본 `runs.jsonl`에서 원본 문서 본문만 뺐다. 검색 후보·최종 문맥·도구 발췌의 본문, 모델 문맥, 모델 호출 입력은 SHA-256과 글자 수로 바꿨다.
  - 그대로 남은 것: 검색 경로, 후보·최종 출처, planner 도구 호출(인자·중간 요약)과 도구 결과의 출처·표 정보, DB 조회 결과 필드, 호출별 모델·시간·토큰·오류, 문맥 해시, SQL 결과, 진단 시간.
  - 만드는 방법은 [strip_run_text.py](strip_run_text.py)에 있다. 표 제목(`heading`)과 구역 제목(`section`)은 식별용 제목이라 남겼다. 그 밖에 원문 줄이 남지 않았는지 두 방법으로 검사했다.
- 전체 실행 기록 `runs.jsonl`(7MB, 원본 PDF 본문·모델 입력 포함)은 관례대로 Git에 올리지 않았다. Damsebi PC의 `artifacts/experiments/table-rag/table-rag-additional-20261112/`에 있다. SHA-256은 `53034192…ce273`이고, 세 채점 manifest의 `runs_sha256`과 같다. 재채점하려면 이 파일을 받아 같은 위치에 둔다.
- 공유본의 manifest는 로컬 절대 경로(`data_directory`)를 저장소 기준 상대 경로로 바꿨다. 그 밖의 파일은 원본과 바이트 단위로 같다.

## 조건

| 항목 | 값 |
|---|---|
| 코드 | `92e8c038`(#59 반영), 미커밋 변경 없음 |
| 번들 | A가 제공한 `artifacts/advanced-table-rag`. corpus `d6b746e5…`. A의 SHA256SUMS 9개와 공식 `prepare_table_rag check` 결과가 모두 일치 |
| 생성 | `run_table_rag run` 1회(질문만 입력, 순차 실행). 모델 `gpt-6-luna`, 추론 강도 medium. 출력 한도 planner 3,500·answer 2,200토큰. 호출당 30초, 재시도 0. 후보 20쪽·최종 8쪽·근거 8,000토큰(서비스 manifest 기준) |
| 질문·정답 | `additional_questions.json` `7ada38ca…` / `additional.json` `c887faa5…`(30문항, ID·질문 원문 일치) |
| 채점 | 같은 `runs.jsonl`을 `additional-luna-e1~e3`로 3회 채점. Judge v3 `5c29900b…`, 출력 스키마 `judge-output-v2`. Judge·RAGAS 모델 `gpt-6-luna`. Langfuse 점수는 게시하지 않았다 |
| 실행 환경 | Windows `.venv`(torch 2.15.0+cu132, 서비스 장치 cpu). trace는 Langfuse로 전송됐지만 저장 확인은 하지 않았다(`recorded_unverified`) |

실행 전에 30건 모두 다음을 확인했다.
- 질문 원문이 질문 파일과 같다.
- 문서 조각 기준 `final_context_hash`와 모델 문맥 텍스트 기준 `model_context_hash`가 각각 다시 계산한 값과 같다.
- answer 호출 입력의 문맥 해시와 본문이 기록과 같다.
- 인용이 모두 최종 출처 안에 있다.

## 결과(다수결)

| 지표 | 값 |
|---|---|
| Judge 사실 · 조건 · 근거 · 판단 | 23/30 · 25/30 · 27/30 · 27/30 (동률·평가 불가 0) |
| RAGAS Faithfulness | 0.775(문항별 3회 평균의 평균, 30문항 모두 scored) |
| Hit@8 · Recall@8 | 1.0 · 0.902(29문항, 근거 문서가 없는 1문항은 해당 없음) |
| TableRAG 최종 페이지 recall · 필수 근거 전부 포함 · MRR | 0.747 · 0.517 · 0.897(29문항) |
| SQL 기대값 | 30문항 해당 없음(참고 파일에 기대값이 없어 채우지 않음) |
| 상태 | answered 28, abstained 1, needs_clarification 1, 오류 0 |
| 인용 | 66개 모두 최종 문맥 안. 그중 55개가 참고 근거 쪽 |

검색·TableRAG 지표는 세 회차가 같다. 같은 답변·문맥이기 때문이다. 다수결은 `fix_compare.py` 출력과 별도로 다시 계산한 값이 일치한다.

## 채점 안정성

| 회차 | 사실 | 조건 | 근거 | 판단 | Faithfulness |
|---|---|---|---|---|---|
| e1 | 0.767 | 0.867 | 0.900 | 0.900 | 0.786 |
| e2 | 0.700 | 0.800 | 0.900 | 0.900 | 0.782 |
| e3 | 0.767 | 0.867 | 0.931 | 0.967 | 0.756 |

- 3회 모두 같은 항목은 사실 26, 조건 25, 근거 29, 판단 28(각 30문항 중)이다.
- 갈린 12건(10문항: AS-ADD02·03·06·09·10·12·13·15·18·29)은 사람 검수 대상이다. 다수결 값은 검수 전 잠정값이다.
- Faithfulness는 회차 차이(최대−최소)가 0.2 이상인 문항이 9개다. 예를 들어 AS-ADD25는 0.88/0.88/0.14, AS-ADD29는 0.56/0.89/0.00이다. 평균만 보면 이런 변동이 가려진다.

## 기대 행동과 다른 상태

- AS-ADD06은 답변을 기대했는데 보류했다. 판단 0(2/3).
- AS-ADD13·15·16은 기대 행동이 abstain이지만 허용 라벨에 answer가 있어, 부분 답변은 규칙 위반이 아니다. 판단은 ADD13·16이 1(3/3), ADD15가 0(3/3)이다.
- AS-ADD12는 기대대로 확인 질문을 했다. 인용이 없어 근거는 0/0/해당 없음이다.

## 운영

- **생성 시간**: 합계 757초, 문항 중앙값 22.7초, 최대 97.6초.
- **생성 토큰**: 입력 791,568, 출력 18,694. 30문항 모두 사용량이 완전히 기록됐다.
- **모델 호출**: 92회(planner 62, answer 30). 재시도는 0이다.
- **SQL**: 30문항 모두 SQL 호출이 0건이다. 검색 경로는 하이브리드 22, DB 기록 조회 8이다.
- **채점 시간**: 회차당 731·702·700초.
- **채점 토큰**: Judge는 회차당 입력 210,876, 출력 33,015 / 32,361 / 30,453. RAGAS 토큰은 기록되지 않는다.

## 해석 주의와 발견한 문제

- 생성은 1회라 답변 자체의 변동은 반영되지 않았다. Judge v3의 조건 규칙 충돌도 그대로다.
- SQL 경로가 한 번도 실행되지 않았다. 이 결과로는 SQL 조회 품질을 판단할 수 없다.
- 채점 폴더의 `README.md`·`summary.json`에 있는 "재시도"(`retry_attempts` 62)는 실제 재시도가 아니다. planner·answer 호출을 재시도로 센 값이다(`app/evaluation/metrics.py:133`, attempts−1). 다수결 표의 재시도 열은 보정한 값(0)이다.
- `http_benchmark`의 기본 제한 시간 150초는 TableRAG의 최악 경우(호출당 30초 × planner 최대 6회 + answer)보다 짧을 수 있다. 코드 상수로 계산한 값이다.

## 사전 연결 검증: AS-DEV19 (개발 문항)

본 평가 전에 HTTP 요청부터 채점까지의 연결을 1문항으로 확인했다. 연결 검증용이며 품질 근거가 아니다. 기록은 [reports/table-rag-asdev19-connect-20261112/](reports/table-rag-asdev19-connect-20261112/)에 있다(HTTP 측정 manifest·`http_runs.jsonl`, 서버 요청 manifest, 본문을 뺀 요청 로그 `request-runs-no-text.jsonl`, 채점 1회).

- 서버를 띄워 `/health` `query_ready=true`를 확인했다. 그다음 `http_benchmark`를 `--modes` 없이 1회 요청했다(200, 17.9초).
- HTTP 응답, `artifacts/rag-runs/<request_id>/` 기록, Langfuse trace를 대조했다. request_id·질문·상태·답변·reason·인용, 두 종류의 문맥 해시, 호출별 토큰까지 28개 항목이 모두 일치했다.
- `run_table_rag grade --reference-id AS-DEV19`로 채점했다. Judge 네 항목 1, Faithfulness 1.0이다.

## 재현

```bash
python -m app.evaluation.scripts.run_table_rag run --questions data/advanced/evaluation/additional_questions.json --experiment-id table-rag-additional-20261112
python -m app.evaluation.scripts.run_table_rag grade --run-directory artifacts/experiments/table-rag/table-rag-additional-20261112 --references data/advanced/evaluation/additional.json --grade-id additional-luna-e1 --judge-model gpt-6-luna --ragas-model gpt-6-luna
python -m experimental.Damsebi.fix_compare --references data/advanced/evaluation/additional.json --table-rag-run artifacts/experiments/table-rag/table-rag-additional-20261112 additional=additional-luna-e1+additional-luna-e2+additional-luna-e3 --out <md>
```

`grade`는 `-e2`, `-e3`로 두 번 더 실행한다. 이 PC에서는 `uv run --locked` 대신 `.venv` python으로 실행했다. `uv run --locked`는 환경을 다시 맞추면서 CUDA용 torch를 lock 파일의 CPU 빌드로 되돌린다.
