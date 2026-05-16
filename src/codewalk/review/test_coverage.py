from pathlib import Path

from src.codewalk.review.models import Issue, Severity, Category


class TestCoverage:
    """Check if changed source files have corresponding test updates."""

    # Files to skip — config, init files, generated code
    SKIP_PATTERNS = {
        # Python
        "__init__", "__main__", "conftest", "setup", "manage",
        # JS / TS
        "index", "vite.config", "jest.config", "eslint.config",
        "next.config", "tailwind.config", "webpack.config", "tsconfig",
        # Java / Kotlin
        "Application", "package-info",
        # Go
        "doc",
        # Swift
        "App", "AppDelegate", "SceneDelegate",
        # Dart
        "generated_plugin_registrant",
        # Ruby
        "Rakefile", "application",
        # Rust
        "lib", "build",
        # C#
        "Program", "Startup", "AssemblyInfo",
        # Generic (cross-language)
        "main", "config", "settings", "constants", "types", "interfaces",
    }

    def analyze(self, diff_files: list) -> list[Issue]:
        """Flag source files changed without corresponding test updates."""
        changed_source = set()
        changed_tests = set()

        for file in diff_files:
            if self._is_test_file(file.file_path):
                changed_tests.add(file.file_path)
            else:
                changed_source.add(file.file_path)

        issues = []
        for src_file in changed_source:
            expected_tests = self._guess_test_file(src_file)
            if not expected_tests:
                continue

            # Check if ANY of the expected test files were updated
            if not any(test in changed_tests for test in expected_tests):
                primary = expected_tests[0]
                issues.append(Issue(
                    severity=Severity.WARNING,
                    category=Category.TEST,
                    file_path=src_file,
                    line_number=None,
                    title=f"No test updates for {Path(src_file).name}",
                    explanation=f"You changed {src_file} but didn't update "
                                f"any of: {', '.join(expected_tests[:3])}. "
                                f"Consider adding tests.",
                    suggestion=f"Add or update tests in {primary}",
                ))

        return issues
    
    def _is_test_file(self, file_path: str) -> bool:
        """Detect if a file is a test file based on naming conventions."""
        lower = file_path.lower()
        name = Path(file_path).stem.lower()

        # Common patterns across all languages
        return (
            "test" in name
            or "spec" in name
            or "/__tests__/" in lower
            or "/test/" in lower
            or "/tests/" in lower
            or "/spec/" in lower
            or lower.startswith("test/")
            or lower.startswith("tests/")
        )
    
    def _guess_test_file(self, source_file: str) -> list[str]:
        """Return possible test file paths for a source file. Empty = don't flag."""
        path = Path(source_file)
        stem = path.stem
        suffix = path.suffix.lower()

        # Skip init/config/generated files
        if stem.lower() in self.SKIP_PATTERNS:
            return []
        
        # ── Python (.py) ──
        if suffix == ".py":
            return [
                f"tests/test_{path.name}",
                f"test/test_{path.name}",
                f"tests/{stem}_test.py",
            ]

        # ── JavaScript (.js, .jsx) ──
        if suffix in (".js", ".jsx"):
            base = path.name
            return [
                f"{stem}.test{suffix}",
                f"{stem}.spec{suffix}",
                f"__tests__/{base}",
                f"__tests__/{stem}.test{suffix}",
            ]

        # ── TypeScript (.ts, .tsx) ──
        if suffix in (".ts", ".tsx"):
            return [
                f"{stem}.test{suffix}",
                f"{stem}.spec{suffix}",
                f"__tests__/{stem}.test{suffix}",
                f"__tests__/{stem}.spec{suffix}",
            ]

        # ── Java (.java) ──
        if suffix == ".java":
            # Maven/Gradle convention: src/main/java/... → src/test/java/...Test.java
            test_name = f"{stem}Test.java"
            candidates = [test_name]
            if "src/main/java/" in source_file:
                test_path = source_file.replace("src/main/java/", "src/test/java/")
                test_path = test_path.replace(path.name, test_name)
                candidates.insert(0, test_path)
            return candidates

        # ── Kotlin (.kt) ──
        if suffix == ".kt":
            test_name = f"{stem}Test.kt"
            candidates = [test_name]
            if "src/main/kotlin/" in source_file:
                test_path = source_file.replace("src/main/kotlin/", "src/test/kotlin/")
                test_path = test_path.replace(path.name, test_name)
                candidates.insert(0, test_path)
            return candidates

        # ── Go (.go) ──
        if suffix == ".go":
            # Go tests live in the SAME directory: foo.go → foo_test.go
            return [str(path.parent / f"{stem}_test.go")]

        # ── Swift (.swift) ──
        if suffix == ".swift":
            return [
                f"{stem}Tests.swift",
                f"{stem}Test.swift",
                f"Tests/{stem}Tests.swift",
            ]

        # ── Dart (.dart) ──
        if suffix == ".dart":
            return [
                f"test/{stem}_test.dart",
                f"test/{path.name}",
            ]

        # ── Ruby (.rb) ──
        if suffix == ".rb":
            return [
                f"spec/{stem}_spec.rb",
                f"test/test_{path.name}",
                f"test/{stem}_test.rb",
            ]

        # ── Rust (.rs) ──
        # Rust often has inline #[cfg(test)] — can't check that from filenames.
        # Only check for separate test files in tests/ dir.
        if suffix == ".rs":
            return [f"tests/{path.name}"]

        # ── C# (.cs) ──
        if suffix == ".cs":
            return [
                f"{stem}Tests.cs",
                f"{stem}Test.cs",
            ]

        # ── PHP (.php) ──
        if suffix == ".php":
            return [
                f"tests/{stem}Test.php",
                f"tests/Unit/{stem}Test.php",
            ]

        return []

