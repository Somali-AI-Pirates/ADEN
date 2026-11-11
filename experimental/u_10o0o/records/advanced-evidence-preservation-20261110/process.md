# 실행 과정

1. 기존 동일 대상144문항과 혼합6문항 진단에서 확인한 손실을 읽었다. 개인 기준선·데이터·모델은 고정하고, 요청별 리랭킹 전 후보 축소와 최종 문맥 배치만 독립 변수로 삼았다.
2. 순수 후보/문맥 함수와 실험 실행기를 추가했다. 문맥 배치는 같은 조회 후 후보에서만 움직이도록 제한했고 테스트6개로 이 경계와 페이지·토큰 한도를 확인했다.
3. pilot-01은 검색 결과를 전부 저장한 뒤 참고 근거 형식을 변환하는 단계에서 실패했다. 결과 폴더를 보존하고 채점 어댑터만 고쳤다.
4. pilot-02에서 기준선1/6 → 요청40+배치4/6의 신호를 확인했다. 수동 확인한 원문 규정 절을 썼으므로 자동 계획기 결과로 주장하지 않았다. summary의 페이지 Recall이 합산값으로 출력된 점을 발견해 후속 출력만 평균으로 수정하고 원 결과를 보존했다.
5. 기존24문항의 자동 계획을 그대로 적용했다. 기준선19/24 → 요청20 20/24 → 요청40 21/24였다. 배치 변경의 추가 이득은 없었다.
6. 동료304·일상40문항의 보관 자동 계획을 고정해 실행했다. 종속/조건부100개와 anchor 오류19개는 기준선으로 복귀했다. 모든344문항을 저장한 뒤 정답을 읽었다. 후보·최종 근거, 비교군별 개선·하락과 지원 범위를 독립 에이전트가 재집계했다.
7. 같은 대상144문항의 회귀를 실행했다. 충분120문항의 최종 근거 충족과 불충분24문항의 단일 오확정0을 유지했다.
8. 새 표본의 정답 열람 전에 요청40 하나를 검증 후보로 고정했다. 독립 작성자는 실험 점수를 보지 않고24개 업무형 질문을 만들고, 다른 검수자는 질문과 원본의 의미·출처 관계를 확인한다. 개발에 쓰인 질문과 새 검증을 분리한다.
9. 두 비교군의 계산을 공유하지 않는 실행기를 만들었다. 기존24문항에서 최종 문맥 지문48/48이 기존 공유 계산 실행과 같았다. 별도 준비·워밍업 시간과 질문별 계산량·지연을 남겼다.
10. 저장소 Ruff, 새 파일을 명시한 Ruff와 포맷, tests 및 개인 실험 테스트를 실행했다. 271개 테스트와9개 subtest가 통과했다.

## 실행 명령

재현 명령은 WSL의 /home/jake/workspace/projects/kant/ADEN에서 실행한다. Windows의 비슷한 이름 폴더는 이번 저장소와 다르다. 아래 출력 경로는 재실행 예시다. 실제 완료 결과는 각 -01/-02 폴더에 보존했고 기존 경로를 덮어쓰지 않는다.

    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false OPENBLAS_NUM_THREADS=4 .venv/bin/python -m experimental.u_10o0o.advanced_evidence_benchmark --suite broad --output-dir artifacts/u_10o0o/advanced-evidence-preservation-20261110/broad-replay

suite는 pilot, holdout, identity, broad 중 선택한다. pilot만 수동 확인한 원문 규정 절을 쓰고 나머지는 기록된 입력 정책을 따른다.

    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false OPENBLAS_NUM_THREADS=4 .venv/bin/python -m experimental.u_10o0o.advanced_evidence_validation --input-dir artifacts/u_10o0o/advanced-plan-holdout-20261110 --plans artifacts/u_10o0o/advanced-plan-holdout-20261110/live/local_plans.jsonl --output-dir artifacts/u_10o0o/advanced-evidence-preservation-20261110/isolated-holdout-replay

--plans가 있으면 API를 부르지 않는다. 없으면 질문마다 GPT-6 Luna low 계획1회를 요청한다. 질문 입력은 id·split·question만 허용하며 정답 내용은 검색 저장 후에 읽는다.

## 새 표본 준비 중 환경 오류

작성 에이전트의 첫 저장 명령은 Windows 명령행 길이 제한으로 실행되지 않았다. 명령을 나눈 뒤에는 WSL 작업 디렉터리가 저장소가 아닌 Windows 쪽 경로를 가리켜 제외 대상 메타데이터를 찾지 못했다. 두 오류 모두 질문/정답 저장이나 검색·API 실행 전에 발생했다. 실제 WSL 저장소 경로를 지정해 입력을 생성했고, 최종 질문과 근거의 해시를 별도 검수 후 고정한다.

## 새 검증 완료

- 독립 검수에서24/24 질문·요청 의미,98/98 원문/복원 인용,47/47 PDF 해시가 통과했다. 실행 전 조사 오류1곳만 고친 최종 지문을 고정했다.
- fresh-01은 기준선과 요청40을 독립 계산하고 순서를 교대로 실행했다. GPT-6 Luna low24회 모두 완료했으며 추가 API 호출이나 답변 생성은 없었다.
- 검색48개 조건을 모두 저장한 후 근거를 읽어 채점했다. 최종 전체 근거14/24 →21/24, 개선7·하락0이다. 결과를 본 뒤 입력·참고 근거·프롬프트·검색/선택 코드를 수정하지 않았다.
- 실제 직렬 계획·검색 p95는 기준선0.434초, 요청40 6.552초다. 캐시 계획 시간을 더한 과거 재현 수치와 구분한다.
- 최종 실패3개는 별도 읽기 전용 진단에 넘겼다. 이 표본에 맞춘 규칙 추가는 이번 실행에 포함하지 않았다.

새 검증을 API 재호출 없이 재현하는 예시:

    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false OPENBLAS_NUM_THREADS=4 .venv/bin/python -m experimental.u_10o0o.advanced_evidence_validation --input-dir artifacts/u_10o0o/advanced-evidence-preservation-20261110/fresh-validation --plans artifacts/u_10o0o/advanced-evidence-preservation-20261110/fresh-01/luna_plans.jsonl --output-dir artifacts/u_10o0o/advanced-evidence-preservation-20261110/fresh-replay

캐시 실행의 wall_ms에는 API 시간이 들어가지 않는다. 실시간 전체 경로 지연은 fresh-01의 wall_ms를 참고한다. 새 모델 계획을 부르려면 --plans를 생략하지만, 계획 출력이 달라질 수 있어 보관 계획 재현과 같은 조건으로 취급하지 않는다.
