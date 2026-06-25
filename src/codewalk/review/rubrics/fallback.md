# Fallback framework guidance

If the changed files use a framework or architecture not covered by a specific rubric, identify it from the code and apply the best matching principles below.

## How to identify the stack
- Look at imports, package declarations, file extensions, and top-level types.
- Infer the dominant framework and UI toolkit from the file tree and dependencies.
- If the framework is ambiguous, treat the code as the language's base idioms plus common patterns.

## Review principles
1. **State ownership** — ensure state is owned by the appropriate layer and not scattered across UI or unrelated components.
2. **Lifecycle correctness** — verify initialization, updates, and cleanup are handled correctly for the framework's lifecycle model.
3. **Naming conventions** — ensure file names, types, functions, and variables follow the ecosystem's conventions.
4. **Layer placement** — ensure files live in the correct architectural layer and dependencies point inward, not across boundaries.
5. **Error and empty states** — ensure loading, empty, error, and failure states are handled explicitly in user-facing flows.
6. **Performance** — avoid unnecessary work on the main or UI thread; flag redundant recomputation or re-rendering.
7. **Edge cases** — verify null or missing inputs, incomplete data, race conditions, and cancellation are handled safely.
8. **DRY and clarity** — flag duplicated literals, values, or logic that should be centralized; ensure complex logic is documented.

## Severity
- **critical**: crash, security vulnerability, data loss, race condition, breaking API change
- **warning**: logic error, missing edge case, unsafe pattern, missing coverage, type/architecture issue
- **suggestion**: readability, naming, minor consistency
