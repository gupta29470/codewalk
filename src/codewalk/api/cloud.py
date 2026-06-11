import hmac
import hashlib
import secrets
import json
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException, Header, FastAPI
from fastapi.responses import StreamingResponse

from src.codewalk.api import state as api_state
from src.codewalk.config import settings as _settings

cloud_router = APIRouter()

MAX_WEBHOOK_SIZE = 50 * 1024 * 1024  # 50MB


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

    Also triggers catch-up indexing for any registered repos that have never
    been indexed or are in a failed state. This ensures that after a fresh
    deployment (or a deployment that fixes an indexing bug), indexing starts
    automatically without waiting for a GitHub webhook.
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

    # Start catch-up indexer after a short delay so the API finishes startup
    def _delayed_catchup():
        time.sleep(15)
        _run_catchup_indexing(logger)

    threading.Thread(target=_delayed_catchup, daemon=True).start()
    logger.info("[cloud] Catch-up indexer scheduled (15s delay)")


def _run_catchup_indexing(logger):
    """Index any repos that have never been indexed or are in failed state."""
    try:
        db = api_state.get_db()
        rows = db.fetchall(
            """SELECT * FROM repos
            WHERE last_indexed_sha IS NULL
               OR index_status IN ('pending', 'failed')
            ORDER BY updated_at DESC"""
        )
        if not rows:
            logger.info("[catchup] No repos need indexing")
            return

        logger.info(f"[catchup] {len(rows)} repo(s) need indexing: "
                    f"{', '.join(dict(r)['full_name'] for r in rows)}")

        for row in rows:
            repo = dict(row)
            full_name = repo.get("full_name", "")
            branch = repo.get("branch", "master")

            logger.info(f"[catchup] Starting index for {full_name}")
            db.execute(
                "UPDATE repos SET index_status=$1, updated_at=NOW() WHERE full_name=$2",
                "indexing", full_name,
            )

            try:
                repo_path = _clone_or_pull_repo(repo, branch)
                # Catch-up does a full index + full DuckDB rebuild to ensure consistency
                result = _analyze_repo(repo_path, full_name)

                if result["status"] == "success":
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

                    # Write manifest and increment index_version
                    row_ver = db.fetchone("SELECT index_version FROM repos WHERE full_name=$1", full_name)
                    new_version = ((row_ver["index_version"] or 0) if row_ver else 0) + 1
                    from src.codewalk.pipeline import write_manifest
                    write_manifest(
                        str(_artifacts_dir(full_name)),
                        file_count=result.get("files_scanned", 0),
                        chunk_count=result.get("chunks_embedded", 0),
                        repo_name=full_name,
                        commit_sha=git_sha,
                        commit_message=git_msg,
                        branch=git_branch,
                        index_version=new_version,
                        embedding_model=_settings.embedding_model,
                        minimum_mcp_version="1.0.0",
                    )
                    db.execute(
                        "UPDATE repos SET last_indexed_sha=$1, index_status=$2, index_version=$3, updated_at=NOW() WHERE full_name=$4",
                        git_sha or "catchup", "ready", new_version, full_name,
                    )
                    logger.info(
                        f"[catchup] ✅ {full_name} indexed (v{new_version}): "
                        f"{result.get('files_scanned', 0)} files, "
                        f"{result.get('chunks_embedded', 0)} chunks embedded"
                    )
                else:
                    error = result.get("error", "unknown")
                    db.execute(
                        "UPDATE repos SET index_status=$1, updated_at=NOW() WHERE full_name=$2",
                        "failed", full_name,
                    )
                    logger.error(f"[catchup] ❌ {full_name} failed: {error}")

            except Exception as e:
                logger.exception(f"[catchup] ❌ {full_name} exception: {e}")
                db.execute(
                    "UPDATE repos SET index_status=$1, updated_at=NOW() WHERE full_name=$2",
                    "failed", full_name,
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
    """Clone repo if not exists, or git pull if it does."""
    storage_path = repo.get("storage_path") or f"/var/codewalk/repos/{repo['full_name']}"
    repo_path = Path(storage_path)

    if repo_path.exists() and (repo_path / ".git").exists():
        # Pull latest
        subprocess.run(
            ["git", "pull", "origin", branch],
            cwd=str(repo_path),
            check=False,
            capture_output=True,
        )
    else:
        # Clone fresh
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        clone_url = repo.get("clone_url", f"https://github.com/{repo['full_name']}.git")
        subprocess.run(
            ["git", "clone", "--depth=1", "--branch", branch, clone_url, str(repo_path)],
            check=False,
            capture_output=True,
        )

    return repo_path


def _artifacts_dir(repo_full_name: str) -> Path:
    """Return the standard artifacts directory for a repo's index.

    All cloud index artifacts (chroma/, graph.duckdb, manifest.json) live here.
    Source code lives separately at /var/codewalk/repos/{owner}/{repo}/.
    """
    storage = os.environ.get("INDEX_STORAGE_PATH", "/var/codewalk")
    return Path(storage) / "indexes" / repo_full_name


def _analyze_repo(repo_path: Path, repo_full_name: str) -> dict:
    """Run the FULL /analyze pipeline with team config support.

    Reads codewalk.yaml from the repo root, respects exclude patterns,
    and produces both ChromaDB embeddings and DuckDB analysis.
    Artifacts are stored at /var/codewalk/indexes/{owner}/{repo}/.
    Manifest writing and index_version increment are the caller's responsibility.
    """
    from src.codewalk.team_config import load_codewalk_yaml
    from src.codewalk.pipeline import full_index_parallel, build_full_analysis

    try:
        adir = _artifacts_dir(repo_full_name)
        persist_dir = str(adir / "chroma")
        db_path = str(adir / "graph.duckdb")

        config = load_codewalk_yaml(str(repo_path))

        # full_index_parallel scans files internally (with team_config exclusions)
        # and returns them in index_result["files"] — reuse instead of scanning twice
        index_result = full_index_parallel(
            repo_path=str(repo_path),
            collection_name="codebase",
            persist_dir=persist_dir,
            team_config=config,
        )

        guidelines_path = ""
        docs_path = ""
        if config.guidelines_path:
            guidelines_path = str(repo_path / config.guidelines_path)
        if config.docs_path:
            docs_path = str(repo_path / config.docs_path)

        build_full_analysis(
            db_path=db_path,
            files=index_result["files"],
            embedded_chunks=index_result.get("embedded_chunks"),
            guidelines_path=guidelines_path,
            docs_path=docs_path,
        )

        return {
            "status": "success",
            "files_scanned": index_result.get("files_scanned", 0),
            "chunks_embedded": index_result.get("chunks_embedded", 0),
        }
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def _run_incremental_index(repo_path: Path, repo_full_name: str) -> dict:
    """Run incremental re-index, then rebuild full DuckDB analysis.

    Artifacts are stored at /var/codewalk/indexes/{owner}/{repo}/.
    Manifest writing and index_version increment are the caller's responsibility.
    """
    from src.codewalk.team_config import load_codewalk_yaml, team_scan_directory
    from src.codewalk.pipeline import reindex, build_full_analysis

    try:
        adir = _artifacts_dir(repo_full_name)
        persist_dir = str(adir / "chroma")
        db_path = str(adir / "graph.duckdb")

        config = load_codewalk_yaml(str(repo_path))

        # reindex() fetches indexed files from ChromaDB, compares hashes,
        # re-embeds only changed/new, deletes removed files.
        result = reindex(
            repo_path=str(repo_path),
            collection_name="codebase",
            persist_dir=persist_dir,
            team_config=config,
        )

        # Rebuild deps/modules/DuckDB even for incremental changes
        files = team_scan_directory(str(repo_path), config)

        guidelines_path = ""
        docs_path = ""
        if config.guidelines_path:
            guidelines_path = str(repo_path / config.guidelines_path)
        if config.docs_path:
            docs_path = str(repo_path / config.docs_path)

        build_full_analysis(
            db_path=db_path,
            files=files,
            guidelines_path=guidelines_path,
            docs_path=docs_path,
        )

        return {
            "status": "success",
            "files_scanned": result.get("files_scanned", 0),
            "files_changed": result.get("changed_files", 0),
            "files_deleted": result.get("deleted_files", 0),
        }
    except Exception as e:
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

    if not _verify_webhook(body, signature, os.environ["GITHUB_WEBHOOK_SECRET"]):
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

    # ── Skip if SHA unchanged ─────────────────────────────────────────
    if repo.get("last_indexed_sha") == commit:
        db.execute(
            "UPDATE webhook_deliveries SET status=$1 WHERE delivery_id=$2",
            "ignored_unchanged", delivery_id,
        )
        return {"status": "skipped", "reason": "Commit already indexed", "sha": commit[:7]}

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
        try:
            repo_path = _clone_or_pull_repo(repo, branch)
            result = _run_incremental_index(repo_path, repo_full_name)

            if result["status"] == "success":
                # Fetch current version, increment, write manifest, then update DB
                row = db.fetchone("SELECT index_version FROM repos WHERE full_name=$1", repo_full_name)
                new_version = ((row["index_version"] or 0) if row else 0) + 1
                from src.codewalk.pipeline import write_manifest
                write_manifest(
                    str(_artifacts_dir(repo_full_name)),
                    file_count=result.get("files_scanned", 0),
                    chunk_count=result.get("chunks_embedded", 0),
                    repo_name=repo_full_name,
                    commit_sha=commit,
                    commit_message=msg,
                    branch=branch,
                    index_version=new_version,
                    embedding_model=_settings.embedding_model,
                    minimum_mcp_version="1.0.0",
                )
                db.execute(
                    "UPDATE repos SET last_indexed_sha=$1, index_status=$2, index_version=$3, updated_at=NOW() WHERE full_name=$4",
                    commit, "ready", new_version, repo_full_name,
                )
                db.execute(
                    "UPDATE jobs SET status=$1, finished_at=NOW() WHERE repo_name=$2 AND commit_sha=$3 AND status='queued'",
                    "done", repo_full_name, commit,
                )
                db.execute(
                    "UPDATE webhook_deliveries SET status=$1 WHERE delivery_id=$2",
                    "processed", delivery_id,
                )
                return result
            else:
                error_msg = result.get("error", "Unknown indexing error")
                db.execute(
                    "UPDATE repos SET index_status=$1, updated_at=NOW() WHERE full_name=$2",
                    "failed", repo_full_name,
                )
                db.execute(
                    "UPDATE jobs SET status=$1, error=$2, finished_at=NOW() WHERE repo_name=$3 AND commit_sha=$4 AND status='queued'",
                    "failed", error_msg, repo_full_name, commit,
                )
                db.execute(
                    "UPDATE webhook_deliveries SET status=$1, error=$2 WHERE delivery_id=$3",
                    "failed", error_msg, delivery_id,
                )
                return result
        except Exception as e:
            db.execute(
                "UPDATE repos SET index_status=$1, updated_at=NOW() WHERE full_name=$2",
                "failed", repo_full_name,
            )
            db.execute(
                "UPDATE jobs SET status=$1, error=$2, finished_at=NOW() WHERE repo_name=$3 AND commit_sha=$4 AND status='queued'",
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
    if not secrets.compare_digest(x_admin_key, os.environ["ADMIN_API_KEY"]):
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
    if not secrets.compare_digest(x_admin_key, os.environ["ADMIN_API_KEY"]):
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
    if not secrets.compare_digest(x_admin_key, os.environ["ADMIN_API_KEY"]):
        raise HTTPException(403, "Invalid admin key")

    body = await request.json()
    full_name = body.get("full_name", "")
    branch = body.get("branch", "")

    db = api_state.get_db()
    repo = db.fetchone("SELECT * FROM repos WHERE full_name=$1", full_name)
    if not repo:
        raise HTTPException(404, f"Repo '{full_name}' not registered")

    repo = dict(repo)
    repo_path = _clone_or_pull_repo(repo, branch or repo["branch"])
    result = _run_incremental_index(repo_path, full_name)

    if result["status"] == "success":
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
        from src.codewalk.pipeline import write_manifest
        write_manifest(
            str(_artifacts_dir(full_name)),
            file_count=result.get("files_scanned", 0),
            chunk_count=result.get("files_changed", 0),
            repo_name=full_name,
            commit_sha=git_sha,
            commit_message=git_msg,
            branch=git_branch,
            index_version=new_version,
            embedding_model=_settings.embedding_model,
            minimum_mcp_version="1.0.0",
        )
        db.execute(
            "UPDATE repos SET index_status=$1, index_version=$2, updated_at=NOW() WHERE full_name=$3",
            "ready", new_version, full_name,
        )
    else:
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

    index_path = _artifacts_dir(repo_name)
    if not index_path.exists():
        raise HTTPException(404, "No index available yet")

    import tarfile
    storage = os.environ.get("INDEX_STORAGE_PATH", "/var/codewalk")

    def stream_tarball():
        tmp_path = Path(storage) / f"{owner}__{repo}.tmp.tar.gz"
        try:
            with tarfile.open(tmp_path, "w:gz") as tar:
                tar.add(index_path, arcname=".codewalk")
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
