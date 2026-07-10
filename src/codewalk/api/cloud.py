"""Cloud indexing webhooks, GitHub App integration, and index publishing."""
import hmac
import hashlib
import secrets
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException, Header, FastAPI
from fastapi.responses import StreamingResponse

from src.codewalk import __version__ as _codewalk_version
from src.codewalk.api import state as api_state
from src.codewalk.config import settings as _settings
from src.codewalk.embeddings.vector_store import VectorStore

cloud_router = APIRouter()

MAX_WEBHOOK_SIZE = 50 * 1024 * 1024  # 50MB
_STUCK_INDEX_WATCH_SEC = 600  # periodic stuck-index sweep (10 min)

# One active index writer per repo — newer push/catch-up supersedes the previous claim.
_index_claim_lock = threading.Lock()
_active_index_commit: dict[str, str] = {}


def _stuck_index_minutes() -> int:
    raw = os.environ.get("CODEWALK_STUCK_INDEX_MINUTES", "30")
    try:
        return max(5, int(raw))
    except ValueError:
        return 30


def _require_env(name: str) -> str:
    """Return an environment variable or raise HTTPException(403) if missing/empty."""
    value = os.environ.get(name)
    if not value:
        raise HTTPException(
            status_code=403,
            detail=f"Missing or empty environment variable: {name}",
        )
    return value


def _claim_index_slot(full_name: str, commit: str) -> None:
    """Mark commit as the only writer allowed to publish index artifacts for this repo."""
    with _index_claim_lock:
        _active_index_commit[full_name] = commit


def _index_slot_active(full_name: str, commit: str) -> bool:
    with _index_claim_lock:
        return _active_index_commit.get(full_name) == commit


def _cancel_open_jobs(
    db,
    full_name: str,
    reason: str,
    *,
    except_commit: str | None = None,
) -> None:
    """Fail queued/running jobs so a newer index run can take over."""
    if except_commit:
        db.execute(
            """UPDATE jobs SET status='failed', error=$1, finished_at=NOW()
               WHERE repo_name=$2 AND status IN ('queued', 'running') AND commit_sha <> $3""",
            reason, full_name, except_commit,
        )
    else:
        db.execute(
            """UPDATE jobs SET status='failed', error=$1, finished_at=NOW()
               WHERE repo_name=$2 AND status IN ('queued', 'running')""",
            reason, full_name,
        )


def _git_head_sha(repo_path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _abort_superseded_index(
    db,
    full_name: str,
    commit: str,
    *,
    reason: str = "superseded by newer push",
) -> dict | None:
    """If another run claimed this repo, stop without touching manifest or repo status."""
    if _index_slot_active(full_name, commit):
        return None
    db.execute(
        """UPDATE jobs SET status='failed', error=$1, finished_at=NOW()
           WHERE repo_name=$2 AND commit_sha=$3 AND status IN ('queued', 'running')""",
        reason, full_name, commit,
    )
    return {"status": "superseded", "reason": reason}


def is_cloud_enabled() -> bool:
    """Check if all required cloud environment variables are set."""
    from src.codewalk.worker.github_app import has_github_app_private_key
    return bool(
        os.environ.get("DATABASE_URL")
        and os.environ.get("GITHUB_APP_ID")
        and has_github_app_private_key()
    )


def start_cloud_worker() -> None:
    """Start the background worker thread for cloud indexing jobs.

    Safe to call even when cloud is not configured — it becomes a no-op.
    Called from the FastAPI lifespan startup handler in main.py.

    Also triggers catch-up indexing for repos that have never been indexed,
    are in a failed state, or whose manifest codewalk_version is older than
    the running API (re-stamp after deploy).
    """
    if not is_cloud_enabled():
        return

    import threading
    import time
    import logging
    logger = logging.getLogger("codewalk.cloud")

    # NOTE: We intentionally do NOT start worker_loop here.
    # The webhook handler already runs indexing in its own background thread,
    # and the catch-up indexer below handles startup indexing. Starting the
    # worker_loop would race against the webhook threads on the same jobs table
    # and the worker has schema mismatches (repo.name vs full_name).
    # All cloud indexing goes through: webhook → _do_index() OR catch-up → _analyze_repo().

    try:
        db = api_state.get_db()
        reconciled = _reconcile_orphaned_indexing(db)
        if reconciled:
            logger.info(f"[cloud] Reconciled {reconciled} orphaned job(s) after API startup")
    except Exception:
        logger.exception("[cloud] Startup orphan reconciliation failed")

    def _delayed_catchup():
        time.sleep(15)
        _run_catchup_indexing(logger)

    def _watchdog_loop():
        while True:
            time.sleep(_STUCK_INDEX_WATCH_SEC)
            try:
                stuck = _reconcile_stuck_indexing(api_state.get_db())
                if stuck:
                    logger.warning(
                        f"[cloud] Reconciled {stuck} stuck indexing repo(s); "
                        "catch-up will retry on next API restart or admin/index"
                    )
                    _run_catchup_indexing(logger)
            except Exception:
                logger.exception("[cloud] Stuck-index watchdog failed")

    threading.Thread(target=_delayed_catchup, daemon=True).start()
    threading.Thread(target=_watchdog_loop, daemon=True).start()
    logger.info("[cloud] Catch-up indexer scheduled (15s delay)")


def _run_catchup_indexing(logger):
    """Index repos never indexed, failed, or stamped with an older Codewalk version."""
    try:
        db = api_state.get_db()
        repos = _repos_needing_catchup(db)
        if not repos:
            logger.info("[catchup] No repos need indexing")
            return

        logger.info(
            f"[catchup] {len(repos)} repo(s) need indexing: "
            f"{', '.join(r['full_name'] for r in repos)}"
        )

        for repo in repos:
            full_name = repo.get("full_name", "")
            branch = repo.get("branch", "master")

            logger.info(f"[catchup] Starting index for {full_name}")
            repo_path = _clone_or_pull_repo(repo, branch)
            head_sha = _git_head_sha(repo_path) or "catchup"
            _claim_index_slot(full_name, head_sha)
            _cancel_open_jobs(db, full_name, "superseded by catch-up")
            db.execute(
                "UPDATE repos SET index_status=$1, updated_at=NOW() WHERE full_name=$2",
                "indexing", full_name,
            )
            # Track this catch-up run in the jobs table so /admin/repos shows a
            # current job instead of a stale failed orphan.
            db.execute(
                "INSERT INTO jobs (repo_name, commit_sha, commit_message, status, started_at) VALUES ($1, $2, $3, $4, NOW())",
                full_name, head_sha, "", "running",
            )

            try:
                # Catch-up: use incremental if a previous index exists (same as webhook path),
                # fall back to full if no previous index or incremental fails.
                active = _active_index_dir(full_name)
                if active and (active / "chroma").exists():
                    logger.info(f"[catchup] {full_name}: incremental reindex (previous index found)")
                    result = _run_incremental_index(repo_path, full_name, head_sha)
                else:
                    logger.info(f"[catchup] {full_name}: full index (no previous index)")
                    result = _analyze_repo(repo_path, full_name, head_sha)

                superseded = _abort_superseded_index(
                    db, full_name, head_sha, reason="superseded by catch-up"
                )
                if superseded:
                    _discard_incoming(full_name, head_sha)
                    db.execute(
                        "UPDATE jobs SET status=$1, finished_at=NOW() WHERE repo_name=$2 AND commit_sha=$3",
                        "failed", full_name, head_sha,
                    )
                    logger.info(f"[catchup] Skipped {full_name} — newer index run claimed slot")
                    continue

                if result["status"] == "success":
                    # Get commit info from the cloned repo
                    git_sha = git_msg = git_branch = ""
                    try:
                        git_sha = head_sha or _git_head_sha(repo_path)
                        git_msg = subprocess.run(
                            ["git", "log", "-1", "--format=%s"], cwd=str(repo_path),
                            capture_output=True, text=True, check=True,
                        ).stdout.strip()
                        git_branch = subprocess.run(
                            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo_path),
                            capture_output=True, text=True, check=True,
                        ).stdout.strip()
                    except Exception:
                        pass

                    # Write manifest and increment index_version
                    row_ver = db.fetchone("SELECT index_version FROM repos WHERE full_name=$1", full_name)
                    new_version = ((row_ver["index_version"] or 0) if row_ver else 0) + 1
                    _publish_index(
                        full_name,
                        head_sha,
                        file_count=result.get("files_scanned", 0),
                        chunk_count=result.get("chunks_embedded", 0),
                        repo_name=full_name,
                        collection_name=_collection_name(full_name),
                        commit_sha=git_sha,
                        commit_message=git_msg,
                        branch=git_branch,
                        index_version=new_version,
                    )
                    db.execute(
                        "UPDATE repos SET last_indexed_sha=$1, index_status=$2, index_version=$3, updated_at=NOW() WHERE full_name=$4",
                        git_sha or "catchup", "ready", new_version, full_name,
                    )
                    db.execute(
                        "UPDATE jobs SET status=$1, finished_at=NOW() WHERE repo_name=$2 AND commit_sha=$3",
                        "done", full_name, head_sha,
                    )
                    logger.info(
                        f"[catchup] ✅ {full_name} indexed (v{new_version}): "
                        f"{result.get('files_scanned', 0)} files, "
                        f"{result.get('chunks_embedded', 0)} chunks embedded"
                    )
                else:
                    error = result.get("error", "unknown")
                    _discard_incoming(full_name, head_sha)
                    db.execute(
                        "UPDATE repos SET index_status=$1, updated_at=NOW() WHERE full_name=$2",
                        "failed", full_name,
                    )
                    db.execute(
                        "UPDATE jobs SET status=$1, error=$2, finished_at=NOW() WHERE repo_name=$3 AND commit_sha=$4",
                        "failed", error, full_name, head_sha,
                    )
                    logger.error(f"[catchup] ❌ {full_name} failed: {error}")

            except Exception as e:
                logger.exception(f"[catchup] ❌ {full_name} exception: {e}")
                _discard_incoming(full_name, head_sha)
                db.execute(
                    "UPDATE repos SET index_status=$1, updated_at=NOW() WHERE full_name=$2",
                    "failed", full_name,
                )
                db.execute(
                    "UPDATE jobs SET status=$1, error=$2, finished_at=NOW() WHERE repo_name=$3 AND commit_sha=$4",
                    "failed", str(e), full_name, head_sha,
                )

    except Exception as e:
        logger.exception(f"[catchup] Fatal error: {e}")


def setup_cloud(app: FastAPI) -> None:
    """Register cloud routes if cloud env vars are set.

    Call this once after creating the FastAPI app. Does nothing if cloud
    is not configured. Worker startup is handled separately via lifespan.
    """
    if is_cloud_enabled():
        app.include_router(cloud_router)


def _verify_webhook(body: bytes, signature: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _allowed_index_branches(repo: dict) -> list[str]:
    """Resolve which branches may trigger indexing (from codewalk.yaml on server clone).

    Before the repo has been cloned we fall back to the default branch list so we
    do not create a non-empty directory that would break the subsequent git clone.
    """
    from src.codewalk.codewalk_config import load_codewalk_yaml, index_branches
    from src.codewalk.repo_discovery import ensure_codewalk_yaml

    storage = repo.get("storage_path") or f"/var/codewalk/repos/{repo['full_name']}"
    storage_path = Path(storage)

    # Not cloned yet — defer to defaults; _clone_or_pull_repo will ensure yaml.
    if not storage_path.exists() or not (storage_path / "codewalk.yaml").exists():
        return ["main", "master"]

    repo_path = Path(str(ensure_codewalk_yaml(storage, create=True)))
    return index_branches(load_codewalk_yaml(str(repo_path)))


def _get_or_create_repo(db, full_name: str, clone_url: str, installation_id: str, branch: str = "master") -> dict:
    """Get repo from DB or auto-register it on first webhook."""
    parts = full_name.split("/")
    owner = parts[0] if len(parts) > 0 else ""
    name  = parts[1] if len(parts) > 1 else full_name
    storage_path = os.environ.get("INDEX_STORAGE_PATH", "/var/codewalk")
    repo_storage = f"{storage_path}/repos/{full_name}"

    row = db.fetchone("SELECT * FROM repos WHERE full_name=$1", full_name)
    if row:
        return dict(row)

    # Auto-register on first webhook
    repo_token = "cw_repo_" + secrets.token_urlsafe(16)
    db.execute(
        """INSERT INTO repos (full_name, name, owner, clone_url, branch, installation_id, repo_token, storage_path)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (full_name) DO NOTHING
        """,
        full_name, name, owner, clone_url, branch, installation_id, repo_token, repo_storage,
    )
    row = db.fetchone("SELECT * FROM repos WHERE full_name=$1", full_name)
    return dict(row) if row else {}


def _clone_or_pull_repo(repo: dict, branch: str) -> Path:
    """Clone repo if not exists, or git pull if it does.

    Returns the discovered repo root (where codewalk.yaml lives), auto-creating
    a default codewalk.yaml if the repository does not contain one.
    """
    from src.codewalk.repo_discovery import ensure_codewalk_yaml

    storage_path = repo.get("storage_path") or f"/var/codewalk/repos/{repo['full_name']}"
    repo_path = Path(storage_path)

    if repo_path.exists() and (repo_path / ".git").exists():
        # Sync with the remote branch. Use fetch + reset --hard so force-pushes
        # and rewritten history are handled deterministically. The server-side
        # clone is a cache, not a user workspace, so discarding local changes is
        # acceptable and preferred over a failing merge.
        fetch_result = subprocess.run(
            ["git", "fetch", "origin", branch],
            cwd=str(repo_path),
            check=False,
            capture_output=True,
            text=True,
        )
        if fetch_result.returncode != 0:
            raise RuntimeError(
                f"git fetch failed for {repo['full_name']}: {fetch_result.stderr.strip()}"
            )
        reset_result = subprocess.run(
            ["git", "reset", "--hard", f"origin/{branch}"],
            cwd=str(repo_path),
            check=False,
            capture_output=True,
            text=True,
        )
        if reset_result.returncode != 0:
            raise RuntimeError(
                f"git reset failed for {repo['full_name']}: {reset_result.stderr.strip()}"
            )
    else:
        # Clone fresh
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        clone_url = repo.get("clone_url", f"https://github.com/{repo['full_name']}.git")
        clone_result = subprocess.run(
            ["git", "clone", "--depth=1", "--branch", branch, clone_url, str(repo_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if clone_result.returncode != 0:
            raise RuntimeError(
                f"git clone failed for {repo['full_name']}: {clone_result.stderr.strip()}"
            )

    return Path(str(ensure_codewalk_yaml(str(repo_path), create=True)))


def _artifacts_dir(repo_full_name: str) -> Path:
    """Public symlink path to the active index.

    The returned path is a symlink that atomically points to a versioned
    real directory (e.g. repo.v3). Callers that need the real directory
    should use _active_index_dir().
    """
    storage = os.environ.get("INDEX_STORAGE_PATH", "/var/codewalk")
    return Path(storage) / "indexes" / repo_full_name


def _active_index_dir(repo_full_name: str) -> Path | None:
    """Resolved real directory currently serving as the active index."""
    active = _artifacts_dir(repo_full_name)
    if active.is_symlink():
        resolved = active.resolve(strict=False)
        if resolved.is_dir():
            return resolved
    # Legacy layout: a real directory at the active path.
    if active.is_dir():
        return active
    return None


def _versioned_index_dir(repo_full_name: str, index_version: int) -> Path:
    """Versioned real directory for a given index version."""
    storage = os.environ.get("INDEX_STORAGE_PATH", "/var/codewalk")
    return Path(storage) / "indexes" / f"{repo_full_name}.v{index_version}"


def _cleanup_old_index_versions(repo_full_name: str, keep: int = 3) -> None:
    """Remove old versioned index dirs, keeping the latest `keep` plus current symlink target."""
    active = _artifacts_dir(repo_full_name)
    parent = active.parent
    if not parent.exists():
        return

    versions = sorted(
        parent.glob(f"{active.name}.v*"),
        key=lambda p: int(p.name.split(".v")[-1]),
        reverse=True,
    )

    active_target: Path | None = None
    if active.is_symlink():
        try:
            active_target = active.resolve(strict=True)
        except OSError:
            pass

    for old in versions[keep:]:
        if active_target and old.resolve(strict=False) == active_target:
            continue
        shutil.rmtree(old, ignore_errors=True)

    # Also remove legacy atomic_swap backup dirs if any remain.
    legacy_backup = parent / f"{active.name}_old"
    if legacy_backup.exists():
        shutil.rmtree(legacy_backup, ignore_errors=True)


def _incoming_artifacts_dir(repo_full_name: str, run_id: str) -> Path:
    """Per-run scratch space; promoted to a versioned dir on publish."""
    active = _artifacts_dir(repo_full_name)
    safe = "".join(c if c.isalnum() else "_" for c in run_id)[:20] or "run"
    return active.parent / f"{active.name}.incoming.{safe}"


def _cleanup_orphan_incoming(repo_full_name: str) -> None:
    """Remove abandoned .incoming.* dirs from failed or superseded runs."""
    active = _artifacts_dir(repo_full_name)
    parent = active.parent
    if not parent.exists():
        return
    prefix = f"{active.name}.incoming."
    for entry in parent.iterdir():
        if entry.is_dir() and entry.name.startswith(prefix):
            shutil.rmtree(entry, ignore_errors=True)


def _prepare_incoming_workspace(
    repo_full_name: str,
    run_id: str,
    *,
    seed_from_active: bool,
) -> Path:
    """Create incoming workspace; optionally copy current active index as incremental base."""
    _cleanup_orphan_incoming(repo_full_name)
    incoming = _incoming_artifacts_dir(repo_full_name, run_id)
    if incoming.exists():
        shutil.rmtree(incoming)
    active = _active_index_dir(repo_full_name)
    if seed_from_active and active is not None and any(active.iterdir()):
        shutil.copytree(active, incoming)
    else:
        incoming.mkdir(parents=True, exist_ok=True)
        (incoming / "chroma").mkdir(parents=True, exist_ok=True)
    return incoming


def _discard_incoming(repo_full_name: str, run_id: str) -> None:
    incoming = _incoming_artifacts_dir(repo_full_name, run_id)
    if incoming.exists():
        shutil.rmtree(incoming, ignore_errors=True)


def _publish_index(
    repo_full_name: str,
    run_id: str,
    *,
    file_count: int,
    chunk_count: int,
    repo_name: str,
    collection_name: str,
    commit_sha: str,
    commit_message: str,
    branch: str,
    index_version: int,
) -> None:
    """Write manifest into incoming/, then atomically promote it via symlink."""
    from src.codewalk.pipeline import write_manifest
    from src.codewalk.graph.knowledge_graph_export import _patch_manifest

    incoming = _incoming_artifacts_dir(repo_full_name, run_id)
    if not incoming.is_dir():
        raise FileNotFoundError(f"Incoming index missing for {repo_full_name}")

    write_manifest(
        str(incoming),
        file_count=file_count,
        chunk_count=chunk_count,
        repo_name=repo_name,
        collection_name=collection_name,
        commit_sha=commit_sha,
        commit_message=commit_message,
        branch=branch,
        index_version=index_version,
        embedding_model=_settings.embedding_model,
        minimum_mcp_version=_codewalk_version,
    )
    # Stamp manifest with knowledge-graph metadata if the file was generated.
    _patch_manifest(str(incoming))

    active = _artifacts_dir(repo_full_name)
    active.parent.mkdir(parents=True, exist_ok=True)

    target = _versioned_index_dir(repo_full_name, index_version)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)

    # Move incoming scratch dir to its versioned permanent location.
    incoming.rename(target)

    # Legacy layout migration: if active path is a real directory, archive it first.
    if active.exists() and active.is_dir() and not active.is_symlink():
        legacy_target = _versioned_index_dir(repo_full_name, 0)
        if legacy_target.exists():
            shutil.rmtree(legacy_target, ignore_errors=True)
        active.rename(legacy_target)

    # Atomically repoint the public symlink to the new version.
    tmp_link = active.parent / f"{active.name}.new"
    if tmp_link.exists() or tmp_link.is_symlink():
        tmp_link.unlink()
    tmp_link.symlink_to(target, target_is_directory=True)
    tmp_link.replace(active)

    _cleanup_old_index_versions(repo_full_name)


def _manifest_codewalk_version(repo_full_name: str) -> str:
    """Read codewalk_version from on-disk manifest, or empty string."""
    manifest_path = _artifacts_dir(repo_full_name) / "manifest.json"
    if not manifest_path.exists():
        return ""
    try:
        return json.loads(manifest_path.read_text()).get("codewalk_version", "")
    except (json.JSONDecodeError, OSError):
        return ""


def _reconcile_orphaned_indexing(db, reason: str = "API restarted — job orphaned") -> int:
    """After API restart: daemon indexing threads are dead; unblock repos for catch-up."""
    indexing_rows = db.fetchall("SELECT full_name FROM repos WHERE index_status='indexing'")
    job_row = db.fetchone(
        "SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued', 'running')"
    )
    open_jobs = int(job_row["n"]) if job_row else 0

    db.execute(
        """UPDATE jobs SET status='failed', error=$1, finished_at=NOW()
           WHERE status IN ('queued', 'running')""",
        reason,
    )
    db.execute(
        """UPDATE repos SET index_status='pending', updated_at=NOW()
           WHERE index_status='indexing'"""
    )
    return len(indexing_rows) + open_jobs


def _reconcile_stuck_indexing(db, reason: str = "Indexing timed out") -> int:
    """While API is running: fail jobs/repos stuck in indexing past the threshold."""
    stuck_mins = _stuck_index_minutes()
    stuck_rows = db.fetchall(
        f"""SELECT full_name FROM repos
            WHERE index_status='indexing'
              AND updated_at < NOW() - INTERVAL '{stuck_mins} minutes'"""
    )
    if not stuck_rows:
        return 0

    db.execute(
        f"""UPDATE jobs SET status='failed', error=$1, finished_at=NOW()
            WHERE status IN ('queued', 'running')
              AND queued_at < NOW() - INTERVAL '{stuck_mins} minutes'""",
        reason,
    )
    db.execute(
        f"""UPDATE repos SET index_status='pending', updated_at=NOW()
            WHERE index_status='indexing'
              AND updated_at < NOW() - INTERVAL '{stuck_mins} minutes'"""
    )
    return len(stuck_rows)


def _repos_needing_catchup(db) -> list[dict]:
    """Repos that need catch-up indexing on API startup.

    Includes never-indexed / failed / pending repos, stuck indexing zombies,
    repos behind their latest queued job commit, AND repos whose manifest was
    built with an older Codewalk version than the running API (post-deploy).
    """
    from packaging.version import parse as parse_version
    from src.codewalk import __version__

    current = parse_version(__version__)
    stuck_mins = _stuck_index_minutes()
    rows = db.fetchall(
        """SELECT * FROM repos
        WHERE last_indexed_sha IS NULL
           OR index_status IN ('pending', 'failed')
        ORDER BY updated_at DESC"""
    )
    seen = {dict(r)["full_name"] for r in rows}
    result = [dict(r) for r in rows]

    stuck = db.fetchall(
        f"""SELECT * FROM repos
            WHERE index_status = 'indexing'
              AND updated_at < NOW() - INTERVAL '{stuck_mins} minutes'
            ORDER BY updated_at DESC"""
    )
    for row in stuck:
        repo = dict(row)
        full_name = repo.get("full_name", "")
        if full_name and full_name not in seen:
            result.append(repo)
            seen.add(full_name)

    behind_job = db.fetchall(
        """SELECT r.* FROM repos r
           JOIN LATERAL (
               SELECT commit_sha, status FROM jobs
               WHERE repo_name = r.full_name
               ORDER BY queued_at DESC LIMIT 1
           ) j ON TRUE
           WHERE j.status IN ('queued', 'running')
             AND (r.last_indexed_sha IS NULL OR r.last_indexed_sha <> j.commit_sha)"""
    )
    for row in behind_job:
        repo = dict(row)
        full_name = repo.get("full_name", "")
        if full_name and full_name not in seen:
            result.append(repo)
            seen.add(full_name)

    ready = db.fetchall(
        "SELECT * FROM repos WHERE index_status = 'ready' ORDER BY updated_at DESC"
    )
    for row in ready:
        repo = dict(row)
        full_name = repo.get("full_name", "")
        if not full_name or full_name in seen:
            continue
        stored_ver = _manifest_codewalk_version(full_name)
        if not stored_ver:
            continue
        try:
            if parse_version(stored_ver) < current:
                result.append(repo)
                seen.add(full_name)
        except Exception:
            pass

    return result


def _collection_name(repo_full_name: str) -> str:
    """Chroma collection prefix from GitHub slug (owner/repo → repo). Default: codebase."""
    if not repo_full_name:
        return "codebase"
    name = repo_full_name.rsplit("/", 1)[-1].strip()
    return name or "codebase"


def _analyze_repo(repo_path: Path, repo_full_name: str, run_id: str) -> dict:
    """Run the FULL /analyze pipeline into incoming/, then caller publishes via atomic_swap."""
    from src.codewalk.codewalk_config import load_codewalk_yaml
    from src.codewalk.pipeline import full_index_parallel, build_full_analysis

    try:
        adir = _prepare_incoming_workspace(repo_full_name, run_id, seed_from_active=False)
        persist_dir = str(adir / "chroma")
        db_path = str(adir / "graph.duckdb")

        config = load_codewalk_yaml(str(repo_path))

        col = _collection_name(repo_full_name)
        index_result = full_index_parallel(
            repo_path=str(repo_path),
            collection_name=col,
            persist_dir=persist_dir,
            codewalk_config=config,
        )

        docs_path = ""
        if config.docs_path:
            docs_path = str(repo_path / config.docs_path)

        build_full_analysis(
            db_path=db_path,
            files=index_result["files"],
            embedded_chunks=index_result.get("embedded_chunks"),
            docs_path=docs_path,
            force_reindex_extras=True,
            collection_name=col,
        )

        # Report total counts from the freshly built ChromaDB index.
        vs = VectorStore(persist_dir=persist_dir)
        vs.create_collection(col)
        return {
            "status": "success",
            "files_scanned": len(index_result.get("files", [])),
            "chunks_embedded": vs.chunk_count(),
        }
    except Exception as e:
        _discard_incoming(repo_full_name, run_id)
        return {"status": "failed", "error": str(e)}


def _run_incremental_index(repo_path: Path, repo_full_name: str, run_id: str) -> dict:
    """Incremental re-index into incoming/ (seeded from latest), then caller publishes."""
    from src.codewalk.codewalk_config import load_codewalk_yaml, codewalk_scan_directory
    from src.codewalk.pipeline import reindex, build_full_analysis

    try:
        active = _active_index_dir(repo_full_name)
        seed = active is not None and (active / "chroma").exists()
        adir = _prepare_incoming_workspace(repo_full_name, run_id, seed_from_active=seed)
        persist_dir = str(adir / "chroma")
        db_path = str(adir / "graph.duckdb")

        config = load_codewalk_yaml(str(repo_path))

        col = _collection_name(repo_full_name)
        result = reindex(
            repo_path=str(repo_path),
            collection_name=col,
            persist_dir=persist_dir,
            codewalk_config=config,
        )

        files = codewalk_scan_directory(str(repo_path), config)

        # Rebuild DuckDB + KG from every chunk currently in ChromaDB, not just
        # the changed ones, so the graph store stays consistent.
        vs = VectorStore(persist_dir=persist_dir)
        vs.create_collection(col)
        all_chunks = vs.get_all_chunks()

        docs_path = ""
        if config.docs_path:
            docs_path = str(repo_path / config.docs_path)

        build_full_analysis(
            db_path=db_path,
            files=files,
            embedded_chunks=all_chunks,
            docs_path=docs_path,
            force_reindex_extras=True,
            collection_name=col,
        )

        return {
            "status": "success",
            "files_scanned": len(files),
            "files_changed": result.get("changed_files", 0),
            "files_deleted": result.get("deleted_files", 0),
            "chunks_embedded": result.get("chunks_embedded", 0),
            "total_chunks": vs.chunk_count(),
        }
    except Exception as e:
        _discard_incoming(repo_full_name, run_id)
        return {"status": "failed", "error": str(e)}


@cloud_router.post("/webhooks/github")
async def github_webhook(request: Request):
    """Receive GitHub push/installation events. HMAC-verified. Auto-registers repo, skips unchanged, incremental re-index."""
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_WEBHOOK_SIZE:
        raise HTTPException(413, "Payload too large (max 50MB)")
    body = await request.body()
    if len(body) > MAX_WEBHOOK_SIZE:
        raise HTTPException(413, "Payload too large (max 50MB)")

    signature = request.headers.get("X-Hub-Signature-256", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", "")
    event_type = request.headers.get("X-GitHub-Event", "")

    if not _verify_webhook(body, signature, _require_env("GITHUB_WEBHOOK_SECRET")):
        raise HTTPException(403, "Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(422, "Invalid JSON in webhook payload")

    # ── Log webhook delivery ──────────────────────────────────────────
    db = api_state.get_db()
    repo_full_name = payload.get("repository", {}).get("full_name", "")
    commit_sha = payload.get("after", "")
    db.execute(
        "INSERT INTO webhook_deliveries (event_type, delivery_id, repo_full_name, commit_sha, payload_size, status) VALUES ($1, $2, $3, $4, $5, $6)",
        event_type, delivery_id, repo_full_name, commit_sha, len(body), "received",
    )

    # ── Handle ping (GitHub App test) ─────────────────────────────────
    if event_type == "ping":
        return {"status": "pong"}

    # ── Handle installation events ────────────────────────────────────
    if event_type in ("installation", "installation_repositories"):
        return {"status": "ignored", "reason": "Installation events are handled lazily on first push"}

    # ── Only process push events ──────────────────────────────────────
    if event_type != "push":
        return {"status": "ignored", "reason": f"Event type '{event_type}' not handled"}

    # ── Extract push payload ──────────────────────────────────────────
    try:
        branch = payload.get("ref", "").replace("refs/heads/", "")
        repo_full_name = payload.get("repository", {}).get("full_name", "")
        clone_url = payload.get("repository", {}).get("clone_url", "")
        commit = payload.get("after", "")
        msg = payload.get("head_commit", {}).get("message", "")
        installation_id = str(payload.get("installation", {}).get("id", ""))
        if commit == "0000000000000000000000000000000000000000":
            # Branch deleted
            return {"status": "ignored", "reason": "Branch deleted"}
    except (AttributeError, KeyError):
        raise HTTPException(422, "Malformed webhook payload")

    if not repo_full_name or not branch:
        raise HTTPException(422, "Missing repository name or branch in payload")

    if not commit:
        return {"status": "ignored", "reason": "No commit SHA in payload"}

    # ── Get or auto-register repo ─────────────────────────────────────
    repo = _get_or_create_repo(db, repo_full_name, clone_url, installation_id, branch)
    if not repo:
        raise HTTPException(500, "Failed to register repository")

    # ── Branch allowlist (codewalk.yaml indexing.branches) ────────────
    from src.codewalk.codewalk_config import branch_allowed

    allowed = _allowed_index_branches(repo)
    if not branch_allowed(branch, allowed):
        db.execute(
            "UPDATE webhook_deliveries SET status=$1 WHERE delivery_id=$2",
            "ignored_branch", delivery_id,
        )
        return {
            "status": "ignored",
            "reason": f"Branch '{branch}' not in allowed index branches",
            "allowed_branches": allowed,
        }

    # ── Skip if SHA unchanged ─────────────────────────────────────────
    if repo.get("last_indexed_sha") == commit:
        db.execute(
            "UPDATE webhook_deliveries SET status=$1 WHERE delivery_id=$2",
            "ignored_unchanged", delivery_id,
        )
        return {"status": "skipped", "reason": "Commit already indexed", "sha": commit[:7]}

    # ── Supersede any in-flight index; newest push wins ─────────────
    _cancel_open_jobs(db, repo_full_name, "superseded by newer push")
    _claim_index_slot(repo_full_name, commit)

    # ── Queue job and update status ───────────────────────────────────
    db.execute(
        "INSERT INTO jobs (repo_name, commit_sha, commit_message, status) VALUES ($1, $2, $3, $4)",
        repo_full_name, commit, msg, "queued",
    )
    db.execute(
        "UPDATE repos SET index_status=$1, updated_at=NOW() WHERE full_name=$2",
        "indexing", repo_full_name,
    )

    # ── Clone/pull and incremental index (async-friendly: offload to thread) ──
    import asyncio
    loop = asyncio.get_event_loop()

    def _do_index():
        # Each webhook indexing thread gets its own Postgres connection.
        db = api_state.get_db()
        try:
            db.execute(
                "UPDATE jobs SET status=$1, started_at=NOW() WHERE repo_name=$2 AND commit_sha=$3 AND status=$4",
                "running", repo_full_name, commit, "queued",
            )
            aborted = _abort_superseded_index(db, repo_full_name, commit)
            if aborted:
                return aborted

            repo_path = _clone_or_pull_repo(repo, branch)
            aborted = _abort_superseded_index(db, repo_full_name, commit)
            if aborted:
                return aborted

            result = _run_incremental_index(repo_path, repo_full_name, commit)

            if result["status"] == "success":
                aborted = _abort_superseded_index(db, repo_full_name, commit)
                if aborted:
                    _discard_incoming(repo_full_name, commit)
                    return aborted
                # Fetch current version, increment, write manifest, then update DB
                row = db.fetchone("SELECT index_version FROM repos WHERE full_name=$1", repo_full_name)
                new_version = ((row["index_version"] or 0) if row else 0) + 1
                _publish_index(
                    repo_full_name,
                    commit,
                    file_count=result.get("files_scanned", 0),
                    chunk_count=result.get("total_chunks", result.get("chunks_embedded", 0)),
                    repo_name=repo_full_name,
                    collection_name=_collection_name(repo_full_name),
                    commit_sha=commit,
                    commit_message=msg,
                    branch=branch,
                    index_version=new_version,
                )
                db.execute(
                    "UPDATE repos SET last_indexed_sha=$1, index_status=$2, index_version=$3, updated_at=NOW() WHERE full_name=$4",
                    commit, "ready", new_version, repo_full_name,
                )
                db.execute(
                    "UPDATE jobs SET status=$1, finished_at=NOW() WHERE repo_name=$2 AND commit_sha=$3 AND status IN ('queued', 'running')",
                    "done", repo_full_name, commit,
                )
                db.execute(
                    "UPDATE webhook_deliveries SET status=$1 WHERE delivery_id=$2",
                    "processed", delivery_id,
                )
                return result
            else:
                error_msg = result.get("error", "Unknown indexing error")
                _discard_incoming(repo_full_name, commit)
                db.execute(
                    "UPDATE repos SET index_status=$1, updated_at=NOW() WHERE full_name=$2",
                    "failed", repo_full_name,
                )
                db.execute(
                    "UPDATE jobs SET status=$1, error=$2, finished_at=NOW() WHERE repo_name=$3 AND commit_sha=$4 AND status IN ('queued', 'running')",
                    "failed", error_msg, repo_full_name, commit,
                )
                db.execute(
                    "UPDATE webhook_deliveries SET status=$1, error=$2 WHERE delivery_id=$3",
                    "failed", error_msg, delivery_id,
                )
                return result
        except Exception as e:
            _discard_incoming(repo_full_name, commit)
            db.execute(
                "UPDATE repos SET index_status=$1, updated_at=NOW() WHERE full_name=$2",
                "failed", repo_full_name,
            )
            db.execute(
                "UPDATE jobs SET status=$1, error=$2, finished_at=NOW() WHERE repo_name=$3 AND commit_sha=$4 AND status IN ('queued', 'running')",
                "failed", str(e), repo_full_name, commit,
            )
            db.execute(
                "UPDATE webhook_deliveries SET status=$1, error=$2 WHERE delivery_id=$3",
                "failed", str(e), delivery_id,
            )
            return {"status": "failed", "error": str(e)}

    # Run indexing in background thread so webhook responds immediately
    import threading
    threading.Thread(target=_do_index, daemon=True).start()

    return {
        "status": "queued",
        "repo": repo_full_name,
        "commit": commit[:7],
        "branch": branch,
        "previous_sha": repo.get("last_indexed_sha", "")[:7] if repo.get("last_indexed_sha") else "none",
    }


@cloud_router.post("/admin/register")
async def register_repo(request: Request, x_admin_key: str = Header(alias="X-Admin-Key")):
    """Register a repo manually. Returns a per-repo download token."""
    if not secrets.compare_digest(x_admin_key, _require_env("ADMIN_API_KEY")):
        raise HTTPException(403, "Invalid admin key")

    body = await request.json()
    full_name = body.get("full_name") or body.get("name", "")
    if "/" not in full_name:
        raise HTTPException(422, "full_name must be in 'owner/repo' format")

    parts = full_name.split("/")
    owner = parts[0]
    name = parts[1]
    clone_url = body.get("github_url", f"https://github.com/{full_name}.git")
    branch = body.get("branch", "master")
    installation_id = body.get("installation_id", "")
    repo_token = "cw_repo_" + secrets.token_urlsafe(16)
    storage_path = os.environ.get("INDEX_STORAGE_PATH", "/var/codewalk")
    repo_storage = f"{storage_path}/repos/{full_name}"

    db = api_state.get_db()
    db.execute(
        """INSERT INTO repos (full_name, name, owner, clone_url, branch, installation_id, repo_token, storage_path)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (full_name) DO UPDATE SET clone_url=$4, branch=$5, installation_id=$6, repo_token=$7, storage_path=$8, updated_at=NOW()
        """,
        full_name, name, owner, clone_url, branch, installation_id, repo_token, repo_storage,
    )

    return {"repo_token": repo_token, "full_name": full_name, "status": "registered"}


@cloud_router.post("/admin/repos")
async def list_repos(x_admin_key: str = Header(alias="X-Admin-Key")):
    if not secrets.compare_digest(x_admin_key, _require_env("ADMIN_API_KEY")):
        raise HTTPException(403, "Invalid admin key")

    db = api_state.get_db()
    rows = db.fetchall("""
        SELECT r.full_name, r.name, r.owner, r.branch, r.last_indexed_sha, r.index_status, r.created_at, r.updated_at,
            j.status AS job_status, j.commit_sha AS job_commit, j.finished_at AS job_finished, j.error AS job_error
        FROM repos r
        LEFT JOIN LATERAL (
            SELECT * FROM jobs WHERE repo_name=r.full_name ORDER BY queued_at DESC LIMIT 1
        ) j ON TRUE
        ORDER BY r.updated_at DESC
    """)

    return {"repos": [dict(row) for row in rows]}


@cloud_router.post("/admin/index")
async def trigger_index(
    request: Request,
    x_admin_key: str = Header(alias="X-Admin-Key"),
):
    """Manually trigger indexing for a registered repo."""
    if not secrets.compare_digest(x_admin_key, _require_env("ADMIN_API_KEY")):
        raise HTTPException(403, "Invalid admin key")

    body = await request.json()
    full_name = body.get("full_name", "")
    branch = body.get("branch", "")

    db = api_state.get_db()
    repo = db.fetchone("SELECT * FROM repos WHERE full_name=$1", full_name)
    if not repo:
        raise HTTPException(404, f"Repo '{full_name}' not registered")

    import asyncio

    repo = dict(repo)
    try:
        repo_path = await asyncio.to_thread(
            _clone_or_pull_repo, repo, branch or repo["branch"]
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    head_sha = (await asyncio.to_thread(_git_head_sha, repo_path)) or "admin-index"
    _cancel_open_jobs(db, full_name, "superseded by admin/index")
    _claim_index_slot(full_name, head_sha)
    db.execute(
        "UPDATE repos SET index_status=$1, updated_at=NOW() WHERE full_name=$2",
        "indexing", full_name,
    )
    result = await asyncio.to_thread(
        _run_incremental_index, repo_path, full_name, head_sha
    )

    if result["status"] == "success":
        if not _index_slot_active(full_name, head_sha):
            _discard_incoming(full_name, head_sha)
            db.execute(
                "UPDATE repos SET index_status=$1, updated_at=NOW() WHERE full_name=$2",
                "failed", full_name,
            )
            return {"repo": full_name, "status": "superseded"}

        # Get commit info from the cloned repo
        git_sha = git_msg = git_branch = ""
        try:
            git_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=str(repo_path),
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            git_msg = subprocess.run(
                ["git", "log", "-1", "--format=%s"], cwd=str(repo_path),
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            git_branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo_path),
                capture_output=True, text=True, check=True,
            ).stdout.strip()
        except Exception:
            pass

        row_ver = db.fetchone("SELECT index_version FROM repos WHERE full_name=$1", full_name)
        new_version = ((row_ver["index_version"] or 0) if row_ver else 0) + 1
        _publish_index(
            full_name,
            head_sha,
            file_count=result.get("files_scanned", 0),
            chunk_count=result.get("total_chunks", result.get("chunks_embedded", 0)),
            repo_name=full_name,
            collection_name=_collection_name(full_name),
            commit_sha=git_sha,
            commit_message=git_msg,
            branch=git_branch,
            index_version=new_version,
        )
        db.execute(
            "UPDATE repos SET last_indexed_sha=$1, index_status=$2, index_version=$3, updated_at=NOW() WHERE full_name=$4",
            git_sha or "admin-index", "ready", new_version, full_name,
        )
    else:
        _discard_incoming(full_name, head_sha)
        db.execute(
            "UPDATE repos SET index_status=$1, updated_at=NOW() WHERE full_name=$2",
            "failed", full_name,
        )

    return {"repo": full_name, **result}


@cloud_router.get("/version")
async def get_version():
    """Return current Codewalk deployment metadata for self-update checks."""
    from src.codewalk import __version__
    # Try to read the deployed commit SHA from env (set by Docker build) or git
    commit_sha = os.environ.get("CODEWALK_COMMIT_SHA", "")
    if not commit_sha:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, check=True,
            )
            commit_sha = result.stdout.strip()
        except Exception:
            commit_sha = "unknown"

    released_at = os.environ.get("CODEWALK_RELEASED_AT", "")
    return {
        "codewalk_version": __version__,
        "commit_sha": commit_sha,
        "released_at": released_at,
        "release_notes_url": f"https://github.com/gupta29470/codewalk/releases/tag/v{__version__}",
        "update_command": "git pull origin master",
    }


@cloud_router.get("/indexes/{owner}/{repo}")
async def download_index(
    owner: str,
    repo: str,
    x_repo_token: str = Header(alias="X-Repo-Token"),
):
    """Stream the latest index as tar.gz. Authenticated per repo."""
    repo_name = f"{owner}/{repo}"
    db = api_state.get_db()
    row = db.fetchone("SELECT repo_token FROM repos WHERE full_name=$1", repo_name)
    if not row or not secrets.compare_digest(row["repo_token"], x_repo_token):
        raise HTTPException(403, "Invalid token")

    active_dir = _active_index_dir(repo_name)
    if active_dir is None:
        raise HTTPException(404, "No index available yet")

    import tarfile
    import tempfile
    import uuid

    def stream_tarball():
        tmp_path = Path(tempfile.gettempdir()) / f"codewalk_{owner}__{repo}_{os.getpid()}_{uuid.uuid4().hex}.tmp.tar.gz"
        try:
            with tarfile.open(tmp_path, "w:gz") as tar:
                # Follow the active symlink so the tarball contains the real files,
                # not a symlink member.
                tar.add(active_dir, arcname=".codewalk")
            with open(tmp_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    yield chunk
        finally:
            tmp_path.unlink(missing_ok=True)

    return StreamingResponse(stream_tarball(), media_type="application/gzip")


@cloud_router.get("/indexes/{owner}/{repo}/manifest")
async def get_manifest(
    owner: str,
    repo: str,
    x_repo_token: str = Header(alias="X-Repo-Token"),
):
    repo_name = f"{owner}/{repo}"
    db = api_state.get_db()
    row = db.fetchone("SELECT repo_token FROM repos WHERE full_name=$1", repo_name)
    if not row or not secrets.compare_digest(row["repo_token"], x_repo_token):
        raise HTTPException(403, "Invalid token")

    manifest_path = _artifacts_dir(repo_name) / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(404, "No manifest available")

    with open(manifest_path) as file:
        return json.load(file)
