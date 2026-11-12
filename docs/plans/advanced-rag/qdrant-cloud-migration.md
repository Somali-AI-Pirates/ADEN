# Advanced TableRAG Qdrant Cloud 적용

## 범위와 유지할 계약

2026-11-12 사용자 요청에 따라 기존 KURE-v3 `vectors.npy`를 재임베딩 없이 Advanced 전용 `advanced_table_rag` 컬렉션에 적재한다. 벡터 검색만 Qdrant exact 검색으로 바꾸고 BM25·5:5 RRF·KURE nano·공통 SQLite·문맥 예산과 API 계약은 유지한다. 기존 Basic 컬렉션과 로컬 번들은 보존한다. 원문 대신 벡터와 출처·검색 단위 ID만 클라우드에 저장한다.

`ADEN_TABLE_RAG_VECTOR_BACKEND`는 `numpy` 또는 `qdrant`를 명시적으로 선택한다. 미지정 기본은 기존 로컬 경로 `numpy`이며, 이 환경은 아래 동등성 검증을 통과한 뒤 `.env`에서 `qdrant`로 전환했다. 컬렉션 기본 이름은 `advanced_table_rag`이고 추가 이름은 `advanced_table_rag_` 접두사만 허용한다. 모델·데이터 지문과 구축 상태는 컬렉션 metadata에 기록한다. 같은 지문의 재실행은 기존 자료를 재사용하고, 다른 지문·차원·거리 함수의 컬렉션은 변경하지 않고 거절한다.

실행 중인 로컬 번들과 ready 컬렉션은 불변 스냅샷으로 취급한다. 기존 번들 파일을 실행 중 교체하거나 외부 작성자가 ready 컬렉션의 벡터를 직접 수정하는 동작은 지원하지 않는다. 전체 원격 정합성 검사는 활성화 전 `check-qdrant --full`로 수행한다.

이번 지원 범위는 exact 검색이다. ANN, 양자화, 모델·청킹 교체, Basic 이관과 원문 클라우드 저장은 포함하지 않는다. 원격 오류나 지문 불일치 시 로컬 검색으로 조용히 전환하지 않는다. 기존 `numpy` 모드는 명시적인 로컬 실행·비교 경로로 유지한다.

## 구현과 확인 순서

1. 기존 데이터 준비 CLI에 `upload-qdrant`, `check-qdrant`를 연결하고 ID·벡터·출처·지문 검사를 수행한다.
2. 같은 질문 벡터로 로컬 dot product와 Qdrant exact Top 50을 비교한다. 순위·점수 차이, RRF 후보 20쪽·리랭킹·최종 출처와 문맥 차이를 각각 기록한다. 동점과 부동소수점 차이를 숨기지 않는다.
3. Basic 컬렉션의 적재 전후 상태를 비교하고 Advanced 컬렉션 준비 완료 후 서버 설정을 전환한다.
4. 빠른 시작을 수정하고 독립적인 데이터·런타임 리뷰, 집중 검사와 저장소 최종 검사를 진행한다.

검색 비교에서는 이미 저장된 질문·벡터를 재사용할 수 있으며 평가 정답은 검색 입력에 전달하지 않는다. 새 LLM 생성·Judge 호출은 이번 이관 검증에 필요하지 않다. 상세 로컬 증거는 `artifacts/verification/qdrant-cloud/`에 둔다. 자격증명과 질문·원문은 공유 기록에 넣지 않는다.

## 참고

- [Qdrant Collections](https://qdrant.tech/documentation/manage-data/collections/): 벡터 차원·거리 함수와 컬렉션 설정.
- [Qdrant Search](https://qdrant.tech/documentation/search/search/): exact 검색과 검색 응답.
- [Qdrant Points](https://qdrant.tech/documentation/manage-data/points/): 기존 벡터와 payload 적재.

## 코드 검사와 비교 입력 고정

- 독립 리뷰에서 Advanced 연결의 HTTPS 제한과 float32 저장 형식 검사를 보완했다. Basic 연결 함수의 동작은 바꾸지 않았다.
- 집중 검사 21개 통과. 이후 Ruff, 전체 pytest 501개 및 하위 검사 15개, lock, 데이터·RAG·평가 CLI, 일반·보안 샘플, sdist/wheel 빌드와 패키지 리소스 검사를 통과했다. 로컬 Qdrant가 항상 exact 검색을 쓴다는 경고 1건은 예상된 테스트 안내다.
- 비교 입력은 기존 기능 확인 8개와 CPU 측정 16개에서 질문만 가져왔다. 공백 정규화 후 중복 1개를 제거해 23개로 고정했으며 평가 정답은 읽지 않았다.
- 질문 파일 지문: `10b87a0de031c1ae8ca2468e37ae3807304a2efe2ba5f5d5ba036e1b71a3c156`.
- 초기 비교 스크립트가 질문 벡터를 고정하는 함수를 반복문에서 복구하지 않아 첫 질문 벡터를 23번 사용한 오류를 발견했다. 해당 로컬·원격 비교 결과는 무효로 보존했다. 원래 임베딩 함수를 별도로 유지하고 질문·벡터의 고유 지문 수를 검사하도록 고친 뒤 같은 23문항 전체를 다시 실행했다. 질문 23개와 벡터 23개의 고유 지문 및 임베딩 호출 23회를 확인했고, 처음·중간·마지막 질문은 별도 인코딩 결과도 일치했다. 클라우드 문서 벡터 적재와 전체 벡터 검사는 이 검증용 캐시와 독립적이다.
- 이 표본은 검색 저장소 이관의 동등성 검사다. 새로운 검색 품질 평가나 LLM 답변 정확도 평가로 해석하지 않는다.

상세 명령과 종료 코드는 `artifacts/verification/qdrant-cloud/final-checks.json`, 입력과 로컬 결과 지문은 `frozen-questions-metadata.json` 및 `local-baseline-summary.json`에 기록했다.

## 적재 경과

HTTP CLI 적재는 765초 후 전송 경로를 바꾸기 위해 이번 작업의 프로세스만 중단했다. 같은 Cloud의 gRPC 읽기 연결을 확인한 뒤 동일한 `building` 지문과 결정적 point ID로 다시 적재했다. 기존 컬렉션을 삭제하지 않았으며 처음부터 재임베딩하지 않았다. 이 재개는 동일 ID의 upsert로 처리하며 완료된 점을 포함해 같은 벡터를 다시 보낼 수 있다. 최초 중단 기록은 `upload-command.json`에 보존했다.

전송 방식 선택의 참고는 [Qdrant 대량 적재 문서](https://qdrant.tech/documentation/operate-and-maintain/bulk-data-import/)다. 제품 CLI 기본 전송은 기존 HTTP 연결을 사용하며, 이번 재개는 같은 업로드 함수에 gRPC 클라이언트를 전달했다. 전송 방식만으로 서비스 검색·생성 속도가 개선됐다고 주장하지 않는다.


## 클라우드 적재 검사

- gRPC 재개는 665.746초 후 `advanced_table_rag` 21,750개를 `ready` 상태로 완료했다. 재개 과정에서 모든 벡터·payload를 대조했다.
- 제품 CLI `check-qdrant --full`도 실제 HTTP 연결로 별도 실행해 133.682초에 통과했다. 검사 결과는 `vectors-and-payload`다.
- 기존 Basic 컬렉션 3개·1,860점의 모든 벡터·payload 및 벡터 설정 지문은 적재 전후 동일했다. `basic-preservation.json`에 기록했다.
- 초기 질문 캐시 오류를 발견한 뒤 서버 전환을 보류했다. 아래 최종 비교는 수정한 검증 입력으로 재실행한 결과만 사용한다.

## 최종 exact 동등성 결과

수정된 입력으로 실제 Qdrant Cloud와 로컬 NumPy 검색을 비교했다. Qdrant 서버는 1.20.0이며, 업로드한 float32 벡터는 21,750개·2,048차원이다. 입력은 페이지 18,000개와 표 검색 단위 3,750개다.

| 비교 항목 | 일치 결과 |
| --- | --- |
| 벡터 Top 50의 ID와 순서 | 23/23문항 |
| BM25 Top 50의 ID·순서·점수 | 23/23문항 |
| 5:5 RRF 후보 Top 20의 ID·순서·점수 | 23/23문항 |
| KURE 입력 후보와 최종 순위 | 23/23문항 |
| 최종 페이지 순서·표 ID | 23/23문항 |
| 최종 문맥·출처·토큰 수 | 23/23문항 |

벡터 유사도는 비트 단위로 같지 않았다. 같은 ID의 최대 절대 점수 차이는 `2.0483322138442617e-7`이었으며 순위는 바뀌지 않았다. 이 표본에서는 Top 50 경계 동점이 없었다. 경계 동점의 추가 조회·행 ID 정렬은 별도 단위 테스트로 확인했다.

로컬 기준은 23문항·460쌍을 실제 KURE로 평가했다. 원격 비교에서는 같은 질문과 동일한 순서의 후보인지 확인하고 20문항의 기존 KURE 점수를 재사용했으며, 3문항은 KURE를 다시 호출해 점수까지 일치했다. 23문항 모두에서 KURE를 두 번씩 독립 실행한 결과는 아니다. 문맥은 제품 검색·조립 경로로 다시 구성했다. 비교 중 질문 벡터 재생성이나 유료 LLM·Judge 호출은 없었다.

현재 모델·검색 계약과 소스가 동일한지도 독립적으로 검수했다. 이후 재실행에서 다른 계약의 점수를 재사용하지 않도록 비교 스크립트에 manifest와 환경 지문 확인을 추가했다. 실행 후 확인한 환경 정보는 사후 감사 기록으로 구분한다.

| 지문 | SHA-256 |
| --- | --- |
| 로컬 corpus.json | `d6b746e5e64ed5d0877593d060b0a6bd397ccb97d77dbcdffec1f945bc865828` |
| 고정 질문 벡터 | `6f17dd532e875446ad5401a6b5566c9aba88c44eca5755a4ad19815d8dd00418` |
| 수정 후 로컬 결과 | `dd0c1ebb47bc737308c77c3eafa5447668fd4a37c0b6525aa5d69c04ca6c162d` |
| 원격 비교 상세 결과 | `88b0a9ec12528a9d568022dbdb5587223d962e603904f2fc3798dd50d9e011d4` |

질문별 순위·점수·문맥 지문은 `qdrant-parity.json`, 집계는 `qdrant-parity-summary.json`에 있다. 최초 잘못된 비교는 `invalid-vector-reuse/`에 별도로 보존하며 위 결과에 포함하지 않는다.

## 서버 활성화와 재현

루트 로컬 `.env`의 다음 두 설정만 추가·변경하고 다른 환경값은 보존했다.

```dotenv
ADEN_TABLE_RAG_VECTOR_BACKEND=qdrant
ADEN_TABLE_RAG_COLLECTION=advanced_table_rag
```

기존 `fastapi dev` 서버를 다시 불러온 뒤 새 작업 프로세스에서 `/health`의 `query_ready=true`를 확인했다. 활성화 기록은 `activation.json`에 있다. 개발 서버가 검증 스크립트 변경을 감지해 추가 재시작한 뒤에도 HTTP 200과 같은 준비 상태를 재확인했다.

다른 환경에서는 로컬 번들과 Qdrant 인증값을 준비하고 아래 순서로 실행한다.

```bash
uv run --locked python -m app.ingestion.scripts.prepare_table_rag upload-qdrant --directory artifacts/advanced-table-rag
uv run --locked python -m app.ingestion.scripts.prepare_table_rag check-qdrant --directory artifacts/advanced-table-rag --full
uv run --locked fastapi dev
```

같은 불변 스냅샷의 업로드 재실행은 기존 ready 컬렉션을 검사해 재사용한다. 미완료 building 컬렉션은 같은 지문일 때만 재개한다. Basic 컬렉션은 이 명령의 대상 이름으로 허용하지 않는다.

이 검증은 기존 벡터 검색 결과가 Cloud 전환 후 유지되는지를 확인했다. 전체 평가 데이터의 품질 재측정, ANN 품질, 동시 부하와 서비스 지연 개선은 측정하지 않았다. 임베딩·리랭커·BM25·SQLite 실행 위치는 바꾸지 않았다.

[빠른 시작](../../guides/quickstart.md) · [데이터와 Qdrant 준비](../../guides/data.md#advanced-tablerag-qdrant-cloud-벡터-검색)
