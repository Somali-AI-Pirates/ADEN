# 구현 계약

2026-11-10. 사용자 승인 계획을 구현한다. 원본 Advanced PDF에서 새 SQLite·벡터와 새 개발60/검증60문항을 만든다. A=원본페이지, B=사건기록/공통규칙 단위, C=B+명시 코드 관계 보충. 모두 BM25+dense50씩, RRF60 5:5, 최대20개를 KURE 한 번, 원본 최대8쪽·8000토큰. 품질은 GPU float32, 지연은 CPU4스레드의 사전고정30문항×3회로 분리한다.

기존 운영 API·기본 실험·다른 작성자의 변경은 보존한다. 과거 결과·정제산출물·질문·참고근거·사건별 보정 규칙을 읽거나 입력으로 쓰지 않는다. 재사용 범위는 일반 layout 파서, BM25 계산, 원문 context 포장이다. 모델은 로컬 pinned Snowflake-ko/KURE nano. 새 유료 API 호출·답변 생성·모델 교체·자율 에이전트 검색·질문 재작성·두 번째 리랭킹은 범위 밖이다. 미인식 구조와 범위는 원문으로 보존하고 잘못된 단일 사건 확정을 하지 않는다.

아래 모듈 소유권으로 구현한다: corpus.py/파싱테스트=원본 처리 에이전트, evaluation.py/reporting.py/채점테스트=채점 에이전트, 로컬 evaluation-v1=독립 문항 작성자, 나머지 CLI/검색/모델/실행은 주 에이전트. 문항 검수자는 작성자와 분리한다. 검색 결과를 전부 저장한 뒤 독립 score 명령이 참고근거를 읽는다. 검증 점수를 본 뒤 규칙을 수정하지 않는다.

SQLite 계약:
- pages(source_id TEXT PRIMARY KEY, book_id TEXT, file_name TEXT, page INTEGER, text TEXT, heading TEXT, pdf_sha256 TEXT)
- units(unit_id TEXT PRIMARY KEY, arm TEXT [A|B], kind TEXT, text TEXT, scope_key TEXT)
- occurrences(unit_id TEXT, source_id TEXT, line_start INTEGER, line_end INTEGER, case_id TEXT, role TEXT, identifiers_json TEXT)
- code_links(code_norm TEXT, code_type TEXT, case_id TEXT, source_id TEXT, line_start INTEGER, line_end INTEGER, relation TEXT)
텍스트·출처는로컬 artifacts에만 저장. corpus.build_corpus(source_dir, output_dir, log) → manifest dict; corpus.sqlite3와 corpus-manifest.json 생성. 모든 원문 블록의 coverage를 검사한다. 명시 관계의 양끝·발견범위가 확인되지 않은 code_links는 생성하지 않는다.

평가 계약:
questions.json=[{"id":str,"question":str}], references.json={id:{"split":"development"|"validation","category":"exact"|"variant"|"paraphrase"|"comparison"|"mixed"|"ambiguous","requirements":[{"primary":[{"book_id":str,"page":int,"quote":str}],"alternatives":[[같은citation]]}],"expected_cases":[str],"resolution_expected":"unique"|"multiple_targets"|"ambiguous"|"conflict"|"not_applicable"}}
manifest.json은 split별 id, category별 id, 사전고정 CPU timing_ids30개(검증 유형별5개), 입력/원본 지문·검수기록을 담는다. ambiguity 유형은 검색 정답률에 넣지 않고 별도 보고한다. 원문 인용과 민감 질문은 공유 기록에 복사하지 않는다.

검색 trace 계약:
{id,arm,question_sha256,pool:[{unit_id,source_id,book_id,page,score}],ranked:[동일항목],final_pages:[{source_id,book_id,page}],context_sha256,context_tokens,excluded_sources,relation:{status,cases,matched_codes,proposed_units},timing_ms:{query_embedding,bm25,dense,relation,rerank,restore,total},work:{rerank_pairs,rerank_tokens,rerank_truncated,query_tokens}}
원문 문맥은 별도 contexts.jsonl에 {id,arm,pages:[{book_id,page,text}]} 저장. score 명령은 frozen corpus source text에서 인용을 대조하고 실제 pool/final에 포함한 출처만 채점한다.

관계 지원 경계: 미등록 코드·한 코드의 복수 사건·2건 초과·제외 표현은 B로 복귀한다. 서로 다른 사건의 코드를 결합할 때 명시적 비교/각각/차이/대비/두 사건 표현이 없으면 충돌로 보류한다. 이는 검증 전 고정한 보수적 질의 지원 범위이며, 평가 문항을 이 규칙에 맞춰 작성하지 않는다.

실행 중단 경계: 벡터 생성만 완료 행 체크포인트로 재개한다. corpus·설정·행 순서·임베딩 코드·모델 자산 지문이 같을 때만 이어간다. retrieve/timing/score는 새 출력 디렉터리만 허용하며, 중단된 결과를 보존한 뒤 새 디렉터리에서 해당 단계를 처음부터 다시 실행한다. 과거 실행의 일부 결과를 새 실행에 붙이지 않는다. CPU는 CLI의 BLAS/MKL/OMP 변수와 런타임 threadpool 제한으로 4개를 강제한다.

C의 식별 지원 범위는 원문 필드가 명시하는 사건·예약·계약·고유 장비·증빙/문서·청구/정산 기록 코드다. generic 번호나 참조라는 이름만으로 관계를 만들지 않는다. 당사자·회사·화주·조직 식별자와 종류/유형/분류값은 사건 확정 코드로 사용하지 않는다. 명시 사건 또는 예약 필드에서 사건 anchor가 확인돼야 하며, 본문 참고 문맥에 사건 코드가 하나 등장했다는 이유만으로 연결하지 않는다. 실제 허용/제외 필드 패턴과 원본 스캔 통계는 corpus manifest에 기록한다. 이는 검증 점수를 보기 전에 확정한 지원 경계다.

원본 기록 제목 범위(품질 실행 전 원본 전수 감사로 확정):
- HG: 첫 제목 항목이 HG 연도·일련번호의 완전 코드이고 네 slash 항목의 마지막이 검증된 다섯 기록 역할인 페이지. 원본 전체에서 고유 사건 코드가 그 하나이며 본문 명시 사건/예약 필드와 충돌하지 않아야 한다.
- HA/SW/DL: pipe 일반 문서가 아닌 typed heading과 다음 블록의 사건 코드 제목을 가진 별도 양식. 역할/제목은 원문을 보존하며 페이지의 고유 사건과 명시 필드가 일치해야 한다.
- 위 기록 페이지는 표 필드와 원문 문단을 모은 B 기록 단위 하나로 만든다. 전체 원본 페이지 및 개별 링크의 근거 행 범위를 보존한다. 제목의 라벨 없는 두 번째 코드를 별칭으로 추정하지 않는다.
- 일반 pipe 문서는 같은 PDF에 있더라도 위 범위를 상속하지 않는다. 표의 명시 사건 행 또는 원문 단위로 유지한다. 파일 범위나 특정 사건 ID 목록으로 분기하지 않는다.
- 명시 record metadata 안의 순수 역할 필드(예: 장비·계약)와 순수 역할 표 열(예: 증빙)은 값이 완전 코드인 경우 번호/코드 접미어 없이도 검증된 관계를 만들 수 있다. 일반 서술의 코드 동시등장과 구분한다.
