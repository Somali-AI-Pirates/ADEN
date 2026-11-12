# 기존 공식 B→C 인계 원본

각 조건 폴더에 manifest.json, request.json, http_response.json, runs_from_trace.jsonl,
trace_link.json, expected_context.json을 첨부했다. 총 12개 원본의 바이트와 파일 해시를 보존했다.
실행 기록에는 실제 문맥과 모델 입력·응답이 있다. 응답·문맥·request_id를 공통 기록 검사기로 대조했다.
모두 #53에서 생성한 이전 기록이다. #54의 새 답변은 없으며 이번 보완에서 새 모델을 호출하지 않았다.
개인 low/high·별도 개인 검증 원본은 포함하지 않았다. 충분 조건은 C 기준선과 문맥이 달라 같은 문맥 비교에서 제외한다.
이전 공식 인계의 인터넷 단절 실패와 반복 횟수 한계는 기존 PR #53 인계 댓글에 기록돼 있다.
