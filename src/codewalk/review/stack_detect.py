"""Stack detection via LLM for specialist rubric loading.

Flow:
  1. Send file tree + changed files to LLM
  2. LLM responds: {"languages": [...], "frameworks": [...], "architecture": "...", ...}
  3. Match response against rubric filenames on disk
  4. Load ALL matched rubrics into the review prompt

Cached per HEAD SHA — one LLM call per repo state.
Falls back to deterministic detection if LLM is unavailable.
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger("codewalk")

_CACHE_FILE = ".codewalk/cache/stack_context.json"

# Rubric names available on disk (language + framework)
AVAILABLE_RUBRICS = {
    # Languages
    "python", "typescript", "javascript", "go", "rust", "java", "kotlin",
    "swift", "dart", "ruby", "php", "c", "cpp", "csharp", "scala", "r",
    "objective_c",
    # Frameworks
    "python_fastapi", "python_django", "python_flask",
    "typescript_nextjs", "typescript_react",
    "dart_flutter",
    "java_android", "java_spring",
    "kotlin_android", "kotlin_spring",
    "swift_ios", "swift_swiftui",
    "ruby_rails",
    "php_laravel",
    "csharp_aspnet", "dotnet",
}

_STACK_DETECT_PROMPT = """You are a senior software architect. Analyze the repository file tree and changed files below.

Respond ONLY with a valid JSON object — no explanation, no markdown fences, no preamble.

{{
  "languages": ["python", "typescript"],
  "frameworks": ["python_fastapi", "typescript_nextjs"],
  "architecture": "clean architecture with service-repository pattern",
  "state_management": "zustand for frontend, dependency injection for backend",
  "data_layer": "sqlalchemy with alembic migrations",
  "testing": "pytest with factory fixtures, jest + RTL for frontend",
  "api_style": "REST with pydantic request/response schemas"
}}

Field definitions:
- `languages`: primary languages used in this project (lowercase: python, typescript, go, dart, java, kotlin, swift, ruby, php, csharp, cpp, c, rust, scala, r, objective_c, javascript)
- `frameworks`: MUST match EXACTLY from this list: {available_rubrics}
- `architecture`: describe the architecture pattern — MVC, clean architecture, hexagonal, feature-based, layered, microservice, monolith with modules, etc.
- `state_management`: what manages application state — redux, zustand, bloc, provider, riverpod, getx, vuex, pinia, mobx, context API, signals, or "none / server-side only"
- `data_layer`: ORM / database access pattern + migrations — sqlalchemy, prisma, typeorm, room, core data, activerecord, django ORM, etc.
- `testing`: testing framework + approach — pytest with fixtures, jest + RTL, widget tests, rspec, junit, xctest, etc.
- `api_style`: REST, GraphQL, tRPC, gRPC, websockets. Include schema approach (pydantic, zod, protobuf, etc.)

Rules:
- If a field cannot be determined, use empty string ""
- Only use framework names from the available list — do not invent names
- Detect ALL languages and frameworks present, not just the dominant one
- Look at folder structure to infer architecture (domain/, features/, controllers/, services/, etc.)
- Look at imports and config files to detect state management and data layer

## Repository file tree
{file_tree}

## Changed files in this review
{changed_files}"""


def _cache_path(repo_path: Path) -> Path:
    path = repo_path / _CACHE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _repo_state_key(repo_path: Path) -> str:
    """HEAD SHA for cache invalidation."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path, capture_output=True, text=True, check=True, timeout=10,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def _load_cached(repo_path: Path) -> dict[str, Any] | None:
    """Load cached stack context if still valid for current HEAD."""
    cache = _cache_path(repo_path)
    if not cache.exists():
        return None
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("_cache_key") == _repo_state_key(repo_path):
            return data
    except Exception:
        pass
    return None


def _save_cache(repo_path: Path, data: dict[str, Any]) -> None:
    """Persist stack context to cache."""
    data["_cache_key"] = _repo_state_key(repo_path)
    try:
        _cache_path(repo_path).write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def _parse_llm_response(raw: str) -> dict[str, Any] | None:
    """Parse LLM JSON response, handling markdown fences."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return None


def _detect_with_llm(
    repo_path: Path,
    file_tree: list[str],
    changed_files: list[str],
    llm: Any,
) -> dict[str, Any] | None:
    """Call LLM to detect the project stack."""
    from src.codewalk.review.reviewers.utils import _invoke_with_timeout_and_retry

    tree_text = "\n".join(f"- {p}" for p in file_tree[:150])
    if len(file_tree) > 150:
        tree_text += f"\n... and {len(file_tree) - 150} more files"

    changed_text = "\n".join(f"- {p}" for p in changed_files)

    prompt = _STACK_DETECT_PROMPT.format(
        available_rubrics=", ".join(sorted(AVAILABLE_RUBRICS)),
        file_tree=tree_text,
        changed_files=changed_text,
    )

    messages = [
        {"role": "system", "content": "You are a senior architect. Return valid JSON only."},
        {"role": "user", "content": prompt},
    ]

    try:
        raw = _invoke_with_timeout_and_retry(
            lambda: llm.invoke(messages),
            timeout_seconds=30.0,
            max_retries=1,
            operation_name="stack detection",
        )
        content = raw.content if hasattr(raw, "content") else str(raw)
        return _parse_llm_response(content)
    except Exception as e:
        logger.warning(f"[stack_detect] LLM call failed: {e}")
        return None


def _fallback_detect(repo_path: Path, changed_files: list[str]) -> dict[str, Any]:
    """Deterministic fallback when LLM is unavailable."""
    from collections import Counter
    from src.codewalk.review.rubric_loader import LANGUAGE_BY_EXTENSION

    lang_counts: Counter[str] = Counter()
    for fp in changed_files:
        suffix = Path(fp).suffix.lower()
        lang = LANGUAGE_BY_EXTENSION.get(suffix)
        if lang:
            lang_counts[lang] += 1

    languages = [lang for lang, _ in lang_counts.most_common(3)]
    frameworks: list[str] = []

    if (repo_path / "package.json").exists():
        try:
            pkg = json.loads((repo_path / "package.json").read_text(encoding="utf-8"))
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            if "next" in deps:
                frameworks.append("typescript_nextjs")
            elif "react" in deps:
                frameworks.append("typescript_react")
        except Exception:
            pass

    if (repo_path / "pubspec.yaml").exists():
        frameworks.append("dart_flutter")

    for req_file in ("requirements.txt", "pyproject.toml"):
        if (repo_path / req_file).exists():
            try:
                content = (repo_path / req_file).read_text(encoding="utf-8").lower()
                if "fastapi" in content:
                    frameworks.append("python_fastapi")
                elif "django" in content:
                    frameworks.append("python_django")
                elif "flask" in content:
                    frameworks.append("python_flask")
            except Exception:
                pass
            break

    if (repo_path / "Gemfile").exists():
        try:
            content = (repo_path / "Gemfile").read_text(encoding="utf-8").lower()
            if "rails" in content:
                frameworks.append("ruby_rails")
        except Exception:
            pass

    return {
        "languages": languages,
        "frameworks": frameworks,
        "architecture": "",
        "state_management": "",
        "data_layer": "",
        "testing": "",
        "api_style": "",
    }


def detect_stack(
    repo_path: Path,
    file_tree: list[str],
    changed_files: list[str],
    llm: Any | None = None,
) -> dict[str, Any]:
    """Detect project stack — LLM-first with deterministic fallback. Cached per HEAD.

    Returns dict with keys: languages, frameworks, architecture,
    state_management, data_layer, testing, api_style.

    The `frameworks` list contains names matching rubric files on disk.
    """
    cached = _load_cached(repo_path)
    if cached:
        logger.debug("[stack_detect] using cached stack context")
        return {k: v for k, v in cached.items() if not k.startswith("_")}

    if llm is not None:
        result = _detect_with_llm(repo_path, file_tree, changed_files, llm)
        if result:
            # Validate framework names against available rubrics
            result["frameworks"] = [
                f for f in result.get("frameworks", [])
                if f in AVAILABLE_RUBRICS
            ]
            result["languages"] = [
                l for l in result.get("languages", [])
                if l in AVAILABLE_RUBRICS
            ]
            _save_cache(repo_path, result)
            logger.info(f"[stack_detect] LLM detected: {result.get('languages')} / {result.get('frameworks')}")
            return result

    result = _fallback_detect(repo_path, changed_files)
    _save_cache(repo_path, result)
    logger.info(f"[stack_detect] fallback: {result.get('languages')} / {result.get('frameworks')}")
    return result


def get_rubric_names_from_stack(stack: dict[str, Any]) -> list[str]:
    """Extract rubric filenames to load from stack detection result.

    Returns list of rubric names (without .md extension) that exist on disk.
    """
    names: list[str] = []
    for lang in stack.get("languages", []):
        if lang in AVAILABLE_RUBRICS:
            names.append(lang)
    for fw in stack.get("frameworks", []):
        if fw in AVAILABLE_RUBRICS:
            names.append(fw)
    return list(dict.fromkeys(names))  # dedupe preserving order


def format_stack_context_header(stack: dict[str, Any]) -> str:
    """Format stack as a prompt header injected into review prompts.

    Tells the reviewer about the project's architecture so it doesn't
    have to re-infer from file tree in every batch.
    """
    lines = ["## Repository Architecture Context"]
    if stack.get("languages"):
        lines.append(f"- **Languages:** {', '.join(stack['languages'])}")
    if stack.get("frameworks"):
        lines.append(f"- **Frameworks:** {', '.join(stack['frameworks'])}")
    if stack.get("architecture"):
        lines.append(f"- **Architecture:** {stack['architecture']}")
    if stack.get("state_management"):
        lines.append(f"- **State management:** {stack['state_management']}")
    if stack.get("data_layer"):
        lines.append(f"- **Data layer:** {stack['data_layer']}")
    if stack.get("testing"):
        lines.append(f"- **Testing:** {stack['testing']}")
    if stack.get("api_style"):
        lines.append(f"- **API style:** {stack['api_style']}")

    if len(lines) <= 1:
        return ""
    lines.append("")
    return "\n".join(lines)
