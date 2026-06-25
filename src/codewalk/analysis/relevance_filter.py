"""LLM-based relevance filtering for retrieved code chunks."""
import json
import logging
from src.codewalk.config import get_llm
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")

FILTER_SYSTEM_PROMPT = """You are a code analysis tool. Given a list of file paths from a software project in ANY language, decide which files should be indexed for code search.

## Priority Rule (MOST IMPORTANT)
When uncertain, return "yes". High recall is critical — it is far worse to exclude important code than to include extra files.

## Conflict Resolution
If a file matches both a yes and a no rule, return "yes" UNLESS clearly: auto-generated, lock file, binary asset, or dependency directory.

## Return "yes" for (indexed):
1. Source code with business logic in ANY language (.py, .dart, .ts, .js, .java, .kt, .swift, .go, .rs, .cs, .rb, .php, .scala, .c, .cpp, .h, .m, .lua, .r, .zig, .hs, .ex, .exs, .clj)
2. Platform-specific source: android/app/src/**, ios/Runner/**, Gradle files, AppDelegate, MainActivity
3. Test files in ANY language: test_*, *_test.*, *_spec.*, *Test.java, *_test.go, spec/*, __tests__/*, cypress/*, e2e/*
4. Schema/API definitions: *.proto, *.graphql, *.gql, *.thrift
5. Entry points: main.*, app.*, index.*, server.*, manage.py, Program.cs
6. Config with logic: settings.py, urls.py, routes.rb, router.go, build.gradle.kts
7. ORM models, controllers, services, handlers, middleware, repositories, state management
8. UI logic: widgets, components, views, pages, screens
9. Build scripts with logic: Makefile, Dockerfile, CMakeLists.txt, docker-compose*, setup.py
10. SQL files with procedures, functions, views, triggers, or business logic
11. __init__.py files: "yes" unless certain it's empty (you can't verify contents from path)

## Return "no" for (excluded):
1. Generated code: *.g.dart, *.freezed.dart, *.gen.*, *.generated.*, *.pb.go, *_pb2.py, R.java, Pods/*, DerivedData/*
2. Translation/localization: *.arb, *.xliff, *.po, *.mo, l10n/*, locales/*, *.lproj/*
3. Lock files: *.lock, package-lock.json, yarn.lock, Podfile.lock, go.sum, pnpm-lock.yaml
4. Documentation: *.md, *.rst, *.txt, docs/*, doc/*
5. CI/CD: .github/*, .circleci/*, .gitlab-ci.yml, Jenkinsfile, .travis.yml
6. IDE configs: .vscode/*, .idea/*, .settings/*, *.iml
7. Dependencies: vendor/*, node_modules/*, Pods/*, .gradle/*, build/*, dist/*, target/*
8. Assets: *.svg, *.png, *.jpg, *.gif, *.ico, *.woff, *.ttf, *.mp3, *.mp4, fonts/*, images/*
9. Minified/bundled: *.min.js, *.min.css, *.bundle.js, *.chunk.js
10. Snapshots/fixtures: __snapshots__/*

## Final reminders
- Evaluate each path independently — do not infer repo-wide patterns
- The project could be in ANY language
- Every input path MUST appear exactly once in the output JSON
- Return valid JSON only — no markdown, no explanation"""


FILTER_HUMAN_PROMPT = """Decide which of these {total_files} files should be indexed.

Your goal is HIGH RECALL — it is far worse to exclude important code than to
include extra files. When uncertain, return "yes".

File paths:
{file_list}

Return a JSON object mapping EVERY file path above to "yes" or "no".
Every input path must appear exactly once in the output.
Example:
{{
  "src/services/auth_service.py": "yes",
  "tests/test_auth_service.py": "yes",
  "internal/handlers/user.go": "yes",
  "migrations/001_create_users.sql": "yes",
  "app/controllers/orders_controller.rb": "yes",
  "package-lock.json": "no"
}}"""


BATCH_SIZE = 3000  # max files per LLM call to stay under context limits


def _format_file_list(files: list[dict]) -> str:
    """Format file paths for the LLM."""
    lines = []
    for f in files:
        lines.append(f"  {f['file_path']}")
    return "\n".join(lines)


def _extract_json(text: str) -> str:
    """Strip markdown fences and surrounding whitespace from LLM JSON output."""
    text = text.strip()
    if not text.startswith("```"):
        return text

    lines = text.split("\n")
    # Drop the opening fence line (may include a language tag)
    lines = lines[1:]
    # Drop everything from the first closing fence onward
    for i, line in enumerate(lines):
        if line.strip() == "```":
            lines = lines[:i]
            break
    return "\n".join(lines).strip()


def _filter_batch(batch: list[dict]) -> dict:
    """Send one batch of files to the LLM and return yes/no decisions."""
    prompt = ChatPromptTemplate.from_messages([
        ("system", FILTER_SYSTEM_PROMPT),
        ("human", FILTER_HUMAN_PROMPT),
    ])

    llm = get_llm()
    chain = prompt | llm | StrOutputParser()

    try:
        result = chain.invoke({
            "total_files": len(batch),
            "file_list": _format_file_list(batch),
        })
    except Exception as e:
        raise RuntimeError(
            f"{e} — Try reducing BATCH_SIZE in "
            f"src/codewalk/analysis/relevance_filter.py (currently {BATCH_SIZE})"
        ) from e

    # Parse JSON response
    text = _extract_json(result)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # If LLM returns garbage, keep all files in this batch
        return {f["file_path"]: "yes" for f in batch}


def filter_files_with_llm(files: list[dict]) -> list[dict]:
    """Use LLM to filter which files should be embedded.

    Splits files into batches of BATCH_SIZE to stay under LLM context limits.

    Args:
        files: from scan_directory() — list of file dicts

    Returns:
        Filtered list — only files worth embedding.
    """
    if not files:
        return files

    # Split into batches
    batches = [files[i:i + BATCH_SIZE] for i in range(0, len(files), BATCH_SIZE)]
    _log(f"[filter] Filtering {len(files)} files in {len(batches)} batch(es)...")

    # Collect all decisions across batches
    all_decisions: dict = {}
    for i, batch in enumerate(batches, 1):
        _log(f"[filter] Batch {i}/{len(batches)}: {len(batch)} files...")
        decisions = _filter_batch(batch)
        all_decisions.update(decisions)

    # Keep only files marked "yes"
    filtered = [
        f for f in files
        if all_decisions.get(f["file_path"], "yes").lower() == "yes"
    ]

    skipped = len(files) - len(filtered)
    if skipped > 0:
        _log(f"[filter] LLM filtered out {skipped} files — {len(filtered)} remain")

    return filtered