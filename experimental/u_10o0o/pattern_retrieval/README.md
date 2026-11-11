# Advanced 반복 패턴 검색 실험

원본 PDF에서 새 색인과 평가 자료를 만들어 A(페이지), B(기록·공통 규칙), C(B와 명시 코드 관계)를 비교한다. 운영 API와 기존 기본 검색을 바꾸지 않는다.

[구현 계약](IMPLEMENTATION.md)과 [설계·공개 참고자료](../research/pattern-retrieval-fresh-20261110/plan.md)에 지원 범위와 고정 조건이 있다. 결과는 [실험 보고서](../records/pattern-retrieval-v1-20261110/report.md), 과정은 [진행 기록](../records/pattern-retrieval-v1-20261110/process.md)에 있다.

## 실행

저장소 루트에서 로컬 모델 캐시와 CUDA가 있는 개인 실험 환경을 사용한다. 아래 경로는 이번 실행 전용이며, 새 실행은 출력 디렉터리 이름을 바꾼다. 이전 평가 문항·정제 산출물·벡터를 입력으로 쓰지 않는다.

    .venv/bin/python -m experimental.u_10o0o.pattern_retrieval build \
      --output-dir artifacts/u_10o0o/pattern-retrieval-v1-20261110/index-v4

    .venv/bin/python -m experimental.u_10o0o.pattern_retrieval retrieve \
      --index-dir artifacts/u_10o0o/pattern-retrieval-v1-20261110/index-v4 \
      --questions artifacts/u_10o0o/pattern-retrieval-v1-20261110/evaluation-v1/questions.json \
      --output-dir artifacts/u_10o0o/pattern-retrieval-v1-20261110/retrieval-v1

    .venv/bin/python -m experimental.u_10o0o.pattern_retrieval score \
      --index-dir artifacts/u_10o0o/pattern-retrieval-v1-20261110/index-v4 \
      --retrieval-dir artifacts/u_10o0o/pattern-retrieval-v1-20261110/retrieval-v1 \
      --evaluation-dir artifacts/u_10o0o/pattern-retrieval-v1-20261110/evaluation-v1 \
      --output-dir artifacts/u_10o0o/pattern-retrieval-v1-20261110/score-v2

    .venv/bin/python -m experimental.u_10o0o.pattern_retrieval timing \
      --index-dir artifacts/u_10o0o/pattern-retrieval-v1-20261110/index-v4 \
      --evaluation-dir artifacts/u_10o0o/pattern-retrieval-v1-20261110/evaluation-v1 \
      --gpu-dir artifacts/u_10o0o/pattern-retrieval-v1-20261110/retrieval-v1 \
      --output-dir artifacts/u_10o0o/pattern-retrieval-v1-20261110/timing-v1

    .venv/bin/python -m experimental.u_10o0o.pattern_retrieval report \
      --scored-dir artifacts/u_10o0o/pattern-retrieval-v1-20261110/score-v2 \
      --timing-dir artifacts/u_10o0o/pattern-retrieval-v1-20261110/timing-v1 \
      --output-dir experimental/u_10o0o/records/pattern-retrieval-v1-20261110/results

build --corpus-only는 원본 추출과 SQLite만 만든다. 벡터 생성은 동일 입력·구현·모델 자산 지문의 체크포인트만 재개한다. 검색과 CPU 측정은 중간 결과를 섞지 않고 새 출력 폴더에서 다시 실행한다.

평가 문항과 근거는 독립 작성·검수한 뒤 먼저 고정한다. retrieve는 ID와 질문만 읽는다. score는 전체 검색이 완료되고 입력 지문이 일치할 때만 참고 근거를 읽는다. 원문 인용, 질문 전문, 정답 출처는 artifacts 안에서만 관리한다.

## 집중 검사

    .venv/bin/ruff check --isolated --select E4,E7,E9,F,I experimental/u_10o0o/pattern_retrieval
    .venv/bin/ruff format --isolated --check experimental/u_10o0o/pattern_retrieval
    .venv/bin/python -m pytest -q experimental/u_10o0o/pattern_retrieval/tests

공통 최종 검사는 저장소의 개발 가이드를 따른다. 모델 호출 없이 검사하는 테스트와 실제 GPU·CPU 벤치마크는 별개다.

## 기존 공식·카테고리 문항에서 A 비교

별도 요청에 따라 기존 공식 60문항과 카테고리 220문항에서 새 A와 현재 `advanced_search` 기본 기준선을 비교한다. 위의 새 평가 세트 실험과 분리한 실행이다. B·C는 포함하지 않고, 기존 기준선의 제목 보너스·원문 조회·순위 정책을 유지한다. A의 색인은 재사용하지만 두 구성의 질문 검색 결과는 새로 만든다.

`established_retrieve --arm A|baseline --output-dir 새경로`로 두 결과를 먼저 저장한 뒤 `established_score`로 동일 참고 근거를 채점한다. `established_report`는 저장된 집계에서 보고서를 만든다. 각 모듈은 `python -m experimental.u_10o0o.pattern_retrieval.<모듈>`로 실행하며 `--help`를 제공한다.

[공식·카테고리 비교 보고서](../records/advanced-a-baseline-20261111/report.md)에 세부 조건, 재현 명령, 카테고리별 결과와 문항별 변화가 있다. 토큰 계산기와 리랭커 입력 상한도 서로 달라, 단일 변경의 효과로 해석하지 않는다.
