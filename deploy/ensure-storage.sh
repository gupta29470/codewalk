#!/bin/bash
# Ensure /var/codewalk layout exists and is writable by the API container (uid 999).
# Safe to run anytime — idempotent. Works for one repo, all known repos, or base only.
#
# Usage:
#   ./ensure-storage.sh [--dry-run]                  # base + all repos
#   ./ensure-storage.sh [--dry-run] --all
#   ./ensure-storage.sh [--dry-run] owner/repo
set -euo pipefail

STORAGE_ROOT="${INDEX_STORAGE_PATH:-/var/codewalk}"
API_UID="${CODEWALK_API_UID:-999}"
API_GID="${CODEWALK_API_GID:-999}"
COMPOSE_DIR="${COMPOSE_DIR:-/opt/codewalk}"

REPO_SLUG=""
FIX_ALL=false
DRY_RUN=false

for arg in "$@"; do
    case "${arg}" in
        --all) FIX_ALL=true ;;
        --dry-run) DRY_RUN=true ;;
        */*) REPO_SLUG="${arg}" ;;
    esac
done

if [ $# -eq 0 ] || { [ $# -eq 1 ] && [ "${1:-}" = "--dry-run" ]; }; then
    FIX_ALL=true
fi

run_or_dry() {
    local desc="$1"
    shift
    if ${DRY_RUN}; then
        echo "  [dry-run] ${desc}"
    else
        "$@"
    fi
}

ensure_repo_paths() {
    local slug="$1"
    [ -n "${slug}" ] || return 0
    run_or_dry "mkdir -p ${STORAGE_ROOT}/indexes/${slug}/chroma" \
        mkdir -p "${STORAGE_ROOT}/indexes/${slug}/chroma"
    run_or_dry "mkdir -p ${STORAGE_ROOT}/repos/${slug}" \
        mkdir -p "${STORAGE_ROOT}/repos/${slug}"
    echo "    repo paths: indexes/${slug}, repos/${slug}"
}

discover_repos() {
    local -a found=()
    local slug=""

    if [ -f "${COMPOSE_DIR}/docker-compose.yml" ]; then
        while IFS= read -r slug; do
            [ -n "${slug}" ] && found+=("${slug}")
        done < <(docker compose -f "${COMPOSE_DIR}/docker-compose.yml" exec -T postgres \
            psql -U codewalk -d codewalk -t -A -c "SELECT full_name FROM repos ORDER BY full_name;" \
            2>/dev/null || true)
    fi

    if [ -d "${STORAGE_ROOT}/indexes" ]; then
        while IFS= read -r slug; do
            [ -n "${slug}" ] && found+=("${slug}")
        done < <(find "${STORAGE_ROOT}/indexes" -mindepth 2 -maxdepth 2 -type d 2>/dev/null \
            | sed "s|^${STORAGE_ROOT}/indexes/||" || true)
    fi

    if [ -d "${STORAGE_ROOT}/repos" ]; then
        while IFS= read -r slug; do
            [ -n "${slug}" ] && found+=("${slug}")
        done < <(find "${STORAGE_ROOT}/repos" -mindepth 2 -maxdepth 2 -type d 2>/dev/null \
            | sed "s|^${STORAGE_ROOT}/repos/||" || true)
    fi

    printf '%s\n' "${found[@]}" 2>/dev/null | sort -u
}

prefix="==>"
${DRY_RUN} && prefix="[dry-run] ==>"

echo "${prefix} Ensuring storage at ${STORAGE_ROOT} (owner ${API_UID}:${API_GID})"

run_or_dry "mkdir base dirs under ${STORAGE_ROOT}" \
    mkdir -p "${STORAGE_ROOT}/repos" "${STORAGE_ROOT}/indexes" "${STORAGE_ROOT}/secrets"
run_or_dry "mkdir ${STORAGE_ROOT}/hf-cache" \
    mkdir -p "${STORAGE_ROOT}/hf-cache" 2>/dev/null || true

if [ -n "${REPO_SLUG}" ]; then
    ensure_repo_paths "${REPO_SLUG}"
elif ${FIX_ALL}; then
    echo "${prefix} Ensuring paths for all registered / on-disk repos"
    while IFS= read -r slug; do
        [ -n "${slug}" ] && ensure_repo_paths "${slug}"
    done < <(discover_repos)
fi

run_or_dry "chown -R ${API_UID}:${API_GID} ${STORAGE_ROOT}" \
    chown -R "${API_UID}:${API_GID}" "${STORAGE_ROOT}"
run_or_dry "chmod 755 ${STORAGE_ROOT}" chmod 755 "${STORAGE_ROOT}"
run_or_dry "chmod 755 repos/ indexes/" \
    chmod 755 "${STORAGE_ROOT}/repos" "${STORAGE_ROOT}/indexes" 2>/dev/null || true

if [ -d "${STORAGE_ROOT}/secrets" ]; then
    run_or_dry "chmod 700 secrets/" chmod 700 "${STORAGE_ROOT}/secrets"
    if ! ${DRY_RUN}; then
        find "${STORAGE_ROOT}/secrets" -type f -name '*.pem' -exec chmod 600 {} + 2>/dev/null || true
    else
        echo "  [dry-run] chmod 600 ${STORAGE_ROOT}/secrets/*.pem"
    fi
fi

echo "${prefix} Storage OK"
