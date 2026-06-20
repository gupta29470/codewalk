# `src/codewalk/tools/` — External Tool Runners

This package discovers and runs external command-line tools (linters, type-checkers, test runners) per language, normalizing their output into a common format for the API and MCP.

## Modules

| File | Role |
|------|------|
| `tool_runner.py` | `which()`, `run_command()`, `get_tool_commands()` — command discovery, availability caching, and normalized subprocess execution. Reads tool overrides from `codewalk.yaml`. |
| `static_analysis.py` | `run_static_analysis()` — language-aware linter/type-checker runner with parsers for Ruff, mypy, bandit, ESLint, `go vet`, `cargo check`, etc. |
| `test_runner.py` | `run_tests()` — auto-detects the right test command from repo files and file extensions, then runs it and returns a normalized result. |

## Data flow

```
API / MCP request (e.g. POST /tools/static-analysis)
    ↓
_detect_language(file_paths)
    ↓
_default_analyzer_commands(...) / _detect_test_command(...)
    ↓
run_command(...) via tool_runner.py
    ↓
normalized list[StaticIssue] / ExecutionResult
```

## Connections

- `static_analysis.py` and `test_runner.py` both use `tool_runner.py` for command discovery and execution.
- `tool_runner.py` reads optional tool overrides from `codewalk.yaml` (`tools.<type>[.<language>]`).
- Used by API `/tools/static-analysis` and `/tools/run-tests` and by MCP `codewalk_run_static_analysis` / `codewalk_run_tests`.

## Notes

- Tool availability is cached in `AVAILABLE_TOOLS_CACHE`; call `clear_tool_cache()` in tests if needed.
- Analyzers/test runners are optional dependencies; missing tools are skipped gracefully.
