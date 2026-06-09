import os
import tempfile
import subprocess
import threading
import time
import logging

logger = logging.getLogger("codewalk.worker")

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
    """Clone → filter → chunk → embed → graph → atomic swap. Full re-index."""
    from src.codewalk.worker.github_app import get_installation_token
    from src.codewalk.worker.atomic_store import atomic_swap
    from src.codewalk.team_config import load_codewalk_yaml, team_scan_directory
    from src.codewalk.pipeline import full_index_parallel, build_full_analysis, write_manifest

    token = get_installation_token(app_id, private_key_pem, installation_id)
    clone_url = github_url.replace("https://", f"https://x-access-token:{token}@")

    with tempfile.TemporaryDirectory() as work_dir:
        # 1. Shallow clone — only latest commit, fast
        subprocess.run(
            ["git", "clone", "--depth=1", "--branch", branch, clone_url, work_dir],
            check=True, capture_output=True,
        )

        # 2. Read team's codewalk.yaml (exclude patterns)
        config = load_codewalk_yaml(work_dir)

        # 3. Index into incoming/
        incoming = os.path.join(storage_path, repo_name, "incoming")
        os.makedirs(incoming, exist_ok=True)

        result = full_index_parallel(
            repo_path=work_dir,
            collection_name="codebase",
            persist_dir=os.path.join(incoming, "chroma"),
            team_config=config,
        )

        # 4. Analysis + DuckDB + docs + guidelines — one shared call
        files = team_scan_directory(work_dir, config)
        guidelines_path = os.path.join(work_dir, config.guidelines_path) if config.guidelines_path else ""
        docs_path = os.path.join(work_dir, config.docs_path) if config.docs_path else ""
        build_full_analysis(
            db_path=os.path.join(incoming, "graph.duckdb"),
            files=files,
            embedded_chunks=result["embedded_chunks"],
            guidelines_path=guidelines_path,
            docs_path=docs_path,
        )

        # 5. Write cloud manifest
        write_manifest(
            index_dir=incoming,
            file_count=result["files_scanned"],
            chunk_count=result["chunks_embedded"],
            repo_name=repo_name,
            commit_sha=commit_sha,
            commit_message=commit_message,
            branch=branch,
        )

        # 6. Atomic swap: incoming/ → latest/
        latest = os.path.join(storage_path, repo_name, "latest")
        atomic_swap(incoming, latest)

    logger.info(f"[worker] {repo_name} indexed at {commit_sha[:7]}")


def worker_loop(db_url: str, app_id: str, private_key_pem: str, storage_path: str):
    """Poll Postgres jobs table. Runs as background thread inside FastAPI.
    
    Creates its own DB connection — psycopg2 connections are NOT thread-safe.
    """
    from src.codewalk.api.state import _PgHelper
    import psycopg2
    import psycopg2.extras

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