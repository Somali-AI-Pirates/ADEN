# Advanced TableRAG A 이식 검증

> 2026-11-12 현재 구현 기준은 DB 우선 통합 TableRAG다. [현재 구현 기록](unified-implementation-log.md)과 [빠른 시작](../../guides/quickstart.md)을 따른다. 아래 A 이식 내용은 초기 설계 기록이다.

검증 대상은 A(v5)의 KURE-v3 · KURE nano, BM25 제목 보너스, RRF 5:5, HG 원문 보충, 읽기 전용 SQL, 최대 5회 도구 반복, 8쪽·8,000토큰 문맥이다. [실행 방법](../../guides/development.md#advanced-tablerag-실행) · [평가 방법](../../guides/c-module-guide.md#tablerag-평가)

## 스냅샷과 평가 입력

- `artifacts/advanced-table-rag/a-v5-hg`에 원본 18,000쪽, IS 표 검색 단위 3,750개, 물리 표 21,126개, HG 사건 2,730건과 원본 역할 연결 13,650개를 묶었다. 페이지·벡터·표 데이터 파일의 지문은 준비된 A(v5)와 일치한다.
- 새 스냅샷 manifest SHA-256은 `3048fc8ad36fb117aa81d3a3d79a79fd6f392c6a9baf4656e524873776e04821`이다. HG 식별자·프로필·날짜 값과 연결 페이지를 로드할 때 검증한다.
- 공식 개발·추가 60문항과 카테고리 220문항의 질문과 참고답 파일은 `artifacts/advanced-table-rag/product-replay/dataset-manifest.json`에 각각 ID와 해시를 남겼다. 페이지 참고 근거가 있는 문항은 공식 58개, 카테고리 200개다.

## 이식 재생

저장된 A(v5) 검색 결과와 계획 도구 인자를 각각 다시 넣고, 현재 코드로 표 조회와 최종 문맥을 계산했다. 공식 60문항과 카테고리 220문항에서 후보 순서, 최종 페이지, 원문 문맥 해시, SQL 시도·포함 결과가 모두 **280/280** 일치했다. SQL 비교에서 각 실행의 소요 시간은 제외했다.

```bash
uv run --locked python -m experimental.u_10o0o.table_rag.replay_product \
  --reference artifacts/u_10o0o/advanced-table-rag-20261111/v5 \
  --corpus artifacts/advanced-table-rag/a-v5-hg \
  --output artifacts/advanced-table-rag/product-replay/v5-fixed-check.json
```

## 실제 로컬 검색

명시 HG, 기준일과 사건일, 두 사건 비교, IS 표 SQL, 모호·범위 밖, 업무 규정이 포함된 8문항에서 로컬 모델로 검색을 다시 실행했다. 저장된 계획을 사용해 검색 호출 13회의 RRF 후보·HG 경로·표 ID·최종 검색 순서와 8문항의 문맥 해시를 대조했다.

| 실행 장치 | 검색 결과와 경로 일치 | 최종 문맥 해시 일치 | 워밍업 후 단일 검색 p50 / p95 |
| --- | ---: | ---: | ---: |
| CPU 임베딩 · CUDA 리랭킹 | 13/13 | 8/8 | 1.44초 / 2.37초 |
| CPU 임베딩 · CPU 리랭킹 | 13/13 | 8/8 | 13.99초 / 18.90초 |

단일 검색에는 임베딩·BM25·벡터·RRF·KURE·HG 보충을 포함한다. 시간은 이 호스트의 표본에서 측정했다. 장치별 단계 시간과 문항별 대조는 `v5-local-cpu_gpu_rerank-verified.json`과 `v5-local-cpu-verified.json`에 있다.

날짜 기준과 규격 약칭의 HG 분기를 다듬은 뒤 세 문항에서 CUDA로 추가 확인했다. 검색 4/4회의 후보·표·HG 분기와 최종 문맥 3/3이 일치했다. 기록은 `v5-local-hg-branches.json`에 있다.

저장된 검색 494회의 `searches[].ranked`를 양쪽 HG 정책에 동일하게 넣어 비교했을 때, 페이지 순서와 두 경로 상태가 **494/494** 일치했다. 입력 목록은 이미 HG 보충을 거친 기록이다. 두 정책 모두 원래 저장 순서는 492/494회 재현했으며, 나머지 두 건에서도 두 정책의 재적용 결과는 일치했다.

## 제공자와 평가 연결

- 가상 표 한 문항에서 실제 GPT-6 Luna가 계획 4회와 답변 1회를 실행했다. HTTP 시도도 5회였다. SQLite가 USD 32.50을 반환했고, 답변 인용은 최종 근거의 원본 쪽과 일치했다. 서비스 응답은 15.99초, 제공자 사용량은 입력 8,788·출력 1,175토큰이었다.
- Langfuse에서 같은 요청의 검색·계획·SQL·조립·답변 관측을 조회하고 조립 결과와 답변 입력의 문맥 해시를 대조했다. 가상 표의 합계 검증 점수를 같은 ID·이름·timestamp로 두 번 보내고, 저장된 점수 한 건을 확인했다.
- 별도로 고정한 가상 답변에서 GPT-6 Luna 4축 Judge와 Ragas Faithfulness를 실제 호출해 응답과 점수를 확인했다. Ragas의 실제 토큰 사용량은 확인 불가로 기록했다.
- `fastapi dev`에서 실제 스냅샷의 `/health` 준비 상태와 Swagger를 확인했다. 빈 질문과 기존 검색 방식을 명시한 요청은 각각 422와 501을 반환했고 헤더와 응답의 요청 ID가 일치했다.

## 저장소 검사

Ruff, 전체 pytest 370개와 서브테스트 15개, 변경 범위 Pyright, 잠금 파일 검사, wheel·sdist 빌드가 통과했다. 배포 파일의 TableRAG 진입점과 계획·답변 프롬프트를 확인했다. 새 평가 CLI의 `run`·`grade`·`publish` 도움말과 기존 일반·보안 가상 평가도 확인했다.

기록은 Git이 무시하는 `artifacts/advanced-table-rag/product-replay/`에 보관한다. 가상 검증과 고정 재생으로 연결과 이식 동작을 확인했다. 공식·카테고리 질문에 대한 새 실제 답변 품질과 장비별 운영 지연은 별도 비교가 필요하다.
