# 개인 TableRAG 적용 실험

최종 채택과 기능별 구분은 [종료 결정](../records/advanced-table-rag-closeout-20261112/decision.md)을 따른다. 선택한 진입점은 `table_rag_unified query --db-first`이며 `--verify-evidence`는 붙이지 않는다. 아래 비교용 경로의 기본값은 과거 재현을 위해 유지한다.

새 비교의 최종 보고는 [개인 평가 기준](../evaluation-policy.md)을 따른다. [공식58·카테고리220 A/B](../records/advanced-table-rag-official-category-20261111/report.md)는 실패 문항과 양쪽 출력을 검수한 보정 전후 결과를 함께 제공한다.

현재 개인 TableRAG의 최종 선택은 **KURE-v3 B**다(2026-11-12). 새 `run`은 KURE-v3와 **추가 확인 지침 + 관측한 선택 출처 보존**을 사용한다. KURE nano, 전체 코드 보존 BM25와 5:5 검색, 질문/리랭커 2,048토큰, 검색별·최종 후보 20쪽, 최종 8쪽·8,000토큰은 유지한다. [선정 기준](../records/advanced-table-rag-preservation-20261111/baseline.md)과 [잔여 오류 원인 분석](../records/advanced-table-rag-v3-final-20261112/report.md)을 따른다.

`workflow-*`는 별도의 단계 분리 실험이다. 현재 최종 선택 경로는 `run`이며 workflow에는 이번 정책 변경을 적용하지 않는다. 두 경로는 같은 저장소·검색기를 쓰지만 SQL 생성 시점과 중간 답변·근거 검증 방식이 다르다. KURE-v2와 기존 A는 과거 결과 및 명시적 비교 실행용으로 보존한다.

## 기본 run 경로와 이전 v5 기록

새 run은 v5 검색·표 조회 흐름에 채택한 B 정책을 적용한다. 저장된 과거 v5 실행은 당시 A 정책이며 결과를 덮어쓰지 않는다. 새 출력 경로에서만 실행한다.

원본 표 내용 검색 → 원본 물리 표·스키마 연결 → 필요한 경우 질문 분해·SQL → 조회 결과 피드백 → 종료 → 최종 근거로 답변한다. advanced_search나 운영 API의 기본 설정은 바꾸지 않는다.

- 스냅샷: 기존 원본/레이아웃/구조화 저장소의 해시를 검증하고, 원본 열 위치로 다시 파싱해 모든 셀을 대조한다. 다른 페이지·절·헤더를 묵시적으로 합치지 않는다.
- 검색: 원본 페이지와 새 IS Markdown 표 본문을 KURE-v3 + 전체 코드 보존 BM25로 5:5 RRF 결합한다. 검색별 상위 20개를 KURE nano로 한 번 리랭킹한다. 여러 검색을 합칠 때는 관측한 선택 출처를 먼저 두고 나머지를 순환 순서로 채워 최대 20개를 남긴다.
- SQL: 관측된 표만 읽는 SQLite SELECT/WITH. 집계·조건·조인·UNION을 지원한다. 미리보기는 40행이며 집계 대상은 전체 표다. 소수는 문자열로 보존하며 decimal_sum/avg/cmp/add/sub를 제공한다.
- 계획: GPT-6 Luna medium. 한 번에 하위 질문 하나, 최대 다섯 번. 대상 정보가 부족하면 추가 확인하도록 지시하며, 이미 충분하면 분해 없이 종료할 수 있다. SQL 오류와 빈 결과도 다음 호출에 전달한다.
- 최종 근거: 모델이 관측하고 선택한 출처를 병합 후보 20쪽에 먼저 보존한 뒤, 최대 8쪽·8,000토큰을 구성한다. SQL 결과도 예산에 포함하며, 사용한 원본 표의 모든 출처가 함께 남을 때만 포함한다. 답변 생성은 별도 새 호출이며 내부 계획 이력을 전달하지 않는다.
- 평가는 모든 280문항의 검색·답변 저장이 끝난 후에만 참고 정답을 연다. 일반 답변과 음성 대조군을 구분한다. 질문 계획 자체의 의미 정확도는 별도 정답이 없어 주장하지 않는다.

저장소 루트에서 새 경로로 실행한다. 기존 키·외부 원문 전송 권한은 실행 전에 해당 사용자의 승인이 있어야 한다. 현재 실행은 사용자가 기존 .env 키와 비용 제한 없는 호출을 승인한 범위다.

    .venv/bin/python -B -m experimental.u_10o0o.table_rag build --output artifacts/u_10o0o/table-rag-new
    .venv/bin/python -B -m experimental.u_10o0o.table_rag vectors --output artifacts/u_10o0o/table-rag-new --device cpu
    .venv/bin/python -B -m experimental.u_10o0o.table_rag run --output artifacts/u_10o0o/table-rag-new --device cpu
    .venv/bin/python -B -m experimental.u_10o0o.table_rag score --output artifacts/u_10o0o/table-rag-new
    .venv/bin/python -B -m experimental.u_10o0o.table_rag grade --output artifacts/u_10o0o/table-rag-new
    .venv/bin/python -B -m experimental.u_10o0o.table_rag report --output artifacts/u_10o0o/table-rag-new

shared_cuda는 다른 작업을 보존하기 위한 제한된 GPU/CPU 분산 실행 옵션이다. 추가 accelerate, psutil이 필요하다. 이번 실행은 별도 /tmp/aden-side-model-deps에 설치하고 PYTHONPATH로만 제공했으며 공유 .venv는 바꾸지 않았다. GPU 메모리 비율 0.22, 가중치 GPU 예산 1,000MiB, 임베딩 배치 1, CPU 스레드 2를 사용했다. 리랭커는 CPU float32다. CPU와 GPU 실행을 동일 지연으로 해석하지 않는다.

원본·SQL·질문·답변은 무시되는 artifacts/에만 저장한다. 공유 보고서에는 지문·문항 ID·출처 ID·집계만 남긴다. 중간에 끊긴 유료 호출은 자동 재전송하지 않고 시작/응답 체크포인트를 남긴다. 완료된 문항은 입력·코드 지문이 같을 때 재사용한다.

[실험 기록](../records/advanced-table-rag-20261111/work-log.md)

저장된 최종 품질 평가 v5는 cpu_gpu_rerank 모드를 사용했다. 당시 질문 임베딩은 CPU bfloat16, 리랭커는 CUDA float32·배치 1이었다. 프로세스 GPU 메모리 비율을 0.12로 제한한다. 앞의 shared_cuda 설명은 표 색인 생성과 CPU 리랭커 초도 실행에 해당한다. 새 평가를 시작할 때의 실행 예시는 다음과 같다. 완료된 v5 경로에는 run을 다시 실행하지 않는다.

    OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 .venv/bin/python -B -m experimental.u_10o0o.table_rag run --output artifacts/u_10o0o/table-rag-new --device cpu_gpu_rerank

baseline-answers 명령은 동일 최종 문맥의 대조군 답변을 먼저 생성하는 선택 단계다. run은 질문·문맥·모델·지침 지문이 같은 완료 답변만 재사용한다.

## 완료된 v5 평가 기록

공식 60문항과 카테고리 220문항을 비교했다. 기존 개선 HG 날짜 조회를 새 경로에도 유지했으며, 저장된 기준선 순위·문맥은 280/280 일치했다. v4의 280개 출력을 품질 채점하기 전에 조회 조건 차이를 발견했다. 영향을 받는 14문항은 v5에서 재실행했고, 원질문과 모든 후속 질문에서 새 분기가 발동하지 않는 266문항은 코드·출력 지문을 확인해 재사용했다. 상세 근거는 reuse-proof.json과 hg-parity.json에 있다.

완료 시점의 실행 코드 77개를 v5/executed-code에 보존했다. protocol.executed.json과 run.executed.json은 보존 전 실행 지문이고, 현재 protocol.json은 같은 코드 해시의 보존 경로를 가리킨다. 이후 보고서 서식·로컬 지연 집계 수정은 실행 결과와 분리했다. run을 재개하려고 기존 지문을 덮어쓰면 안 된다. 새 검색 실험은 반드시 다른 출력 경로를 사용한다.

저장된 출력으로 채점·보고서만 다시 만드는 명령은 다음과 같다. 모델 호출은 없다.

    .venv/bin/python -B -m experimental.u_10o0o.table_rag score --output artifacts/u_10o0o/advanced-table-rag-20261111/v5
    .venv/bin/python -B -m experimental.u_10o0o.table_rag report --output artifacts/u_10o0o/advanced-table-rag-20261111/v5
    .venv/bin/python -B -m experimental.u_10o0o.records.advanced-table-rag-20261111.analyze artifacts/u_10o0o/advanced-table-rag-20261111/v5

답변 채점 grade는 유료 호출 단계다. 기존 완료 채점은 재사용하고 실패 호출을 자동 재전송하지 않는다. timing은 저장된 계획의 로컬 검색을 다시 실행하며 API를 호출하지 않는다. 이번 20개 표본에서 TableRAG의 최종 문맥은 20/20, 기준선은 16/20이 저장본과 같았다. 따라서 이 지연만으로 결과까지 같은 엄밀한 속도 비교라고 주장하지 않는다.

[최종 비교 보고서](../records/advanced-table-rag-20261111/report.md) · [논문 대응표](../records/advanced-table-rag-20261111/paper-coverage.md)


## 검색 후 SQL·중간 답변·근거 확인 경로

workflow는 최초 문서/표 검색 → 자연어 하위 질문 도구 → 그 질문으로 검색 → 현재 검색에서 발견한 스키마로 SQL 생성/실행 → 별도 중간 답변 → 검증된 중간 근거를 function_call_output으로 계획자에게 반환 → 후속 조회 또는 종료 순서다. 하위 질문에 SQL을 미리 끼워 제출하지 않는다.

처음 계획 단계에서 질문의 대상·요청별 필요 근거 목록을 고정한다. 각 항목은 질문 원문의 실제 인용으로 연결한다. 중간 답변은 supported/partial/missing/conflict/needs_clarification을 기록하고, 문서 인용 또는 SQL 셀이 실제 관측값과 일치하는지 검사한다. 한 주장에 잘못된 인용이 하나라도 있으면 주장 전체를 기각한다. 필요한 근거가 남아 있거나 최종 예산에서 사라지면 sufficient/answered 상태를 허용하지 않는다. 의미 적합성과 필요 항목의 완전성은 여전히 모델의 판단이므로 정답을 보장하는 검증기로 부르지 않는다.

최종 원문은 8쪽, 원문 후보는 20쪽이다. SQL 셀과 검증 인용을 포함한 중간 답변은 별도로 출처를 연결해 보존하고, 답변 입력 evidence JSON 전체를 8,000토큰 안에 담는다. 따라서 원본 출처 참조는 8개를 넘을 수 있다. 출처 전체를 원문 8쪽으로 다시 펼치지 못했다는 이유로 검증 SQL 값을 버리지는 않는다. 실제 원문 8쪽 점수와 추가 인용 점수, 답변 모델 채점을 구분한다.

고정된 v5 표/벡터 자산을 사용하며 재임베딩하지 않는다. HG/HA는 페이지에서 표로 연결하고 IS만 별도 Markdown 색인을 가진 조건은 그대로다. 여러 페이지가 논리적으로 같은 표인지 자동으로 추론해 합치지는 않는다. 제공된 표가 전체 모집단인지 확인할 수 없는 집계는 완료로 판정하지 않는다.

    OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 .venv/bin/python -B -m experimental.u_10o0o.table_rag workflow-run --output artifacts/u_10o0o/table-rag-workflow-new --device cpu_gpu_rerank
    .venv/bin/python -B -m experimental.u_10o0o.table_rag workflow-score --output artifacts/u_10o0o/table-rag-workflow-new
    .venv/bin/python -B -m experimental.u_10o0o.table_rag workflow-grade --output artifacts/u_10o0o/table-rag-workflow-new
    .venv/bin/python -B -m experimental.u_10o0o.table_rag workflow-report --output artifacts/u_10o0o/table-rag-workflow-new

run과 grade는 유료 API 호출 단계다. score와 report는 저장 결과만 처리한다. run --limit 1은 형식/연결 확인용이며, 같은 코드·입력으로 다시 run하면 완료 문항만 건너뛴다. 시작됐으나 완료 파일이 없는 문항은 자동 재호출하지 않는다. 중간 실패를 해결할 때 기존 출력이나 코드 지문을 덮어쓰지 않는다. 평가 코드와 입력은 실행 시작 시 보존하고, 모든 280개 검색·답변 저장 후 참고 정답을 읽는다.

[범위·실행 과정](../records/advanced-table-rag-workflow-20261111/work-log.md) · [결과 보고서](../records/advanced-table-rag-workflow-20261111/report.md) · [논문 대응](../records/advanced-table-rag-workflow-20261111/paper-alignment.md).

## v5 로컬 실행 최적화 비교

speed 명령은 v5에 저장된 280문항·494개 검색과 SQL을 재생한다. 외부 API·답변 생성·새 질문 계획 없이 전체 잠금과 CPU/GPU 잠금 분리, GPU 배치 1·4·8을 비교한다. 모델·검색 후보·HG 조회·최종 문맥 조건은 유지한다. speed 명령 자체는 기본 Retriever 설정을 바꾸지 않는다.

    OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 TOKENIZERS_PARALLELISM=false .venv/bin/python -B -m experimental.u_10o0o.table_rag.speed run --output artifacts/u_10o0o/table-rag-speed-new
    .venv/bin/python -B -m experimental.u_10o0o.table_rag.speed report --output artifacts/u_10o0o/table-rag-speed-new

run은 새로운 출력 경로만 허용한다. 각 조건의 검색 순서·표 후보·조회 경로·최종 문맥을 저장 v5 및 계측 대조군과 비교한다. 별도 15문항은 정답이나 속도를 보지 않고 고정해 동시 요청 1개/4개에서 3회 반복한다. 측정은 API 대기 없는 로컬 재생이며 기존 서비스 전체 응답시간과 직접 섞지 않는다.

[범위와 과정](../records/advanced-table-rag-speed-20261111/work-log.md) · [비교 결과](../records/advanced-table-rag-speed-20261111/report.md)

완료된 속도 실험에서는 잠금 분리·배치 4가 v5의 494개 검색 순서와 280개 최종 문맥을 유지했다. 배치 8은 기존 GPU 메모리 상한에서 OOM이 발생했다. 중단된 품질 검사만 검증해 잇는 --resume-from 옵션은 시간 측정값을 재사용하지 않는다. 동시 요청과 단일 요청의 차이, 실패 조건은 [실험 결론](../records/advanced-table-rag-speed-20261111/conclusion.md)에 정리했다. 해당 비교를 실행할 당시에는 기본 Retriever를 변경하지 않았다. 이후 적용은 아래 절을 따른다.

## 검증한 속도 설정 적용

현재 run/workflow-run의 `--device cpu_gpu_rerank`는 CPU 임베딩과 GPU 리랭커에 별도 잠금을 사용하고 리랭킹 배치 4로 실행한다. 같은 모델은 한 번에 한 요청만 사용한다. CPU 전용과 shared_cuda의 전체 잠금은 유지한다. 모델·정밀도·프롬프트·HG 조회·후보/문맥 예산은 바꾸지 않았다. CLI의 기기 기본값은 기존 cpu이므로 GPU 실행에는 위 옵션을 명시한다.

과거 v5와 speed v1/v2 결과는 당시 코드와 함께 보존했다. 새 실행에는 새 출력 경로를 사용한다. [실제 응답 6문항 확인](../records/advanced-table-rag-e2e-six-20261111/report.md)과 [적용 과정](../records/advanced-table-rag-e2e-six-20261111/work-log.md)에 변경과 검증 범위를 기록했다.

## KURE-v2·128/1024 과거 비교 — 신규 실험 종료

현재 기본 KURE-v3 설정은 유지한다. kure_benchmark는 KURE-v2 토큰 벡터 MaxSim, 질문 상한128·확장 바닥64, KURE nano 상한1024를 실험한다. 원본18000쪽의 검증된 KURE-v2 벡터를 재사용하고 IS 표3750개를 같은 모델로 임베딩한다. 검색·HG·SQL·문맥 조립과 Luna medium 지침은 기존 TableRAG를 공유한다. 다른 실험의 범위 필터나 Top3+RRF는 적용하지 않는다.

토큰 색인을 GPU에 상주시켜 프로세스 메모리 비율은0.65이며, KURE-v2 질의·MaxSim·리랭커를 하나의 GPU 잠금으로 보호한다. 기존 CPU 질문 임베딩+GPU 리랭킹과 메모리·기기 배치가 다르다.

    OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 TOKENIZERS_PARALLELISM=false .venv/bin/python -B -m experimental.u_10o0o.table_rag.kure_benchmark build --output artifacts/u_10o0o/table-rag-kure-v2-new
    OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 TOKENIZERS_PARALLELISM=false .venv/bin/python -B -m experimental.u_10o0o.table_rag.kure_benchmark run --output artifacts/u_10o0o/table-rag-kure-v2-new

새 출력 경로에만 실행한다. build는 로컬 임베딩이며 run은 기존280문항의 실제 모델 계획과 고정6문항의 실제 답변을 호출한다. 이미 시작한 run을 자동 재호출하지 않는다. 이번 실행의 저장 결과 후처리는 아래 명령이며 API를 호출하지 않는다.

    .venv/bin/python -B -m experimental.u_10o0o.records.advanced-table-rag-kure-v2-20261111.analyze

[범위와 과정](../records/advanced-table-rag-kure-v2-20261111/work-log.md) · [결과](../records/advanced-table-rag-kure-v2-20261111/report.md)
