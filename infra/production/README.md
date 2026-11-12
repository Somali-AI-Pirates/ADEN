# ADEN 운영 설정과 고정 배포 명령

이 디렉터리는 기존 OCI 서버의 ADEN 전용 설정과 배포 프로그램을 담는다. 설치·자산 준비·복구는 [배포 가이드](../../docs/guides/deployment.md), 기존 서버에 적용된 DNS·HTTPS·상태 확인 gate는 [클라우드 준비 기록](../../docs/plans/advanced-rag/cloud-setup-record.md)을 따른다.

- `aden-http.conf`: 인증서 발급 전 HTTP challenge와 미배포 503 응답.
- `aden.conf`: HTTPS, 정적 웹 경로, loopback API 포트 18010, SSE·PDF 프록시, readiness 차단과 API 오류 전달.
- `aden-limits.conf`: ADEN 질의에만 적용하는 초기 IP별 분당 6건·burst 2·동시 1건, 전체 동시 2건 제한.
- `aden-deploy-gate`: 전용 SSH 키의 `aden-status`, `aden-deploy`, `aden-rollback` 명령을 고정 wrapper로 전달한다.
- `aden-reload-nginx`: ADEN 인증서 갱신 때만 Nginx 검사·reload.

기존 사내비의 vhost·컨테이너·데이터를 수정하지 않는다. ADEN의 실제 앱이 준비되기 전 웹과 API는 503을 반환한다. 503은 배포 성공이 아니라 미배포 상태다.

첫 배포 때 서버의 실제 메모리·디스크 여유와 공개 질의 제한을 확인한다. TLS 키와 배포 개인키는 저장소에 포함하지 않는다. 공통 Nginx 설정은 기존 서버에서 사용하므로 적용 전후 전체 `nginx -t`와 기존 도메인의 응답을 함께 확인한다.

Nginx 파일은 `/etc/nginx/sites-available/aden`, 제한 설정은 `/etc/nginx/conf.d/aden-limits.conf`에 배치한다. 인증서는 기존 Certbot webroot `/var/www/letsencrypt`를 사용하며 사내비 인증서와 분리한다. 이 파일들을 다른 서버에 적용하기 전 도메인·포트·경로를 확인한다.
