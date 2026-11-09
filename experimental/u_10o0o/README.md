# 페이지 검색 실험 기록

## 현재 Advanced 구성

개인 Advanced 기본값은 **제목 보너스 검색 + 검증된 명시·간접 HG 사건 조회 + 복원 원본 페이지 문맥**이다. Bigram-ID BM25와 제목 보너스로 후보 20쪽을 정하고 KURE nano로 재정렬한다. 질문에서 검증된 HG 사건을 식별하면 그 사건의 다섯 원본 페이지를 먼저 두고, 남은 자리는 기존 검색 순서로 채운다. 식별되지 않으면 제목 보너스 검색 결과를 그대로 쓴다. 최종 문맥은 최대 8쪽·4,000토큰이다. 발췌문 대신 복원한 원본 페이지를 쓴다. [선택 근거와 비교 결과](records/decisions.md#2026-11-09--advanced-개인-기준선을-hg-사건-조회와-원본-페이지로-갱신) · [HG 조회와 문맥 비교 기록](advanced-curation-plan.md#현재-개인-기준선)

Advanced 복원 페이지 캐시, 로컬 Advanced 정제본 `artifacts/u_10o0o/pattern-curation/advanced`, HG 사건·출처 인덱스 `artifacts/u_10o0o/advanced-curation/hg-case-sources-20261109`, [추가 평가 데이터](evaluation/advanced_holdout/README.md), 공식 평가 파일, KURE nano의 로컬 모델 캐시와 CUDA가 필요하다. 기본 실행은 공식 평가 60문항과 블라인드 holdout 280문항, 총 340문항을 사용한다. 정제본이 없으면 [정제 계획](advanced-curation-plan.md#0-기준선과-로컬-입력-확인)에 따라 준비하고, 사건 인덱스는 [생성 기록](records/advanced-curation/hg-case-sources-20261109/report.md)의 안내대로 새 출력 경로에 만든다. 저장소 루트에서 실행하면 새 `records/advanced-selected-…/`에 실행 로그와 문항별 지표를 저장한다. `--output-dir`로 새 경로를 지정할 수 있다. 정제본과 사건 인덱스 경로를 바꿀 때는 `--curated`와 `--case-index`를 지정한다.

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  uv run --no-sync python -m experimental.u_10o0o.advanced_search
```

사용자 지정 로컬 경로를 쓸 때는 기본 명령에 다음 옵션을 추가한다.

```bash
uv run --no-sync python -m experimental.u_10o0o.advanced_search \
  --curated artifacts/my-advanced-curation/advanced \
  --case-index artifacts/my-advanced-case-index
```

기본 결과 키는 `bigram_id_fielded_kure_top3_hg_lookup`이다. `--compare`를 붙이면 **제목 보너스만 적용한 이전 구성**을 비교군으로 남기고, 본문 BM25 기준선, 순수 KURE 재정렬, Bigram-ID BM25 단독, Kiwi·Bigram-ID 각각의 Snowflake 6:4 RRF도 평가한다. 제목 보너스 단독 결과의 기존 키는 `bigram_id_fielded_kure_top3`다. 이 옵션에는 Kiwi와 Snowflake-ko 모델 및 페이지 벡터 캐시가 필요하다.

[Advanced 근거 범위 확장 세트](evaluation/advanced_coverage_v2/README.md)에 파일럿 40문항과 블라인드 벤치 후보 410문항을 보관한다. 본 세트는 원본 120개 PDF 각각의 앞·중간·뒤 구간을 참조하며, 사람 검수 상태와 PDF별 근거 페이지 수를 별도로 기록한다.

[Advanced 전 페이지 근거 세트](evaluation/advanced_full_coverage/README.md)에 신규 6,500문항을 보관한다. 이 묶음의 제목 보너스 결과는 [전 페이지 패턴 비교](records/advanced-industry-patterns-20261108/report.md#전-페이지-증강-6500문항)에, HG 조회의 추가 효과는 [간접 조회 벤치](records/advanced-curation/hg-indirect-lookup-20261109/report.md)에 기록했다. 기존 평가와 합하면 120개 PDF의 18,000쪽 전체를 참조한다. 페이지별 문항 연결과 기존 블라인드 후보의 사건을 공유하는 문항 목록을 함께 제공한다.

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
