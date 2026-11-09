# custom 평가 결과 분석 (2026-11-07)

chsopo89(B)가 Basic 데이터에서 직접 작성한 평가 질문·기대 답변으로 세 검색 방식을 평가한 결과다. 같은 폴더의 `README.md`·`comparison.csv`·`summary.json`은 C 평가기의 `write_report()`가 만든 보고서이고, 이 문서는 실행 조건·재현 정보와 해석을 보충한다.

## 평가 세트

| 항목 | 값 |
| --- | --- |
| 질문 파일 | `data/basic/evaluation/custom_questions.json` (`id`, `question`만, Git 추적) |
| 정답 파일 | `data/basic/evaluation/custom.json` (`reference_answer`, `evidence_groups`, Git 추적) — 채점 단계에서만 사용 |
| 문항 수 | 40개: 단일 근거형 S01~S20 20개, 복수 근거형 M01~M20 20개 |
| 근거 범위 | Basic PDF(SB01~SB15)만. 문항당 원문 출처 1~3쪽 |
| 작성 원본 | `Basic_바로붙여넣기_40개.txt`(질문), `Basic_40개_프롬프트와_기대답변_출처.txt`(기대 답변·출처) |

- 제공 평가 데이터(`development`·`additional`·`expansion_validation`)와 문장이 같은 문항은 없다. 일부 문항(S04·S07·S08·S10·S11·S18·S19, M16)은 `expansion_validation` 등과 내용이 대응하므로 독립 평가 세트로 검증된 것은 아니다.
- 정답 파일에 `expected_action`(답변·추가 확인·보류)은 없다. Judge의 판단 항목은 기대 답변 내용을 기준으로 채점했다.
- 팀원이 같은 기준으로 채점하도록 질문 파일과 정답 파일을 모두 `.gitignore` 예외로 Git에 포함한다. 아래 hash로 실행에 쓴 파일과 같은지 확인한다. 정답 파일은 채점에만 쓰며 검색·생성 입력에 넣지 않는다.
- S01 실행 기록(`answers.jsonl` 등)의 질문 맨 앞에는 원본 텍스트 파일의 BOM 문자(U+FEFF)가 붙은 채 입력됐다. 질문 파일에는 없으며 답변 내용에는 영향이 확인되지 않았다.

## 실행 조건과 재현 정보

| 항목 | 값 |
| --- | --- |
| 코드 | `main` `361d92fa316990068f8477fdba8119a3491fec1b` + 미커밋 변경(브랜치 `feat/prompt-choi`: `k`=8, `app/service/bootstrap.py`, 관련 문서). `manifest.json`의 `git_dirty: true` |
| 설정 | `configs/basic.yaml` (SHA-256 `9f802f7c…57d4aa`): `k` 8, 문맥 예산 4,000토큰, 후보 20개, 생성 `gpt-6-luna`, 시간 제한 60초, 최대 생성 2회 |
| 생성 프롬프트 | `app/generation/prompts/shipping_qa.txt` 원본 (SHA-256 `3d689c76…6af9230`) |
| 색인 | Qdrant `basic_multilingual-e5-small_p800` (청크 620개). `artifacts/basic/index_manifest.json`은 이 PC에 없어 hash를 기록하지 못했다 |
| 검색 | `naive`, `hybrid`(BM25+RRF), `hybrid_rerank`(`BAAI/bge-reranker-v2-m3`, CPU) |
| 서비스 연결 | `app.service.bootstrap:query_adapter` (일반 평가 어댑터). 시간 범위 `in_process_adapter` — HTTP 응답 시간이 아니다 |
| 채점 | Judge `gpt-6-luna`(`app/evaluation/prompts/judge.txt`, SHA-256 `de8f990b…263d5f`), RAGAS Faithfulness `gpt-6-luna` |
| 데이터 hash (Git에 저장된 LF 줄바꿈 기준) | `custom_questions.json` `f0629771a62643e1f1c4074f107aba4f087ae5adb7f98dd89071bb7dfae0cfc9`, `custom.json` `3b36be5a8b9c62fd5fc51a204cd1e754c9269676528193ff5a183da0e2a3213e`. 실행 당시 파일은 CRLF 줄바꿈이라 hash가 달랐으며(`c7427222…`, `6bcbc3fb…`), 내용은 같다 |
| Langfuse | 서비스 실행 120건 기록. 저장 조회(`verify_trace`)는 이번 실행에서 하지 않았다 |

실행 방식의 차이: 이번 답변 120건은 공식 CLI 대신 같은 어댑터를 호출하는 임시 스크립트로 생성했고, 채점은 C의 `normalize_run_record` → `grade_all` → `write_report`를 그대로 적용했다. RAGAS는 공통 환경의 `langchain-core` 1.x와 충돌해 별도 가상환경(`ragas` 0.4.3)에서 실행했으며, 기본 출력 한도로 문장 추출이 잘리는 문제 때문에 `max_tokens=8192`를 지정했다. 제출용으로 재현하려면 공식 명령으로 다시 실행한다.

```bash
python -m app.evaluation.scripts.run_benchmark \
  --questions data/basic/evaluation/custom_questions.json \
  --references data/basic/evaluation/custom.json \
  --adapter app.service.bootstrap:query_adapter \
  --modes naive,hybrid,hybrid_rerank --config configs/basic.yaml --k 8 \
  --judge-model gpt-6-luna --experiment-id custom-<날짜> --selection-reason "B 작성 Basic 평가 세트"
```

## 결과

평균은 `scored`만 대상이며 해당 없음·평가 불가는 제외했다. 괄호는 채점된 문항 수다.

| 지표 | naive | hybrid | hybrid_rerank |
| --- | --- | --- | --- |
| Hit@8 | 97.5% (40) | 97.5% (40) | 97.5% (40) |
| Recall@8 | 82.1% (40) | **90.8%** (40) | 88.3% (40) |
| RAGAS Faithfulness | 0.830 (40) | 0.854 (39, 평가 불가 1) | **0.890** (40) |
| Judge 사실 | 42.5% (40) | 53.8% (39) | **55.0%** (40) |
| Judge 조건 | 85.0% (40) | 84.6% (39) | **87.5%** (40) |
| Judge 근거 | 70.0% (40) | 79.5% (39) | **82.5%** (40) |
| Judge 판단 | 95.0% (40) | 92.5% (40) | **97.5%** (40) |
| 오류 | 0 / 40 | 1 / 40 (2.5%) | 0 / 40 |
| 평균 응답 시간(정상 요청) | 7.6초 (40) | 7.6초 (39) | 13.6초 (40) |
| 입력 / 출력 토큰 (확인 40건) | 174,537 / 28,155 | 182,942 / 28,010 | 182,673 / 27,912 |
| 재생성 | 1회 | 1회 | 0회 |

- `hybrid_rerank`가 Judge 네 항목과 RAGAS에서 가장 높다. 대신 리랭커(CPU) 때문에 응답 시간이 약 6초 길다.
- 사실 점수는 세 방식 모두 60% 미만이다. 세 방식 모두 사실 0인 문항이 16개다: S04, S05, S06, S09, M01, M03, M04, M05, M07, M10, M12, M13, M14, M17, M18, M20.
- 모델 호출 비용은 단가를 확인하지 못해 계산하지 않았다. 채점 호출(Judge·RAGAS)의 시간·토큰은 `summary.json`의 `grading`에 서비스 지표와 따로 있다.

### 단일 근거형(S)과 복수 근거형(M)

| Judge 사실 | naive | hybrid | hybrid_rerank |
| --- | --- | --- | --- |
| S01~S20 | 12 / 20 | 13 / 19 | 15 / 20 |
| M01~M20 | 5 / 20 | 8 / 20 | 7 / 20 |

근거가 여러 문서·페이지에 나뉜 M 문항에서 사실 점수가 크게 낮다.

### 근거 페이지 포함 여부와 사실 점수

정답의 원문 출처 페이지가 모델에 전달한 최종 문맥 8개에 모두 들어갔는지로 나눴다.

| | naive | hybrid | hybrid_rerank |
| --- | --- | --- | --- |
| 근거 페이지 모두 포함 | 10 / 14 | 12 / 16 | 14 / 19 |
| 일부만 포함 또는 없음 | 7 / 26 | 9 / 23 | 8 / 21 |

근거 페이지가 모두 들어오면 사실 점수가 71~75%, 일부만 들어오면 30~39%다. 사실 누락의 주원인은 **검색이 필요한 페이지를 최종 문맥에 넣지 못하는 것**이다.

## 사례

### M12 — 검색 누락

- 기대 답변: 11월 2일까지 신규 접수하면 기본 이메일 `vgm@gaonroute.example`[SB08 p2], 11월 3일부터 업무 포털 VGM 등록함[SB11 p2].
- 세 방식 모두 SI 마감(11월 7일 12:00 KST, `docs@gaonroute.example`)과 VGM 판단 기준(최초 신규 접수일)은 맞혔지만, 11월 2일까지의 이메일 주소를 "자료에서 확인할 수 없다"고 답해 사실 0이다.
- 원래 질문으로 검색하면 기본 경로 페이지(SB08 p2, SB03 p2, SB03 p9, SB14 p1)가 세 방식 모두 후보 20개 안에 없다. 질문이 SI 마감·11월 변경·접수일 기준을 함께 묻기 때문에 변경 공지 페이지가 상위를 차지한다. "VGM 기본 제출 창구"만 검색하면 `hybrid`·`hybrid_rerank`에서 SB08 p2가 1위다.
- 모델은 문맥에 없는 주소를 만들지 않았다. 답변 생성 규칙은 의도대로 동작했다.

### S13(hybrid) — 출처 형식 오류

- 두 번의 생성 시도 모두 `source_ids`에 자료 ID `S3` 대신 문서 ID `SB01`을 넣어 출처 검증에 실패했고 `error`(API 502에 해당)로 끝났다. 답변 내용 자체는 맞는 방향이었다.
- 문맥 블록에 `문서 ID: SB01`이 함께 표시되어 모델이 혼동한 것으로 보인다. 프롬프트에서 `source_ids`에는 `[S1]` 형식의 자료 ID만 쓴다는 점을 더 분명히 할 수 있다.

## 해석과 다음 작업

1. 세 방식 중 품질 지표는 `hybrid_rerank`가 가장 높고, 응답 시간은 가장 길다. 최종 방식 선택은 팀 공식 비교(제공 평가 데이터 포함)와 함께 정한다.
2. 사실 누락은 검색 누락과 강하게 연결된다. 복수 근거형 질문에는 질의를 하위 질문으로 나눠 검색하는 방식(질의 분해)을 검토할 수 있다. 세 방식 공통 변경이므로 A와 함께 결정한다.
3. 출처 형식 오류(S13)는 B 프롬프트에서 `source_ids` 형식 지시를 보강해 줄일 수 있다.
4. 한 번 실행한 결과다. Judge·생성은 실행마다 1~2문항씩 달라질 수 있어 작은 차이는 우연일 수 있다.
