"""Eval dataset loader — loads per-tool JSON files, exports to RAGAS format."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class EvalSample:
    question: str
    ground_truth: str
    ground_truth_contexts: list[str]
    category: str  # "function" | "flow" | "location" | "caller"
    source_file: str  # which JSON file this came from


_DATA_DIR = Path(__file__).parent / "data"

# All dataset files — one per tool/feature area
DATASET_FILES = [
    "search_codebase.json",
    "explain_function.json",
    "module_info.json",
    "blast_radius.json",
    "reading_order.json",
    "execution_flow.json",
    "overview.json",
    "review.json",
    "architecture_health.json",
    "docs.json",
    "ingestion.json",
    "embeddings.json",
    "pipeline.json",
    "config.json",
    "graph.json",
]


def load_dataset(files: list[str] | None = None) -> list[EvalSample]:
    """Load eval samples from JSON files.

    Args:
        files: Specific JSON filenames to load. None = load all.

    Returns:
        List of EvalSample objects.
    """
    targets = files or DATASET_FILES
    samples: list[EvalSample] = []

    for filename in targets:
        path = _DATA_DIR / filename
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        for item in raw:
            samples.append(EvalSample(
                question=item["question"],
                ground_truth=item["ground_truth"],
                ground_truth_contexts=item["ground_truth_contexts"],
                category=item["category"],
                source_file=filename,
            ))

    return samples


def load_by_category(category: str) -> list[EvalSample]:
    """Load only samples matching a specific category."""
    return [s for s in load_dataset() if s.category == category]


def load_by_tool(tool_name: str) -> list[EvalSample]:
    """Load samples from a specific tool's JSON file.

    Args:
        tool_name: e.g. "search_codebase", "review", "graph"
    """
    filename = f"{tool_name}.json"
    return load_dataset(files=[filename])


def to_ragas_dataset(samples: list[EvalSample]):
    """Convert to RAGAS-compatible HuggingFace Dataset.

    Returns a datasets.Dataset with columns:
        question, ground_truth, contexts (empty — filled during eval run)
    """
    from datasets import Dataset

    return Dataset.from_dict({
        "question": [s.question for s in samples],
        "ground_truth": [s.ground_truth for s in samples],
        # contexts left empty — evaluator fills with actual retrieved chunks
        "contexts": [s.ground_truth_contexts for s in samples],
    })


def summary() -> dict[str, dict[str, int]]:
    """Show dataset stats: per-file and per-category counts."""
    samples = load_dataset()
    by_file: dict[str, int] = {}
    by_category: dict[str, int] = {}

    for s in samples:
        by_file[s.source_file] = by_file.get(s.source_file, 0) + 1
        by_category[s.category] = by_category.get(s.category, 0) + 1

    return {
        "total": len(samples),
        "by_file": by_file,
        "by_category": by_category,
    }
