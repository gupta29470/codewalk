"""Language-aware execution tools for Codewalk.

Provides discovery, static analysis, and test execution helpers that dispatch
to the right tool based on file language and repo configuration.
"""

from src.codewalk.tools.static_analysis import run_static_analysis, StaticIssue
from src.codewalk.tools.test_runner import run_tests, ExecutionResult

__all__ = ["run_static_analysis", "StaticIssue", "run_tests", "ExecutionResult"]
