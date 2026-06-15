import typer
import os

from src.codewalk.pipeline import full_index_parallel, incremental_reindex, build_full_analysis
from src.codewalk.team_config import team_scan_directory, load_codewalk_yaml
from src.codewalk.ingestion.tech_detect import detect_tech_stack
from src.codewalk.embeddings.vector_store import VectorStore

app_cli = typer.Typer(help="Codewalk — AI codebase intelligence")

@app_cli.command()
def analyze(
    repo: str = typer.Option(".", help="Path to repository root"),
    collection: str = typer.Option("codebase", help="ChromaDB collection name"),
):
    """Full local index: scan → tech detect → chunk → embed → dep graph → modules → DuckDB.
    Produces .codewalk/ in the repo root — identical output to codewalk_analyze_codebase."""

    typer.echo(f"Analyzing {repo} ...")

    # 1. Load team's codewalk.yaml (exclude patterns)
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
        team_config=config,
    )
    typer.echo(f"Indexed {result['chunks_embedded']} chunks from {result['files_scanned']} files")

    # 4. Analysis + DuckDB + docs + guidelines — one call, no duplication
    db_path = os.path.join(repo.rstrip("/"), ".codewalk", "graph.duckdb")
    files = team_scan_directory(repo, config)
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
    repo: str = typer.Option(".", help="Path to repository root"),
    collection: str = typer.Option("codebase", help="ChromaDB collection name"),
):
    """Incremental reindex — only re-embeds files whose content hash changed.
    Skips unchanged files, deletes chunks for removed files, rebuilds DuckDB."""

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

    # 3. Load team's codewalk.yaml
    config = load_codewalk_yaml(repo)

    typer.echo(f"Incremental reindex: {repo} ({len(paths)} files in index)")

    # 4. Hash-based incremental reindex → ChromaDB (team_config filtering)
    result = incremental_reindex(
        paths, repo, collection, persist_dir=chroma_dir, team_config=config,
    )
    typer.echo(
        f"  Skipped (same): {result['files_skipped']}\n"
        f"  Re-indexed:     {result['files_reindexed']}\n"
        f"  Deleted:        {result['files_deleted']}\n"
        f"  Chunks embedded: {result['chunks_embedded']}"
    )

    # 5. Rebuild analysis + DuckDB + re-index docs/guidelines
    db_path = os.path.join(repo.rstrip("/"), ".codewalk", "graph.duckdb")
    files = team_scan_directory(repo, config)
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
    repo: str = typer.Option(".", help="Path to repository root"),
):
    """Refresh analysis (dep graph, modules, DuckDB) WITHOUT re-embedding.
    Use after code changes when you only need updated blast radius / modules."""

    config = load_codewalk_yaml(repo)
    typer.echo(f"Refreshing analysis: {repo}")

    db_path = os.path.join(repo.rstrip("/"), ".codewalk", "graph.duckdb")
    files = team_scan_directory(repo, config)
    analysis = build_full_analysis(db_path=db_path, files=files)
    module_names = list(analysis["modules_result"]["modules"].keys())

    typer.echo(
        f"  Files: {len(analysis['files'])}\n"
        f"  Dep graph: {len(analysis['deps']['graph'])} files\n"
        f"  Modules: {', '.join(module_names) or 'none'}"
    )
    typer.echo("Done. (no re-embedding)")


def main():
    app_cli()


if __name__ == "__main__":
    main()


