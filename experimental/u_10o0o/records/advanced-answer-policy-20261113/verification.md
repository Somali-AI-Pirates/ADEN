# 최종 검사 기록

이 문서는 최초 실험 종료 시점의 검사다. 후속 커밋 요청에 따라 다른 변경을 제외한 트리의 검사 결과는 [커밋 인계 기록](commit-handoff.md)에 별도로 남긴다.

실행 위치: WSL Ubuntu-26.04, `/home/jake/workspace/projects/kant/ADEN`. 기준 HEAD는 `6707a4dab8c6b49a1f1630ff296414a3a82d17d9`이며 작업 시작 시 다른 변경이 있던 트리다. 이번 작업의 비교 기준은 Git HEAD 전체 차이가 아니라 `artifacts/u_10o0o/advanced-answer-policy-20261113/before/`에 저장한 파일과 실제 작업 범위다.

## 코드와 패키지

| 명령·검사 | 결과 | 로컬 증거 |
| --- | --- | --- |
| `.venv/bin/python -m ruff check .` | 통과 | `ruff-final.log` |
| `.venv/bin/python -m ruff check --isolated experimental/u_10o0o/answer_policy_ab.py` | 통과. 개인 실험은 공통 Ruff 제외여서 별도 실행 | 실행 출력 |
| `.venv/bin/python -m pytest -q tests` | **695 passed, 11 subtests passed**, 경고 1개, 11.58초 | `pytest-final.log` |
| `uv lock --check` | 통과, lock 변경 없음 | `lock-final.log` |
| `uv build --out-dir artifacts/u_10o0o/advanced-answer-policy-20261113/dist` | sdist·wheel 성공 | `build.log`, `dist/` |
| wheel 리소스 검사 | `app/rag/prompts/answer.txt`, `service.py`, `model.py`, `result.py`가 현재 파일 바이트와 일치 | 검증 실행 출력 |
| `.venv/bin/python -m experimental.u_10o0o.answer_policy_ab --help` | 새 실험 진입점 실행 성공 | 실행 출력 |
| `.venv/bin/python -m experimental.u_10o0o.answer_policy_ab report` | 실제 모델 근거·후보·문맥 동일성, 최종 D의 요약 제외 검증 후 집계 성공 | `aggregation.log` |
| 로컬 Markdown 링크 검사 | 최종 보고서·프로토콜·구현 검토·설계 문서의 상대 링크 검사 | 실행 출력 |

증거 파일의 기준 디렉터리는 `artifacts/u_10o0o/advanced-answer-policy-20261113/`다. Qdrant 경고는 로컬 모의 Qdrant가 검색 파라미터를 무시한다는 기존 테스트 경고다. 실제 유료 모델 평가는 위 단위 테스트와 분리해 아래에 기록한다.

## 실제 비교

- 공식 질문 60개의 검색 완료, 검색 계획 호출 118회. 경계 질문 20개는 합성 문맥으로 별도 준비.
- A/B/C/D 각각 80개, 총 320개 답변 저장. 생성 및 출력 검증 오류 0개.
- Judge 320개 저장. D의 경계 `EDGE-02` 한 건은 출력 스키마 불일치로 네 점수 모두 평가 불가. 나머지는 정상 채점.
- Ragas 결과 320개 저장. 평가 대상이 없는 응답과 위 한 건을 별도로 표시. 실제 호출 592회, 실제 API 호출 오류는 없었다.
- 질문·근거·상태를 동결했고 참고답은 생성 후에만 사용. 모델 입력 기록을 읽어 80개 질문의 네 조건 모두 같은 원문·SQL을 받았는지 확인.
- 검색 관련 7개 파일의 전후 SHA-256 동일. 데이터·모델·문맥 지문은 [provenance.json](provenance.json), 집계는 [summary.json](summary.json).
- 초기 연결 시험 답변 2회는 `connection-pilot/`에 별도 보존. 본 비교 점수에 섞지 않음.

주요 실행 로그: `freeze.log`, `answers.log`, `answers-c.log`, `answers-d.log`, `judges.log`, `judges-c.log`, `judges-d.log`, `faithfulness.log`, `faithfulness-c.log`, `faithfulness-d.log`.

## 발견 후 처리한 문제

1. 새 필수 `reason`에 맞지 않는 기존 모의 응답 5개가 첫 전체 테스트에서 실패했다. 해당 테스트 응답만 갱신하고 전체 재실행했다. 원래 실패 로그 `pytest.log`도 보존했다.
2. 최초 Judge 시험은 평가 파일 저장 후 화면용 출력이 `score`와 `value`를 혼동해 종료했다. 저장 결과를 확인한 뒤 출력만 고쳤고, 같은 문항을 다시 호출하지 않았다.
3. 첫 보완안의 회귀와 검색 요약의 사실 유입을 확인했다. 기존 결과를 덮어쓰지 않고 C/D를 추가했으며, 최종 제품에서 자유 서술 요약 전달을 제거했다.
4. D의 경계 Judge 한 건은 모순된 스키마 출력이다. 오류를 0점이나 통과로 바꾸거나 자동 재시도하지 않았다.

## 리뷰와 남은 범위

구현·호환·저장·스트리밍과 평가 입력·정답 분리·원점수 보존을 자체 점검했다. 사이드 대화의 제약에 따라 독립 에이전트 리뷰는 수행하지 않았다. 원문과 답변 대조 결과를 사람 검수 완료로 표현하지 않는다.

검색 성능을 개선하거나, 모든 프롬프트 주입을 방어하거나, 답변의 의미상 출처 정확도를 코드로 보증하는 작업은 아니다. 지연은 통제된 운영 벤치가 아니며 운영 성능으로 환산하지 않는다. 원격 Langfuse 게시·운영 배포·커밋·푸시는 실행하지 않았다. 자동 평가 불일치와 계산·모호성의 남은 사례는 [문항별 검토](case-review.md)에 기록했다.
