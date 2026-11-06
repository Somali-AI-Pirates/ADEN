# 데이터 배치와 검증

제공 원본 자료는 Git에 올리지 않는다. Basic은 저장소 루트의 `data/basic/`에 풀고 아래 해시로 같은 파일인지 확인한다. 팀이 검토할 정제본은 [curated/](curated/README.md)에서 Git으로 관리한다.

## 1. 원본
프로젝트(3) Basic 데이터셋의 해운 ZIP 2개를 사용한다.

| ZIP | documents | evaluation |
| --- | --- | --- |
| `Basic_shipping_part01of02` | SB01~SB09 | JSON 8개 |
| `Basic_shipping_part02of02` | SB10~SB15 | JSON 8개 (part01과 같은 파일) |

## 2. 배치
```text
data/basic/
├── documents/    SB01.pdf ~ SB15.pdf (15개, 원본 파일명 그대로)
└── evaluation/   JSON 8개 (한 벌만)
```

Intermediate 원본은 `data/intermediate/documents/IS01.pdf`~`IS50.pdf`에 둔다(50개·3,750쪽). Advanced는 `data/advanced/documents/AS001.pdf`~`AS120.pdf`에 둔다(120개·18,000쪽). 각 난이도의 평가 자료는 기존 `evaluation/`에 별도로 보존한다. 정제 입력은 해당 난이도의 `documents/*.pdf`만 사용하고 난이도별 지식을 섞지 않는다.

| 파일 | 용도 |
| --- | --- |
| `{development,additional,expansion_validation}_questions.json` | `id`, `question`만 있음. 실행기가 읽는 파일 |
| `{development,additional,expansion_validation}.json` | 정답·근거(`evidence_groups`). **검색·생성이 끝난 뒤 평가에서만** 읽는다 |
| `security_questions.json`, `security_tasks.json` | 보안 비교용 |

일반 평가에서는 같은 split의 질문 파일과 참고 파일을 `id`로 대응한다. 예를 들어 `development_questions.json`을 실행할 때 `development.json`을 `--references`로 지정한다. 질문 입력에는 채점 라벨을 넣지 않는다. `run_benchmark`의 `--references` 기본값은 질문 파일과 같은 경로이므로, 두 파일을 별도로 제공한 이 데이터셋에서는 참고 파일 경로를 명시해야 한다. 세 split의 이름만으로 개발·최종 검증 용도가 정해지지는 않으므로, 공식 비교 전에 사용할 split과 역할을 정해 결과에 남긴다. 실행 규칙은 [C 평가 가이드](../docs/guides/c-module-guide.md)를 따른다.

서비스 실행과 분리한 오프라인 준비 점검에서는 질문·참고 파일의 구조와 ID 대응을 먼저 확인한다. 이는 정답을 생성 입력으로 넘기는 단계가 아니다. 실행 파이프라인은 위 표처럼 질문만 서비스에 전달하고, 정답·근거는 실행 기록 저장 뒤 채점에서 읽는다. 현재 CLI에는 참고 파일의 필수 평가 필드 선행 검증이 없으므로 준비 점검을 대신해 주지 않는다.

색인은 `documents/*.pdf`만 읽는다. 정답 JSON은 색인·검색·생성 입력에 넣지 않는다.
일부 PDF와 `security_tasks.json`에는 보안 시험용 지시문이 일부러 들어 있다. 모두 데이터로만 다룬다.

저장소 `.gitignore`는 제공 원본·평가 자료와 `artifacts/`를 Git에서 제외한다. `data/README.md`·자리표시와 `data/curated/`의 명시한 검토 데이터 파일은 추적한다.

원본을 보존하며 공통 규칙·표 행·사례 참조를 나누려면 [오프라인 지식 데이터 준비](../docs/design/knowledge-data.md)를 사용한다. 아래 기존 Qdrant 색인 절차와는 별도의 단계다.

```bash
python -m app.ingestion.scripts.curate --datasets basic intermediate advanced
python -m app.ingestion.scripts.curate --check --render-search
```

정제본은 `data/curated/<dataset>/`에 생성한다. 원본이 Basic만 있으면 `--datasets basic`으로 실행한다. 검색용 기록·규칙은 `artifacts/knowledge/search/<dataset>/<build_id>/`로 분리한다. 외부 모델·벡터DB에는 연결하지 않는다. 기존 `prepare_knowledge` 명령과 `artifacts/knowledge/basic/`의 v1 결과는 호환·과거 비교용으로 남기며 새 검토본의 기준으로 덮어쓰지 않는다. 스키마와 정규화 한계는 위 문서를 따른다.

## 3. 검증
PDF 15개, 각 24쪽, 총 360쪽이다. Git Bash에서 `data/basic/documents`로 이동해 아래 해시와 비교한다.
```bash
sha256sum *.pdf
```
```text
8bb26df3c546f89abddf5d8750bf8823dc291d51926b3670cb6a788e34933d93  SB01.pdf
d758b4e07825999f050dc2ad3d742d9b850c06db34fe34681011ecf915779736  SB02.pdf
7649b60c206abf687225711aac9bf8dacb5e6fb2bb3503ba2b14112eaece0ccd  SB03.pdf
4498d4e1a92861d67aad3f900a603f49d1b61b706b29d8e5756ada004450f8b9  SB04.pdf
6b90e9f57ee2aa58f585c621c94e4240578832422020c14d28c925de7d8dafbc  SB05.pdf
2ce49febe49ae5dc1ec780873e282395925c802051f532bb44b3f7d8884f33c0  SB06.pdf
ac1a46deb897f5d4568a8f6148aa9f3e9f8839d1e52df5eddef3df75ab3476a0  SB07.pdf
c57c33d30aa958e27dc0db1d319bada9336a63e2188a10a661e9f724709f5eba  SB08.pdf
dbdb2c2297a1eeea2a1753928735e9956c0df1ddfa54945268651fb5b4960f93  SB09.pdf
b24fa78ffbae570db25fd8834d6bd11fc3778e2f287a2e42c61ac3b35f9d3cb8  SB10.pdf
2bf92073d9d5687bfcd06c56213281557deee1c4b3beb94c91ca1f34ce93aa19  SB11.pdf
b8d29debedde03821a4c84424d9860d12ce34d3a08fa6345c7a9fa35a297c081  SB12.pdf
47dc421f9c2a77611d16555834f54d85e5c7589c6aa158687ef76220aa16bf89  SB13.pdf
aa971e1bf8dd590ab3a23eafe80571ae3286afc778b34ff7cc8ebf1e26373acc  SB14.pdf
5eda541cf900103b814418349d6593ab9a31a14e7cb890d7367e53887381bdde  SB15.pdf
```

## 4. Qdrant Cloud 접속 정보
벡터 색인은 팀 공용 Qdrant Cloud 클러스터(무료 티어)에 둔다. 저장소 루트에 `.env`를 만들고 두 값을 넣는다.
`.env`는 Git에서 제외되어 있다. 키를 코드·문서·로그·채팅에 붙여 넣지 않는다.
```text
QDRANT_URL=https://<cluster-id>.<region>.cloud.qdrant.io:6333
QDRANT_API_KEY=<클러스터 API 키>
```
2026-11-06 확인 기준으로 무료 클러스터는 1주일 동안 사용하지 않으면 일시정지되고, 4주가 지나면 삭제된다. 정지되면 Qdrant Cloud 콘솔에서 다시 켠다. [Qdrant Cloud 공식 문서](https://qdrant.tech/cloud/)

## 5. 색인 생성
저장소 루트에서 실행한다. [개발 가이드](../docs/guides/development.md)의 공통 설치 절차를 따른다. 처음 새 색인을 만들 때는 E5 모델(약 470MB)을 내려받는다.
```bash
python -m pip install -e '.[test]'
python -m app.indexing.scripts.build_index
```
2026-11-06 작업 기록의 확인 버전: pypdf 6.21.4, sentence-transformers 6.2.0, qdrant-client 1.20.0, python-dotenv 1.2.4, torch 2.15.0(CPU). 현재 설치 환경의 검증된 잠금 파일을 뜻하지 않는다.

컬렉션 이름은 `<데이터 폴더>_<모델>_p<청크 글자 수>`이다 (Basic 기본값: `basic_multilingual-e5-small_p800`). 같은 이름이 이미 있으면 기본적으로 중단한다. 색인을 새로 만들기 전에는 기존 `artifacts/basic/index_manifest.json`과 팀 공용 컬렉션을 확인한다. 구조 이전 자체로 색인을 다시 만들지 않는다. 컬렉션을 삭제하는 `--recreate`는 색인을 의도적으로 재생성하기로 합의한 경우에만 사용한다. 다른 구성원도 같은 manifest의 컬렉션 이름과 설정을 사용한다.

처음 색인을 생성해 새 컬렉션을 만들 수 있으면 다음과 같은 요약이 나온다. 숫자는 2026-11-06 A의 처리 기록에 나온 예시다. 기존 컬렉션이 이미 있으면 보호를 위해 색인 생성 중간에 멈추므로, 이 출력 예시가 매 실행의 예상 결과는 아니다.
```text
추출·청킹 | PDF 15개, 페이지 360개, 청크 620개
  빈 페이지 0개, 실패 0개 []
임베딩 입력 토큰 최대 482 / 한도 512 | 초과 0개
Dense 색인 → Qdrant Cloud 'basic_multilingual-e5-small_p800'
자체 점검 OK: 청크 본문으로 검색하면 자기 청크가 1위
```

| 출력 | 내용 |
| --- | --- |
| `artifacts/basic/pages.jsonl` | 페이지별 원문 (`book_id`, `file_name`, `page`, `text`) |
| `artifacts/basic/chunks.jsonl` | 청크 (`chunk_id` = `SB01-p1-c1`, 페이지를 넘지 않음) |
| `artifacts/basic/ingestion_report.json` | 파일별 쪽수·청크 수, 빈 페이지, 실패 |
| `artifacts/basic/index_manifest.json` | 임베딩 모델·차원·접두어, 청크 파일 해시, 컬렉션 이름 |
| Qdrant Cloud 컬렉션 | 청크 벡터와 청크 정보(payload) |

팀 공식 비교에서 같은 색인을 썼다는 증거로 실행 시점의 `index_manifest.json`과 컬렉션 이름을 보존한다. 현재 benchmark manifest는 색인 manifest hash를 자동으로 포함하지 않으므로, 공식 결과와 함께 다음 파일들의 SHA-256을 수동으로 기록한다. 질문·정답 파일은 Git에서 제외되어 있으므로 두 파일 hash를 각각 남긴다.

```bash
sha256sum data/basic/evaluation/development_questions.json data/basic/evaluation/development.json artifacts/basic/index_manifest.json
```

팀 공식 비교·제출 결과에는 해당 질문·정답 파일과 색인 manifest의 hash를 함께 기록한다. 개인 실험에는 이 기록 형식이나 폴더 구조를 요구하지 않는다. 다른 split을 쓰면 명령의 `development` 경로를 그 split 이름으로 바꾼다.

## 6. 검색만 확인
LLM 생성 없이 상위 5개 청크의 문서로 Hit@5·Recall@5를 잰다. 검색 시간에는 클라우드 왕복 시간이 포함된다. 이 실행은 검색 전용 측정이며 생성 모델을 포함한 전체 서비스 통합 검증을 뜻하지 않는다.
```bash
python -m app.evaluation.scripts.eval_retrieval --mode naive
```

2026-11-05 기록 (클러스터 리전 us-west, Naive, k=5; 당시 검색 전용 실행):

| 질문 | 채점 / N/A | Hit@5 | Recall@5 | 검색 시간 중앙값 |
| --- | --- | --- | --- | --- |
| development 30 | 28 / 2 | 0.929 | 0.564 | 142ms |
| additional 30 | 28 / 2 | 0.929 | 0.641 | 140ms |

같은 색인을 로컬 모드로 쟀을 때와 점수는 같고, 검색 시간은 약 10ms에서 140ms로 늘었다고 당시 기록했다. 이 과거 수치는 현재 기능별 구조에서의 통합 검증 결과가 아니다.
