# 원문 근거 단위와 요청 계획을 분리한 실험

최종 채택과 기능별 구분은 [종료 결정](../records/advanced-table-rag-closeout-20261112/decision.md)을 따른다. 선택한 진입점은 `table_rag_unified query --db-first`이며 `--verify-evidence`는 붙이지 않는다. 아래 비교용 경로의 기본값은 과거 재현을 위해 유지한다.

기존 `table_rag_unified --db-first`를 비교군으로 유지한다. 운영 API와 기본 검색은 변경하지 않는다. 결과는 [비교 기록](../records/advanced-table-rag-bounded-workflow-20261112/work-log.md)에 남긴다.

**최종 비교에서는 채택하지 않았다.** 공식·카테고리 근거 충족과 지연이 기존 A보다 나빠 기존 DB 우선 기준선을 유지한다. 수치와 문항별 원인은 [완료 보고서](../records/advanced-table-rag-bounded-workflow-20261112/report.md)를 확인한다.

1. 완전 코드 하나와 명시 기록 종류 또는 원문에서 필드명을 확인한 단순 금액·날짜 조회를 요구하는 제한된 문법은 유일한 사건·원본을 확인하면 DB만으로 반환한다. 그 외 자연어·값 계산·규정·제외·복수 대상 질문은 이 빠른 경로에 들어가지 않는다.
2. Luna medium이 질문을 최대4개 대상별 요청으로 구조화한다. 코드와 필드 값, 질문 구절을 원질문으로 검증한다. 형식·API 오류 시 원질문 검색은 수행하되 충분하다고 선언하지 않는다.
3. 코드는 요청별 DB 조회와 BM25·벡터5:5 검색을 수행한다. 규정·혼합 요청에는 원문 절/표 셀 FTS 상위4쪽을 보충한다. 후보20개를 근거 단위로 한 번 리랭킹한다. 여러 요청은 독립 실행하되 모델 자원 잠금을 재사용한다.
4. 검색에 걸린 표 행·문단과 같은 원본 절을 원문 위치·근거 ID와 함께 제공한다. 페이지 앞부분만 보여주는 방식과 모델의 인용 복사를 사용하지 않는다. 원문 표의 열 이름·값은 검증된 TableStore에서 온다.
5. Luna가 요청별 근거 ID를 선택한다. 부족한 요청 한 번의 추가 검색 또는 읽기 전용 SQL 한 번 후 선택을 한 번 더 한다. 최대 API3회이며 SQL도 요청을 충족하지 못하면 미충족 상태다.
6. 요청별 출처를 교대로 보존해 최종 후보20쪽·원본8쪽·8,000토큰을 구성한다. 합친 후보를 원질문으로 다시 리랭킹하지 않는다. 출처·셀·원문 보존을 코드로 검사한다.

## 원문 관계의 범위

색인은 원본 PDF 지문에 연결된 레이아웃과 검증된 표 행을 읽는다. 같은 원본 쪽·절의 연결과 원문 명시 코드 참조를 SQLite에 저장한다. 코드 참조는 사건 동일성이나 규정 적용의 확정이 아니다. 제목이 같은 다른 시점·사건의 규정을 병합하지 않는다. 표가 아닌 서술은 원문 범위를 그대로 보존하며 자동 의미 검수를 통과한 규정으로 주장하지 않는다. 기존 원본/벡터/DB 파일을 수정하지 않는다.

## 실행

```bash
.venv/bin/python -m experimental.u_10o0o.table_rag_workflow build --output artifacts/u_10o0o/advanced-snapshots/새-경로
.venv/bin/python -m experimental.u_10o0o.table_rag_workflow query '질문' --output artifacts/u_10o0o/새-결과.json
.venv/bin/python -m experimental.u_10o0o.records.advanced-table-rag-bounded-workflow-20261112.run_benchmark freeze
.venv/bin/python -m experimental.u_10o0o.records.advanced-table-rag-bounded-workflow-20261112.run_benchmark run
```

`query`와 벤치 실행은 기존 키로 유료 API를 호출한다. 결과 경로는 새 경로를 사용하고 미완료 요청을 자동 재시도하지 않는다. 답변 생성은 하지 않는다. 근거 충분성 판정은 검색 후 사후 검수와 구분하며, 요구 자체를 빠뜨리는 모델 오류는 남을 수 있다.
