# 현재 Advanced 기준선의 임베딩 모델 교체 실험

2026-11-11 사용자 요청으로 실행한다. 기준선 채택, 운영 API 변경, 커밋·푸시는 범위 밖이다.

## 고정 조건

현재 `advanced_search`의 `bigram-id-normalized` 제목 보너스 BM25, 각 검색기 Top 20, 5:5 RRF(k=60), KURE nano(max 512, CUDA FP16, batch 32), KURE 상위 3개 보존 후 RRF 순서, 기존 HG·원문 조회, 원본 최대 8쪽·o200k_base 8,000토큰을 유지한다. 기존 `pattern_retrieval.legacy_baseline.LegacyBaseline`을 재사용한다. 원본 18,000쪽과 행 순서를 유지하고 임베딩 배열과 질문 임베딩만 교체한다.

## 모델 조건

| 이름 | 저장소 | 고정 리비전 |
|---|---|---|
| Snowflake-ko (대조군) | dragonkue/snowflake-arctic-embed-l-v2.0-ko | 55ec6e9358a56d56af759bc8372e970caf8c305f |
| KURE-v3 | nlpai-lab/KURE-v3 | 73d5e710bc0b1f324a60746922cbad6d38f480d4 |
| Nemotron-3-Embed-8B | nvidia/Nemotron-3-Embed-8B-BF16 | d1f2f25730bbd775b99b29185134bc86653bf2d1 |
| Qwen3-Embedding-8B | Qwen/Qwen3-Embedding-8B | 1d8ad4ca9b3dd8059ad90a75d4983776a23d44af |

각 모델 공식 설정의 전체 차원과 질문·문서 프롬프트를 사용한다. Snowflake는 기존 문서 벡터와 CPU float32 질문 임베딩을 사용한다. 새 세 모델은 배포 기본 정밀도인 BF16으로 계산하고 FP32에서 L2 정규화·저장한다. 양자화하지 않는다. KURE-v3 첫 FP32 표본은 정밀도 차이 확인용으로 별도 보존한다. 8GB GPU 메모리에 들어가지 않는 8B 모델은 BF16과 CPU/GPU 분산을 검증한다. 장치·정밀도·최대 길이·절단·실제 소요 시간을 기록하며, 배치 실험 시간을 서비스 요청 지연으로 해석하지 않는다. 동일 문서의 모델 간 입력 토큰 수 차이는 모델 자체 설정의 차이로 기록한다.

## 평가와 무결성

공식 60문항과 카테고리 220문항을 사용한다. 검색에는 ID와 질문만 전달하고 네 조건의 모든 결과를 저장한 뒤 참고 근거를 읽는다. 기존 채점 함수로 book_id+page, 필수 근거(허용 대체 근거 포함)를 판정한다. 정답 근거 없는 공식 문항과 범위 밖 대조군은 분모를 구분한다. Dense Top 20/50, 하이브리드 후보 20, 조회 이후, 최종 8쪽의 지표와 문항별 개선·하락을 기록한다. 민감 질문·본문·정답 인용은 로컬 artifacts에만 둔다.

문서·질문·검색 코드·모델·벡터 지문을 보존하고 중단 재개 시 일치 여부를 확인한다. 기존 파일과 다른 작업자의 변경을 수정하지 않는다. 사이드 대화의 사용자 지시에 따라 독립 에이전트 리뷰 대신 자체 검토하며, 이를 독립 리뷰로 표현하지 않는다.

## 참고

- [KURE 공식 평가 및 사용법](https://github.com/nlpai-lab/KURE)
- [KURE-v3 모델 카드](https://huggingface.co/nlpai-lab/KURE-v3)
- [Nemotron 모델 카드](https://huggingface.co/nvidia/Nemotron-3-Embed-8B-BF16)
- [Qwen 모델 카드](https://huggingface.co/Qwen/Qwen3-Embedding-8B)

공개 벤치마크 점수를 ADEN에서의 개선으로 간주하지 않는다. 이 문서는 실행 전 조건이며 결과는 실행 완료 후 별도로 기록한다.

## 사용자 요청에 따른 1차 범위 변경

8B 모델을 실행하던 중 사용자가 다운로드만 백그라운드에 두고 KURE-v3를 먼저 비교하도록 요청했다. 따라서 1차 채점은 완료된 Snowflake·KURE-v3 두 조건의 560개 검색 결과를 모두 검증한 뒤 수행한다. Nemotron은 512쪽 체크포인트를 보존하고 중단했고, Qwen 임베딩은 시작하지 않았다. 초기 네 모델 비교는 아직 완료하지 않았으며 이번 1차 보고서와 구분한다.
