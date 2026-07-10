"""Cloud indexing worker: poll Postgres jobs, clone repos, build indexes, publish atomically."""
import json
import os
import shutil
import tempfile
import subprocess
import threading
import time
import logging

import psycopg2
import psycopg2.extras

from src.codewalk.worker.github_app import get_installation_token
from src.codewalk.worker.atomic_store import atomic_swap
from src.codewalk.codewalk_config import load_codewalk_yaml, codewalk_scan_directory
from src.codewalk.pipeline import full_index_parallel, incremental_reindex, build_full_analysis, write_manifest
from src.codewalk.api.state import _PgHelper

logger = logging.getLogger("codewalk.worker")


def _read_previous_version(latest_dir: str) -> int:
    """Read index_version from the previous latest/manifest.json. Returns 0 if missing."""
    manifest_path = os.path.join(latest_dir, "manifest.json")
    try:
        with open(manifest_path) as f:
            return json.load(f).get("index_version", 0)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return 0


def _has_previous_index(storage_path: str, repo_name: str) -> str | None:
    """Return the latest/ chroma path if a previous index exists, else None."""
    latest_chroma = os.path.join(storage_path, repo_name, "latest", "chroma")
    if os.path.isdir(latest_chroma):
        return latest_chroma
    return None


def build_index(
    repo_name: str,
    commit_sha: str,
    commit_message: str,
    github_url: str,
    branch: str,
    installation_id: str,
    storage_path: str,          # /var/codewalk
    app_id: str,
    private_key_pem: str,
):
    """Clone → incremental reindex (or full if first time) → graph → atomic swap."""

    token = get_installation_token(app_id, private_key_pem, installation_id)
    clone_url = github_url.replace("https://", f"https://x-access-token:{token}@")

    with tempfile.TemporaryDirectory() as work_dir:
        # 1. Shallow clone — only latest commit, fast
        subprocess.run(
            ["git", "clone", "--depth=1", "--branch", branch, clone_url, work_dir],
            check=True, capture_output=True,
        )

        # 2. Read codewalk.yaml (exclude patterns)
        config = load_codewalk_yaml(work_dir)

        # 3. Prepare incoming/
        incoming = os.path.join(storage_path, repo_name, "incoming")
        # Clean any leftover from a failed previous run
        if os.path.exists(incoming):
            shutil.rmtree(incoming)
        os.makedirs(incoming, exist_ok=True)

        incoming_chroma = os.path.join(incoming, "chroma")
        latest_dir = os.path.join(storage_path, repo_name, "latest")
        previous_version = _read_previous_version(latest_dir)
        previous_chroma = _has_previous_index(storage_path, repo_name)

        # 4. Index: incremental if previous exists, full otherwise
        if previous_chroma:
            try:
                # Copy previous ChromaDB so incremental_reindex can compare hashes
                shutil.copytree(previous_chroma, incoming_chroma)
                logger.info(f"[worker] {repo_name}: incremental reindex (previous v{previous_version})")

                result = incremental_reindex(
                    paths=[work_dir],
                    repo_path=work_dir,
                    collection_name="codebase",
                    persist_dir=incoming_chroma,
                    codewalk_config=config,
                )
                files_scanned = result["files_on_disk"]
                chunks_embedded = result["chunks_embedded"]

                # For graph rebuild, we need ALL chunks (not just changed ones).
                # Read the complete chunk set from the updated ChromaDB.
                from src.codewalk.embeddings.vector_store import VectorStore
                _store = VectorStore(persist_dir=incoming_chroma)
                _store.create_collection("codebase")
                all_chunks = _store.get_all_chunks()

                logger.info(
                    f"[worker] {repo_name}: incremental done — "
                    f"{result['files_skipped']} skipped, {result['files_reindexed']} reindexed, "
                    f"{result['files_deleted']} deleted"
                )
            except Exception as e:
                # Incremental failed (corrupted chroma, schema mismatch, etc.)
                # Fall back to full re-index
                logger.warning(f"[worker] {repo_name}: incremental failed ({e}), falling back to full")
                if os.path.exists(incoming_chroma):
                    shutil.rmtree(incoming_chroma)

                result = full_index_parallel(
                    repo_path=work_dir,
                    collection_name="codebase",
                    persist_dir=incoming_chroma,
                    codewalk_config=config,
                )
                all_chunks = result["embedded_chunks"]
                files_scanned = result["files_scanned"]
                chunks_embedded = result["chunks_embedded"]
        else:
            # First index — no previous data to compare against
            logger.info(f"[worker] {repo_name}: first index (full)")
            result = full_index_parallel(
                repo_path=work_dir,
                collection_name="codebase",
                persist_dir=incoming_chroma,
                codewalk_config=config,
            )
            all_chunks = result["embedded_chunks"]
            files_scanned = result["files_scanned"]
            chunks_embedded = result["chunks_embedded"]

        # 5. Analysis + DuckDB + docs — always rebuild (deps change even when embeddings don't)
        files = codewalk_scan_directory(work_dir, config)
        docs_path = os.path.join(work_dir, config.docs_path) if config.docs_path else ""
        build_full_analysis(
            db_path=os.path.join(incoming, "graph.duckdb"),
            files=files,
            embedded_chunks=all_chunks,
            docs_path=docs_path,
        )

        # 6. Write cloud manifest (increment version)
        write_manifest(
            index_dir=incoming,
            file_count=files_scanned,
            chunk_count=chunks_embedded,
            repo_name=repo_name,
            commit_sha=commit_sha,
            commit_message=commit_message,
            branch=branch,
            index_version=previous_version + 1,
        )

        # 7. Atomic swap: incoming/ → latest/
        atomic_swap(incoming, latest_dir)

    logger.info(f"[worker] {repo_name} indexed at {commit_sha[:7]} (v{previous_version + 1})")


def worker_loop(db_url: str, app_id: str, private_key_pem: str, storage_path: str):
    """Poll Postgres jobs table. Runs as background thread inside FastAPI.
    
    Creates its own DB connection — psycopg2 connections are NOT thread-safe.
    """
    conn = psycopg2.connect(db_url, cursor_factory=psycopg2.extras.RealDictCursor)
    conn.autocommit = True
    db = _PgHelper(conn)

    logger.info("[worker] Started polling for jobs")
    try:
        while True:
            # SELECT FOR UPDATE SKIP LOCKED — safe for multiple workers, no Redis needed
            job = db.fetchone("""
                UPDATE jobs SET status='running', started_at=NOW()
                WHERE id = (
                    SELECT id from jobs where status='queued'
                    ORDER BY queued_at LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
            RETURNING id, repo_name, commit_sha, commit_message
            """)

            if job:
                repo = db.fetchone("SELECT * FROM repos WHERE name=$1", job["repo_name"])
                try:
                    build_index(
                        repo_name=job["repo_name"],
                        commit_sha=job["commit_sha"],
                        commit_message=job.get("commit_message", ""),
                        github_url=repo["github_url"],
                        branch=repo["branch"],
                        installation_id=repo["installation_id"],
                        storage_path=storage_path,
                        app_id=app_id,
                        private_key_pem=private_key_pem,
                    )
                    db.execute(
                        "UPDATE jobs SET status='done', finished_at=NOW() WHERE id=$1",
                        job["id"]
                    )
                except Exception as e:
                    db.execute(
                        "UPDATE jobs SET status='failed', error=$1, finished_at=NOW() WHERE id=$2",
                        str(e), job["id"],
                    )
                    logger.exception(f"[worker] Failed {job['repo_name']}: {e}")
            else:
                time.sleep(5)   # nothing queued — wait before next poll
    finally:
        conn.close()
        logger.info("[worker] Shut down")