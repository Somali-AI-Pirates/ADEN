# 검증 결과

## 실행·채점 통제

- 공식58·카테고리220, 조건별278개로 총556개 검색 결과를 저장했다.
- 모든 결과 저장 후 참고답을 읽고 지정 근거 채점과 양쪽 출력 검수를 수행했다.
- 원질문의 HG 전 후보와 KURE 순위는278/278에서 같고, OFF의 HG 보강 호출은0회다.
- 지정 실패가 있는63문항의 양쪽 출력126개를 AI 검수하고 자체 점검했다. 인용 존재·출처 지문·필수 판정 완전성 검사가 통과했다. 초기 검수4개에서 발견된 인용 형식 문제는 실제 반환 문맥으로 수정했고, 수정 후 전체 검증을 통과했다.
- 지정 통과 전체를 별도 의미 검수하지 않았다. 독립 에이전트 및 사람 검수는 실시하지 않았다. 이 사이드 대화의 에이전트 사용 금지 범위에 따라 자체 검토했다.
- 완료된 실행의 계획 호출은 ON488회·OFF556회이며 모두 완료 상태다. 사후 검수126회는 검색 호출량·지연에서 제외했다.
- 실행 세션 중단 당시490개 완료 결과를 보존했다. 중단된4개 시도와 확인 가능한 완료 API 응답2회는 로컬 복구 기록으로 분리했다. 응답을 저장하기 전에 중단된 호출의 과금 여부는 확인하지 않았다. 따라서 위 호출 수를 계정 전체 청구 호출 수로 해석하지 않는다.

## 코드 검사

| 검사 | 결과 |
|---|---|
| 이 실험 폴더 Ruff | 통과 |
| 이 실험 폴더 Ruff format --check | 통과 |
| HG ON 전달·OFF 우회·동시 요청 조건 격리·동일 계획 정책 selfcheck | 통과 |
| 공통 pytest tests | 300 passed, 15 subtests passed |
| TableRAG 및 문맥 보존 pytest | 31 passed |
| 저장된126개 검수의 지문·인용·판정 검사 | 통과 |
| 저장소 전체 Ruff | 미통과: 기존 작업 중인 app/rag/table_store.py:38의 빈 함수 본문 구문 오류 |

전체 Ruff 오류는 이 실험에서 수정하지 않은 다른 작업 파일에 있다. 기존 작업을 보존했으며 전체 Ruff 통과라고 표시하지 않는다. 패키지 및 운영 API 변경은 없고 이 실험은 별도 기록 폴더의 실행기로 동작한다.

실험 폴더는 저장소 Ruff의 기본 제외 경로이므로 최종 점검에서는 Python 파일3개를 명시하고 --no-force-exclude로 검사했다. 일반 폴더 명령의 미검사를 통과 근거로 사용하지 않았다. 완료 결과 수 검사는 started/progress 상태 파일을 제외한 결과 JSON만 셌다. 문서 링크와 집계도 확인했다.

## 재현한 검사

~~~bash
.venv/bin/ruff check --no-force-exclude experimental/u_10o0o/records/advanced-table-rag-hg-ablation-20261111/*.py
.venv/bin/ruff format --check --no-force-exclude experimental/u_10o0o/records/advanced-table-rag-hg-ablation-20261111/*.py
.venv/bin/python -B -m experimental.u_10o0o.records.advanced-table-rag-hg-ablation-20261111.run_benchmark selfcheck
.venv/bin/python -B -m pytest -q tests -p no:cacheprovider
.venv/bin/python -B -m pytest -q experimental/u_10o0o/table_rag/test_table_rag.py experimental/u_10o0o/table_rag/test_preservation.py -p no:cacheprovider
.venv/bin/python -B -m experimental.u_10o0o.records.advanced-table-rag-hg-ablation-20261111.analyze report
~~~

검사 원본은 artifacts/u_10o0o/advanced-table-rag-hg-ablation-20261111/v1의 verification.log, pytest-common.log, pytest-table-rag.log에 보관했다.
