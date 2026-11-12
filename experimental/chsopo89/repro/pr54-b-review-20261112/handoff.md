# C 인계 — 실행 기준 PR #54

## 코드·설정

재평가용 코드는 main `284004bb18c8ca8408973acc51985bd2851651ed`(#54)이다.
#53에서 생성 프롬프트·검산 미완료 표기 검사·실패 로그를 고쳤고 #54에서 본문의 부정 표현 처리를 보완했다.

#53 변경 파일: `app/api/errors.py`, `app/api/routes.py`, `app/generation/chain.py`,
`app/generation/prompts/shipping_qa.txt`, `docs/guides/generation-checks.md`,
`tests/api/test_error_logging.py`, `tests/generation/test_incomplete_consistency.py`.

#54 생성 관련 변경: `chain.py`, 해당 회귀 테스트, 생성 검증 안내.
#54에서 생성 프롬프트는 추가 변경되지 않았다. `configs/advanced.yaml`, `judge.txt`, `graders.py`는 #52 대비 동일하다.
`app/evaluation` 전체는 #54의 HTTP 연결 추가 등으로 달라졌으므로 동일하다고 간주하지 않는다.
파일 해시는 manifest에 있다.

## 사용 문항

직전 #53 수정·확인에는 Advanced AS-DEV19의 근거 충분/부족 변형을 사용했다.
과거 B 답변 규칙 수정·회귀에는 Advanced M01(실제 서명자), S09(내부 출력 설명),
S07(명시적 서명자 회귀), M19(날짜 산입 회귀)를 사용했다.
b-used-questions.csv에 실제 사용한 다섯 문항을 별도로 정리했다. question-usage-proof.json에는 변형 메타데이터와 원본 해시가 있다.
현재 #54 모의 테스트는 DEMO 합성 입력으로, 실제 질문의 새 생성으로 세지 않는다.
공유 브랜치의 바탕은 #55·#56 병합 뒤 main이지만, B 생성 코드·프롬프트·해당 테스트는 #54와 동일하다.
과거 기록의 난이도별 ID는 used-questions.csv, 위치는 usage-evidence.csv에 구분했다.
답변 보관에는 복사·재사용이 포함되므로 모두 직접 생성 또는 특정 수정 사용이라고 단정하지 않는다.
미확정 난이도·사용 목적은 그대로 표시했다. 최종 확인용 세트 확정 목록이 아니다.

## 기존 답변 자료

reference-traces.json은 이전 #53 생성 답변의 request_id·팀 trace·원본 응답 해시를 연결한다.
공식 B→C 인계용 기존 원본을 raw/ 두 조건 폴더에 각 6개씩 첨부했다.
실제 문맥·모델 입력이 있는 실행 기록을 normalize_run_record로 확인하고 HTTP 응답과 대조했다.
원본 바이트와 해시를 보존했다. 개인 low/high·별도 개인 검증 원본은 복사하지 않았다.
기존 전달 댓글에도 답변·출처·실행 정보가 있다. HTTP 원본 파일의 바이트 해시와 댓글·trace 연결은 JSON을 참고한다.
원격 trace를 이번에 다시 조회하지 않았으며 현재 접근 가능성은 미확인이다.
부족 조건 문맥은 C 문서의 기준 접두사와 같지만, 충분 조건은 달라 같은 문맥의 전후 비교에서 제외한다.
두 답변 모두 #54 적용 후 생성 결과로 사용하지 않는다.

## 후속 평가

C는 합의한 고정 근거와 #54 코드로 새 답변을 생성하고 HTTP 기록→http_records→regrade→fix_compare로 진행한다.
서버와 HTTP 클라이언트 버전·문맥 해시·채점 조건을 맞춘다.
반복 채점, 최종 세트, v4, 수정 전 HTTP 기준선 추가 생성 여부는 팀 결정 사항으로 남긴다.
B는 이번 공유에서 실제 모델 생성·Judge·RAGAS·점수 게시를 실행하지 않았다.
