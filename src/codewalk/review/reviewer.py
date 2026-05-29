from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING
from src.codewalk.config import get_llm

if TYPE_CHECKING:
    from src.codewalk.graph.graph_store import GraphStore
from src.codewalk.review.diff_parser import get_diff, get_parsed_diff
from src.codewalk.review.models import ReviewResult, Issue, Severity, Category, Verdict, Confidence, DiffFile
from src.codewalk.review.test_coverage import TestCoverage
from src.codewalk.review.guidelines_loader import get_guidelines_store, search_guidelines
from src.codewalk.review.review_prompts import REVIEW_SYSTEM_PROMPT, REVIEW_USER_PROMPT

# Threshold: if total added lines exceed this, use per-file chunked review
CHUNK_THRESHOLD = 200

# ── Module-level maps for LLM response parsing (avoid rebuilding per call) ──
_CATEGORY_MAP: dict[str, Category] = {
    "bug": Category.BUG,
    "security": Category.SECURITY,
    "style": Category.STYLE,
    "test": Category.TEST,
    "blast_radius": Category.BLAST_RADIUS,
    "design": Category.DESIGN,
    "naming": Category.NAMING,
    "complexity": Category.COMPLEXITY,
    "error_handling": Category.ERROR_HANDLING,
    "type_safety": Category.TYPE_SAFETY,
    "architecture": Category.ARCHITECTURE,
    "logging": Category.LOGGING,
    "compatibility": Category.COMPATIBILITY,
    "privacy": Category.PRIVACY,
    "hygiene": Category.HYGIENE,
}

_CONFIDENCE_MAP: dict[str, Confidence] = {
    "high": Confidence.HIGH,
    "medium": Confidence.MEDIUM,
    "low": Confidence.LOW,
}


@dataclass
class FileReviewContext:
    """Prepared context for reviewing a single file."""
    diff_file: DiffFile
    file_diff_text: str
    file_content: str = ""
    caller_context: str = ""
    security_context: str = ""


@dataclass
class ReviewContext:
    """All prepared context needed for a code review (shared by MCP + LLM flows)."""
    diff_text: str
    diff_files: list[DiffFile]
    file_contexts: list[FileReviewContext]
    pre_check_issues: list[Issue]
    blast_radius_warnings: list[str]
    guidelines_context: str
    architecture_context: str
    total_added: int
    total_removed: int


def _get_file_content(diff_file: DiffFile, repo_path: str | None) -> str:
    """Get full file content for modified files (not new files).
    
    For new files: diff already contains everything — return empty.
    For modified files: read the full file so LLM sees class structure.
    """
    if diff_file.is_new_file or not repo_path:
        return ""

    file_path = Path(repo_path) / diff_file.file_path
    if not file_path.exists():
        return ""

    try:
        content = file_path.read_text(errors="replace")
        # Cap at 5000 lines to avoid token overflow
        lines = content.splitlines()
        if len(lines) > 5000:
            lines = lines[:5000]
            content = "\n".join(lines) + "\n... (truncated at 5000 lines)"
        return content
    except (OSError, UnicodeDecodeError):
        return ""


def _get_caller_context(diff_file: DiffFile, deps: dict | None = None,
                        graph_store: GraphStore | None = None) -> str:
    """Symbol-level caller context for code review."""
    if graph_store:
        symbols = graph_store.get_symbols_in_file(diff_file.file_path)
        if symbols:
            changed_lines = set()
            for hunk in diff_file.hunks:
                for line in hunk.lines:
                    if line.change_type in ("added", "removed"):
                        if line.line_number is not None:
                            changed_lines.add(line.line_number)
            
            sections = []
            for symbol in symbols:
                sym_range = set(range(symbol["start_line"], symbol["end_line"] + 1))
                if not changed_lines or changed_lines & sym_range:
                    callers = graph_store.get_callers_of_symbol(symbol["qualified_name"])
                    if callers:
                        caller_lines = []
                        for caller in callers[:15]: # Cap at 15 per symbol
                            caller_lines.append(
                                f"  - {caller['caller']}() at {caller['file']}:{caller['line']}"
                            )
                        sections.append(
                            f"### {symbol['name']}() — called by {len(callers)} symbol(s):\n"
                            + "\n".join(caller_lines)
                        )
            
            if sections:
                return (
                    f"## Caller context for {diff_file.file_path}\n"
                    + "\n\n".join(sections)
                )
            
    if not deps or "graph" not in deps:
        return ""

    from src.codewalk.analysis.blast_radius import build_reverse_graph

    graph = deps["graph"]
    reverse = build_reverse_graph(graph)
    importers = reverse.get(diff_file.file_path, [])

    if not importers:
        return ""

    return (
        f"## Who imports this file\n"
        f"{diff_file.file_path} is imported by: {', '.join(importers[:10])}"
    )


def _get_security_context_for_file(diff_file: DiffFile, store) -> str:
    """Query vector store with security-focused questions for this specific file."""
    if not store:
        return ""

    from src.codewalk.rag.chain import format_context

    added_code = "\n".join(
        line.content for hunk in diff_file.hunks
        for line in hunk.lines if line.change_type == "added"
    )

    if not added_code:
        return ""

    # Build targeted query based on what's in the file
    keywords_to_queries = {
        ("url", "redirect", "launch", "navigate", "href", "link"):
            "URL validation domain allowlist redirect security",
        ("token", "key", "secret", "password", "credential", "auth", "jwt"):
            "authentication token management secure credential storage",
        ("cache", "store", "persist", "save", "memory"):
            "cache eviction memory management cleanup dispose",
        ("timer", "periodic", "stream", "subscription", "controller"):
            "resource disposal cancel timer stream subscription lifecycle",
        ("setstate", "mounted", "dispose", "async"):
            "Flutter async setState mounted check lifecycle",
    }

    queries = []
    lower_code = added_code.lower()
    for keywords, query in keywords_to_queries.items():
        if any(kw in lower_code for kw in keywords):
            queries.append(query)

    if not queries:
        return ""

    all_results = []
    for query in queries[:2]:
        results = store.search(query, n_results=2)
        from src.codewalk.rag.retrieval_quality import filter_by_distance
        filtered, _ = filter_by_distance(results)
        all_results.extend(filtered)

    if not all_results:
        return ""

    # Deduplicate
    seen_ids = set()
    unique_results = []
    for r in all_results:
        if r["id"] not in seen_ids:
            seen_ids.add(r["id"])
            unique_results.append(r)

    return (
        "## How similar patterns are handled elsewhere in this codebase\n"
        + format_context(unique_results[:4])
    )


def _detect_architecture_context(store, diff_files: list[DiffFile]) -> str:
    """Query vector store to detect architecture patterns the codebase already uses.

    For each language in the diff, runs targeted queries to identify:
    - Framework (FastAPI, Django, React, Flutter, Spring, etc.)
    - Architecture pattern (MVC, MVVM, BLoC, service layer, repository, etc.)
    - State management (Redux, Provider, Riverpod, Zustand, etc.)
    - Logging approach (structlog, winston, Timber, etc.)
    - Error handling conventions

    Returns a formatted context string for injection into the review prompt.
    """
    if not store:
        return ""

    from src.codewalk.rag.chain import format_context
    from src.codewalk.rag.retrieval_quality import filter_by_distance

    # Detect languages present in the diff
    languages = set(df.language for df in diff_files if df.language != "unknown")

    if not languages:
        return ""

    # Language → targeted architecture detection queries
    lang_queries: dict[str, list[tuple[str, str]]] = {
        "python": [
            ("framework", "FastAPI Django Flask route handler endpoint app"),
            ("architecture", "service repository layer use case interactor"),
            ("logging", "logger logging structlog loguru getLogger"),
            ("error_handling", "raise exception error handler try except"),
        ],
        "javascript": [
            ("framework", "React Vue Angular Next Nuxt component"),
            ("state", "Redux Zustand MobX Pinia store useSelector dispatch"),
            ("api_layer", "fetch axios api service client hook"),
            ("logging", "winston pino console logger bunyan"),
        ],
        "typescript": [
            ("framework", "React Vue Angular Next Nuxt component"),
            ("state", "Redux Zustand MobX Pinia store useSelector dispatch"),
            ("api_layer", "fetch axios api service client hook"),
            ("logging", "winston pino console logger bunyan"),
        ],
        "dart": [
            ("state_management", "BLoC Cubit Provider Riverpod GetX ChangeNotifier"),
            ("architecture", "repository data source service usecase domain"),
            ("navigation", "GoRouter Navigator auto_route routes"),
            ("di", "GetIt injectable provider service locator"),
            ("logging", "logger print debugPrint Crashlytics Sentry"),
        ],
        "go": [
            ("framework", "gin echo fiber chi http handler mux"),
            ("architecture", "service repository handler interface struct"),
            ("logging", "zap zerolog logrus slog log"),
            ("error_handling", "errors fmt.Errorf wrap unwrap"),
        ],
        "java": [
            ("framework", "Spring Boot Controller Service Repository Autowired"),
            ("architecture", "DTO ViewModel UseCase Interactor Repository"),
            ("android", "Activity Fragment ViewModel LiveData Compose"),
            ("logging", "SLF4J Logback Log4j logger"),
        ],
        "kotlin": [
            ("framework", "Spring Boot Controller Service Repository"),
            ("android", "Activity Fragment ViewModel LiveData Compose Hilt"),
            ("architecture", "UseCase Repository DataSource Domain"),
            ("logging", "Timber Logger SLF4J logcat"),
        ],
        "swift": [
            ("architecture", "ViewController ViewModel Coordinator VIPER MVVM"),
            ("ui", "SwiftUI UIKit Combine ObservableObject"),
            ("networking", "Alamofire URLSession Moya async await"),
            ("logging", "os.log CocoaLumberjack print Logger"),
        ],
        "rust": [
            ("framework", "actix axum rocket warp handler"),
            ("architecture", "service handler mod trait impl domain"),
            ("logging", "tracing log instrument span event"),
            ("error_handling", "thiserror anyhow Error From impl"),
        ],
        "csharp": [
            ("framework", "ASP.NET Controller MediatR CQRS Minimal API"),
            ("architecture", "Service Repository Unit of Work DbContext"),
            ("logging", "ILogger Serilog NLog Microsoft.Extensions.Logging"),
            ("di", "AddScoped AddTransient AddSingleton IServiceCollection"),
        ],
        "ruby": [
            ("framework", "Rails Sinatra Hanami controller action"),
            ("architecture", "service object interactor form object concern"),
            ("logging", "Rails.logger Logger Tagged Logging"),
        ],
        "php": [
            ("framework", "Laravel Symfony controller route middleware"),
            ("architecture", "Service Repository Action FormRequest Resource"),
            ("logging", "Monolog Log facade logger channel"),
        ],
        "c": [
            ("architecture", "header module static inline extern struct typedef"),
            ("build", "Makefile CMake cmake_minimum_required add_executable"),
            ("logging", "printf fprintf syslog LOG_ stderr"),
            ("error_handling", "errno perror return -1 goto cleanup NULL"),
        ],
        "cpp": [
            ("framework", "Qt Boost POCO gRPC Unreal Engine"),
            ("architecture", "namespace class template RAII PIMPL interface"),
            ("build", "CMake Makefile Bazel conan vcpkg"),
            ("logging", "spdlog glog LOG_ std::cerr fmt::print"),
            ("error_handling", "exception try catch throw std::error_code"),
        ],
        "objc": [
            ("architecture", "ViewController delegate protocol category UIKit"),
            ("ui", "UIKit AppKit Interface Builder storyboard xib"),
            ("networking", "NSURLSession AFNetworking Alamofire NSOperationQueue"),
            ("logging", "NSLog os_log CocoaLumberjack DDLog"),
        ],
    }

    detected_patterns: list[str] = []

    for lang in languages:
        queries = lang_queries.get(lang)
        if not queries:
            continue

        lang_findings: list[str] = []

        for aspect, query_text in queries:
            results = store.search(query_text, n_results=2)
            filtered, _ = filter_by_distance(results)

            if not filtered:
                continue

            # Extract key signals from the results
            file_paths = [r.get("metadata", {}).get("file_path", "") for r in filtered]
            snippets = [r.get("document", "")[:300] for r in filtered]

            # Summarize what we found
            signal = _extract_pattern_signal(aspect, query_text, file_paths, snippets)
            if signal:
                lang_findings.append(signal)

        if lang_findings:
            detected_patterns.append(
                f"### {lang.title()}\n" + "\n".join(f"- {f}" for f in lang_findings)
            )

    if not detected_patterns:
        return ""

    return (
        "## Detected Architecture Patterns\n"
        "IMPORTANT: When reviewing, suggest improvements that ALIGN with these\n"
        "existing patterns. Do NOT suggest switching to a different architecture.\n\n"
        + "\n\n".join(detected_patterns)
    )


# ── Module-level constant: rebuilt once, not per call ──
_ASPECT_REGISTRY: dict[str, tuple[str, dict[str, str], dict[str, str]]] = {
    "framework": (
        "Framework",
        {
            "fastapi": "FastAPI", "django": "Django", "flask": "Flask",
            "from react": "React", "import react": "React",
            "vue": "Vue", "angular": "Angular",
            "next/": "Next.js", "nuxt": "Nuxt",
            "spring": "Spring Boot", "gin.": "Gin", "echo.": "Echo",
            "fiber.": "Fiber", "actix": "Actix", "axum::": "Axum",
            "rocket::": "Rocket", "rails": "Rails", "laravel": "Laravel",
            "symfony": "Symfony", "asp.net": "ASP.NET",
            "qt": "Qt", "boost::": "Boost", "poco::": "POCO",
            "grpc": "gRPC", "unreal": "Unreal Engine",
        },
        {
            "/controllers/": "MVC framework",
            "/templates/": "Django/Flask",
        },
    ),
    "state": (
        "State management",
        {
            "bloc": "BLoC/Cubit", "cubit": "BLoC/Cubit",
            "riverpod": "Riverpod",
            "getx": "GetX", "changenotifier": "ChangeNotifier",
            "createslice": "Redux Toolkit", "useselector": "Redux",
            "zustand": "Zustand",
            "mobx": "MobX", "pinia": "Pinia",
            "recoil": "Recoil", "jotai": "Jotai",
        },
        {
            "/store/": "Store-based", "/stores/": "Store-based",
            "/bloc/": "BLoC/Cubit", "/blocs/": "BLoC/Cubit",
            "/providers/": "Provider", "/state/": "State layer",
            "/redux/": "Redux", "/slices/": "Redux Toolkit",
        },
    ),
    "state_management": None,  # alias — resolved at bottom
    "architecture": (
        "Architecture",
        {
            "repository_impl": "Repository pattern",
            "repositoryimpl": "Repository pattern",
            "base_repository": "Repository pattern",
            "usecase": "Use cases / Clean Architecture",
            "use_case": "Use cases / Clean Architecture",
            "interactor": "Interactor pattern",
            "data_source": "Data source abstraction",
            "datasource": "Data source abstraction",
            "viewmodel": "ViewModel (MVVM)",
            "coordinator": "Coordinator pattern",
            "viper": "VIPER",
            "raii": "RAII",
            "pimpl": "PIMPL idiom",
        },
        {
            "/repositories/": "Repository pattern",
            "/repository/": "Repository pattern",
            "/services/": "Service layer",
            "/usecases/": "Use cases / Clean Architecture",
            "/use_cases/": "Use cases / Clean Architecture",
            "/domain/": "Domain layer separation",
            "/presentation/": "Presentation layer",
            "/infrastructure/": "Infrastructure layer",
            "/features/": "Feature-based structure",
            "/modules/": "Modular architecture",
            "/entities/": "Entity layer",
            "/viewmodels/": "ViewModel (MVVM)",
            "/coordinators/": "Coordinator pattern",
            "/interactors/": "Interactor pattern",
        },
    ),
    "logging": (
        "Logging",
        {
            "structlog": "structlog", "loguru": "loguru",
            "getlogger": "stdlib logging", "import logging": "stdlib logging",
            "winston": "winston", "pino(": "pino", "bunyan": "bunyan",
            "timber.": "Timber", "slf4j": "SLF4J", "logback": "Logback",
            "zap.": "zap", "zerolog": "zerolog", "logrus.": "logrus",
            "slog.": "slog", "serilog": "Serilog", "nlog": "NLog",
            "os_log": "os.log", "cocoalumberjack": "CocoaLumberjack",
            "tracing::": "tracing (Rust)", "#[instrument": "tracing (Rust)",
            "monolog": "Monolog",
            "rails.logger": "Rails.logger",
            "crashlytics": "Crashlytics", "sentry_sdk": "Sentry",
            "spdlog::": "spdlog", "glog": "glog",
            "nslog(": "NSLog", "ddlog": "CocoaLumberjack",
            "syslog(": "syslog",
        },
        {
            "/logging/": "Logging module", "/logger/": "Logger module",
        },
    ),
    "error_handling": (
        "Error handling",
        {
            "thiserror": "thiserror", "anyhow::": "anyhow",
            "fmt.errorf": "Go error wrapping", "errors.new(": "Go stdlib errors",
            "apperror": "App-level error type", "appexception": "App-level error type",
            "result<": "Result type pattern", "result::": "Result type pattern",
            "errno": "errno-based", "goto cleanup": "goto cleanup pattern",
            "std::error_code": "std::error_code",
        },
        {
            "/errors/": "Error module", "/exceptions/": "Exception module",
            "/failure/": "Failure types",
        },
    ),
    "di": (
        "Dependency injection",
        {
            "getit": "GetIt", "@injectable": "Injectable",
            "service_locator": "Service Locator",
            "@hiltandroidapp": "Hilt", "@inject": "DI framework",
            "dagger": "Dagger", "koin": "Koin",
            "addscoped": "Microsoft DI", "addtransient": "Microsoft DI",
            "inversify": "Inversify", "tsyringe": "tsyringe",
        },
        {
            "/di/": "DI module", "/injection/": "DI module",
            "/container/": "DI container",
        },
    ),
    "navigation": (
        "Navigation",
        {
            "gorouter": "GoRouter", "go_router": "GoRouter",
            "auto_route": "auto_route", "@autoroute": "auto_route",
            "react-router": "React Router", "react-navigation": "React Navigation",
        },
        {
            "/routes/": "Routes module", "/routing/": "Routing module",
            "/navigation/": "Navigation module", "/router/": "Router module",
        },
    ),
    "api_layer": (
        "API layer",
        {
            "axios.": "axios", "axios(": "axios",
            "react-query": "React Query", "@tanstack/query": "TanStack Query",
            "useswr": "SWR", "rtk query": "RTK Query",
            "retrofit": "Retrofit", "dio.": "Dio",
        },
        {
            "/api/": "API module", "/client/": "Client module",
            "/network/": "Network layer", "/remote/": "Remote data source",
            "/http/": "HTTP layer",
        },
    ),
    "android": (
        "Android",
        {
            "@composable": "Jetpack Compose", "setcontent": "Jetpack Compose",
            "viewmodel(": "ViewModel (AAC)", "livedata": "LiveData",
            "@hiltviewmodel": "Hilt DI", "@entity": "Room DB",
        },
        {
            "/compose/": "Jetpack Compose",
            "/fragments/": "Fragments", "/activities/": "Activities",
            "/viewmodel/": "ViewModel layer",
        },
    ),
    "ui": (
        "UI",
        {
            "swiftui": "SwiftUI", "uikit": "UIKit",
            "combine": "Combine", "observableobject": "ObservableObject",
            "appkit": "AppKit", "interface builder": "Interface Builder",
            "storyboard": "Storyboard",
        },
        {
            "/screens/": "Screens",
            "/widgets/": "Widgets", "/components/": "Components",
        },
    ),
    "networking": (
        "Networking",
        {
            "alamofire": "Alamofire", "moya": "Moya",
            "urlsession": "URLSession",
            "afnetworking": "AFNetworking",
            "nsurlsession": "NSURLSession",
        },
        {
            "/network/": "Network layer", "/networking/": "Networking module",
        },
    ),
    "build": (
        "Build system",
        {
            "cmake_minimum": "CMake", "add_executable": "CMake",
            "bazel": "Bazel", "meson": "Meson",
            "conan": "Conan", "vcpkg": "vcpkg",
        },
        {
            "cmakelists": "CMake", "makefile": "Makefile",
            "build.gradle": "Gradle",
        },
    ),
}
_ASPECT_REGISTRY["state_management"] = _ASPECT_REGISTRY["state"]


def _extract_pattern_signal(
    aspect: str, query: str, file_paths: list[str], snippets: list[str]
) -> str:
    """Extract a human-readable signal from vector store results.

    Two-sided detection:
      1. Code content (imports, class names, function calls in snippets)
      2. Directory structure (path segments like /services/, /bloc/, /repositories/)
    Both sides contribute equally; results are merged and deduplicated.
    """
    combined = " ".join(snippets).lower()
    paths_str = " ".join(file_paths).lower()

    entry = _ASPECT_REGISTRY.get(aspect)
    if not entry:
        return ""

    label, code_patterns, path_patterns = entry

    # Side 1: Code content signals (imports, class names, function calls)
    code_hits = [
        name for key, name in code_patterns.items()
        if key in combined
    ]

    # Side 2: Directory structure signals (file path segments)
    path_hits = [
        name for key, name in path_patterns.items()
        if key in paths_str
    ]

    # Merge both sides, deduplicate, preserve order (code hits first — stronger signal)
    all_hits = list(dict.fromkeys(code_hits + path_hits))

    if not all_hits:
        return ""

    max_display = 3 if aspect == "architecture" else 2
    return f"{label}: {', '.join(all_hits[:max_display])}"


def _build_file_diff_text(diff_file: DiffFile) -> str:
    """Reconstruct unified diff text for a single file from parsed hunks."""
    lines = []
    lines.append(f"--- a/{diff_file.file_path}")
    lines.append(f"+++ b/{diff_file.file_path}")

    for hunk in diff_file.hunks:
        lines.append(f"@@ -{hunk.start_line},{len(hunk.lines)} @@")
        for line in hunk.lines:
            if line.change_type == "added":
                lines.append(f"+{line.content}")
            elif line.change_type == "removed":
                lines.append(f"-{line.content}")
            else:
                lines.append(f" {line.content}")

    return "\n".join(lines)


def prepare_review_context(
    staged: bool = False,
    target_branch: str | None = None,
    commit: str | None = None,
    store=None,
    deps: dict | None = None,
    repo_path: str | None = None,
    graph_store = None,
) -> ReviewContext | None:
    """Common preparation for both MCP and LLM review flows.

    Parses diff, runs pre-checks, builds per-file context.
    Returns None if diff is empty.
    """
    # Get diff
    diff_text = get_diff(staged=staged, target_branch=target_branch,
                         commit=commit, repo_path=repo_path)
    if not diff_text.strip():
        return None

    # Parse diff
    diff_files = get_parsed_diff(diff_text)
    total_added = sum(df.added_lines for df in diff_files)
    total_removed = sum(df.removed_lines for df in diff_files)

    # Pre-checks
    pre_check_issues = list(TestCoverage().analyze(diff_files))

    # Blast radius
    blast_warnings = []
    if deps:
        from src.codewalk.analysis.blast_radius import get_blast_radius
        for df in diff_files:
            radius = get_blast_radius(df.file_path, deps)
            if radius["risk_level"] in ("high", "critical"):
                blast_warnings.append(
                    f"{df.file_path} — {radius['risk_level'].upper()} risk, "
                    f"{radius['affected_files']} dependents"
                )

    # Guidelines
    guidelines_context = ""
    guidelines_store = get_guidelines_store()
    if guidelines_store:
        guidelines_context = search_guidelines(guidelines_store, diff_files, n_results=3)

    # Architecture detection
    architecture_context = _detect_architecture_context(store, diff_files)

    # Per-file context
    file_contexts = []
    for df in diff_files:
        fc = FileReviewContext(
            diff_file=df,
            file_diff_text=_build_file_diff_text(df),
            file_content=_get_file_content(df, repo_path),
            caller_context=_get_caller_context(df, deps, graph_store),
            security_context=_get_security_context_for_file(df, store),
        )
        file_contexts.append(fc)

    return ReviewContext(
        diff_text=diff_text,
        diff_files=diff_files,
        file_contexts=file_contexts,
        pre_check_issues=pre_check_issues,
        blast_radius_warnings=blast_warnings,
        guidelines_context=guidelines_context,
        architecture_context=architecture_context,
        total_added=total_added,
        total_removed=total_removed,
    )


def _review_single_file(
    file_ctx: FileReviewContext,
    guidelines_context: str,
    architecture_context: str = "",
) -> list[Issue]:
    """Review a single file — one focused LLM call.

    Uses pre-computed FileReviewContext (file content, caller context, security
    context) to avoid redundant I/O and vector store queries.
    """
    llm = get_llm(temperature=0)
    diff_file = file_ctx.diff_file

    # Build per-file context from pre-computed data
    context_parts = []

    # Architecture context (detected patterns)
    if architecture_context:
        context_parts.append(architecture_context)

    # Full file content (pre-computed)
    if file_ctx.file_content:
        context_parts.append(
            f"## Full file content ({diff_file.file_path})\n"
            f"```\n{file_ctx.file_content}\n```"
        )

    # Caller context (pre-computed)
    if file_ctx.caller_context:
        context_parts.append(file_ctx.caller_context)

    # Security context (pre-computed)
    if file_ctx.security_context:
        context_parts.append(file_ctx.security_context)

    # Guidelines
    if guidelines_context:
        context_parts.append(guidelines_context)

    context_sections = "\n\n".join(context_parts) if context_parts else ""

    system = REVIEW_SYSTEM_PROMPT.format(context_sections=context_sections)

    # Use pre-built diff text
    file_diff = file_ctx.file_diff_text

    user = REVIEW_USER_PROMPT.format(
        diff_content=file_diff,
        truncation_notice="",
        pre_checks="(handled separately)",
    )

    response = llm.invoke([
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])

    # Parse response
    issues = []
    try:
        content = response.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        parsed = json.loads(content)

        for issue in parsed.get("issues", []):
            issues.append(Issue(
                severity=Severity[issue["severity"].upper()],
                category=_CATEGORY_MAP.get(
                    issue.get("category", "bug"), Category.BUG
                ),
                file_path=issue.get("file", diff_file.file_path),
                line_number=issue.get("line"),
                title=issue.get("title", ""),
                explanation=issue.get("explanation", ""),
                confidence=_CONFIDENCE_MAP.get(
                    issue.get("confidence", "high"), Confidence.HIGH
                ),
                suggestion=issue.get("suggestion"),
                fix_description=issue.get("fix_description"),
                code_snippet=issue.get("code_snippet"),
            ))
    except (json.JSONDecodeError, KeyError, IndexError):
        pass  # skip unparseable responses for individual files

    return issues


def _review_all_at_once(
    diff_text: str,
    diff_files: list[DiffFile],
    repo_path: str | None,
    store,
    deps: dict | None,
    pre_check_issues: list[Issue],
    architecture_context: str = "",
) -> tuple[list[Issue], str, Verdict, str]:
    """Original single-pass review for small diffs (< CHUNK_THRESHOLD lines)."""
    llm = get_llm(temperature=0)

    # Build context
    context_parts = []

    # Architecture context (detected patterns — goes first so LLM sees it early)
    if architecture_context:
        context_parts.append(architecture_context)

    # Blast radius
    if deps:
        from src.codewalk.analysis.blast_radius import get_blast_radius
        high_risk = []
        for df in diff_files:
            radius = get_blast_radius(df.file_path, deps)
            if radius["risk_level"] in ("high", "critical"):
                high_risk.append(
                    f"⚠️ {df.file_path} — {radius['risk_level'].upper()} risk, "
                    f"{radius['affected_files']} dependents"
                )
        if high_risk:
            context_parts.append(
                "## Blast Radius Warnings\n" + "\n".join(high_risk)
            )

    # File content for modified files only
    for df in diff_files[:3]:
        file_content = _get_file_content(df, repo_path)
        if file_content:
            context_parts.append(
                f"## Full file: {df.file_path}\n```\n{file_content}\n```"
            )

    # Security context
    if store:
        for df in diff_files[:2]:
            sec_ctx = _get_security_context_for_file(df, store)
            if sec_ctx:
                context_parts.append(sec_ctx)
                break

    # Guidelines
    guidelines_store = get_guidelines_store()
    if guidelines_store:
        gl = search_guidelines(guidelines_store, diff_files, n_results=3)
        if gl:
            context_parts.append(gl)

    context_sections = "\n\n".join(context_parts) if context_parts else ""

    system = REVIEW_SYSTEM_PROMPT.format(context_sections=context_sections)

    pre_check_str = "\n".join(
        f"- [{issue.severity.value}] {issue.file_path}:{issue.line_number} — {issue.title}"
        for issue in pre_check_issues
    ) or "None found."

    user = REVIEW_USER_PROMPT.format(
        diff_content=diff_text,
        truncation_notice="",
        pre_checks=pre_check_str,
    )

    response = llm.invoke([
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])

    issues = []
    summary = ""
    try:
        content = response.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        parsed = json.loads(content)
        summary = parsed.get("summary", "")

        verdict_map = {
            "approve": Verdict.APPROVE,
            "approve_with_nits": Verdict.APPROVE_WITH_NITS,
            "request_changes": Verdict.REQUEST_CHANGES,
        }
        verdict = verdict_map.get(parsed.get("verdict", "approve"), Verdict.APPROVE)
        verdict_reason = parsed.get("verdict_reason", "")

        for issue in parsed.get("issues", []):
            issues.append(Issue(
                severity=Severity[issue["severity"].upper()],
                category=_CATEGORY_MAP.get(
                    issue.get("category", "bug"), Category.BUG
                ),
                file_path=issue.get("file", "unknown"),
                line_number=issue.get("line"),
                title=issue.get("title", ""),
                explanation=issue.get("explanation", ""),
                confidence=_CONFIDENCE_MAP.get(
                    issue.get("confidence", "high"), Confidence.HIGH
                ),
                suggestion=issue.get("suggestion"),
                fix_description=issue.get("fix_description"),
                code_snippet=issue.get("code_snippet"),
            ))
    except (json.JSONDecodeError, KeyError, IndexError):
        summary = response.content
        verdict = Verdict.APPROVE
        verdict_reason = ""

    return issues, summary, verdict, verdict_reason


def review_diff(
    staged: bool = False,
    target_branch: str | None = None,
    commit: str | None = None,
    use_llm: bool = True,
    store=None,
    deps: dict | None = None,
    repo_path: str | None = None,
    graph_store = None,
) -> ReviewResult:
    """LLM/API review pipeline: git diff → checks → LLM → ReviewResult.
    
    For small diffs (< 200 added lines): single LLM call with all context.
    For large diffs: per-file parallel LLM calls for focused deep review.
    """
    ctx = prepare_review_context(
        staged=staged,
        target_branch=target_branch,
        commit=commit,
        store=store,
        deps=deps,
        repo_path=repo_path,
        graph_store=graph_store,
    )

    if ctx is None:
        return ReviewResult(summary="No changes to review.")

    # ── LLM review ──
    llm_issues = []
    llm_summary = ""
    verdict = Verdict.APPROVE
    verdict_reason = ""

    if use_llm:
        if ctx.total_added > CHUNK_THRESHOLD:
            # ─── CHUNKED: Per-file parallel review ───
            errors = []
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [
                    executor.submit(
                        _review_single_file,
                        fc, ctx.guidelines_context,
                        ctx.architecture_context,
                    )
                    for fc in ctx.file_contexts
                ]
                for i, future in enumerate(futures):
                    try:
                        file_issues = future.result(timeout=120)
                        llm_issues.extend(file_issues)
                    except Exception as e:
                        errors.append(f"{ctx.diff_files[i].file_path}: {type(e).__name__}: {e}")

            if errors:
                error_detail = "\n".join(errors)
                llm_summary = (
                    f"Reviewed {len(ctx.diff_files)} files individually. "
                    f"Found {len(llm_issues)} issues. "
                    f"⚠️ {len(errors)} file(s) failed:\n{error_detail}"
                )
            else:
                llm_summary = (
                    f"Reviewed {len(ctx.diff_files)} files individually. "
                    f"Found {len(llm_issues)} issues."
                )
        else:
            # ─── SINGLE PASS: Small diff, one call ───
            llm_issues, llm_summary, verdict, verdict_reason = _review_all_at_once(
                ctx.diff_text, ctx.diff_files, repo_path, store, deps,
                ctx.pre_check_issues, ctx.architecture_context,
            )

    # ── Merge and return ──
    all_issues = ctx.pre_check_issues + llm_issues

    # Determine verdict (if chunked path, derive from issues)
    if ctx.total_added > CHUNK_THRESHOLD or not use_llm:
        has_high_confidence_critical = any(
            i.severity == Severity.CRITICAL and i.confidence == Confidence.HIGH
            for i in all_issues
        )
        if has_high_confidence_critical:
            verdict = Verdict.REQUEST_CHANGES
            verdict_reason = "Critical issues found that must be fixed before merge."
        elif all_issues:
            verdict = Verdict.APPROVE_WITH_NITS
            verdict_reason = "Non-critical issues found. Fix recommended but not blocking."
        else:
            verdict = Verdict.APPROVE
            verdict_reason = "No issues found. Code looks good."

    return ReviewResult(
        issues=all_issues,
        summary=llm_summary or f"Reviewed {len(ctx.diff_files)} files. "
                                f"Found {len(all_issues)} issues.",
        verdict=verdict,
        verdict_reason=verdict_reason,
        files_reviewed=len(ctx.diff_files),
        lines_added=ctx.total_added,
        lines_removed=ctx.total_removed,
    )


