# 데이터 배치와 검증

제공 자료는 Git에 올리지 않는다. 각자 아래 위치에 풀고 해시로 같은 파일인지 확인한다.

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

| 파일 | 용도 |
| --- | --- |
| `{development,additional,expansion_validation}_questions.json` | `id`, `question`만 있음. 실행기가 읽는 파일 |
| `{development,additional,expansion_validation}.json` | 정답·근거(`evidence_groups`). **검색·생성이 끝난 뒤 평가에서만** 읽는다 |
| `security_questions.json`, `security_tasks.json` | 보안 비교용 |

색인은 `documents/*.pdf`만 읽는다. 정답 JSON은 색인·검색·생성 입력에 넣지 않는다.
일부 PDF와 `security_tasks.json`에는 보안 시험용 지시문이 일부러 들어 있다. 모두 데이터로만 다룬다.

Git 제외 규칙은 B가 `.gitignore`에 취합한다. 반영 전까지는 각자 `.git/info/exclude`에 아래를 추가한다.
```text
/data/basic/documents/
/data/basic/evaluation/
/artifacts/
```

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
무료 클러스터는 1주일 동안 쓰지 않으면 일시정지되고, 4주가 지나면 삭제된다. 정지되면 Qdrant Cloud 콘솔에서 다시 켠다.

## 5. 색인 생성
저장소 루트에서 실행한다. 처음 실행하면 E5 모델(약 470MB)을 내려받는다.
```bash
pip install pypdf sentence-transformers qdrant-client python-dotenv
python -m member_ysl727727.scripts.build_index
```
확인한 버전: pypdf 6.21.4, sentence-transformers 6.2.0, qdrant-client 1.20.0, python-dotenv 1.2.4, torch 2.15.0(CPU).

컬렉션 이름은 `<데이터 폴더>_<모델>_p<청크 글자 수>`이다 (Basic 기본값: `basic_multilingual-e5-small_p800`).
같은 이름이 이미 있으면 멈춘다. 팀원이 쓰는 중일 수 있으므로, 확인한 뒤에만 `--recreate`로 다시 만든다.
색인은 담당자(A) 한 명이 만들고, 다른 팀원은 같은 `artifacts/basic/index_manifest.json`으로 컬렉션 이름을 맞춘다.

정상이면 다음과 같이 나온다.
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

## 6. 검색만 확인
LLM 없이 상위 5개 청크의 문서로 Hit@5·Recall@5를 잰다. 검색 시간에는 클라우드 왕복 시간이 포함된다.
```bash
python -m member_ysl727727.scripts.eval_retrieval --mode naive
```

2026-11-05 측정 (클러스터 리전 us-west, Naive, k=5):

| 질문 | 채점 / N/A | Hit@5 | Recall@5 | 검색 시간 중앙값 |
| --- | --- | --- | --- | --- |
| development 30 | 28 / 2 | 0.929 | 0.564 | 142ms |
| additional 30 | 28 / 2 | 0.929 | 0.641 | 140ms |

같은 색인을 로컬 모드로 쟀을 때와 점수는 같고, 검색 시간만 약 10ms에서 140ms로 늘었다.
