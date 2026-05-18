"""
=============================================================================
 test_coverage.py - Test Coverage Checker
=============================================================================

WHAT THIS FILE DOES:
    Checks if source files changed in a diff have corresponding test updates.
    If you modify auth.py but don't touch test_auth.py, this flags it.

    Supports 14+ languages: Python, JS, TS, Java, Kotlin, Go, Swift,
    Dart, Ruby, Rust, C#, PHP.

HOW IT WORKS:
    1. Classify each changed file as "source" or "test" (by naming convention)
    2. For each source file, guess where its test file should live
    3. If no matching test file was updated in the diff, emit a WARNING

WHERE IT'S CALLED:
    - reviewer.py -> prepare_review_context() runs this as a pre-check

=============================================================================
"""

from pathlib import Path
from src.codewalk.review.models import Issue, Severity, Category


class TestCoverage:
    """Check if changed source files have corresponding test updates."""

    # Files to skip - config, init files, generated code
    SKIP_PATTERNS = {
        "__init__", "__main__", "conftest", "setup", "manage",
        "index", "vite.config", "jest.config", "eslint.config",
        "next.config", "tailwind.config", "webpack.config", "tsconfig",
        "Application", "package-info",
        "doc",
        "App", "AppDelegate", "SceneDelegate",
        "generated_plugin_registrant",
        "Rakefile", "application",
        "lib", "build",
        "Program", "Startup", "AssemblyInfo",
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
        """Detect test file by naming convention (cross-language)."""
        lower = file_path.lower()
        name = Path(file_path).stem.lower()
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
        """Return possible test file paths for a source file.

        Returns empty list for files that shouldn't have tests (config, etc).
        Each language has its own convention for test file location/naming.
        """
        path = Path(source_file)
        stem = path.stem
        suffix = path.suffix.lower()

        if stem.lower() in self.SKIP_PATTERNS:
            return []

        # Python: tests/test_foo.py
        if suffix == ".py":
            return [f"tests/test_{path.name}", f"test/test_{path.name}", f"tests/{stem}_test.py"]

        # JavaScript: foo.test.js, __tests__/foo.js
        if suffix in (".js", ".jsx"):
            base = path.name
            return [f"{stem}.test{suffix}", f"{stem}.spec{suffix}", f"__tests__/{base}", f"__tests__/{stem}.test{suffix}"]

        # TypeScript: foo.test.ts, __tests__/foo.test.ts
        if suffix in (".ts", ".tsx"):
            return [f"{stem}.test{suffix}", f"{stem}.spec{suffix}", f"__tests__/{stem}.test{suffix}", f"__tests__/{stem}.spec{suffix}"]

        # Java: src/test/java/.../FooTest.java
        if suffix == ".java":
            test_name = f"{stem}Test.java"
            candidates = [test_name]
            if "src/main/java/" in source_file:
                test_path = source_file.replace("src/main/java/", "src/test/java/").replace(path.name, test_name)
                candidates.insert(0, test_path)
            return candidates

        # Kotlin: src/test/kotlin/.../FooTest.kt
        if suffix == ".kt":
            test_name = f"{stem}Test.kt"
            candidates = [test_name]
            if "src/main/kotlin/" in source_file:
                test_path = source_file.replace("src/main/kotlin/", "src/test/kotlin/").replace(path.name, test_name)
                candidates.insert(0, test_path)
            return candidates

        # Go: foo_test.go (same directory)
        if suffix == ".go":
            return [str(path.parent / f"{stem}_test.go")]

        # Swift: Tests/FooTests.swift
        if suffix == ".swift":
            return [f"{stem}Tests.swift", f"{stem}Test.swift", f"Tests/{stem}Tests.swift"]

        # Dart: test/foo_test.dart
        if suffix == ".dart":
            return [f"test/{stem}_test.dart", f"test/{path.name}"]

        # Ruby: spec/foo_spec.rb
        if suffix == ".rb":
            return [f"spec/{stem}_spec.rb", f"test/test_{path.name}", f"test/{stem}_test.rb"]

        # Rust: tests/foo.rs
        if suffix == ".rs":
            return [f"tests/{path.name}"]

        # C#: FooTests.cs
        if suffix == ".cs":
            return [f"{stem}Tests.cs", f"{stem}Test.cs"]

        # PHP: tests/FooTest.php
        if suffix == ".php":
            return [f"tests/{stem}Test.php", f"tests/Unit/{stem}Test.php"]

        return []