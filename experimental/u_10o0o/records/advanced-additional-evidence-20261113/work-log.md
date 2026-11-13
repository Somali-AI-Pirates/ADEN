# 작업 기록

## 2026-11-13 — 준비와 고정

- 사용자 요청: 평가 기준을 통일하고 ADD02·09 조기 종료, ADD06 근거 누락을 개선해 같은 추가30문항을 반복 비교. 기존 기준선 유지.
- 키·예산: 이 대화에서 기존 .env 키·한도 없음 확인. 답변 생성 제외, 최대 계획1,080회·검수180회. 코드에 키를 기록하지 않았다.
- 현재 작업 트리의 운영 코드·평가 코드·CI·CD에 다른 변경이 있어 수정하지 않았다. PR #62 실제 실행 커밋 92e8c038의 app/shared/configs를 전용 artifacts/runtime에 복사했다.
- 기존 additional.json의 질문·참고자료를 복사하고 별도 rubric.json으로 필수 요구를 고정했다. 원본 data 파일은 변경하지 않았다. ADD03·10의 질문 밖 부가 요구를 제외했고 ADD09의 신규 접수 적용 기준은 명시했다.
- A: 동결 제품 그래프 그대로. B: 동일 그래프에 종료 전 요구 확인 지침과 미노출 후보 서두(최대384토큰)만 추가. 원문 재임베딩·모델 교체·후보 확대·최종 예산 변경·문항별 정답 페이지 보충 없음.
- A/B × 3회, 질문별 실행 순서 교대, 동시2요청. 새 모델 응답과 실제 검색을 매번 실행한다. 첫 문항 양쪽 실행은 본실험의 첫 반복에 포함하며 중복 재호출하지 않는다.
- 공개 기록은 이 폴더와 개인 실험 패키지, 질문·정답·원문·API 입출력은 ignored artifacts/u_10o0o/advanced-additional-evidence-20261113에 저장한다.
- Langfuse·LangSmith 외부 추적은 사용하지 않는다. 모델 API 전송과 로컬 원문 기록은 사용자 승인 실험의 범위다.

## 구현 자체 검토

사이드 대화 지침으로 독립 에이전트는 사용하지 않았다. 독립 리뷰를 수행한 것으로 표현하지 않는다.

1. 데이터·평가 경계: 실행기는 질문 ID와 질문만 읽으며 평가 기준은 실행 전 고정한 파일의 해시만 확인한다. 평가기는 180개 검색 결과 저장이 끝난 뒤 별도 실행한다. B는 실제 검색 후보에 있는 출처만 미리보기하고, 관측하지 않은 원문·정답 출처를 검색 입력에 추가하지 않는다.
2. 상태·기존 작업 경계: 제품 코드를 수정하거나 git checkout하지 않는다. 실험 전용 경로에 원자적으로 결과를 저장하고 실행 시작 표식을 남긴다. 미완료 유료 호출은 자동 재시도하지 않는다. B 지침은 실험용 모델 객체에만 연결한다. 평가 모델에는 A/B 이름·종료 요약을 주지 않는다.

## 준비 검사

- 개인 Ruff: 통과. 개인 파일만 format 적용.
- 집중 pytest: 4개 통과. 발견하지 않은 출처 차단, 미열람 미리보기, 입력 메시지 불변, 검수 요구 ID/인용 검증, book_id+page 구분.
- prepare CLI: 원본 질문30개와 동결 런타임·평가 기준 저장 완료.
- 실제 연결 표본: ADD01 A/B 각각 완료, 각각 계획2회, 답변 생성0회. 결과를 본 뒤 개선 지침을 수정하지 않았다.

## 재현

저장소 루트에서 실행한다. prepare는 기존 폴더 덮어쓰기를 거절한다. 이미 저장된 실행은 동일 프로토콜일 때만 재사용한다.

```bash
.venv/bin/python -B -m experimental.u_10o0o.additional_evidence_ab.run prepare
.venv/bin/python -B -u -m experimental.u_10o0o.additional_evidence_ab.run run
.venv/bin/python -B -u -m experimental.u_10o0o.additional_evidence_ab.evaluate review
.venv/bin/python -B -m experimental.u_10o0o.additional_evidence_ab.evaluate summarize
.venv/bin/ruff check --no-force-exclude experimental/u_10o0o/additional_evidence_ab
.venv/bin/pytest -q -p no:cacheprovider experimental/u_10o0o/additional_evidence_ab/test_experiment.py
```

실제 실행 로그: artifacts/u_10o0o/advanced-additional-evidence-20261113/{smoke,run,review}.log.
최종 결과와 검증은 완료 후 report.md와 verification.json에 기록한다.

## 2026-11-13 — 실행 완료와 판정

- A/B×3회,180회 실제 검색 종료. 자동 재시도·답변 생성 없음. API 계획380회·고유 문맥 검수53회, 합계433회. 모델 오류·시간 초과 없음.
- 초기90쌍은 근거 내용이 같다. 하이브리드66쌍의 순서 일치, DB24쌍의 차이는 실행 시간 필드뿐이다. 실행·런타임·질문·평가 기준·코퍼스 지문을 사후 대조했다.
- 자동 검수의 인용 전사 오류5개 문맥을 원문으로 교정했다. ADD06에는 같은 표의 이메일 분리와 최초 제출/접수 확인 범위 혼동이 있어 모든 대응 실행에 같은 기준을 적용했다. 원본 평가와 별도 수정 기록을 모두 보존했다.
- 검수 도중의 잠정 집계84/87 대84/87은 확정 결과가 아니다. B 2회 ADD06에서 접수 확인 연락처를 최초 제출 규정으로 인정한 범위 오류를 바로잡은 뒤 최종은 A84/87, B83/87이다. 평가지표·요구사항·검색 결과는 변경하지 않았다.
- ADD09: A1/3→B3/3. 후보 미리보기 이후 낮은 순위의 원문을 읽고 선택했다. ADD02: 2/3→2/3. ADD06: 3/3→0/3. B의 두 실행은 처음부터 있던 순중량 정의를 최종에서 제외했고, 다른 한 실행은 최초 제출 규정을 검색 후보에 확보하지 못했다.
- 기준선 유지·B 미채택. B는 입력 토큰56.8%·관측 p95 27.0% 증가, 전체 근거 충족 개선 없음. 이 결과를 더 좋게 만들기 위한 프롬프트 조정이나 추가 유료 재실행은 하지 않았다.
- 공통 Ruff 통과; 공통 pytest663개와 하위 검사11개 통과. Qdrant 로컬 exact 옵션 경고1개. 개인 Ruff와pytest4개 통과, CLI 도움말 확인. 결과 보고서 작성 후 파일·링크·집계·비밀값 노출 여부를 자체 점검한다.
- Git 상태에서 이번 산출물은 개인 실험 패키지와 기록 폴더다. 다른 작업의 변경은 건드리지 않았다. 커밋·푸시 없음.

## 저장 결과를 다시 집계하는 방법

이미 완료된 검색과 검수를 새로 호출하지 않고 다음 명령으로 같은 판정을 다시 집계한다. 로컬 `adjudications.json`도 필요하며 원본 검수 파일과 함께 보관한다.

```bash
.venv/bin/python -B -m experimental.u_10o0o.additional_evidence_ab.evaluate summarize
```

`summary.json`은 반복별, `aggregate.json`은 구성별 합산, `per-question.json`은 문항별3회 성공 횟수다. `audit.json`은 입력 고정과 실행 경계, `stage-diagnosis.json`은 진단18개 실행, `review-audit.json`은 검수 수정 요약이다. 합산은29문항의3회 반복이며87개의 독립 문항으로 해석하지 않는다.

## 최종 산출물 점검

고정 지문 재검사, 최종53개 검수 스키마·연속 인용 검사, 구성별 합산 일치, 문서 로컬 링크와 키 형태 문자열 검사, git diff --check를 통과했다. 원문 실행 기록이 Git ignored 경로임을 확인했다. 점검 중 다른 작업의 web/next-env.d.ts 변경이 새로 보였으며 수정하거나 되돌리지 않았다.
