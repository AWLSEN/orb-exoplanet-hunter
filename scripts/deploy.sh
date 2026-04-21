#!/usr/bin/env bash
# Deploy orb-exoplanet-hunter to Orb Cloud via the docs.orbcloud.dev API.
# Carries every learning from orb-async-dev + orb-antibiotic-scientist:
#   - lang=python (native) so no bin/start wrapper
#   - git fetch+reset in build steps (Orb only clones once)
#   - HTTP_PORT, not ORB_PORT (Orb reserves ORB_PORT)
#   - [resources] runtime_mb + disk_mb as ints
#   - ${SECRET} placeholders resolved via /agents org_secrets
#
# Idempotent: every step checks .orb-state/ before hitting the API.
# Re-runs just push the latest commit via the git-fetch build step.

set -euo pipefail

BASE_URL="${ORB_BASE_URL:-https://api.orbcloud.dev}"
COMPUTER_NAME="${ORB_COMPUTER_NAME:-orb-exoplanet-hunter}"
RUNTIME_MB="${ORB_RUNTIME_MB:-8192}"
DISK_MB="${ORB_DISK_MB:-30720}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${REPO_ROOT}/.orb-state"
mkdir -p "$STATE_DIR"

log() { printf "[deploy] %s\n" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

for bin in curl jq; do
    command -v "$bin" >/dev/null || die "missing required binary: $bin"
done

# --- auth ---------------------------------------------------------------
ensure_key() {
    if [[ -f "$STATE_DIR/api-key" ]]; then
        ORB_API_KEY="$(cat "$STATE_DIR/api-key")"
        export ORB_API_KEY
        return
    fi
    if [[ -n "${ORB_API_KEY:-}" ]]; then
        printf '%s' "$ORB_API_KEY" > "$STATE_DIR/api-key"
        chmod 600 "$STATE_DIR/api-key"
        return
    fi
    : "${ORB_REGISTER_EMAIL:?set ORB_API_KEY or ORB_REGISTER_EMAIL}"
    log "registering new api key for $ORB_REGISTER_EMAIL"
    local resp
    resp=$(curl -fsS -X POST "$BASE_URL/api/v1/auth/register" \
        -H 'Content-Type: application/json' \
        -d "{\"email\":\"$ORB_REGISTER_EMAIL\"}")
    ORB_API_KEY=$(printf '%s' "$resp" | jq -r .api_key)
    [[ -n "$ORB_API_KEY" && "$ORB_API_KEY" != "null" ]] || die "register failed"
    printf '%s' "$ORB_API_KEY" > "$STATE_DIR/api-key"
    chmod 600 "$STATE_DIR/api-key"
    export ORB_API_KEY
}

auth() { echo "Authorization: Bearer $ORB_API_KEY"; }

# --- computer -----------------------------------------------------------
ensure_computer() {
    if [[ -f "$STATE_DIR/computer-id" ]]; then
        COMPUTER_ID="$(cat "$STATE_DIR/computer-id")"
        log "reusing computer $COMPUTER_ID"
        return
    fi
    log "creating computer $COMPUTER_NAME (runtime=${RUNTIME_MB}MB disk=${DISK_MB}MB)"
    local resp
    resp=$(curl -fsS -X POST "$BASE_URL/v1/computers" \
        -H "$(auth)" -H 'Content-Type: application/json' \
        -d "{\"name\":\"$COMPUTER_NAME\",\"runtime_mb\":$RUNTIME_MB,\"disk_mb\":$DISK_MB}")
    COMPUTER_ID=$(printf '%s' "$resp" | jq -r '.computer_id // .id')
    [[ -n "$COMPUTER_ID" && "$COMPUTER_ID" != "null" ]] || die "create computer failed: $resp"
    printf '%s' "$COMPUTER_ID" > "$STATE_DIR/computer-id"
    local short
    short=$(printf '%s' "$resp" | jq -r '.short_id // empty')
    [[ -n "$short" ]] && printf '%s' "$short" > "$STATE_DIR/short-id"
    log "computer created: $COMPUTER_ID"
}

# --- config -------------------------------------------------------------
upload_config() {
    log "uploading orb.toml"
    curl -fsS -X POST "$BASE_URL/v1/computers/$COMPUTER_ID/config" \
        -H "$(auth)" -H 'Content-Type: application/toml' \
        --data-binary "@$REPO_ROOT/orb.toml" >/dev/null
}

# --- build --------------------------------------------------------------
trigger_build() {
    log "building (git fetch + pip install; ~2-4 min)"
    curl -fsS -X POST "$BASE_URL/v1/computers/$COMPUTER_ID/build" \
        -H "$(auth)" -m 900 >/dev/null
}

# --- start agent --------------------------------------------------------
start_agent() {
    : "${ANTHROPIC_AUTH_TOKEN:?ANTHROPIC_AUTH_TOKEN required (Z.AI GLM or anthropic)}"
    # orb.toml also references ${ORB_API_KEY} so /stats can pull per-computer
    # metrics. Orb validates placeholders against org_secrets and 400s if any
    # are missing — forward both.
    local body
    body=$(jq -n --arg k "$ANTHROPIC_AUTH_TOKEN" --arg o "$ORB_API_KEY" \
        '{org_secrets: {ANTHROPIC_AUTH_TOKEN: $k, ORB_API_KEY: $o}}')
    log "starting agent"
    local resp
    resp=$(curl -fsS -X POST "$BASE_URL/v1/computers/$COMPUTER_ID/agents" \
        -H "$(auth)" -H 'Content-Type: application/json' \
        -d "$body")
    local port
    port=$(printf '%s' "$resp" | jq -r '.port // empty')
    [[ -n "$port" ]] && printf '%s' "$port" > "$STATE_DIR/agent-port"
    local short
    short="$(cat "$STATE_DIR/short-id" 2>/dev/null || echo "${COMPUTER_ID:0:8}")"
    local url="https://${short}.orbcloud.dev"
    printf '%s' "$url" > "$STATE_DIR/live-url"
    log "deployed → $url (agent port=$port)"
    printf '%s\n' "$url"
}

case "${1:-deploy}" in
    deploy)
        ensure_key
        ensure_computer
        upload_config
        trigger_build
        start_agent
        ;;
    status)
        ensure_key
        [[ -f "$STATE_DIR/computer-id" ]] || die "not deployed"
        curl -fsS "$BASE_URL/v1/computers/$(cat "$STATE_DIR/computer-id")/agents" -H "$(auth)" | jq .
        ;;
    logs)
        ensure_key
        [[ -f "$STATE_DIR/computer-id" ]] || die "not deployed"
        curl -fsS "$BASE_URL/v1/computers/$(cat "$STATE_DIR/computer-id")/files/agent/data/orchestrator.log" -H "$(auth)"
        ;;
    *)
        die "unknown subcommand: $1 (try deploy|status|logs)"
        ;;
esac
