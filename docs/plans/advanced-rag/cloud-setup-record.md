# ADEN 클라우드 배포 기반 준비 기록

- 작업일: 2026-11-12
- 사용자 확정: `aden.29232923.xyz`, 누구나 공개 접근, **기존 자원만 사용**.
- 상태: **DNS·HTTPS·서버 전용 경로·GitHub 배포 환경 준비 완료. RAG 앱과 CD 워크플로는 미배포.**
- 구현 순서: [구조 정리·CI/CD 계획](architecture-operations-implementation-plan.md).
- 적용 설정 원본: [infra/production](../../../infra/production/README.md).

## 1. 확인한 기존 자원

| 항목 | 확인값 |
| --- | --- |
| 서버 | 기존 OCI 서버 `oci-free-01` |
| 접속 | 기존 `my-cloud` SSH 별칭. 알려진 호스트 키 검증 유지 |
| 공인 IPv4 | `129.225.142.54` |
| OS·CPU | Ubuntu 24.04.5 LTS, aarch64, 2개 논리 CPU |
| 메모리 | 총 11,925MiB, 조사 시 available 약 9,634MiB |
| 루트 디스크 | 약 48GB 중 사용 38GB·여유 11GB |
| 기존 서비스 | 사내비 API·색인 컨테이너, Nginx, Certbot |
| 기존 API 바인딩 | `127.0.0.1:18000` |
| DNS | Cloudflare. 기존 apex·sanaebi·excel 레코드 보존 |

자원 수치는 조사 시점의 값이다. OCI API의 shape 조회 결과가 아니라 서버에서 확인한 OS·자원 상태다. OCI CLI 3.95.0과 CODEX 프로필은 존재했지만 security-token 세션은 만료돼 API 인벤토리를 조회하지 못했다. 이번 작업에는 VM·네트워크 신규 생성이 필요 없어 유효한 SSH 접속을 사용했으며 CLI 세션을 새로 만들지 않았다.

현재 로컬 번들은 약 1.4GB, 원본 PDF는 약 88MB다. KURE-v3 캐시 디렉터리는 약 2.2GB였다. 이 크기를 RAM이나 배포 시 필요한 전체 용량으로 해석하지 않는다. 이미지·모델 캐시·복구용 자산을 포함한 디스크와 추론 메모리를 첫 앱 배포 전에 실측해야 한다.

## 2. 적용한 설정

### DNS와 HTTPS

- A 레코드 `aden.29232923.xyz → 129.225.142.54`를 추가했다.
- 기존 레코드와 같은 DNS only 방식, TTL Auto를 사용했다. 기존 DNS 레코드는 수정하지 않았다.
- 별도 인증서 `aden.29232923.xyz`를 기존 Certbot webroot 방식으로 발급했다.
- 확인한 인증서 만료일: **2027-02-10**. 서버의 기존 Certbot timer가 갱신을 담당한다.
- `/etc/letsencrypt/renewal-hooks/deploy/aden-reload-nginx`는 ADEN 인증서 갱신일 때만 Nginx 검사 후 reload한다.
- HTTP는 HTTPS로 308 전환한다. ACME challenge 경로는 기존 `/var/www/letsencrypt`를 사용한다.

Cloudflare 전용 CLI·토큰 설정을 알려진 로컬·서버 위치에서 찾지 못해, 사용자가 로그인한 브라우저에서 DNS 레코드를 추가했다. OCI CLI 인증과 Cloudflare DNS 인증은 별개다.

### 서버 격리

| 경로·항목 | 용도 |
| --- | --- |
| `/srv/aden/releases/` | 향후 웹·배포 릴리스 |
| `/srv/aden/shared/bundle/` | 검증된 SQLite·검색 번들 |
| `/srv/aden/shared/documents/` | 원본 PDF |
| `/srv/aden/shared/models/` | 고정 모델 캐시 |
| `/srv/aden/shared/runs/` | 요청 실행 기록 |
| `/etc/aden/` | ADEN 전용 런타임 설정·비밀 주입 경계 |
| `/var/lib/aden/` | 배포 준비 기준·향후 상태 기록 |
| `aden` 계정 | 로그인 불가 런타임 계정, UID 994·GID 985 |
| `aden-deploy` 계정 | 별도 배포 접속 계정. Docker 그룹·sudo 권한 없음 |
| `127.0.0.1:18010` | 미래 ADEN API 전용 포트. 현재 앱 리스너 없음 |

현재 데이터·모델·런타임 API 키는 서버로 복사하지 않았다. 디렉터리 준비와 실제 애플리케이션 준비를 구분한다.

### Nginx

- `/etc/nginx/sites-available/aden`와 전용 enabled 링크를 추가했다.
- `/etc/nginx/conf.d/aden-limits.conf`의 zone은 ADEN에서만 사용한다.
- 정적 웹 경로는 `/srv/aden/current/web`, API 대상은 `127.0.0.1:18010`이다.
- SSE 버퍼링·캐시를 끄고 300초 프록시 대기 한도를 적용했다. API 프록시는 PDF Range 헤더를 별도로 제거하지 않는다.
- 초기 질의 제한은 IP별 분당 6건·burst 2, IP별 동시 1건·전체 동시 2건이다.
- 클라이언트가 제공한 X-Forwarded-For를 그대로 신뢰하지 않고 직접 연결 주소로 전달한다. 향후 Cloudflare proxy를 켜면 신뢰 프록시 설정을 별도 검토해야 한다.
- 원문·기록을 Nginx 정적 루트에 두지 않는다. 현재는 앱이 없어 웹·API에 503을 반환한다.
- 전용 access/error 로그는 기존 Nginx 로그 회전의 `/var/log/nginx/*.log` 대상에 포함된다.

위 제한은 비용 상한이나 처리량 보장이 아니다. 여러 IP·실제 도구 호출량에 대한 앱 수준의 수용·사용량 제한은 후속 구현에서 검증한다.

### GitHub 배포 환경

대상: `Somali-AI-Pirates/ADEN`의 `production`.

- 허용 deployment branch는 `main` 하나로 설정했다.
- 별도 승인 대기는 추가하지 않았다. 기존 PR 리뷰·CI 규칙은 변경하지 않았다.
- 환경 Secret `ADEN_DEPLOY_SSH_KEY`를 등록했다. 개인키 값은 문서·로그·저장소에 기록하지 않았다.
- 개인키는 로컬 사용자 SSH 디렉터리의 `aden_deploy_ed25519`에 0600 권한으로 보관한다. 관리용 SSH 키를 GitHub에 복사하지 않았다.
- 서버의 authorized key는 `restrict`와 고정 `aden-deploy-gate` 명령을 사용한다.
- 현재 gate는 `aden-status`만 허용한다. 셸·임의 명령·Docker·실제 배포는 사용할 수 없다.
- 호스트 키는 이미 검증된 관리 SSH 연결로 서버의 **공개 호스트 키**를 읽어 고정했다. 검증 없는 ssh-keyscan 결과를 신뢰하지 않았다.
- `ADEN_DEPLOY_ENABLED=false`로 두었다. 워크플로·이미지·실제 배포 명령이 구현되고 검증되기 전 자동 배포를 활성화하지 않는다.

| 환경 변수 | 값 또는 용도 |
| --- | --- |
| ADEN_DEPLOY_ENABLED | false |
| ADEN_DOMAIN | aden.29232923.xyz |
| ADEN_PUBLIC_URL | https://aden.29232923.xyz |
| ADEN_DEPLOY_HOST / PORT | 129.225.142.54 / 22 |
| ADEN_DEPLOY_USER | aden-deploy |
| ADEN_DEPLOY_ROOT | /srv/aden |
| ADEN_API_PORT | 18010 |
| ADEN_BUILD_PLATFORM | linux/arm64 |
| ADEN_RUNTIME_UID / GID | 994 / 985 |
| ADEN_SSH_KNOWN_HOSTS | 검증된 서버 공개 호스트 키. 값은 이 문서에 복사하지 않음 |

배포 개인키를 등록했다고 실제 CD가 동작하는 것은 아니다. 현재 키는 준비 상태 확인에만 사용할 수 있다. 후속 구현에서 검증한 이미지·ADEN 경로만 처리하는 배포 명령을 만들고, 그때 gate의 허용 동작을 좁게 확장한다.

## 3. 검증 결과

| 확인 | 결과 |
| --- | --- |
| DNS 조회 | aden.29232923.xyz가 기존 서버 IPv4로 해석됨 |
| `nginx -t` | 전체 설정 검사 통과 |
| HTTPS | 인증서·호스트 검증을 켠 요청 성공. 앱 미배포 503 |
| HTTP | 308, 같은 aden HTTPS 주소로 이동 |
| 기존 사내비 | HTTPS 200 |
| 기존 기본 도메인 | 기존 사내비로 301 유지 |
| 기존 Nginx 파일 | 작업 전 20개 파일 SHA-256과 일치 |
| SSH `aden-status` | exit 0, deployment_enabled=false |
| SSH `id` 및 `aden-status; id` | 모두 exit 64로 거절 |
| GitHub 환경 재조회 | main 정책, Secret 이름, 등록 변수 확인 |
| 인증서 갱신 dry-run·deploy hook | ADEN 인증서만 대상으로 통과, Nginx 검사·reload 성공 |
| 서버 설정 원본 대조 | 저장소의 Nginx·제한·SSH gate·갱신 hook 4개와 서버 SHA-256 일치 |
| 기존 컨테이너 | 두 컨테이너의 이름·ID 유지 |
| 배포 환경 변수 | 12개 재조회·기대값 확인. 호스트 키도 로컬 고정값과 일치 |
| 문서·스크립트 | 상대 링크 62개, 코드 블록, sh -n, git diff --check 통과 |

인증서 갱신 검사는 다음 명령으로 최종 통과했다.

```bash
sudo certbot renew \
  --cert-name aden.29232923.xyz \
  --dry-run --run-deploy-hooks --non-interactive \
  --no-random-sleep-on-renew
```

첫 갱신 검사는 Certbot의 기본 무작위 대기로 실행 시간 한도를 넘었다. 해당 ADEN 시험 프로세스만 확인해 종료한 뒤, 대기를 끈 위 명령으로 다시 검증했다. 다른 인증서 갱신 작업은 종료하지 않았다.

초기 SSH host-key 조회는 기본 known_hosts에서 찾지 못했다. 실제 기존 별칭이 별도 known_hosts를 사용함을 확인하고, 검증된 관리 연결로 얻은 호스트 키를 별도 파일에 고정했다. SSH 호스트 검증을 끈 적은 없다.

마지막 외부 요청 점검 중 로컬 DNS 해석이 한 번 실패했으나, 일반 DNS 조회와 TLS 검증을 켠 동일 요청 재실행은 모두 통과했다. 호스트 강제 지정이나 TLS 검증 해제로 우회하지 않았다.

비밀값을 포함하지 않는 상세 결과는 로컬 `artifacts/deployment-preparation/`에 있다. 설정 원본은 Git에 포함 가능한 `infra/production/`에 저장했다. 독립 에이전트 리뷰는 이 별도 대화의 제한으로 수행하지 않았으며 자체 점검했다.

## 4. 아직 완료하지 않은 범위

- 제품 설정·그래프·도구·구축 코드의 리팩터링.
- ARM64 API·웹 이미지 빌드와 용량·메모리·추론 지연 실측.
- 서버의 번들·모델·원본·런타임 비밀값 준비.
- main의 CI 통과 후 이미지 게시·배포·복구를 실행하는 워크플로.
- 실제 배포 명령과 gate 확장, rollback 시험.
- 앱이 연결된 SSE·PDF·실제 질문·Langfuse·Ragas 검증.
- 앱 실행 기록의 별도 백업 목적지와 복구 시험.
- Ruff·전체 pytest·패키지 검사 재실행. 앱 코드는 이 작업에서 수정하지 않았다.

현재 URL의 503은 의도한 **미배포 상태**다. 서비스가 준비됐다는 200 응답이나 실제 챗봇 운영 완료로 보고하지 않는다. 추가 유료 VM·볼륨·네트워크 서비스를 만들지 않았고, 기존 사내비 컨테이너·데이터를 변경하지 않았다.
