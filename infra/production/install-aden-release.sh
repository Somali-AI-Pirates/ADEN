#!/bin/sh
# Proposed one-time server installation; never invoked by deploy.sh or GitHub Actions.
set -eu
[ "$(id -u)" -eq 0 ] || { echo 'Run as root.' >&2; exit 77; }
command -v visudo >/dev/null 2>&1 || { echo 'visudo is required.' >&2; exit 69; }
id aden-deploy >/dev/null 2>&1 || { echo 'Pre-provisioned aden-deploy account is required.' >&2; exit 69; }
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
[ -f "$repo_root/infra/compose.production.yaml" ] || { echo 'Production Compose file is missing.' >&2; exit 66; }
sudoers_tmp=$(mktemp /etc/sudoers.d/.aden-deploy.XXXXXX)
trap 'rm -f "$sudoers_tmp"' EXIT HUP INT TERM
printf '%s\n' 'aden-deploy ALL=(root) NOPASSWD: /usr/local/sbin/aden-release ""' > "$sudoers_tmp"
chown root:root "$sudoers_tmp"
chmod 0440 "$sudoers_tmp"
visudo -cf "$sudoers_tmp" >/dev/null
if [ -e /etc/sudoers.d/aden-deploy ] || [ -L /etc/sudoers.d/aden-deploy ]; then
    cmp -s "$sudoers_tmp" /etc/sudoers.d/aden-deploy || { echo 'Existing sudoers file differs; refusing installation.' >&2; exit 73; }
fi
for directory in /usr/local/libexec /usr/local/bin /usr/local/sbin /etc/aden; do
    if [ -L "$directory" ]; then echo 'Unsafe installation directory.' >&2; exit 73; fi
    if [ ! -d "$directory" ]; then install -d -o root -g root -m 0755 "$directory"; fi
done
if [ -L /etc/aden/compose.production.yaml ]; then echo 'Unsafe Compose target.' >&2; exit 73; fi
install -d -o root -g root -m 0700 /var/lib/aden/deploy
install -o root -g root -m 0640 "$repo_root/infra/production/aden_release.py" /usr/local/libexec/aden_release.py
install -o root -g root -m 0755 "$repo_root/infra/production/aden-deploy-gate" /usr/local/bin/aden-deploy-gate
install -o root -g root -m 0750 "$repo_root/infra/production/aden-release-wrapper" /usr/local/sbin/aden-release
if [ ! -e /etc/aden/compose.production.yaml ]; then
    install -o root -g root -m 0644 "$repo_root/infra/compose.production.yaml" /etc/aden/compose.production.yaml
fi
if [ ! -e /etc/aden/deploy-policy.json ]; then
    install -o root -g root -m 0600 "$repo_root/infra/production/deploy-policy.example.json" /etc/aden/deploy-policy.json
fi
if [ -e /etc/sudoers.d/aden-deploy ] || [ -L /etc/sudoers.d/aden-deploy ]; then
    rm -f "$sudoers_tmp"
else
    mv "$sudoers_tmp" /etc/sudoers.d/aden-deploy
fi
trap - EXIT HUP INT TERM
visudo -cf /etc/sudoers.d/aden-deploy >/dev/null
printf '%s\n' 'Fixed release gate installed; deployment remains disabled until approved fingerprints and runtime assets are provisioned.'
