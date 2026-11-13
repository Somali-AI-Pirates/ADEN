# Damsebi 평가 실행 계획

[평가 가이드의 팀 공식 실행](../../docs/guides/c-module-guide.md#팀-공식-실행) 1단계 기록이다. 계획이 바뀌면 이 파일을 고친다.

- 작성: 2026-11-09
- 담당: Damsebi
- 목적: RAGAS Faithfulness와 Judge를 실제 모델로 검증해 고정하고, 채점 기준을 평가하면서 다듬는다. 기준 변경 이력은 [채점 기준 이력](../../docs/guides/judge-rubric.md)에 남긴다.

## 실행 조건

| 항목 | 내용 |
| --- | --- |
| 서비스 설정 | `configs/basic.yaml` (`retrieval.k`=8, 문맥 4,000토큰). 기본 설정은 PR #40 이후 `configs/advanced.yaml`(8,000토큰)이므로 `--config`를 항상 명시한다 |
| 어댑터 | 표준 `app.service.bootstrap:query_adapter`. 색인은 API와 같은 환경변수로 연결한다: `ADEN_QDRANT_CLIENT=experimental.Damsebi.evidence_sources:memory_copy`(Cloud 컬렉션의 메모리 복사, 실제 검색) 또는 `ADEN_EVIDENCE_PROVIDER=experimental.Damsebi.evidence_sources:stored_evidence`(저장 후보 재생). PR #40 통합 전 기록은 Qdrant Cloud 직접, 통합 직후 기록은 개인 어댑터(지금은 없음)로 실행했다 |
| 검색 방식 | naive, hybrid, hybrid_rerank |
| Judge | `gpt-6-luna`, 기준은 `judge.txt` 현재 버전(v2) |
| Faithfulness | RAGAS Faithfulness, 평가 모델 `gpt-6-luna`, 출력 한도 8,192토큰 |
| 질문·정답 | `data/basic/evaluation/custom_questions.json`·`custom.json`(40문항). 제공 Basic 평가 파일은 이 PC에 없다 |
| 색인 | Qdrant `basic_multilingual-e5-small_p800`(청크 620개, SB01~SB15) |
| Langfuse | `https://cloud.langfuse.com`(EU), 프로젝트 `Somali-AI-Pirates` |
| 가리기 | Judge 입력에 검색 방식 이름을 넣지 않고 답변 하나씩 따로 채점한다(현재 코드 동작) |
| 질문 구분 | 개발용 8문항: S01, S04, S09, S12, M01, M03, M07, M16. 확인용: 나머지 32문항. 확인용은 기준·프롬프트 조정에 쓰지 않는다 |

생성 모델과 평가 모델이 같아 자기 답변을 후하게 볼 수 있다. 세 방식에 같은 조건이라 방식 간 비교는 유지되지만 절대 점수는 높게 나올 수 있으므로 결과에 함께 적는다.

## 예상 호출 수

질문 N개 기준이다. 단가는 확인하지 않았으며 실제 토큰은 결과의 `summary.json`(서비스·`grading`)으로 확인한다.

| 단계 | 호출 |
| --- | --- |
| 서비스 실행 | 3N 요청, 요청당 생성 호출 최대 2회(`generation_max_attempts`) |
| Judge | 3N |
| RAGAS Faithfulness | 사실 주장이 있는 답변마다 주장 추출·검증 호출 |
| 기준 버전별 재채점 | Judge 3N (답변 재생성 없음) |

## 한도와 중단 기준

- 비용 한도: 두지 않는다(2026-11-09 팀 합의).
- 중단: 1문항 점검에서 Judge 파싱 실패나 RAGAS 평가 불가가 나오면 원인을 확인한 뒤 진행한다. 평가 호출이 연속 3회 실패하면 멈추고 부분 결과를 확인한다.

## 결과 위치

- 전체 기록: `artifacts/experiments/<실험 ID>/` (Git에 올리지 않음)
- 점검·개발용 비교 보고서: `experimental/Damsebi/reports/<실험 ID>/` (`--reports-dir experimental/Damsebi/reports`)
- 공식 비교·제출: `reports/<실험 ID>/`
- 기준 변경과 점수 변화: [채점 기준 이력](../../docs/guides/judge-rubric.md)
- 날짜별 결과: [2026-11-10 Langfuse 검증·1문항 점검·v1·v2 비교](results-2026-11-10.md), [2026-11-10 PR #40 통합과 실제 검증](pr40-integration-2026-11-10.md), [2026-11-10 공통 실행 경로와 평가 세트별 점수 게시](common-path-2026-11-10.md), [2026-11-11 Judge v3·B/C 통합 검증](judge-v3-bc-2026-11-11.md), [2026-11-12 c4 검수·B 수정 재평가 준비·HTTP 채점 연결](c-followup-2026-11-12.md), [2026-11-12 TableRAG additional 최종 평가(3회 채점 다수결)](table-rag-additional-2026-11-12.md), [2026-11-13 필수 사실 근거 평가(v4·검수 반영)](reports/table-rag-additional-20261112/fact-support-v4-report.md)·[최종 보고서 PDF](reports/table-rag-additional-20261112/ADEN_additional_eval_report_20261113.pdf)
- 도구: `check_langfuse.py`(저장된 trace의 관측·구간·내용과 게시 점수의 수신·중복 확인), `compare_scores.py`(재채점 결과 나란히 비교), `evidence_sources.py`(공통 자원 로더에 연결하는 검증용 색인 복사·저장 후보 재생), `republish_scores.py`(저장된 채점 결과를 다시 채점하지 않고 재게시), `fix_compare.py`(수정 전후 채점 결과를 같은 비교 항목으로 정리, 반복 채점 다수결과 사람 검수 목록, `--table-rag-run`으로 TableRAG 채점 폴더 지원), `strip_run_text.py`(TableRAG 실행 기록에서 원본 문서 본문을 뺀 공유용 상세 로그), `fact_support.py`·`fact-support/prompt-v1.txt`(필수 사실 근거 평가: 질문·필수 사실·후보 또는 최종 문맥으로 사실별 충족·출처·구절·사유 판정, 답변 제외), `fact_support_report.py`(근거 평가 집계: 필수 사실 충족률·후보/최종 전체 근거 충족률·실패 원인 분류, Judge 다수결 연결), `fact-support/required-facts-additional-v{1,2,3,4}.json`(필수 사실 버전과 판정 경로), `fact-support/review-overrides.json`(원래 판정 위에 겹쳐 적용하는 검수 판정), `bfix-baseline-20261112.sha256`(B 수정 전 기준선 해시)

## 진행 상황

- [x] 재채점 기능 (#34)
- [x] 채점 기준 v2와 이력 문서 (#35)
- [x] RAGAS 설치·버전 고정과 연결 확인 (2026-11-09)
  - `eval` 추가 설치: ragas 0.4.3, langchain-community 0.4.1(0.4.2는 ragas가 쓰는 모듈 제거)
  - `gpt-6-luna` 실제 호출: 주장 2개 중 1개만 문맥과 맞는 답변 0.5, 모두 맞는 답변 1.0. 각 5회 반복 시 같은 값, 호출당 약 5초
  - RAGAS는 토큰 사용량을 반환하지 않아 Faithfulness 평가 토큰은 `null`로 남는다
- [x] Langfuse 실제 연결 (2026-11-10): 합성 1건과 실제 27건을 API로 조회해 필수 관측·입출력·문맥·토큰 확인. 독립 `--verify-langfuse`와 `regrade`가 `.env`를 읽지 않던 문제 수정
- [x] 1문항 점검 (2026-11-10, S01): 형식·Judge 파싱·시간·토큰 정상. RAGAS는 기본 출력 한도 1,024에서 세 건 모두 평가 불가였고 8,192로 고정해 해결. 같은 답변 3회 재채점에서 Judge 판정 동일, Faithfulness 0.04 이내 변동
- [x] 개발용 8문항 세 방식 실행, v1·v2 재채점 (2026-11-10): v2는 묻지 않은 부가 정보로 인한 사실 0점을 없앴고(S01·S09) 부분 답변의 판단을 엄격히 봤다(M01). 조건·근거는 v2 반복 채점 차이(96칸 중 7~12칸)와 구분되지 않는다
- [x] PR #40(B·C 근거 주입, 단계별 추적, 점수 게시) 통합과 실제 검증 (2026-11-10, S01): 후보 재생·최종 문맥 주입·메모리 Qdrant 실제 검색 세 경로, 관측의 상하 관계와 실제 구간, 점수 185개 수신, 재게시 중복 없음, v1·v2 분리 보존, 평가 불가 미게시 확인. 메모리 복사 색인의 후보가 Cloud 실행과 같음
- [x] 공통 실행 경로와 평가 세트 (2026-11-10, S01): API와 평가 CLI가 같은 환경변수로 같은 설정·색인을 쓰고 질문·후보·최종 문맥·해시가 일치. 점수 이름을 `<평가 세트>.<지표>`로 나눠 v1·v2·반복 회차의 집계가 섞이지 않음을 서버 조회로 확인. 재채점은 게시 전에 결과를 저장
- [x] c4 판정 불일치 검수와 반복 채점 처리 제안, HTTP 답변 채점 연결(`http_records`), B 수정 전 기준선 고정 (2026-11-12)
- [ ] B 수정본 수령 후 재평가: [실행 순서](c-followup-2026-11-12.md#실행-순서-b-수정본-수령-후)
- [ ] Advanced 전환: A의 색인 적재 경로 연결 후 Advanced 질문으로 1문항 점검, 세 방식 비교
- [ ] v2 조건·근거 채점 안정화: 반복 채점 다수결 또는 기준 보완([열린 결정](../../docs/guides/judge-rubric.md#열린-결정))
- [ ] 기준 확정 후 전체 재채점, 확인용 질문 확인, 대표 사례 사람 검수

## 확인 필요

- 공식 Basic 질문·정답 파일 위치(저장소에는 `data/basic/evaluation/custom.json`만 있음). 현재는 custom 40문항으로 진행
- Langfuse 프로젝트의 멤버·접근 범위와 실제 보존 기간(프로젝트 키로는 조회 불가, 보존 기간 미설정)
- `artifacts/basic/index_manifest.json`이 이 PC에 없어 색인의 청크 파일 해시를 대조하지 못함
- Advanced 질문·정답 파일과 색인(`advanced_snowflake_ko_pages`)의 위치·적재 방식. 이 PC에는 `data/advanced/evaluation/custom*.json`만 있다
- Langfuse 화면에서 평가 세트별 점수 선택·비교가 기대대로 보이는지(API의 이름별 조회와 집계로만 확인했다)
- 최종 비교에 쓸 평가 세트 이름(기준 확정 후 한 세트로 전체 지표군을 게시)
