# 검색 실험 기록

실험 결과 요약과 채택 근거를 조회하는 시작점이다. 모델 선택 및 채택 결정은 [선택 기록](decisions.md)에 기록한다.

현재 Advanced 개인 기준선은 **제목 보너스 Bigram-ID BM25와 Snowflake-ko의 5:5 RRF, KURE nano 상위 3쪽, HG 및 원문 조회, 복원 원본 최대 8쪽·8,000토큰**이다. 이전 BM25 기준선은 `--compare`로 함께 평가한다. [기준선 선택 기록](decisions.md#2026-11-09--55-하이브리드를-개인-advanced-기준선에-채택) · [현재 실행 방법](../README.md#현재-advanced-구성)

[Advanced 검색 실험 종합 보고서 PDF](advanced-curation/advanced-retrieval-report-20261109.pdf)에서 초기 검색 실험, 명시·간접 HG 조회 예시, 동료 설정 비교와 최종 선정 근거를 볼 수 있다. [추가 실험 후 최종 결정](decisions.md#2026-11-09--추가-실험-후-개인-advanced-기준선-확정)에 기존 검색과 HG 조회를 유지한 근거를 기록했다.

## 보관 범위

- 기록은 Basic·Advanced PDF 페이지 검색, 임베딩 모델 비교, BM25 토크나이저 비교, RRF 하이브리드 실험 순서로 정리했다.
- `retrieval_evaluations/` 아래 보고서와 문항별 검색 결과, 설정·입력 해시·실행 시간은 기존 실험 산출물에서 복사했다. 원래 산출물은 로컬 `artifacts/u_10o0o/`에 보존했다.
- JSON 사본에는 문항 ID, 검색된 문서·쪽·순위·점수, 집계 지표, 실행 설정과 입력 SHA-256 지문이 있다. 원 질문·답변 텍스트와 정답 근거·매칭 쪽 목록은 평가 정답을 배포하지 않도록 제외했다. 집계 보고서의 수치는 기존 실행 결과 그대로 보존했다.
- PDF 캐시와 벡터 산출물 `.npy`·`.npz`는 로컬 실행 자원이다. 실험을 다시 실행할 때는 [실행 안내](../README.md#실행)에 따라 준비한다.

## Basic 페이지 검색과 임베딩 비교

처음 Basic PDF 360쪽으로 검색을 실험하고 CPU 임베딩 모델을 비교했다.

- [초기 CPU 비교 보고서](retrieval_evaluations/cpu-comparison-20261108-171530-824848/report.md)는 BAAI BGE-M3, KURE-v1, GTE multilingual base 결과를 정리한다.
- [후보 모델 비교 보고서](retrieval_evaluations/cpu-candidates-20261108-173451/report.md)는 Snowflake-ko, KURE-v2·v3, EmbeddingGemma, Kanana, Bekko를 비교한다. KURE-v2의 token-vector 검색 결과도 포함한다.
- [Basic BGE-M3 실행](retrieval_evaluations/BAAI-bge-m3-page-top3-20261108-170506-582126/report.md)과 [첫 Snowflake-ko 실행](retrieval_evaluations/dragonkue-snowflake-arctic-embed-l-v2.0-ko-page-top3-20261108-182440-625847/report.md)에는 문항별 결과가 있다.

## Advanced 페이지 검색 기준선

Advanced 원본 PDF 120개에서 복원한 18,000쪽과 일반 평가 60문항으로 비교했다. 근거 페이지가 있는 58문항을 채점했다.

- [기준선 비교 보고서](retrieval_evaluations/advanced-baselines.md)는 Snowflake-ko, BM25, Bigram·Kiwi RRF 하이브리드 결과를 한 표에 모았다.
- [Snowflake-ko 실행](retrieval_evaluations/advanced-dragonkue-snowflake-arctic-embed-l-v2.0-ko-page-top3-20261108-185720-335334/report.md)과 [실행 자원·시간 기록](retrieval_evaluations/advanced-dragonkue-snowflake-arctic-embed-l-v2.0-ko-page-top3-20261108-185720-335334/runtime.json)에서 조건·결과를 확인한다.
- [BM25 토크나이저 비교](retrieval_evaluations/advanced-bm25-tokenizers.md)에는 Bigram과 Kiwi 단독 결과가 있다. 세부 실행은 [Bigram BM25](retrieval_evaluations/advanced-bm25-bigram-page-top3-20261108-194056-559597/report.md), [Kiwi BM25](retrieval_evaluations/advanced-bm25-kiwi-page-top3-20261108-194207-443549/report.md)에 저장했다.
- 하이브리드의 가중치별 보고서와 JSON 결과는 [초기 Bigram RRF](retrieval_evaluations/advanced-hybrid-rrf-page-top3-20261108-191947-192465/bm25-7-dense-3/report.md), [Bigram 재실행](retrieval_evaluations/advanced-hybrid-rrf-bigram-page-top3-20261108-195536-027821/bm25-7-dense-3/report.md), [Kiwi RRF](retrieval_evaluations/advanced-hybrid-rrf-kiwi-page-top3-20261108-195419-857080/bm25-6-dense-4/report.md)에서 시작한다. 각 실행 폴더에는 다섯 비율의 하위 폴더가 있다.

## Advanced 리랭커 비교

- [CPU 리랭커 비교](advanced-reranking-20261108-201548-983299/report.md)는 같은 RRF Top 20으로 BAAI BGE, KURE nano, Horizon small을 평가한 결과다. 기준선과 후보 커버리지, 최종 Top 3 지표, 지연, 입력 길이, 설정과 진행 로그를 확인할 수 있다.
- [최종 결과 수 비교](advanced-reranking-cutoffs-20261108/report.md)는 Kiwi 단독, RRF, 리랭커 세 모델의 Hit·Recall·Precision을 @3·5·8·10·15·20에서 비교한다.

## Advanced 소제목·문단 청킹 비교

- [페이지와 구조 청크 비교](advanced-structure-chunking-20261108-215143-955031/report.md)는 같은 후보 20개·최종 3개에서 원본 출처 페이지 지표와 4,000토큰 문맥의 실제 인용문 포함률을 비교한다.

## Advanced Kiwi 식별자 토큰 비교

- [식별자 토큰 비교](advanced-kiwi-identifiers-20261108-220437-379358/report.md)는 기존 Kiwi와 하이픈 식별자를 한 토큰으로 보존한 변형을 비교한다. 일반 평가 60문항과 식별자 단독 BM25 진단 30개를 구분해 기록했다.

## Advanced 문맥 확장 답변 비교

- [KURE Top 3와 RRF 2쪽 추가 비교](advanced-context-expansion-20261108-223239-683500/report.md)는 같은 4,000토큰 상한에서 실제 답변의 사실·조건·근거·판단, 생성 입력 토큰과 p95 지연을 비교한다. 생성과 채점에 `gpt-6-luna`를 사용했다.

## Advanced 변수 분리와 동료 재벤치

- [변수별 결과와 선택 근거](advanced-factor-ablation-20261108/report.md)는 후보 1,812개 조합, 리랭킹과 문맥 예산 비교, 최종 순서 10개 변형, 추가 280·공식 58문항의 개인·동료 비교를 정리한다.

## Advanced 검색·문맥 패턴 비교

- 직접 실행하는 [Advanced 기본 검색 명령](../README.md#현재-advanced-구성)은 공식 평가 60문항과 블라인드 holdout 280문항, 총 340문항을 사용한다. 별도의 전 페이지 증강 6,500문항은 보관된 `advanced-industry-patterns-20261108/full-coverage/` 결과에서 확인한다. 두 실행은 입력 세트와 결과 폴더가 다르다.
- [패턴 비교 보고서](advanced-industry-patterns-20261108/report.md)는 제목 보너스, 표 행 문맥, 근거 역할·보강 검색, 유효 시점 검증을 기준선과 비교한다. 기존 340문항, 파일럿 40문항, 전 페이지 증강 6,500문항의 검색 결과와 별도 답변 표본을 구분한다. [조사 출처와 가설](advanced-industry-patterns-20261108/research.md)
- [동료 ID 연결 재벤치](advanced-industry-patterns-20261108/peer-comparison/report.md)는 질문 분해와 근거 집합 선택을 끈 설정을 동일한 증강 6,500문항에서 비교한다. 패턴 비교 보고서의 전체 지표 표와 유형별 표에 함께 실었다.

## Advanced 대상별 검색과 평가 근거 점검

- [데이터 감사와 검색 비교](advanced-target-retrieval-20261109/report.md)에 동료 합성 세트의 근거 재검증, 대상별 필드 조회와 조건부 벡터 검색 비교, 사람 검수 절차를 정리했다.
- [업무 상황 질문 20개](advanced-workflow-20261109/report.md)는 질문 원형을 먼저 고정하고 원본에서 근거를 판정한 별도 검색 비교다. 기존 개인·동료 평가의 용도도 함께 정리했다.
- [두 사건 문맥 배치](advanced-pair-context-20261109/report.md)는 후보 20쪽과 최종 8쪽·4,000토큰을 고정한 비교다. 업무형 20문항의 기존 기준선·동료 결과와 블라인드 300문항 회귀 확인을 기록했다.
- [두 사건 문맥 배치 별도 검증](advanced-pair-holdout-20261109/report.md)은 새 두 사건 질문 24개와 대조 질문 6개에서 배치 효과와 기본 설정 채택 여부를 확인한다.
- [질문 표현 차이 검색 실험](advanced-vocabulary-20261109/report.md)은 같은 사건의 원문식·일상식 쌍, 요청 항목을 바꾼 대조 질문, 기존 번호 질문에서 별칭 확장과 벡터 후보 구성의 효과를 비교한다.
- [Advanced 최종 8쪽 순위 비교](advanced-rank-policy-8000-20261109/report.md)는 최대 8,000토큰에서 KURE 상위 3쪽 뒤의 RRF·BM25 순서, KURE 전체 순서, 개인 기준선과 동료 무 API 설정을 비교한다. 기존 268문항, 공식 60문항, 동료 304문항을 별도 집계했다.
- [첫 검색 결과의 사건 키로 후보 보충](advanced-seed-lookup-20261109/report.md)은 공식 60문항·4문서 합성 78문항·번호 회귀 160문항에서 후보 회수와 최종 근거를 비교한다.
- [대상과 요청 기록의 단계별 진단](advanced-target-diagnosis-20261109/report.md)은 보관 두 사건 문항과 독립 에이전트가 작성한 새 업무형 표본에서 대상·역할, 후보 20쪽, 최종 문맥의 실패를 나눠 기록한다.
- [두 사건 질문의 절별 대상·기록 연결 A/B](advanced-clause-binding-20261109/report.md)는 같은 후보 20쪽과 선택기로 기존 규칙과 절별 규칙을 비교하고, 최종 8쪽·8,000토큰의 문항별 근거 변화를 기록한다.
- [상황별 140문항의 절별 연결 A/B](advanced-scenarios-clause-ab-20261109/report.md)는 4번 두 사건 비교의 문항별 변화, 나머지 120문항의 회귀, 동료 무 LLM 결과를 함께 기록한다.
- [단일·모호 사건 원문 조회 연결](advanced-source-lookup-20261109/report.md)은 A를 기준으로 2번 조회와 6번 조회를 각각 켠 설정, 두 조회를 함께 켠 설정을 같은 140문항에서 비교한다.
- [두 사건 원문 조회 비교](advanced-pair-source-20261109/report.md)는 두 조회를 채택한 기준선에 4번의 간접 대상별 원문 조회를 실험으로 추가해 개선·하락과 다른 유형의 회귀를 기록한다.
- [Advanced 상황별·업무 규정 180문항 비교](advanced-scenarios-180-20261109/report.md)는 두 사건 조회를 포함한 당시 BM25 기준선과 동료 무 LLM 설정을 비교한다. 근거가 있는 160문항과 범위 밖 대조 20문항을 분리한다.
- [Advanced 일상 표현 40문항 검색 비교](advanced-semantic-queries-20261109/report.md)는 당시 BM25 기준선과 Snowflake-ko 5:5 후보 추가를 비교하고, 한 문서·여러 문서 유형별 개선과 하락 및 팀원 무 LLM 결과를 기록한다.
- [Advanced 전체 카테고리 비교](advanced-category-comparison-20261109/report.md)는 이전 BM25·5:5 하이브리드·동료 무 LLM의 유형별 결과와 새 기본 실행으로 재현한 220문항을 기록한다.

- [Advanced 상황별 160문항과 검색 결과](advanced-scenarios-benchmark-20261109-v2/report.md)는 검색 140문항과 범위 밖 대조 20문항을 나눠 개인 기준선과 동료 무 API 설정을 비교한다. [평가 입력](../evaluation/advanced_scenarios/README.md)

## 결과 해석

각 실행 결과에 데이터셋, 검색 단위, 평가 순위 범위와 실행 조건을 기록했다. 수치를 비교할 때는 해당 조건이 일치하는지 확인한다.

## 원본 기반 반복 패턴 검색 실험

- [종료 기록: A/B/C → 페이지 관계 → 후보 병합 → 최종 기록 선택](advanced-fresh-record-hybrid-20261111/closeout.md) — 기존 기준선은 유지. 공식 60·카테고리 220 재검증과 새 문항의 분포 편향·실패 단계, 관련 레퍼런스를 함께 기록했다.
