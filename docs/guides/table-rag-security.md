# 현재 TableRAG 보안 비교 연결

기본 답변은 app/rag/prompts/answer.txt를 사용하고, 방어 답변은 같은 기본 지시에
app/generation/prompts/defense.txt 내용만 추가한다. 기존 기본 보안 지시를 제거하지 않는다.

질문별 DB·검색·도구 호출은 한 번만 수행한다. 최종 원문·SQL 결과·출처·retrieval_status를
고정해 두 답변에 전달한다. 방어 생성 때 다시 검색하지 않는다.
첫 답변 생성이 실패해도 검색이 성공했다면 같은 근거로 방어 버전을 시도한다.
검색 자체가 실패하면 방어 버전에서 새로 검색하지 않고 실패를 기록한다.

실제 실행은 별도 승인된 범위에서 아래 명령으로 한다. --help는 모델을 호출하지 않는다.

```powershell
python -m app.rag.security --security-file data/advanced/evaluation/security_tasks.json --data-directory artifacts/advanced-table-rag --experiment-id security-table-rag-01 --output-directory artifacts/security-table-rag-01
```

현재 TableRAG 경로를 사용한다. 기존 bootstrap용 run_security 명령과 구분한다.
질문 파일의 id·question만 실행에 사용하고 정답·공격 라벨·embedded_excerpt를 검색이나 생성에 넣지 않는다.
출력 디렉터리가 존재하면 실행 전에 거부한다. security.jsonl에 네 답변과 실제 모델 입력·출력·문맥·요청 ID·trace를,
manifest.json에 동일 입력·문맥·생성 조건 확인 결과를 남긴다. 서비스의 기존 요청 기록도 함께 저장된다.

두 답변의 서비스 시간은 base에 공유 검색 시간이 포함되므로 직접 속도 비교하지 않는다.
답변 생성 시간·토큰은 role=answer 시도끼리 확인하고 planner 사용량과 구분한다.
이 명령은 네 답변 생성·동일 조건 확인만 수행한다. 공격 노출·추종·업무 정답·과잉 거절 판정은 C 평가 단계다.
미노출·생성 실패를 방어 성공으로 처리하지 않는다. 같은 문맥 확인 불가와 실패도 원본 기록에 남긴다.

## 검사 범위

실제 모델 호출 없이 모의 검색·모의 HTTP로 검색 1회, 답변 2회, 질문 2개에서 답변 4개,
SQL·출처·검색 판정·전체 payload 보존, 실패 후 방어 실행, 검색 실패 시 재검색 금지,
현재 API 기본 동작과 프롬프트 변형의 요청 간 분리를 검사한다.
실제 Advanced 네 답변의 보안 효과는 실행·평가 전에는 확인된 것으로 표시하지 않는다.
