"""Generate injected-bug review fixtures from the multi-language repos in data/repos/.

Usage:
    .codewalk-env/bin/python src/codewalk/eval/generate_multilang_review_fixtures.py

Output:
    tests/fixtures/review_eval/diffs/{case}.diff
    tests/fixtures/review_eval/expected/{case}.json
    tests/fixtures/review_eval/originals/{case}.{ext}

The generator creates a temporary git repo for each case, commits the original
file, applies a controlled mutation, and captures the unified diff. Expected
issue metadata is derived from the mutated source so line numbers refer to the
new (buggy) version of the file.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]  # project root
ROOT = REPO_ROOT / "tests" / "fixtures" / "review_eval"


def _line_number(text: str, marker: str) -> int:
    """Return 1-based line number of the first line containing marker."""
    for i, line in enumerate(text.splitlines(), start=1):
        if marker in line:
            return i
    raise ValueError(f"Marker not found: {marker!r}")


# Multi-language injected-bug cases.  Each case mutates one file from data/repos
# into a realistic, language-idiomatic bug.  The review pipeline is expected to
# flag the bug from the diff alone (no project index required).
CASES: list[dict] = [
    # ── Rust ─────────────────────────────────────────────────────────────────
    {
        "name": "rust_anyhow_chain_unwrap",
        "source_file": "data/repos/dtolnay/anyhow/src/chain.rs",
        "file_path": "src/chain.rs",
        "old": "                let error = (*next)?;",
        "new": "                let error = (*next).unwrap();",
        "expected": {
            "title_contains": "unwrap",
            "category": ["error_handling", "bug"],
            "severity": "warning",
            "line_marker": "unwrap()",
        },
    },
    # ── Go ───────────────────────────────────────────────────────────────────
    {
        "name": "go_cobra_range_args_and",
        "source_file": "data/repos/spf13/cobra/args.go",
        "file_path": "args.go",
        "old": "\t\tif len(args) < min || len(args) > max {",
        "new": "\t\tif len(args) < min && len(args) > max {",
        "expected": {
            "title_contains": "range",
            "category": "bug",
            "severity": "warning",
            "line_marker": "&& len(args) > max",
        },
    },
    # ── Swift ────────────────────────────────────────────────────────────────
    {
        "name": "swift_alamofire_force_unwrap_url",
        "source_file": "data/repos/Alamofire/Alamofire/Source/Core/URLConvertible+URLRequestConvertible.swift",
        "file_path": "URLConvertible.swift",
        "old": "        guard let url = URL(string: self) else { throw AFError.invalidURL(url: self) }\n\n        return url",
        "new": "        let url = URL(string: self)!\n\n        return url",
        "expected": {
            "title_contains": "force unwrap",
            "category": ["error_handling", "security"],
            "severity": "warning",
            "line_marker": "URL(string: self)!",
        },
    },
    # ── TypeScript ───────────────────────────────────────────────────────────
    {
        "name": "ts_zod_safe_parse_ignore_issues",
        "source_file": "data/repos/colinhacks/zod/packages/zod/src/v4/core/parse.ts",
        "file_path": "parse.ts",
        "old": """  return result.issues.length
    ? {
        success: false,
        error: new (_Err ?? errors.$ZodError)(result.issues.map((iss) => util.finalizeIssue(iss, ctx, core.config()))),
      }
    : ({ success: true, data: result.value } as any);""",
        "new": "  return { success: true, data: result.value } as any;",
        "expected": {
            "title_contains": "safeParse",
            "category": "bug",
            "severity": "warning",
            "line_marker": "success: true",
        },
    },
    # ── Java ─────────────────────────────────────────────────────────────────
    {
        "name": "java_gson_json_array_null_check",
        "source_file": "data/repos/google/gson/gson/src/main/java/com/google/gson/JsonArray.java",
        "file_path": "JsonArray.java",
        "old": """  public void add(String string) {
    elements.add(string == null ? JsonNull.INSTANCE : new JsonPrimitive(string));
  }""",
        "new": """  public void add(String string) {
    elements.add(new JsonPrimitive(string));
  }""",
        "expected": {
            "title_contains": "null",
            "category": "error_handling",
            "severity": "warning",
            "line_marker": "new JsonPrimitive(string)",
        },
    },
    # ── Kotlin ───────────────────────────────────────────────────────────────
    {
        "name": "kotlin_okio_require_guard_inverted",
        "source_file": "data/repos/square/okio/okio/src/commonMain/kotlin/okio/TypedOptions.kt",
        "file_path": "TypedOptions.kt",
        "old": "    require(this.list.size == options.size)",
        "new": "    require(this.list.size != options.size)",
        "expected": {
            "title_contains": "require",
            "category": "bug",
            "severity": "warning",
            "line_marker": "size != options.size",
        },
    },
    # ── Dart ─────────────────────────────────────────────────────────────────
    {
        "name": "dart_shelf_headers_null_guard",
        "source_file": "data/repos/dart-lang/shelf/pkgs/shelf/lib/src/headers.dart",
        "file_path": "headers.dart",
        "old": """  factory Headers.from(Map<String, List<String>>? values) {
    if (values == null || values.isEmpty) {
      return _emptyHeaders;
    } else if (values is Headers) {
      return values;
    } else {
      return Headers._(values.entries);
    }
  }""",
        "new": """  factory Headers.from(Map<String, List<String>>? values) {
    if (values is Headers) {
      return values;
    } else {
      return Headers._(values!.entries);
    }
  }""",
        "expected": {
            "title_contains": "null",
            "category": "error_handling",
            "severity": "warning",
            "line_marker": "values!.entries",
        },
    },
    # ── PHP ──────────────────────────────────────────────────────────────────
    {
        "name": "php_slim_error_details_leak",
        "source_file": "data/repos/slimphp/Slim/Slim/Error/Renderers/JsonErrorRenderer.php",
        "file_path": "JsonErrorRenderer.php",
        "old": """        if ($displayErrorDetails) {
            $error['exception'] = [];
            do {
                $error['exception'][] = $this->formatExceptionFragment($exception);
            } while ($exception = $exception->getPrevious());
        }""",
        "new": """        $error['exception'] = [];
        do {
            $error['exception'][] = $this->formatExceptionFragment($exception);
        } while ($exception = $exception->getPrevious());""",
        "expected": {
            "title_contains": "error details",
            "category": ["privacy", "security"],
            "severity": "critical",
            "line_marker": "$error['exception']",
        },
    },
    # ── Ruby ─────────────────────────────────────────────────────────────────
    {
        "name": "ruby_rack_basic_auth_guard",
        "source_file": "data/repos/rack/rack/lib/rack/auth/basic.rb",
        "file_path": "basic.rb",
        "old": "        return bad_request unless auth.basic?\n\n        if valid?(auth)",
        "new": "        if valid?(auth)",
        "expected": {
            "title_contains": "basic",
            "category": "security",
            "severity": "critical",
            "line_marker": "if valid?(auth)",
        },
    },
    # ── C ────────────────────────────────────────────────────────────────────
    {
        "name": "c_sds_malloc_null_check",
        "source_file": "data/repos/antirez/sds/sds.c",
        "file_path": "sds.c",
        "old": "    sh = s_malloc(hdrlen+initlen+1);\n    if (sh == NULL) return NULL;\n    if (init==SDS_NOINIT)",
        "new": "    sh = s_malloc(hdrlen+initlen+1);\n    if (init==SDS_NOINIT)",
        "expected": {
            "title_contains": "null",
            "category": "error_handling",
            "severity": "warning",
            "line_marker": "if (init==SDS_NOINIT)",
        },
    },
    # ── C# ───────────────────────────────────────────────────────────────────
    {
        "name": "csharp_tryconvert_arg_validation",
        "source_file": "data/repos/dotnet/try-convert/src/try-convert/Program.cs",
        "file_path": "Program.cs",
        "old": "            if (!string.IsNullOrWhiteSpace(project) && !string.IsNullOrWhiteSpace(workspace))\n            {\n                Console.WriteLine(\"Cannot specify both a project and a workspace.\");\n                return -1;\n            }",
        "new": "            if (!string.IsNullOrWhiteSpace(project) || !string.IsNullOrWhiteSpace(workspace))\n            {\n                Console.WriteLine(\"Cannot specify both a project and a workspace.\");\n                return -1;\n            }",
        "expected": {
            "title_contains": "validation",
            "category": "bug",
            "severity": "warning",
            "line_marker": "|| !string.IsNullOrWhiteSpace(workspace)",
        },
    },
]


def generate_case(case: dict, temp_root: Path) -> dict:
    """Create a git repo, apply mutation, and return diff + metadata."""
    repo = temp_root / case["name"]
    repo.mkdir(parents=True, exist_ok=True)

    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "eval@codewalk.local"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Eval"], cwd=repo, check=True)

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
    diff = subprocess.run(
        ["git", "diff", "--unified=5"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    expected = case["expected"].copy()
    expected["file_path"] = case["file_path"]
    expected["line_number"] = _line_number(mutated, expected.pop("line_marker"))
    expected["title"] = expected.pop("title_contains")

    return {
        "diff": diff,
        "expected": expected,
        "original": original,
    }


def main() -> int:
    diffs_dir = ROOT / "diffs"
    expected_dir = ROOT / "expected"
    originals_dir = ROOT / "originals"
    diffs_dir.mkdir(parents=True, exist_ok=True)
    expected_dir.mkdir(parents=True, exist_ok=True)
    originals_dir.mkdir(parents=True, exist_ok=True)

    failures = []
    with tempfile.TemporaryDirectory() as td:
        temp_root = Path(td)
        for case in CASES:
            print(f"Generating {case['name']}...")
            try:
                result = generate_case(case, temp_root)
            except ValueError as e:
                failures.append(str(e))
                print(f"  FAILED: {e}")
                continue
            ext = Path(case["file_path"]).suffix or ".txt"
            (diffs_dir / f"{case['name']}.diff").write_text(result["diff"])
            (expected_dir / f"{case['name']}.json").write_text(
                json.dumps(result["expected"], indent=2)
            )
            (originals_dir / f"{case['name']}{ext}").write_text(result["original"])

    if failures:
        print(f"\nFailed to generate {len(failures)} case(s):")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"Generated {len(CASES)} multi-language fixtures in {ROOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
