import json
import logging
from src.codewalk.config import get_llm
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")

FILTER_SYSTEM_PROMPT = """You are a code analysis tool. Given a list of file paths
from a software project in ANY programming language, decide which files should be
indexed for code search and understanding.

For each file path, return "yes" or "no".

Return "yes" for:
- Source code with business logic, features, or app behavior in ANY language:
  .py, .dart, .ts, .js, .jsx, .tsx, .java, .kt, .swift, .go, .rs, .cs,
  .rb, .php, .ex, .exs, .scala, .clj, .c, .cpp, .h, .hpp, .m, .mm, .lua,
  .r, .jl, .zig, .nim, .v, .cr, .fs, .fsx, .erl, .hrl, .hs
- Entry points: main.*, app.*, index.*, server.*, manage.py, Program.cs,
  Main.java, main.go, lib.rs, application.rb, artisan, bin/console
- Configuration WITH logic: settings.py, config.ts, urls.py, routes.rb,
  Startup.cs, AppModule.java, router.go, mod.rs, mix.exs, build.gradle.kts
- ORM models, DB schemas, API schema definitions
- Services, controllers, handlers, middleware, repositories, use cases
- State management: stores, reducers, blocs, providers, signals, atoms
- UI logic: widgets, components, views, pages, screens, templates with code
- Build scripts with real logic: Makefile, Dockerfile, CMakeLists.txt,
  setup.py, build.gradle, Package.swift, Cargo.toml, mix.exs

Return "no" for:
- Test files in ANY language:
  test_*, *_test.*, *_spec.*, *Test.java, *Tests.cs, *_test.go,
  *_test.dart, *_test.rs, spec/*, tests/*, __tests__/*, t/*,
  *Spec.scala, *_spec.rb, *Test.kt, *Tests.swift, test/*, cypress/*
- Generated/auto-generated code:
  *.g.dart, *.freezed.dart, *.gen.*, *.generated.*, generated/*,
  .dart_tool/*, __generated__/*, *.pb.go, *_pb2.py, *.swagger.json,
  *.designer.cs, *.g.cs, R.java, BuildConfig.java, Pods/*, *.xcodeproj/*
- Translation/localization data:
  *.arb, *.xliff, *.xlf, *.po, *.pot, *.mo, l10n/*, locales/*, i18n/*,
  *.lproj/*, *.strings, *.stringsdict
- Database migration files:
  migrations/*, alembic/versions/*, db/migrate/*, priv/repo/migrations/*,
  Migrations/*, flyway/*, liquibase/*
- Lock files and auto-generated manifests:
  *.lock, package-lock.json, yarn.lock, Podfile.lock, Gemfile.lock,
  composer.lock, Cargo.lock, pubspec.lock, go.sum, pnpm-lock.yaml
- Fixture/seed/mock data: fixtures/*, seeds/*, mocks/*, factories/*,
  testdata/*, __snapshots__/*
- Documentation: *.md, *.rst, *.txt, *.adoc, docs/*, doc/*
- Empty package markers: __init__.py with no real code
- CI/CD configs: .github/*, .circleci/*, .gitlab-ci.yml, Jenkinsfile,
  .travis.yml, azure-pipelines.yml, .buildkite/*
- IDE/editor configs: .vscode/*, .idea/*, *.xcworkspace/*,
  .settings/*, .classpath, .project, *.iml
- Dependency directories: vendor/*, node_modules/*, Pods/*,
  .gradle/*, build/*, dist/*, target/*, _build/*, deps/*
- Asset files: *.svg, *.png, *.jpg, *.gif, *.ico, *.woff, *.ttf,
  *.eot, *.mp3, *.mp4, *.pdf, fonts/*, images/*, assets/images/*
- Minified/bundled files: *.min.js, *.min.css, *.bundle.js, *.chunk.js

IMPORTANT:
- When in doubt, return "yes" — better to index too much than miss real code
- Use the FULL file path to decide, not just the extension
- The project could be in ANY language — do NOT assume Python or JavaScript
- Return valid JSON only — no markdown, no explanation, no extra text"""


FILTER_HUMAN_PROMPT = """Decide which of these {total_files} files should be indexed.

File paths:
{file_list}

Return a JSON object mapping each file path to "yes" or "no".
Example:
{{
  "src/services/auth_service.dart": "yes",
  "test/auth_service_test.dart": "no",
  "internal/handlers/user.go": "yes",
  "migrations/001_create_users.sql": "no",
  "lib/main.dart": "yes",
  "package-lock.json": "no"
}}"""


BATCH_SIZE = 3000  # max files per LLM call to stay under context limits


def _format_file_list(files: list[dict]) -> str:
    """Format file paths for the LLM."""
    lines = []
    for f in files:
        lines.append(f"  {f['file_path']}")
    return "\n".join(lines)


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
    text = result.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1])

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