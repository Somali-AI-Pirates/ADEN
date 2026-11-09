# 페이지 검색 실험 기록

## 현재 Advanced 구성

개인 Advanced 기본 실행은 제목 보너스를 적용한 Bigram-ID BM25와 Snowflake-ko 벡터 검색에서 각 20쪽을 뽑고, **5:5 RRF(상수 60)**로 합쳐 후보 20쪽을 고른다. KURE nano 상위 3쪽 뒤에는 나머지 후보를 RRF 순으로 잇는다. 명시·간접 HG 조회와 단일 사건, 모호한 두 후보 사건, 두 사건 비교의 대상별 원문 조회를 적용한다. 복원 원본은 기존 선택기로 최대 8쪽·8,000토큰을 담는다. [기준선 결정](records/decisions.md#2026-11-09--55-하이브리드를-개인-advanced-기준선에-채택) · [카테고리 비교](records/advanced-category-comparison-20261109/report.md)

Advanced 복원 페이지와 Snowflake-ko 페이지 벡터, 정제본, HG 사건·출처 인덱스, 역할별 정규화 필드, Snowflake-ko·KURE nano 로컬 모델 캐시와 CUDA가 필요하다. 기본 실행은 공식 평가 60문항과 블라인드 holdout 280문항을 사용한다. 저장소 루트에서 실행하면 새 `records/advanced-selected-…/`에 실행 로그와 문항별 지표를 저장한다. `--output-dir`로 새 경로를 지정할 수 있다. [원문 조회 입력과 생성 절차](advanced-curation-plan.md#현재-개인-기준선)

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  uv run --no-sync python -m experimental.u_10o0o.advanced_search
```

기본 토큰 상한은 8,000이다. 비교 실행에서 다른 한도가 필요하면 `--context-tokens`에 양의 정수를 지정한다. 실제 상한과 원문 조회 경로별 발동 수는 실행 결과에 기록한다.

기준선 채택에 사용한 카테고리별 220문항은 `--suite categories`로 실행한다. 이 실행은 상황별 140문항, 업무 규정 20문항, 일상 표현 40문항, 범위 밖 대조 20문항의 후보 순위와 최종 문맥 지문을 채점 전에 저장한다. 상황별 140문항만 실행하려면 `--suite scenarios`를 사용한다. [카테고리 실행 방법](evaluation/advanced_scenarios/README.md#개인-기준선의-카테고리별-220문항-실행)

사용자 지정 로컬 경로를 쓸 때는 기본 명령에 다음 옵션을 추가한다.

```bash
uv run --no-sync python -m experimental.u_10o0o.advanced_search \
  --curated artifacts/my-advanced-curation/advanced \
  --case-index artifacts/my-advanced-case-index \
  --role-facts artifacts/my-advanced-role-facts
```

기본 결과 키는 `bigram_id_fielded_snowflake_ko_rrf_55_kure_top3_paired_source_lookup`이다. `--compare`를 추가하면 이전 BM25 기준선 `bigram_id_fielded_kure_top3_paired_source_lookup`도 같은 원문 조회와 문맥 선택기로 채점한다. 기존 HG 조회, 제목 보너스 단독, 본문 BM25, 순수 KURE 재정렬, Bigram-ID BM25 단독, Kiwi·Bigram-ID 각각의 Snowflake 6:4 RRF도 이 옵션으로 확인할 수 있다. `--compare`에는 Kiwi가 추가로 필요하다.

### 동료 Advanced 평가 세트

`--suite peer`로 동료가 공유한 Advanced 304문항을 실행한다. 거짓 전제 20문항, 2문서 합성 114문항, 3~4문서 합성 170문항이다. [동료 폴더의 질문·참고 파일](../ysl727727/overnight/data/)을 직접 읽는다. `--suite all`로 기존 공식·holdout 340문항과 함께 실행할 수 있고, 옵션을 생략하면 기존 340문항을 실행한다. `--compare`는 어느 세트에나 함께 쓸 수 있다.

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  uv run --no-sync python -m experimental.u_10o0o.advanced_search --suite peer
```

`results.json`의 `peer`는 304문항 전체, `peer_false_premise`·`peer_two_doc`·`peer_multi_doc`는 각 묶음의 집계다. 2문서 질문 중 동료가 사건 식별이 모호하다고 표시한 45문항은 `peer_two_doc_ambiguous`에도 집계하고, 나머지 69문항은 `peer_two_doc_other`에서 볼 수 있다. 모호 여부는 검색 순위를 확정한 뒤 채점에서 읽는다. 점수는 후보 20쪽·최종 최대 8쪽과 실행에 설정한 토큰 한도 기준이며, 실제 답변과 되묻기 품질은 별도 평가 범위다.

### 추가 평가 기록

[Advanced 근거 범위 확장 세트](evaluation/advanced_coverage_v2/README.md)에 파일럿 40문항과 블라인드 벤치 후보 410문항을 보관한다. 본 세트는 원본 120개 PDF 각각의 앞·중간·뒤 구간을 참조하며, 사람 검수 상태와 PDF별 근거 페이지 수를 별도로 기록한다.

[Advanced 전 페이지 근거 세트](evaluation/advanced_full_coverage/README.md)에 신규 6,500문항을 보관한다. 이 묶음의 제목 보너스 결과는 [전 페이지 패턴 비교](records/advanced-industry-patterns-20261108/report.md#전-페이지-증강-6500문항)에, HG 조회의 추가 효과는 [간접 조회 벤치](records/advanced-curation/hg-indirect-lookup-20261109/report.md)에 기록했다. 기존 평가와 합하면 120개 PDF의 18,000쪽 전체를 참조한다. 페이지별 문항 연결과 기존 블라인드 후보의 사건을 공유하는 문항 목록을 함께 제공한다.

[Advanced 실사용 문맥 평가 후보](evaluation/advanced_usage_blind/README.md)는 업무 질문당 필수 사실 1~5개, 사건 추가 확인, 증빙 부족 보류를 분리한다. 후보 300개와 개발용 파일럿 30개를 별도로 관리한다.

[Advanced 데이터 정제와 검색 실험 계획](advanced-curation-plan.md)에 원본 양식 조사, HG 사건 연결, 명시·간접 ID 조회, 필드 정규화와 문맥 비교의 실행 순서와 검증 기준을 정리했다.

## Basic 기준 임베딩

2026-11-08에 `dragonkue/snowflake-arctic-embed-l-v2.0-ko`를 개인 검색 실험의 기준 임베딩 모델로 정했다. Basic 원본 PDF 360쪽을 대상으로 한 페이지 Top 3 실험에서 세 검색 지표가 후보 중 가장 높았다. GTE보다 근거를 찾은 문항이 2개 늘었고, 질문 임베딩 중앙값은 약 40ms 길었다. [로컬 CPU 비교 결과](records/retrieval_evaluations/cpu-candidates-20261108-173451/report.md)

| 모델 | Hit@3 | Recall@3 | Precision@3 | 질문 임베딩 p50 |
| --- | ---: | ---: | ---: | ---: |
| Snowflake-ko | 63.24% | 38.21% | 23.53% | 69.3ms |
| GTE multilingual base | 60.29% | 35.33% | 20.59% | 28.9ms |

72문항 중 근거 페이지가 있는 68문항을 채점했다. 속도는 CPU 8스레드, float32, 문서 배치 8에서 모델별 한 번씩 측정했다.

## 실행

프로젝트 루트에서 실행한다. [prepare.py](prepare.py)의 `DATASET`으로 Basic 또는 Advanced를 선택한다. 현재는 Advanced 원본 PDF와 일반 평가 60문항을 사용한다. PDF 전처리 결과가 달라지면 문서 벡터를 다시 생성한다.

```bash
uv run python -m experimental.u_10o0o.prepare
uv run python -m experimental.u_10o0o.main --device cuda
uv run python -m experimental.u_10o0o.search
uv run python -m experimental.u_10o0o.evaluate
```

문서 벡터는 CUDA로 생성하고 CPU에서 검색·평가할 수 있다. CPU로 문서를 임베딩하려면 `--device cpu`를 지정한다.

모델 버전과 문서 벡터 경로는 [embeding.py](embeding.py)에서 관리한다. 문서는 복원한 페이지 본문을 그대로 임베딩하고, 질문에는 모델의 `query: ` 접두사를 적용한다. Advanced 페이지는 `artifacts/u_10o0o/advanced_restored_pages.json`에, 정규화한 1024차원 문서 벡터는 `artifacts/u_10o0o/advanced_snowflake_ko_vectors.npy`에 저장한다. 공유할 평가 보고서와 실행 기록은 [records/](records/README.md)에 저장한다.

여러 모델을 비교할 때는 [compare.py](compare.py)를 실행한다. 기본 실행은 Snowflake-ko와 초기 비교 모델을, `--candidates`는 Snowflake-ko와 후속 후보를 같은 입력으로 평가한다.

## Advanced 검색 기준선 비교

같은 Advanced 페이지와 일반 평가 문항을 사용해 Snowflake-ko 벡터 검색, BM25, 가중 RRF 하이브리드 검색을 비교했다. [로컬 기준선 비교 보고서](records/retrieval_evaluations/advanced-baselines.md)

BM25는 복원 페이지 캐시에서 CPU 색인을 만든 뒤 단독으로 검색하고 채점한다.

```bash
uv run python -m experimental.u_10o0o.bm25
```

하이브리드 실험은 각 검색의 상위 20쪽을 가중 RRF로 합쳐 최종 3쪽을 채점한다. BM25:Snowflake-ko 비율을 5:5, 4:6, 6:4, 3:7, 7:3으로 바꾸며 실행한다. `--tokenizer`로 `bigram`, `kiwi`, `kiwi-id`, `bigram-id`를 선택한다. 기본값은 `bigram`이다.

```bash
uv run --no-sync python -m experimental.u_10o0o.hybrid --tokenizer bigram
uv run --no-sync python -m experimental.u_10o0o.hybrid --tokenizer kiwi
```

## Advanced BM25 토크나이저 비교

같은 Advanced 페이지와 평가 문항으로 bigram BM25와 Kiwi BM25를 비교했다. [로컬 토크나이저 비교 보고서](records/retrieval_evaluations/advanced-bm25-tokenizers.md)

Kiwi 실행에는 로컬 가상환경의 `kiwipiepy==0.24.1`을 사용한다.

```bash
uv run --no-sync python -m experimental.u_10o0o.bm25 --tokenizer bigram
uv run --no-sync python -m experimental.u_10o0o.bm25 --tokenizer kiwi
```

## Advanced 리랭커 비교

[rerank.py](rerank.py)는 Kiwi BM25와 Snowflake-ko를 6:4로 합친 뒤 상위 20쪽을 한 번 생성한다. 각 모델에 같은 후보를 제공해 최종 3쪽을 비교하고, 후보 지표와 CPU 지연, 문항별 순위 변화를 기록한다.

Kiwi가 설치된 환경에서 첫 실행 전에 공개 모델 가중치를 로컬 Hugging Face 캐시에 준비한다. 모듈의 `MODELS`에 모델별 커밋을 고정했다.

```bash
uv run --no-sync python - <<'PY'
from huggingface_hub import snapshot_download
from experimental.u_10o0o.rerank import MODELS

for model, revision in MODELS.items():
    snapshot_download(model, revision=revision,
                      allow_patterns=["*.json", "*.txt", "*.model", "model.safetensors"])
PY
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 OPENBLAS_NUM_THREADS=1 \
  uv run --no-sync python -m experimental.u_10o0o.rerank
```

실험을 거쳐 고른 구성과 판단 근거는 [선택 기록](records/decisions.md)에서 확인한다.

## Advanced 소제목·문단 청킹 비교

페이지 기준선의 고정 후보를 재사용하고, 소제목·문단 청크에는 같은 Kiwi BM25, Snowflake-ko, RRF 6:4를 적용한다. 두 후보 목록을 GPU에서 같은 KURE nano로 리랭킹하고 기존 페이지 Top 3 순위가 재현되는지 확인한다. 최종 3개로 출처 메타데이터를 포함한 문맥을 조립하고, `o200k_base` 4,000토큰 예산에서 평가 인용문이 같은 출처 페이지의 전달 본문에 들어왔는지 계산한다.

Advanced 원본 PDF, 복원 페이지 캐시, 앞선 페이지 기준선 기록과 Snowflake-ko·KURE nano의 로컬 모델 캐시가 필요하다. 구조 청크와 임베딩은 로컬 `artifacts/u_10o0o/`에서 준비하고, 비교 집계와 실행 로그는 [records/](records/README.md)에 저장한다.

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --no-sync python -m experimental.u_10o0o.structure_chunks
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --no-sync python -m experimental.u_10o0o.structure_vectors
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 OPENBLAS_NUM_THREADS=1 \
  uv run --no-sync python -m experimental.u_10o0o.structure_compare
```

## Advanced Kiwi 식별자 토큰 비교

`kiwi-id`는 `AB-12344`처럼 영문과 숫자가 섞인 하이픈 식별자를 한 토큰으로 고정한다. 문서와 질문에 같은 규칙을 적용하고, 일반 형태소 분석에는 Kiwi를 사용한다. [비교 실행](identifier_compare.py)은 두 토크나이저를 같은 페이지·Snowflake-ko 후보·RRF 6:4·KURE nano 조건에서 평가한다. 번호만 입력한 BM25 진단은 Advanced 일반 평가와 별도로 집계한다.

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 OPENBLAS_NUM_THREADS=1 \
  uv run --no-sync python -m experimental.u_10o0o.identifier_compare
```
