"""Typer CLI entry point for Codewalk commands."""
import typer
import os
from pathlib import Path
from typing import Optional

from src.codewalk.pipeline import full_index_parallel, incremental_reindex, build_full_analysis
from src.codewalk.codewalk_config import codewalk_scan_directory, load_codewalk_yaml
from src.codewalk.ingestion.tech_detect import detect_tech_stack
from src.codewalk.ingestion.config_generator import generate_codewalk_yaml
from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.repo_discovery import ensure_codewalk_yaml
from src.codewalk.review.engine import run_review
from src.codewalk.review.report import Verdict

app_cli = typer.Typer(help="Codewalk — AI codebase intelligence")


def _resolve_repo(repo: Optional[str]) -> str:
    """Resolve repository root, auto-creating codewalk.yaml if missing."""
    if repo:
        return str(ensure_codewalk_yaml(repo, create=True))
    return str(ensure_codewalk_yaml(create=True))

@app_cli.command()
def analyze(
    repo: Optional[str] = typer.Option(None, help="Path to repository root (default: discover from cwd)"),
    collection: str = typer.Option("codebase", help="ChromaDB collection name"),
):
    """Full local index: scan → tech detect → chunk → embed → dep graph → modules → DuckDB.
    Produces .codewalk/ in the repo root — identical output to codewalk_analyze_codebase."""

    repo = _resolve_repo(repo)
    typer.echo(f"Analyzing {repo} ...")

    # 1. Load codewalk.yaml (exclude patterns)
    config = load_codewalk_yaml(repo)
    if config.exclude:
        typer.echo(f"Exclude: {config.exclude}")

    # 2. Detect tech stack
    tech_stack = detect_tech_stack(repo)
    typer.echo(f"Tech stack: {', '.join(tech_stack) or 'unknown'}")

    # 3. Chunk + embed → ChromaDB (inside .codewalk/chroma/)
    chroma_dir = os.path.join(repo.rstrip("/"), ".codewalk", "chroma")
    result = full_index_parallel(
        repo_path=repo,
        collection_name=collection,
        persist_dir=chroma_dir,
        codewalk_config=config,
    )
    typer.echo(f"Indexed {result['chunks_embedded']} chunks from {result['files_scanned']} files")

    # 4. Analysis + DuckDB + docs + guidelines — one call, no duplication
    db_path = os.path.join(repo.rstrip("/"), ".codewalk", "graph.duckdb")
    files = codewalk_scan_directory(repo, config)
    gl_path = os.path.join(repo, config.guidelines_path) if config.guidelines_path else ""
    docs_p = os.path.join(repo, config.docs_path) if config.docs_path else ""
    analysis = build_full_analysis(
        db_path=db_path,
        files=files,
        embedded_chunks=result.get("embedded_chunks"),
        guidelines_path=gl_path,
        docs_path=docs_p,
    )
    module_names = list(analysis["modules_result"]["modules"].keys())
    typer.echo(f"Modules: {', '.join(module_names) or 'none'}")

    if analysis.get("docs_indexed"):
        typer.echo(f"Docs: {analysis['docs_indexed']['chunks_stored']} chunks")
    if analysis.get("guidelines_indexed"):
        typer.echo(f"Guidelines: {analysis['guidelines_indexed']} chunks")

    typer.echo(f"Done. Index written to {repo}/.codewalk/")


@app_cli.command()
def reindex(
    repo: Optional[str] = typer.Option(None, help="Path to repository root (default: discover from cwd)"),
    collection: str = typer.Option("codebase", help="ChromaDB collection name"),
):
    """Incremental reindex — only re-embeds files whose content hash changed.
    Skips unchanged files, deletes chunks for removed files, rebuilds DuckDB."""

    repo = _resolve_repo(repo)
    chroma_dir = os.path.join(repo.rstrip("/"), ".codewalk", "chroma")

    # 1. Check existing index exists
    store = VectorStore(persist_dir=chroma_dir)
    store.create_collection(collection)
    if store.chunk_count() == 0:
        typer.echo("No existing index. Run `codewalk analyze` first.")
        raise typer.Exit(1)

    # 2. Get all currently indexed file paths
    paths = list(store.get_all_indexed_files())
    if not paths:
        typer.echo("No existing index. Run `codewalk analyze` first.")
        raise typer.Exit(1)

    # 3. Load codewalk.yaml
    config = load_codewalk_yaml(repo)

    typer.echo(f"Incremental reindex: {repo} ({len(paths)} files in index)")

    # 4. Hash-based incremental reindex → ChromaDB (codewalk_config filtering)
    result = incremental_reindex(
        paths, repo, collection, persist_dir=chroma_dir, codewalk_config=config,
    )
    typer.echo(
        f"  Skipped (same): {result['files_skipped']}\n"
        f"  Re-indexed:     {result['files_reindexed']}\n"
        f"  Deleted:        {result['files_deleted']}\n"
        f"  Chunks embedded: {result['chunks_embedded']}"
    )

    # 5. Rebuild analysis + DuckDB + re-index docs/guidelines
    db_path = os.path.join(repo.rstrip("/"), ".codewalk", "graph.duckdb")
    files = codewalk_scan_directory(repo, config)
    gl_path = os.path.join(repo, config.guidelines_path) if config.guidelines_path else ""
    docs_p = os.path.join(repo, config.docs_path) if config.docs_path else ""
    analysis = build_full_analysis(
        db_path=db_path,
        files=files,
        embedded_chunks=result.get("embedded_chunks"),
        guidelines_path=gl_path,
        docs_path=docs_p,
        force_reindex_extras=True,
    )
    module_names = list(analysis["modules_result"]["modules"].keys())

    typer.echo(f"  Modules: {', '.join(module_names) or 'none'}")
    typer.echo(f"Done. ({result['total_time']})")


@app_cli.command()
def refresh(
    repo: Optional[str] = typer.Option(None, help="Path to repository root (default: discover from cwd)"),
):
    """Refresh analysis (dep graph, modules, DuckDB) WITHOUT re-embedding.
    Use after code changes when you only need updated blast radius / modules."""

    repo = _resolve_repo(repo)
    config = load_codewalk_yaml(repo)
    typer.echo(f"Refreshing analysis: {repo}")

    db_path = os.path.join(repo.rstrip("/"), ".codewalk", "graph.duckdb")
    files = codewalk_scan_directory(repo, config)
    analysis = build_full_analysis(db_path=db_path, files=files)
    module_names = list(analysis["modules_result"]["modules"].keys())

    typer.echo(
        f"  Files: {len(analysis['files'])}\n"
        f"  Dep graph: {len(analysis['deps']['graph'])} files\n"
        f"  Modules: {', '.join(module_names) or 'none'}"
    )
    typer.echo("Done. (no re-embedding)")


@app_cli.command("generate-config")
def generate_config(
    repo: Optional[str] = typer.Option(None, help="Path to repository root (default: cwd)"),
    force: bool = typer.Option(False, help="Overwrite an existing codewalk.yaml"),
):
    """Generate a starter codewalk.yaml with stack-specific exclusions."""
    target = Path(repo or os.getcwd()).resolve()
    if not target.is_dir():
        typer.echo(f"❌ Not a directory: {target}", err=True)
        raise typer.Exit(1)

    existing = target / "codewalk.yaml"
    if existing.exists() and not force:
        typer.echo(
            f"⚠️  codewalk.yaml already exists at {existing}.\n"
            "Run with --force to overwrite, or edit the file directly."
        )
        raise typer.Exit(1)

    path = generate_codewalk_yaml(target, force=force)
    if path:
        typer.echo(f"✅ Wrote {path}")
    else:
        typer.echo(f"⚠️  codewalk.yaml already exists at {existing}.")


@app_cli.command()
def review(
    repo: Optional[str] = typer.Option(None, help="Path to repository root (default: discover from cwd)"),
    target_branch: Optional[str] = typer.Option(None, help="Diff target branch (e.g. main)"),
    staged: bool = typer.Option(False, help="Review staged changes only"),
    incremental: bool = typer.Option(False, "--incremental", help="Review only files changed since the last review on this branch"),
    force_full_review: bool = typer.Option(False, "--force-full-review", help="Ignore cache and previous review; run a full review"),
    fail_on: Optional[str] = typer.Option(None, help="Exit non-zero if verdict is this or worse: blocking, warning"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON report"),
):
    """Run one-stop code review on the working tree or a branch diff."""
    repo = _resolve_repo(repo)
    typer.echo(f"Reviewing {repo} ...")

    report = run_review(
        repo_path=Path(repo),
        target_branch=target_branch,
        staged=staged,
        incremental=incremental,
        force_full_review=force_full_review,
    )

    if json_output:
        import json
        typer.echo(json.dumps(report.to_dict(), indent=2))
    else:
        from src.codewalk.review.renderers import render_cli

        typer.echo(render_cli(report))

    if fail_on == "blocking":
        if report.verdict == Verdict.REQUEST_CHANGES:
            raise typer.Exit(1)
    elif fail_on == "warning":
        if report.verdict in (Verdict.REQUEST_CHANGES, Verdict.APPROVE_WITH_NITS):
            raise typer.Exit(1)


def main():
    """CLI entry point."""
    app_cli()


if __name__ == "__main__":
    main()


