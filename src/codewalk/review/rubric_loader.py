"""Language-aware rubric loader for one-stop review."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


# Map file extensions to base language rubrics.
LANGUAGE_BY_EXTENSION: dict[str, str] = {
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".py": "python",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".dart": "dart",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".c": "c",
    ".h": "c",
    ".scala": "scala",
    ".r": "r",
}


def _builtin_rubrics_dir() -> Path:
    return Path(__file__).parent / "rubrics"


def _team_rubrics_dir(repo_path: Path) -> Path:
    return repo_path / ".codewalk" / "rubrics"


def _load_rubric(name: str, repo_path: Path | None = None) -> str | None:
    """Load a rubric file. Team override wins, then builtin."""
    if repo_path:
        team_path = _team_rubrics_dir(repo_path) / f"{name}.md"
        if team_path.exists():
            return team_path.read_text(encoding="utf-8")

    builtin_path = _builtin_rubrics_dir() / f"{name}.md"
    if builtin_path.exists():
        return builtin_path.read_text(encoding="utf-8")

    return None


def detect_language(file_path: str) -> str | None:
    """Detect the base language for a file path."""
    suffix = Path(file_path).suffix.lower()
    return LANGUAGE_BY_EXTENSION.get(suffix)


@dataclass
class Rubrics:
    """Rubric set resolved once per review."""

    core: str = ""
    fallback: str = ""
    language: dict[str, str] = field(default_factory=dict)
    framework: str = ""

    def for_language(self, language: str | None) -> str:
        """Return the rubric for a language, or empty string if not loaded."""
        if not language:
            return ""
        return self.language.get(language, "")


def build_rubrics(
    repo_path: Path,
    file_paths: Iterable[str],
    detected_rubric_names: list[str] | None = None,
) -> Rubrics:
    """Resolve core, fallback, language, and framework rubrics for a set of files.

    Args:
        repo_path: Repository root.
        file_paths: Changed file paths (used for extension-based language detection).
        detected_rubric_names: Optional list of rubric names from stack detection
            (LLM-based). When provided, these are loaded in addition to
            extension-detected languages. Framework rubrics come from this list.
    """
    languages: set[str] = set()
    for file_path in file_paths:
        lang = detect_language(file_path)
        if lang:
            languages.add(lang)

    # Add languages from stack detection
    if detected_rubric_names:
        for name in detected_rubric_names:
            # Language rubrics are single words (python, typescript, etc.)
            if "_" not in name:
                languages.add(name)

    core = _load_rubric("core", repo_path) or ""
    fallback = _load_rubric("fallback", repo_path) or ""

    language_rubrics: dict[str, str] = {}
    for lang in sorted(languages):
        rubric = _load_rubric(lang, repo_path)
        if rubric:
            language_rubrics[lang] = rubric

    # Framework rubrics: prefer stack detection result, fallback to file-based detection
    framework_parts: list[str] = []
    if detected_rubric_names:
        for name in detected_rubric_names:
            # Framework rubrics contain underscore (python_fastapi, dart_flutter, etc.)
            if "_" in name:
                rubric = _load_rubric(name, repo_path)
                if rubric:
                    framework_parts.append(rubric)

    if not framework_parts:
        # Fallback to file-based detection
        detected_framework = _resolve_framework_rubric(repo_path, file_paths)
        if detected_framework:
            framework_parts.append(detected_framework)

    framework = "\n\n".join(framework_parts)

    return Rubrics(
        core=core,
        fallback=fallback,
        language=language_rubrics,
        framework=framework,
    )


def _resolve_framework_rubric(repo_path: Path, file_paths: Iterable[str]) -> str | None:
    """Detect frameworks from package/config files and return combined rubrics.

    Checks actual project manifest files (package.json, pubspec.yaml, build.gradle,
    etc.) for dependency declarations, then loads ALL matching framework rubrics.
    """
    detected: list[str] = []

    # ── JavaScript/TypeScript frameworks ──
    package_json = repo_path / "package.json"
    if package_json.exists():
        try:
            import json
            pkg = json.loads(package_json.read_text(encoding="utf-8"))
            all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            if "next" in all_deps:
                detected.append("typescript_nextjs")
            elif "react" in all_deps or "react-dom" in all_deps:
                detected.append("typescript_react")
        except Exception:
            pass

    # ── Python frameworks ──
    # Check imports in changed files + common config files
    paths_lower = [fp.lower() for fp in file_paths]
    if any("manage.py" in p for p in paths_lower) or (repo_path / "manage.py").exists():
        detected.append("python_django")
    elif (repo_path / "requirements.txt").exists():
        try:
            reqs = (repo_path / "requirements.txt").read_text(encoding="utf-8").lower()
            if "fastapi" in reqs:
                detected.append("python_fastapi")
            elif "flask" in reqs:
                detected.append("python_flask")
            elif "django" in reqs:
                detected.append("python_django")
        except Exception:
            pass
    # Also check pyproject.toml
    pyproject = repo_path / "pyproject.toml"
    if pyproject.exists() and not detected:
        try:
            content = pyproject.read_text(encoding="utf-8").lower()
            if "fastapi" in content:
                detected.append("python_fastapi")
            elif "flask" in content:
                detected.append("python_flask")
            elif "django" in content:
                detected.append("python_django")
        except Exception:
            pass

    # ── Dart/Flutter ──
    if (repo_path / "pubspec.yaml").exists():
        detected.append("dart_flutter")

    # ── Java/Kotlin (Android / Spring) ──
    build_gradle = repo_path / "build.gradle"
    build_gradle_kts = repo_path / "build.gradle.kts"
    settings_gradle = repo_path / "settings.gradle"
    if build_gradle.exists() or build_gradle_kts.exists() or settings_gradle.exists():
        try:
            gradle_content = ""
            for gf in (build_gradle, build_gradle_kts):
                if gf.exists():
                    gradle_content = gf.read_text(encoding="utf-8").lower()
                    break
            if "com.android" in gradle_content or "android {" in gradle_content:
                # Check language
                if any(fp.endswith((".kt", ".kts")) for fp in file_paths):
                    detected.append("kotlin_android")
                else:
                    detected.append("java_android")
            elif "spring" in gradle_content or "org.springframework" in gradle_content:
                if any(fp.endswith((".kt", ".kts")) for fp in file_paths):
                    detected.append("kotlin_spring")
                else:
                    detected.append("java_spring")
        except Exception:
            pass

    # ── Swift (iOS / SwiftUI) ──
    if (repo_path / "Package.swift").exists() or list(repo_path.glob("*.xcodeproj")) or list(repo_path.glob("*.xcworkspace")):
        # Check if SwiftUI is used
        if any("swiftui" in fp.lower() or "ContentView" in fp for fp in file_paths):
            detected.append("swift_swiftui")
        else:
            detected.append("swift_ios")
    elif (repo_path / "Podfile").exists():
        detected.append("swift_ios")

    # ── Ruby on Rails ──
    if (repo_path / "Gemfile").exists():
        try:
            gemfile = (repo_path / "Gemfile").read_text(encoding="utf-8").lower()
            if "rails" in gemfile:
                detected.append("ruby_rails")
        except Exception:
            pass

    # ── PHP Laravel ──
    if (repo_path / "composer.json").exists():
        try:
            import json
            composer = json.loads((repo_path / "composer.json").read_text(encoding="utf-8"))
            all_deps = {**composer.get("require", {}), **composer.get("require-dev", {})}
            if "laravel/framework" in all_deps:
                detected.append("php_laravel")
        except Exception:
            pass

    # ── C# / .NET / ASP.NET ──
    if list(repo_path.glob("*.csproj")) or list(repo_path.glob("*.sln")) or (repo_path / "Program.cs").exists():
        if any("asp" in fp.lower() or "controller" in fp.lower() for fp in file_paths):
            detected.append("csharp_aspnet")
        else:
            detected.append("dotnet")

    # ── Rust (no framework rubric but detect for future) ──
    # ── Go (no framework rubric but detect for future) ──

    if not detected:
        return None

    # Load ALL detected framework rubrics and combine them
    rubric_parts: list[str] = []
    for framework in detected:
        rubric = _load_rubric(framework, repo_path)
        if rubric:
            rubric_parts.append(rubric)

    return "\n\n".join(rubric_parts) if rubric_parts else None
