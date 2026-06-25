#!/usr/bin/env python3
"""Small benchmark for Codewalk review prompts across TS/Python/Go.

Run with the configured LLM (e.g., DeepSeek) and print which expected issues
are caught and which are missed.
"""

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BenchmarkCase:
    name: str
    language: str
    files: dict[str, str]  # path -> initial content
    changes: dict[str, str]  # path -> changed content
    expected: list[dict[str, Any]] = field(default_factory=list)


BENCHMARKS: list[BenchmarkCase] = [
    BenchmarkCase(
        name="ts_missing_await_and_effect_deps",
        language="typescript",
        files={
            "src/cart.ts": """
export async function addToCart(item: string) {
  await fetch('/cart', { method: 'POST', body: item });
}
""".strip(),
            "src/Cart.tsx": """
import { useState } from 'react';

export const Cart = () => {
  const [items, setItems] = useState<string[]>([]);
  return <div>{items.length}</div>;
};
""".strip(),
        },
        changes={
            "src/cart.ts": """
export async function addToCart(item: string) {
  fetch('/cart', { method: 'POST', body: item });
}
""".strip(),
            "src/Cart.tsx": """
import { useState, useEffect } from 'react';

export const Cart = () => {
  const [items, setItems] = useState<string[]>([]);
  useEffect(() => {
    console.log(items);
  }, []);
  return <div>{items.length}</div>;
};
""".strip(),
        },
        expected=[
            {"file_path": "src/cart.ts", "hint": "await"},
            {"file_path": "src/Cart.tsx", "hint": "dependency"},
        ],
    ),
    BenchmarkCase(
        name="ts_cross_package_deep_import",
        language="typescript",
        files={
            "packages/shop/src/Shop.ts": """
export const Shop = () => {};
""".strip(),
            "packages/cart/src/api.ts": """
export const addToCart = () => {};
""".strip(),
        },
        changes={
            "packages/shop/src/Shop.ts": """
import { addToCart } from 'packages/cart/src/api';
export const Shop = () => addToCart();
""".strip(),
        },
        expected=[
            {"file_path": "packages/shop/src/Shop.ts", "hint": "cross-package"},
        ],
    ),
    BenchmarkCase(
        name="py_mutable_default_and_sql_injection",
        language="python",
        files={
            "src/cart.py": """
def add_item(item, items=None):
    if items is None:
        items = []
    items.append(item)
    return items
""".strip(),
            "src/users.py": """
def get_user(user_id):
    return f"SELECT * FROM users WHERE id = {user_id}"
""".strip(),
        },
        changes={
            "src/cart.py": """
def add_item(item, items=[]):
    items.append(item)
    return items
""".strip(),
            "src/users.py": """
def get_user(user_id):
    query = f"SELECT * FROM users WHERE id = {user_id}"
    return query
""".strip(),
        },
        expected=[
            {"file_path": "src/cart.py", "hint": "mutable"},
            {"file_path": "src/users.py", "hint": "SQL"},
        ],
    ),
    BenchmarkCase(
        name="go_unchecked_error_and_nil_map",
        language="go",
        files={
            "main.go": """
package main

import (
    "fmt"
    "os"
)

func load() {
    f, err := os.Open("data.txt")
    if err != nil {
        return
    }
    defer f.Close()
}
""".strip(),
            "cache.go": """
package main

var cache = map[string]int{}
""".strip(),
        },
        changes={
            "main.go": """
package main

import (
    "fmt"
    "os"
)

func load() {
    f, _ := os.Open("data.txt")
    defer f.Close()
}
""".strip(),
            "cache.go": """
package main

var cache map[string]int

func store(k string, v int) {
    cache[k] = v
}
""".strip(),
        },
        expected=[
            {"file_path": "main.go", "hint": "error"},
            {"file_path": "cache.go", "hint": "nil"},
        ],
    ),
    BenchmarkCase(
        name="ts_clean_change_no_issues",
        language="typescript",
        files={
            "src/utils.ts": """
export const add = (a: number, b: number) => a + b;
""".strip(),
        },
        changes={
            "src/utils.ts": """
export const add = (a: number, b: number) => a + b;
export const sub = (a: number, b: number) => a - b;
""".strip(),
        },
        expected=[],
    ),
]


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


def _build_repo(case: BenchmarkCase) -> tuple[Path, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    repo = Path(tmp.name)
    _run(["git", "init"], repo)
    _run(["git", "config", "user.email", "test@test.com"], repo)
    _run(["git", "config", "user.name", "Test"], repo)
    for path, content in case.files.items():
        full = repo / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-m", "init"], repo)
    for path, content in case.changes.items():
        full = repo / path
        full.write_text(content, encoding="utf-8")
    return repo, tmp


def _matches_expected(finding: dict[str, Any], expected: dict[str, Any]) -> bool:
    if finding.get("file_path") != expected["file_path"]:
        return False
    hint = expected["hint"].lower()
    for field in ("title", "explanation", "category"):
        value = str(finding.get(field, "")).lower()
        if hint in value:
            return True
    return False


def run_benchmark() -> None:
    from src.codewalk.config import get_llm
    from src.codewalk.review.engine import run_review

    llm = get_llm(temperature=0)
    results: list[dict[str, Any]] = []

    tmpdirs: list[tempfile.TemporaryDirectory] = []
    for case in BENCHMARKS:
        print(f"\n=== {case.name} ({case.language}) ===")
        repo, tmpdir = _build_repo(case)
        tmpdirs.append(tmpdir)
        try:
            report = run_review(repo, target_branch=None, staged=False, llm=llm)
            print(f"  files_reviewed={report.files_reviewed}, verdict={report.verdict.value}, summary={report.executive_summary}")
            findings = [
                {
                    "file_path": f.file_path,
                    "line_number": f.line_number,
                    "severity": f.severity.value,
                    "category": f.category.value,
                    "title": f.title,
                    "explanation": f.explanation,
                }
                for f in report.findings
            ]
        except Exception as e:
            import traceback
            print(f"ERROR: {e}")
            traceback.print_exc()
            findings = []

        found_count = 0
        missed: list[dict[str, Any]] = []
        for exp in case.expected:
            matched = any(_matches_expected(f, exp) for f in findings)
            if matched:
                found_count += 1
                print(f"  ✓ found: {exp['file_path']} ({exp['hint']})")
            else:
                missed.append(exp)
                print(f"  ✗ missed: {exp['file_path']} ({exp['hint']})")

        false_positives = [
            f
            for f in findings
            if not any(_matches_expected(f, exp) for exp in case.expected)
        ]

        results.append(
            {
                "name": case.name,
                "language": case.language,
                "expected": len(case.expected),
                "found": found_count,
                "missed": missed,
                "false_positives": len(false_positives),
                "findings": findings,
            }
        )

    print("\n=== Summary ===")
    total_expected = sum(r["expected"] for r in results)
    total_found = sum(r["found"] for r in results)
    total_fp = sum(r["false_positives"] for r in results)
    print(f"Expected issues: {total_expected}")
    print(f"Found issues: {total_found}")
    print(f"False positives (unmatched findings): {total_fp}")
    print(f"Recall: {total_found / total_expected:.2%}" if total_expected else "N/A")

    out_path = Path(".codewalk/review_benchmark_results.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nDetailed results written to {out_path}")


if __name__ == "__main__":
    run_benchmark()
