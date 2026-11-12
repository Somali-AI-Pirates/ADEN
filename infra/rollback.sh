#!/bin/sh
# Rollback always selects the server's stored previous release; it has no user-supplied target.
set -eu
[ "$#" -eq 0 ] || { echo 'Usage: infra/rollback.sh' >&2; exit 64; }
: "${ADEN_DEPLOY_HOST:?ADEN_DEPLOY_HOST is required}"
: "${ADEN_DEPLOY_USER:?ADEN_DEPLOY_USER is required}"
: "${ADEN_DEPLOY_KEY_FILE:?ADEN_DEPLOY_KEY_FILE is required}"
: "${ADEN_SSH_KNOWN_HOSTS:?ADEN_SSH_KNOWN_HOSTS is required}"
port=${ADEN_DEPLOY_PORT:-22}
case "$ADEN_DEPLOY_HOST" in ''|-*|*[!A-Za-z0-9.-]*) echo 'Invalid deployment host.' >&2; exit 64;; esac
[ "$ADEN_DEPLOY_USER" = aden-deploy ] || { echo 'Invalid deployment user.' >&2; exit 64; }
case "$port" in ''|*[!0-9]*|??????*) echo 'Invalid SSH port.' >&2; exit 64;; esac
[ "$port" -ge 1 ] && [ "$port" -le 65535 ] || { echo 'Invalid SSH port.' >&2; exit 64; }
[ -f "$ADEN_DEPLOY_KEY_FILE" ] && [ ! -L "$ADEN_DEPLOY_KEY_FILE" ] &&
[ -f "$ADEN_SSH_KNOWN_HOSTS" ] && [ ! -L "$ADEN_SSH_KNOWN_HOSTS" ] || { echo 'SSH key or known_hosts is missing or unsafe.' >&2; exit 66; }
printf '%s\n' '{"schema_version":1,"operation":"rollback"}' | ssh -F /dev/null -p "$port" -i "$ADEN_DEPLOY_KEY_FILE" \
    -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes \
    -o GlobalKnownHostsFile=/dev/null -o UserKnownHostsFile="$ADEN_SSH_KNOWN_HOSTS" \
    -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -T \
    "$ADEN_DEPLOY_USER@$ADEN_DEPLOY_HOST" aden-rollback
