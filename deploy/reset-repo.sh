#!/bin/bash
# Prepare or reset cloud index state — NEW and EXISTING repos.
# Detects on-disk index: existing → update (incremental); missing → create (full embed).
# Every mode supports --dry-run (plan only, no changes).
#
# Usage (on server as root):
#   ./reset-repo.sh fix-perms [owner/repo] [--dry-run]
#   ./reset-repo.sh prepare owner/repo [--index] [--dry-run]   # smart: update OR create
#   ./reset-repo.sh soft-reset owner/repo [--index] [--dry-run]
#   ./reset-repo.sh full-reset owner/repo [--index] [--dry-run]
#   ./reset-repo.sh delete-repo owner/repo [--dry-run]
#   ./reset-repo.sh inspect owner/repo [--dry-run]             # state + planned actions
#
# Env: BRANCH=master  COMPOSE_DIR=/opt/codewalk  API_URL=https://api.codewalk.xyz
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="${COMPOSE_DIR:-/opt/codewalk}"
STORAGE_ROOT="${INDEX_STORAGE_PATH:-/var/codewalk}"
API_URL="${API_URL:-https://api.codewalk.xyz}"
ENV_FILE="${ENV_FILE:-${COMPOSE_DIR}/.env}"
BRANCH="${BRANCH:-master}"

MODE="${1:-}"
shift 2>/dev/null || true

REPO=""
TRIGGER_INDEX=false
DRY_RUN=false

for arg in "$@"; do
    case "${arg}" in
        --index) TRIGGER_INDEX=true ;;
        --dry-run) DRY_RUN=true ;;
        */*) REPO="${arg}" ;;
        *)
            echo "❌ Unknown argument: ${arg}" >&2
            usage
            ;;
    esac
done

usage() {
    sed -n '3,15p' "$0" | sed 's/^# \?//'
    exit 1
}

[ -n "${MODE}" ] || usage

log() { echo "$@"; }
dry() { echo "  [dry-run] $*"; }
run_or_dry() {
    local desc="$1"
    shift
    if ${DRY_RUN}; then
        dry "${desc}"
    else
        "$@"
    fi
}

require_compose() {
    [ -d "${COMPOSE_DIR}" ] || { echo "❌ COMPOSE_DIR not found: ${COMPOSE_DIR}" >&2; exit 1; }
}

psql_exec() {
    if ${DRY_RUN}; then
        dry "SQL: $1"
        return 0
    fi
    docker compose -f "${COMPOSE_DIR}/docker-compose.yml" exec -T postgres \
        psql -U codewalk -d codewalk -v ON_ERROR_STOP=1 -c "$1"
}

psql_scalar() {
    if ${DRY_RUN}; then
        # Best-effort read even in dry-run (inspect needs real state)
        docker compose -f "${COMPOSE_DIR}/docker-compose.yml" exec -T postgres \
            psql -U codewalk -d codewalk -t -A -c "$1" 2>/dev/null | tr -d '[:space:]' || true
        return 0
    fi
    docker compose -f "${COMPOSE_DIR}/docker-compose.yml" exec -T postgres \
        psql -U codewalk -d codewalk -t -A -c "$1" 2>/dev/null | tr -d '[:space:]'
}

load_admin_key() {
    if [ -z "${ADMIN_API_KEY:-}" ] && [ -f "${ENV_FILE}" ]; then
        ADMIN_API_KEY="$(grep '^ADMIN_API_KEY=' "${ENV_FILE}" | cut -d= -f2- | tr -d '\r' || true)"
    fi
    if [ -z "${ADMIN_API_KEY:-}" ]; then
        echo "❌ ADMIN_API_KEY not set and not found in ${ENV_FILE}" >&2
        exit 1
    fi
}

index_dir() { echo "${STORAGE_ROOT}/indexes/${REPO}"; }
clone_dir() { echo "${STORAGE_ROOT}/repos/${REPO}"; }

repo_in_db() {
    local n
    n="$(psql_scalar "SELECT COUNT(*) FROM repos WHERE full_name='${REPO}';" 2>/dev/null || echo 0)"
    [ "${n:-0}" != "0" ]
}

index_on_disk() {
    local idir chroma
    idir="$(index_dir)"
    chroma="${idir}/chroma"
    [ -f "${idir}/manifest.json" ] || { [ -d "${chroma}" ] && [ -n "$(ls -A "${chroma}" 2>/dev/null)" ]; }
}

index_looks_corrupt() {
    # chroma or duckdb partial without manifest — UPDATE may fail; soft-reset safer
    local idir
    idir="$(index_dir)"
    [ ! -f "${idir}/manifest.json" ] && index_on_disk
}

clone_on_disk() {
    [ -d "$(clone_dir)/.git" ]
}

index_size_human() {
    du -sh "$(index_dir)" 2>/dev/null | cut -f1 || echo "0"
}

# create = full embed (empty chroma); update = incremental (admin/index / webhook path)
index_action() {
    if index_on_disk; then
        echo "update"
    else
        echo "create"
    fi
}

print_repo_state() {
    local action db_status db_sha db_ver
    log "┌─ Repo: ${REPO}"
    if repo_in_db; then
        db_status="$(psql_scalar "SELECT index_status FROM repos WHERE full_name='${REPO}';")"
        db_sha="$(psql_scalar "SELECT COALESCE(last_indexed_sha,'') FROM repos WHERE full_name='${REPO}';")"
        db_ver="$(psql_scalar "SELECT COALESCE(index_version::text,'0') FROM repos WHERE full_name='${REPO}';")"
        log "│  Postgres: registered (status=${db_status:-?}, version=${db_ver:-?}, sha=${db_sha:-none})"
    else
        log "│  Postgres: not registered (new)"
    fi
    if index_on_disk; then
        log "│  Index disk: present ($(index_size_human) at $(index_dir))"
        [ -f "$(index_dir)/manifest.json" ] && log "│    manifest.json: yes" || log "│    manifest.json: no"
        [ -d "$(index_dir)/chroma" ] && log "│    chroma/: yes" || log "│    chroma/: no"
        [ -f "$(index_dir)/graph.duckdb" ] && log "│    graph.duckdb: yes" || log "│    graph.duckdb: no"
    else
        log "│  Index disk: absent (will CREATE on --index)"
    fi
    if clone_on_disk; then
        log "│  Clone disk: present at $(clone_dir)"
    else
        log "│  Clone disk: absent (clone on --index)"
    fi
    if repo_in_db; then
        local job_err
        job_err="$(psql_scalar "SELECT COALESCE(error,'') FROM jobs WHERE repo_name='${REPO}' ORDER BY queued_at DESC LIMIT 1;")"
        [ -n "${job_err}" ] && log "│  Last job error: ${job_err}"
    fi
    if index_looks_corrupt; then
        log "│  ⚠️  Corrupt/partial index (no manifest) — use soft-reset --index, not prepare"
    elif [ "$(psql_scalar "SELECT COALESCE(index_status,'') FROM repos WHERE full_name='${REPO}';" 2>/dev/null || true)" = "failed" ]; then
        log "│  ⚠️  index_status=failed — prepare --index cancels stuck jobs and retries"
    fi
    log "└─"
}

print_plan() {
    local mode="$1"
    local action
    action="$(index_action)"
    log ""
    log "Plan (${mode}${DRY_RUN:+ · dry-run}${TRIGGER_INDEX:+ · --index}):"
    case "${mode}" in
        fix-perms)
            if [ -n "${REPO}" ]; then
                dry "ensure dirs + chown for ${REPO}"
                dry "container write test on indexes/${REPO}"
            else
                dry "ensure dirs + chown for ALL repos (postgres + disk)"
            fi
            ;;
        prepare)
            dry "ensure dirs + chown (no wipe)"
            dry "container write test"
            if repo_in_db; then
                dry "postgres: keep row (no token rotation)"
            else
                dry "postgres: REGISTER via POST /admin/register"
            fi
            if ${TRIGGER_INDEX}; then
                if [ "${action}" = "update" ]; then
                    dry "index: UPDATE existing (incremental re-index via POST /admin/index)"
                else
                    dry "index: CREATE new (full embed via POST /admin/index)"
                fi
            fi
            ;;
        soft-reset)
            dry "DELETE $(index_dir) (wipe index artifacts)"
            dry "KEEP $(clone_dir) if present"
            dry "postgres: reset to pending (if registered)"
            dry "jobs: cancel queued/running"
            if ${TRIGGER_INDEX}; then
                dry "index: CREATE (full embed after wipe)"
            fi
            ;;
        full-reset)
            dry "DELETE $(index_dir) and $(clone_dir)"
            dry "postgres: reset to pending (if registered)"
            dry "jobs: cancel queued/running"
            if ${TRIGGER_INDEX}; then
                dry "index: CREATE (full embed + fresh clone)"
            fi
            ;;
        delete-repo)
            dry "DELETE $(index_dir) and $(clone_dir)"
            if repo_in_db; then
                dry "postgres: DELETE jobs, webhook_deliveries, repos rows"
            else
                dry "postgres: no row to delete"
            fi
            ;;
    esac
    log ""
}

ensure_storage() {
    local extra=()
    ${DRY_RUN} && extra+=(--dry-run)
    COMPOSE_DIR="${COMPOSE_DIR}" bash "${SCRIPT_DIR}/ensure-storage.sh" "${extra[@]}" "${REPO}"
}

ensure_storage_all() {
    local extra=()
    ${DRY_RUN} && extra+=(--dry-run)
    COMPOSE_DIR="${COMPOSE_DIR}" bash "${SCRIPT_DIR}/ensure-storage.sh" "${extra[@]}" --all
}

verify_container_write() {
    local test_file="/var/codewalk/indexes/${REPO}/.write_test"
    if ${DRY_RUN}; then
        dry "docker exec codewalk-api touch ${test_file} && rm"
        return 0
    fi
    docker compose -f "${COMPOSE_DIR}/docker-compose.yml" exec -T codewalk-api \
        touch "${test_file}"
    docker compose -f "${COMPOSE_DIR}/docker-compose.yml" exec -T codewalk-api \
        rm -f "${test_file}"
    log "==> Container write test OK for indexes/${REPO}"
}

ensure_repo_registered() {
    if repo_in_db; then
        log "==> Repo already registered in Postgres: ${REPO}"
        return 0
    fi
    load_admin_key
    if ${DRY_RUN}; then
        dry "POST ${API_URL}/admin/register full_name=${REPO} branch=${BRANCH}"
        return 0
    fi
    log "==> Registering new repo: ${REPO} (branch=${BRANCH})"
    local resp
    resp="$(curl -sf -X POST "${API_URL}/admin/register" \
        -H "X-Admin-Key: ${ADMIN_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "{\"full_name\": \"${REPO}\", \"branch\": \"${BRANCH}\"}")"
    echo "${resp}" | python3 -m json.tool
    log "==> Save repo_token from above into MCP CODEWALK_REPO_TOKEN"
}

reset_db_pending() {
    if ! repo_in_db; then
        log "==> No Postgres row for ${REPO} (new repo — skip DB reset)"
        return 0
    fi
    psql_exec "UPDATE repos SET last_indexed_sha=NULL, index_status='pending', index_version=0
               WHERE full_name='${REPO}';"
    ${DRY_RUN} || log "==> Postgres row reset to pending"
}

cancel_jobs() {
    if ! repo_in_db; then
        return 0
    fi
    local reason="${1:-cancelled by reset-repo.sh}"
    psql_exec "UPDATE jobs SET status='failed', error='${reason}', finished_at=NOW()
               WHERE repo_name='${REPO}' AND status IN ('queued','running');"
}

unstick_for_retry() {
    # Failed/zombie indexing row — safe before prepare --index
    if ! repo_in_db; then
        return 0
    fi
    cancel_jobs "unstick for retry"
    psql_exec "UPDATE repos SET index_status='pending', updated_at=NOW()
               WHERE full_name='${REPO}' AND index_status IN ('failed','indexing');"
}

trigger_index() {
    # Optional 1st arg: create | update | auto (default: detect from disk)
    local force="${1:-auto}"
    ensure_repo_registered
    load_admin_key
    local branch action
    branch="$(psql_scalar "SELECT COALESCE(NULLIF(branch,''),'${BRANCH}') FROM repos WHERE full_name='${REPO}';")"
    [ -n "${branch}" ] || branch="${BRANCH}"
    case "${force}" in
        create|update) action="${force}" ;;
        *) action="$(index_action)" ;;
    esac

    if ${DRY_RUN}; then
        if [ "${action}" = "update" ]; then
            dry "POST ${API_URL}/admin/index → incremental UPDATE (existing chroma)"
        else
            dry "POST ${API_URL}/admin/index → full CREATE (empty chroma)"
        fi
        dry "  body: {\"full_name\": \"${REPO}\", \"branch\": \"${branch}\"}"
        return 0
    fi

    if [ "${action}" = "update" ]; then
        log "==> Updating existing index (incremental) for ${REPO} ..."
    else
        log "==> Creating new index (full embed) for ${REPO} ..."
    fi
    log "    POST ${API_URL}/admin/index (branch=${branch}) — may take several minutes"
    curl -sf -X POST "${API_URL}/admin/index" \
        -H "X-Admin-Key: ${ADMIN_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "{\"full_name\": \"${REPO}\", \"branch\": \"${branch}\"}" | python3 -m json.tool
}

finish_index_hint() {
    log "==> Add --index to run, or: $0 ${MODE} ${REPO} --index"
    log "    Dry-run first: $0 ${MODE} ${REPO} --dry-run --index"
}

# ── Modes ───────────────────────────────────────────────────────────

case "${MODE}" in
    inspect)
        [ -n "${REPO}" ] || { echo "❌ inspect requires owner/repo" >&2; exit 1; }
        require_compose
        print_repo_state
        print_plan "prepare"
        print_plan "soft-reset"
        print_plan "full-reset"
        print_plan "delete-repo"
        ;;

    fix-perms)
        require_compose
        cd "${COMPOSE_DIR}"
        ${DRY_RUN} && log "[dry-run] fix-perms"
        if [ -n "${REPO}" ]; then
            print_repo_state
            print_plan "fix-perms"
            ensure_storage
            verify_container_write
        else
            print_plan "fix-perms"
            ensure_storage_all
            ${DRY_RUN} || log "==> Fixed permissions for all known repos"
        fi
        ;;

    prepare)
        [ -n "${REPO}" ] || { echo "❌ prepare requires owner/repo" >&2; exit 1; }
        require_compose
        cd "${COMPOSE_DIR}"
        ${DRY_RUN} && log "[dry-run] prepare (smart: update existing / create new)"
        print_repo_state
        print_plan "prepare"
        # Never wipe on prepare — edit existing or create new
        if index_looks_corrupt && ${TRIGGER_INDEX} && ! ${DRY_RUN}; then
            log "❌ Corrupt index (no manifest). Use: $0 soft-reset ${REPO} --index" >&2
            exit 1
        fi
        ensure_storage
        verify_container_write
        if ${TRIGGER_INDEX}; then
            unstick_for_retry
            if index_looks_corrupt; then
                dry "blocked: corrupt index — run soft-reset --index instead"
            else
                trigger_index
            fi
        else
            if ! repo_in_db; then
                ensure_repo_registered
            fi
            finish_index_hint
        fi
        ;;

    soft-reset)
        [ -n "${REPO}" ] || { echo "❌ soft-reset requires owner/repo" >&2; exit 1; }
        require_compose
        cd "${COMPOSE_DIR}"
        ${DRY_RUN} && log "[dry-run] soft-reset"
        print_repo_state
        print_plan "soft-reset"
        run_or_dry "rm -rf $(index_dir)" rm -rf "$(index_dir)"
        ensure_storage
        verify_container_write
        reset_db_pending
        cancel_jobs "soft reset"
        if ${TRIGGER_INDEX}; then
            trigger_index create
        else
            finish_index_hint
        fi
        ;;

    full-reset)
        [ -n "${REPO}" ] || { echo "❌ full-reset requires owner/repo" >&2; exit 1; }
        require_compose
        cd "${COMPOSE_DIR}"
        ${DRY_RUN} && log "[dry-run] full-reset"
        print_repo_state
        print_plan "full-reset"
        run_or_dry "rm -rf $(index_dir) $(clone_dir)" \
            rm -rf "$(index_dir)" "$(clone_dir)"
        ensure_storage
        verify_container_write
        reset_db_pending
        cancel_jobs "full reset"
        if ${TRIGGER_INDEX}; then
            trigger_index create
        else
            finish_index_hint
        fi
        ;;

    delete-repo)
        [ -n "${REPO}" ] || { echo "❌ delete-repo requires owner/repo" >&2; exit 1; }
        require_compose
        cd "${COMPOSE_DIR}"
        ${DRY_RUN} && log "[dry-run] delete-repo"
        print_repo_state
        print_plan "delete-repo"
        run_or_dry "rm -rf $(index_dir) $(clone_dir)" \
            rm -rf "$(index_dir)" "$(clone_dir)"
        ensure_storage
        if repo_in_db; then
            psql_exec "DELETE FROM jobs WHERE repo_name='${REPO}';"
            psql_exec "DELETE FROM webhook_deliveries WHERE repo_full_name='${REPO}';"
            psql_exec "DELETE FROM repos WHERE full_name='${REPO}';"
            ${DRY_RUN} || log "==> Repo ${REPO} removed from Postgres"
        else
            log "==> No Postgres row for ${REPO} (disk cleaned only)"
        fi
        log "==> Next: $0 prepare ${REPO} --index"
        ;;

    *)
        echo "❌ Unknown mode: ${MODE}" >&2
        usage
        ;;
esac
