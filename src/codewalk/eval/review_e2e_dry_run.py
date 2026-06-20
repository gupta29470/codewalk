"""End-to-end dry run: index a temp repo and review a diff.

Usage:
    .codewalk-env/bin/python src/codewalk/eval/review_e2e_dry_run.py [case_name]
    .codewalk-env/bin/python src/codewalk/eval/review_e2e_dry_run.py --all

This demonstrates the full Codewalk review pipeline:
1. Create a temporary git repo from a real multi-language source file.
2. Apply an injected-bug mutation.
3. Index the repo (scan → chunk → embed → store).
4. Run review_diff on the staged diff using the freshly built index.
5. Print the structured review result.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from src.codewalk.pipeline import full_index_parallel
from src.codewalk.review.reviewer import review_diff
from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.eval.generate_multilang_review_fixtures import (
    CASES,
    REPO_ROOT,
    _line_number,
)


def _generate_case_repo(case: dict, temp_root: Path) -> Path:
    """Create a git repo with original committed and mutation staged."""
    repo = temp_root / case["name"]
    repo.mkdir(parents=True, exist_ok=True)

    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "dryrun@codewalk.local"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "DryRun"], cwd=repo, check=True)

    src_file = REPO_ROOT / case["source_file"]
    original = src_file.read_text()
    if case["old"] not in original:
        raise ValueError(f"[{case['name']}] old string not found in {src_file}")

    mutated = original.replace(case["old"], case["new"], 1)
    if mutated == original:
        raise ValueError(f"[{case['name']}] mutation did not change the file")

    dest = repo / case["file_path"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(original)

    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "original"], cwd=repo, check=True)

    dest.write_text(mutated)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    return repo


def _run_single_case(case: dict, temp_root: Path, verbose: bool = True) -> dict:
    """Run one dry-run case and return a result summary."""
    repo_path = _generate_case_repo(case, temp_root)

    chroma_dir = temp_root / f"chroma_{case['name']}"
    t0 = time.time()
    result = full_index_parallel(
        repo_path=str(repo_path),
        collection_name="dryrun_codebase",
        persist_dir=str(chroma_dir),
    )
    index_time = time.time() - t0
    store = VectorStore(persist_dir=str(chroma_dir))
    store.create_collection("dryrun_codebase")

    review_result = review_diff(
        staged=True,
        repo_path=str(repo_path),
        use_llm=True,
        store=store,
    )

    expected = case["expected"].copy()
    expected["file_path"] = case["file_path"]
    expected["line_number"] = _line_number(
        (repo_path / case["file_path"]).read_text(),
        expected.pop("line_marker"),
    )

    if review_result.issues:
        top_issue = review_result.issues[0]
        expected_categories = expected["category"]
        if isinstance(expected_categories, str):
            expected_categories = [expected_categories]
        category_match = top_issue.category.value in expected_categories
        line_match = abs((top_issue.line_number or 0) - expected["line_number"]) <= 5
        passed = category_match and line_match
        status = "passed" if passed else "partial"
        detail = {
            "file_path": top_issue.file_path,
            "line_number": top_issue.line_number,
            "title": top_issue.title,
            "severity": top_issue.severity.value,
            "category": top_issue.category.value,
            "confidence": top_issue.confidence.value,
        }
    else:
        status = "failed"
        category_match = False
        line_match = False
        detail = None

    if verbose:
        print("=" * 60)
        print(f"CASE: {case['name']}")
        print("=" * 60)
        print(f"Repo: {repo_path}")
        print(f"Source: {case['source_file']}")
        print(f"Files scanned: {len(result['files'])}")
        print(f"Chunks embedded: {result['chunks_embedded']}")
        print(f"Index time: {index_time:.2f}s")
        print(f"Verdict: {review_result.verdict.value}")
        print(f"Issues found: {len(review_result.issues)}")
        if detail:
            print("Top issue:")
            print(json.dumps(detail, indent=2))
        print(f"Expected category: {expected['category']}")
        print(f"Expected severity: {expected['severity']}")
        print(f"Expected line: {expected['line_number']}")
        print(f"Category match: {category_match} | Line match: {line_match}")
        icon = "✅" if status == "passed" else ("⚠️" if status == "partial" else "❌")
        print(f"{icon} {status.upper()}\n")

    return {
        "name": case["name"],
        "status": status,
        "index_time": index_time,
        "chunks_embedded": result["chunks_embedded"],
        "issues_found": len(review_result.issues),
        "detail": detail,
        "expected": expected,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Codewalk end-to-end review dry run")
    parser.add_argument(
        "case",
        nargs="?",
        default=None,
        help="Name of the multi-language case to run",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all available cases and print a summary report",
    )
    args = parser.parse_args()

    if args.all:
        with tempfile.TemporaryDirectory() as td:
            temp_root = Path(td)
            results = []
            for case in CASES:
                results.append(_run_single_case(case, temp_root, verbose=True))

        passed = sum(1 for r in results if r["status"] == "passed")
        partial = sum(1 for r in results if r["status"] == "partial")
        failed = sum(1 for r in results if r["status"] == "failed")
        total_time = sum(r["index_time"] for r in results)

        print("=" * 60)
        print("DRY-RUN SUMMARY")
        print("=" * 60)
        for r in results:
            icon = "✅" if r["status"] == "passed" else ("⚠️" if r["status"] == "partial" else "❌")
            print(f"{icon} {r['name']}: {r['status']} ({r['issues_found']} issues, {r['index_time']:.1f}s indexing)")
        print("-" * 60)
        print(f"Total: {len(results)} cases | Passed: {passed} | Partial: {partial} | Failed: {failed}")
        print(f"Total index time: {total_time:.1f}s")
        return 0 if failed == 0 else 1

    case_name = args.case or "rust_anyhow_chain_unwrap"
    case = next((c for c in CASES if c["name"] == case_name), None)
    if case is None:
        print(f"Unknown case: {case_name}")
        print(f"Available: {', '.join(c['name'] for c in CASES)}")
        return 1

    with tempfile.TemporaryDirectory() as td:
        result = _run_single_case(case, Path(td), verbose=True)

    return 0 if result["status"] != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
