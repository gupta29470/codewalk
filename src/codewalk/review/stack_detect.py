"""Stack detection for specialist rubric loading.

Flow:
  MCP path:
    1. Check .codewalk/stack_context.json (persistent, survives across commits)
    2. If missing → deterministic fallback (languages/frameworks only, NOT written to disk)
    3. If fallback is weak → return prompt for host LLM
    4. Host fills JSON → codewalk_save_stack_context writes the file
    5. All subsequent reviews read the file directly — no re-prompt

  API path:
    1. detect_stack(llm=llm) → check file first, if missing call LLM
    2. LLM responds: {"languages": [...], "frameworks": [...], "architecture": "...", ...}
    3. Result saved to .codewalk/stack_context.json

  Shared:
    - .codewalk/stack_context.json is persistent config, NOT an ephemeral cache
    - It does NOT invalidate on new commits (architecture rarely changes)
    - To refresh: call codewalk_save_stack_context again, or use refresh_stack=True

Falls back to deterministic detection if LLM is unavailable.
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger("codewalk")

# Persistent stack context file — NOT keyed by HEAD SHA.
# Survives across commits. Only overwritten by explicit save_stack_context or API LLM call.
_STACK_CONTEXT_FILE = ".codewalk/stack_context.json"

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


def _stack_context_path(repo_path: Path) -> Path:
    """Path to the persistent stack context file."""
    path = repo_path / _STACK_CONTEXT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_cached(repo_path: Path) -> dict[str, Any] | None:
    """Load persistent stack context from .codewalk/stack_context.json.

    This file persists across commits — no SHA invalidation.
    Returns None only if the file doesn't exist or is malformed.
    """
    path = _stack_context_path(repo_path)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


def _save_cache(repo_path: Path, data: dict[str, Any]) -> None:
    """Write stack context to .codewalk/stack_context.json (persistent).

    Does NOT add a _cache_key — the file survives across commits.
    """
    # Remove any legacy _cache_key if present
    clean = {k: v for k, v in data.items() if k != "_cache_key"}
    try:
        _stack_context_path(repo_path).write_text(
            json.dumps(clean, indent=2), encoding="utf-8"
        )
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


# Content-based framework detection: some ecosystems have no single manifest
# file that reliably distinguishes their frameworks (a Podfile/Package.swift/
# *.xcodeproj can exist for SwiftUI or UIKit; a build.gradle can declare
# Android and/or Spring dependencies in too many shapes to parse reliably),
# and others benefit from reinforcing the manifest check for monorepo diffs
# whose in-scope package.json/requirements.txt doesn't list the dependency
# directly. These scan the *content* of changed files for framework-specific
# imports/patterns instead. Both frameworks may be detected for apps mixing
# two of them (e.g. UIKit + SwiftUI), same as any other rubric combination.
_SWIFTUI_PATTERNS = (
    "import SwiftUI",
    "@State ",
    "@Binding ",
    "@ObservedObject ",
    "@EnvironmentObject ",
    "@StateObject ",
    ": View {",
    ": View,",
)
_UIKIT_PATTERNS = ("import UIKit", "UIViewController", "UIView")

_KOTLIN_ANDROID_PATTERNS = (
    "import android.",
    "androidx.",
    "@Composable",
    "AppCompatActivity",
    ": Activity",
    ": Fragment",
    "ViewModel(",
)
_KOTLIN_SPRING_PATTERNS = (
    "org.springframework",
    "@RestController",
    "@SpringBootApplication",
    "@Service",
    "@Autowired",
    "@Repository",
)

_NEXTJS_PATTERNS = (
    "from 'next/",
    'from "next/',
    "getServerSideProps",
    "getStaticProps",
    '"use client"',
    "'use client'",
    '"use server"',
    "'use server'",
)
_REACT_PATTERNS = (
    "from 'react'",
    'from "react"',
    "import React",
    "useState(",
    "useEffect(",
    "React.FC",
)
_TS_JS_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx"}

_FASTAPI_PATTERNS = ("from fastapi", "import fastapi", "FastAPI(")
_DJANGO_PATTERNS = ("from django", "import django", "models.Model", "django.db", "django.urls")
_FLASK_PATTERNS = ("from flask", "import flask", "Flask(__name__)")


def _detect_swift_frameworks(repo_path: Path, changed_files: list[str]) -> list[str]:
    """Content-based detection for Swift UI frameworks (SwiftUI vs. UIKit)."""
    found: set[str] = set()
    for fp in changed_files:
        if not fp.endswith(".swift"):
            continue
        try:
            content = (repo_path / fp).read_text(encoding="utf-8")
        except Exception:
            continue
        if any(p in content for p in _SWIFTUI_PATTERNS):
            found.add("swift_swiftui")
        if any(p in content for p in _UIKIT_PATTERNS):
            found.add("swift_ios")
    return sorted(found)


def _detect_kotlin_frameworks(repo_path: Path, changed_files: list[str]) -> list[str]:
    """Content-based detection for Kotlin frameworks (Android vs. Spring)."""
    found: set[str] = set()
    for fp in changed_files:
        if not fp.endswith(".kt"):
            continue
        try:
            content = (repo_path / fp).read_text(encoding="utf-8")
        except Exception:
            continue
        if any(p in content for p in _KOTLIN_ANDROID_PATTERNS):
            found.add("kotlin_android")
        if any(p in content for p in _KOTLIN_SPRING_PATTERNS):
            found.add("kotlin_spring")
    return sorted(found)


def _detect_typescript_frameworks(repo_path: Path, changed_files: list[str]) -> list[str]:
    """Content-based detection for React vs. Next.js, reinforcing the
    package.json check above for monorepo diffs with no in-scope manifest."""
    found: set[str] = set()
    for fp in changed_files:
        if Path(fp).suffix.lower() not in _TS_JS_EXTENSIONS:
            continue
        try:
            content = (repo_path / fp).read_text(encoding="utf-8")
        except Exception:
            continue
        if any(p in content for p in _NEXTJS_PATTERNS):
            found.add("typescript_nextjs")
        elif any(p in content for p in _REACT_PATTERNS):
            found.add("typescript_react")
    return sorted(found)


def _detect_python_frameworks_by_content(repo_path: Path, changed_files: list[str]) -> list[str]:
    """Content-based detection for Python web frameworks, for the same
    monorepo/missing-manifest reasons as TypeScript above."""
    found: set[str] = set()
    for fp in changed_files:
        if not fp.endswith(".py"):
            continue
        try:
            content = (repo_path / fp).read_text(encoding="utf-8")
        except Exception:
            continue
        if any(p in content for p in _FASTAPI_PATTERNS):
            found.add("python_fastapi")
        if any(p in content for p in _DJANGO_PATTERNS):
            found.add("python_django")
        if any(p in content for p in _FLASK_PATTERNS):
            found.add("python_flask")
    return sorted(found)


def _detect_php_framework(repo_path: Path) -> str | None:
    composer_json = repo_path / "composer.json"
    if not composer_json.exists():
        return None
    try:
        composer = json.loads(composer_json.read_text(encoding="utf-8"))
        all_deps = {**composer.get("require", {}), **composer.get("require-dev", {})}
        return "php_laravel" if "laravel/framework" in all_deps else None
    except Exception:
        return None


def _detect_dotnet_framework(repo_path: Path, changed_files: list[str]) -> str | None:
    has_dotnet_project = (
        list(repo_path.glob("*.csproj"))
        or list(repo_path.glob("*.sln"))
        or (repo_path / "Program.cs").exists()
    )
    if not has_dotnet_project:
        return None
    is_aspnet = any("asp" in fp.lower() or "controller" in fp.lower() for fp in changed_files)
    return "csharp_aspnet" if is_aspnet else "dotnet"


def _detect_jvm_framework(repo_path: Path, changed_files: list[str]) -> str | None:
    """Java/Kotlin Android vs. Spring, from build.gradle(.kts) content.

    This is the only Android/Spring signal for plain Java (no .kt files at
    all), and complements _detect_kotlin_frameworks above for Kotlin-only
    content signals.
    """
    build_gradle = repo_path / "build.gradle"
    build_gradle_kts = repo_path / "build.gradle.kts"
    settings_gradle = repo_path / "settings.gradle"
    if not (build_gradle.exists() or build_gradle_kts.exists() or settings_gradle.exists()):
        return None

    gradle_content = ""
    for gf in (build_gradle, build_gradle_kts):
        if gf.exists():
            try:
                gradle_content = gf.read_text(encoding="utf-8").lower()
            except Exception:
                gradle_content = ""
            break

    is_kotlin = any(fp.endswith((".kt", ".kts")) for fp in changed_files)
    if "com.android" in gradle_content or "android {" in gradle_content:
        return "kotlin_android" if is_kotlin else "java_android"
    if "spring" in gradle_content or "org.springframework" in gradle_content:
        return "kotlin_spring" if is_kotlin else "java_spring"
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

    # ── PHP Laravel ──
    php_fw = _detect_php_framework(repo_path)
    if php_fw:
        frameworks.append(php_fw)

    # ── C# / .NET / ASP.NET ──
    dotnet_fw = _detect_dotnet_framework(repo_path, changed_files)
    if dotnet_fw:
        frameworks.append(dotnet_fw)

    # ── Java/Kotlin (Android / Spring), from build.gradle content ──
    jvm_fw = _detect_jvm_framework(repo_path, changed_files)
    if jvm_fw:
        frameworks.append(jvm_fw)

    # ── Swift (SwiftUI vs. UIKit), from .swift file content ──
    frameworks.extend(_detect_swift_frameworks(repo_path, changed_files))

    # ── Kotlin (Android vs. Spring), from .kt file content ──
    frameworks.extend(_detect_kotlin_frameworks(repo_path, changed_files))

    # ── TypeScript/JS (React vs. Next.js), reinforcing the package.json check ──
    frameworks.extend(_detect_typescript_frameworks(repo_path, changed_files))

    # ── Python web frameworks, reinforcing the manifest check above ──
    frameworks.extend(_detect_python_frameworks_by_content(repo_path, changed_files))

    frameworks = list(dict.fromkeys(frameworks))  # dedupe, preserve order

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
    """Detect project stack — LLM-first with deterministic fallback.

    Checks .codewalk/stack_context.json first (persistent across commits).
    If missing and LLM is provided, calls LLM and saves result.
    If missing and no LLM, uses deterministic fallback and saves result.

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
