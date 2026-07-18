# Plan: Unified `codewalk_apply_and_verify_fix` Tool

> **Status**: Implemented. This doc is the original plan. The tool now requires `session_id`; the fallback that loaded the latest session on the current branch was removed when `finding_store.py` was deleted.

## Problem

After a review, the user records verdicts by editing `llm_findings.json`. Then the host must make **two separate calls** — `apply_accepted` then `verify_fix` — and there's no persistence of verification results back to the findings JSON. The HITL tools (`approve_action` / `apply_fix`) are irrelevant in the review flow since the verdict IS the approval.

## Goal

One tool that does: **load accepted findings → apply each fix → run static analysis + tests → persist results → return combined report**.

No HITL token needed — the verdict is the approval.

## Backward Compatibility

- **All existing tools stay.** `apply_accepted`, `verify_fix`, `approve_action`, `apply_fix` remain functional and unchanged.
- The new tool is **additive** — the alternative HITL flow (manual fixes) keeps working.
- No existing function signatures, return shapes, or session JSON schemas change.
- Existing tests must continue passing.

---

## New Tool: `codewalk_apply_and_verify_fix(session_id="")`

### Flow

```
1. Load session by session_id
2. Load llm_findings.json
3. Filter: user_verdict == "accepted" + has current_code + recommended_code + file_path
4. For each accepted finding:
   a. apply_fix_to_file(repo_path, file_path, current_code, recommended_code)
   b. Track result: applied / failed
   c. Collect modified file paths
5. If any fixes applied:
   a. Run run_static_analysis(repo_path, modified_file_paths)
   b. Run run_tests(repo_path, modified_file_paths)
6. Update each applied finding in llm_findings.json:
   - status = "fixed" (if tests pass) or "still_present" (if tests fail)
   - verifier_notes = summary of static analysis + test result
7. Persist updated llm_findings.json + regenerate llm_findings.md
8. Return combined markdown report
```

### Return Shape

```markdown
## Apply & Verify — Session `abc123`

### Fixes Applied (3 of 5 accepted)
- ✅ #0 src/auth.py: Missing null check — applied, syntax valid
- ✅ #2 src/errors.py: Handle exception — applied, syntax valid
- ❌ #4 src/utils.py: Unused import — failed: ambiguous match (3 occurrences)

### Skipped (2 accepted without code)
- #1 src/config.py: Add timeout config — no current_code/recommended_code
- #3 src/main.py: Refactor startup — no current_code/recommended_code

### Static Analysis — 1 issue(s)
- **warning** src/auth.py:47 — unused variable `old_token` (ruff)

### Test Results — ✅ PASSED
Command: `pytest`
... (stdout tail)

### Verification Status
All 3 applied fixes passed verification.
```

---

## Complete File Change List

### A. CODE CHANGES

#### 1. `src/codewalk/mcp/server.py` — New tool + instruction updates

**A1. Add new tool** `codewalk_apply_and_verify_fix` (next to `apply_accepted`, around line 2530):

```python
@mcp.tool()
def codewalk_apply_and_verify_fix(session_id: str) -> str:
    """Apply all accepted fixes and verify with static analysis + tests.

    Combines apply_accepted + verify_fix into one step. Reads verdicts from
    llm_findings.json, applies fixes with current_code → recommended_code
    replacement, runs static analysis and tests, then persists verification
    status back to the session.

    No approval token needed — the verdict IS the approval.

    Args:
        session_id: Required session ID returned by `codewalk_run_review` or `codewalk_review_file`.

    Returns:
        Combined markdown: applied/failed/skipped fixes + static analysis
        + test results + per-finding verification status.
    """
```

**Implementation steps inside the tool:**

1. **Load session** by `session_id` via `load_session(repo_path, session_id)`.

2. **Reuse filtering logic** from `apply_accepted`:
   - Filter for `user_verdict == "accepted"` + `recommended_code` + `current_code` + `file_path`

3. **Reuse apply loop** from `apply_accepted`:
   - Path traversal check
   - `apply_fix_to_file(repo_path, file_path, old_code, new_code)`
   - Track applied/failed lists + collect modified file paths

4. **Add verification** (new code):
   ```python
   from src.codewalk.tools.static_analysis import run_static_analysis
   from src.codewalk.tools.test_runner import run_tests

   modified_files = [finding["file_path"] for _, finding in applied_findings]

   sa_issues = run_static_analysis(repo_path, modified_files) if modified_files else []
   test_result = run_tests(repo_path, modified_files) if modified_files else None
   ```

5. **Persist verification status** (new code):
   ```python
   verification_passed = (not sa_issues or all(i.severity != "error" for i in sa_issues)) \
                         and (test_result is None or test_result.ok)

   for idx, finding in applied_findings:
       findings[idx]["status"] = "fixed" if verification_passed else "still_present"
       findings[idx]["verifier_notes"] = f"SA: {len(sa_issues)} issues, Tests: {'pass' if test_result.ok else 'fail'}"

   # Write back
   llm_path.write_text(json.dumps(findings, indent=2), encoding="utf-8")

   # Regenerate markdown companion
   from src.codewalk.review.renderers.markdown import render_findings_markdown
   (session_dir / "llm_findings.md").write_text(
       render_findings_markdown(findings, title="LLM Findings", source_label="review LLM"),
       encoding="utf-8",
   )
   ```

6. **Build combined report** (new code):
   - Applied/failed/skipped section (from apply loop)
   - Static analysis section (format like `verify_fix`)
   - Test results section (format like `verify_fix`)
   - Verification status line

**A2. Update MCP instructions string** (in `server.py`):

- **Tool categories header** — add `apply_and_verify_fix` to REVIEW list
- **Step 6** — change to: `codewalk_apply_and_verify_fix(session_id)` as preferred, `apply_accepted` + `verify_fix` as alternative
- **Step 7** — keep `verify_fix` as optional standalone
- **HITL section** — add `apply_and_verify_fix` as preferred review path
- **ALTERNATIVE section** — keep HITL flow unchanged (`approve_action` → `apply_fix` → `verify_fix`)
- **Query routing** — add routing for "apply and verify"

**A3. Update module docstring** (top of `server.py`):

- Add `apply_and_verify_fix` to the REVIEW tool list
- Tool count: 43 → 44

---

### B. DOCUMENTATION UPDATES

#### 2. `README.md`
- **Tool table** (~line 587-610): Add `apply_and_verify_fix` row with description
- **Review flow section** (~line 123): Update Step 6/7 to mention combined tool
- **ASCII diagram** (~line 835-838): Add `apply_and_verify_fix` as the preferred path

#### 3. `FULL_SETUP_GUIDE.md`
- **Tool list** (~line 551): Add `apply_and_verify_fix`
- **Steps 6-7** (~line 560-561): Update to show `apply_and_verify_fix` as primary, `apply_accepted` + `verify_fix` as alternative

#### 4. `details.md`
- **Tool categorization** (~line 636-640): Add `apply_and_verify_fix` to REVIEW tools
- **Review flow sequence** (~line 535-540): Update sequence diagram

#### 5. `docs/WHY_DUCKDB.md`
- **HITL flow explanation** (~line 325): Add note that review flow uses `apply_and_verify_fix` instead of HITL tokens

#### 6. `codewalk_plan.md`
- **Tool list** (~line 8): Add `apply_and_verify_fix`

#### 7. `.github/agents/codewalk.md`
- **Review fixes section** (~line 55-56): Add `apply_and_verify_fix` as preferred tool after verdicts

#### 8. `env.example.txt`
- **HITL flow comment** (~line 161): Add note about `apply_and_verify_fix` for review path

---

### C. CONTEXT FILES (module-level docs for AI agents)

#### 9. `src/codewalk/mcp/context.md`
- **Line 32**: Add `apply_and_verify_fix` to HITL flow explanation, note it bypasses tokens

#### 10. `src/codewalk/review/context.md`
- **Lines 63-64**: Add `apply_and_verify_fix` to MCP tools list

#### 11. `src/codewalk/agent/context.md`
- **Lines 22-24**: Note that MCP review path now has `apply_and_verify_fix` (agent path unchanged)

---

### D. AGENT/PROMPT UPDATES

#### 12. `src/codewalk/agent/prompts.py`
- **Lines 20-21, 45**: Add note that MCP review flow uses `apply_and_verify_fix` (agent HITL flow unchanged since it uses LangGraph `apply_fix` tool)

---

### E. TEST UPDATES

#### 13. `tests/test_app_e2e.py` — Add MCP tool test
- Add `TestMCPApplyAndVerify` class:
  - `test_apply_and_verify_fix_applies_accepted_and_runs_verification`
    - Create a session with findings (some accepted with code, some rejected)
    - Call `codewalk_apply_and_verify_fix(session_id)`
    - Assert: accepted fixes with code applied to disk
    - Assert: static analysis ran
    - Assert: test runner ran
    - Assert: findings updated with `status` and `verifier_notes`
    - Assert: llm_findings.md regenerated
  - `test_apply_and_verify_fix_no_accepted_returns_warning`
    - All findings rejected → returns warning message
  - `test_apply_and_verify_fix_accepted_without_code_skipped`
    - Finding accepted but no `current_code`/`recommended_code` → skipped, not failed
  - `test_apply_and_verify_fix_latest_session_when_no_id`
    - No session_id → resolves to latest session on branch

#### 14. `tests/test_fix_applier.py` — No changes needed
- Existing `ApplyFixToFileTests` and `ApplyFixesBatchTests` cover the underlying `apply_fix_to_file()` function
- The new tool calls the same function — no new applier logic to test

#### 15. `tests/test_review_engine_fixes.py` — Add verification persistence tests
- Add `TestVerificationPersistence` class:
  - `test_status_set_to_fixed_when_tests_pass`
  - `test_status_set_to_still_present_when_tests_fail`
  - `test_verifier_notes_contain_sa_and_test_summary`
  - `test_markdown_companion_updated_after_verification`

---

### F. NO CHANGES NEEDED (confirmed)

These files use underlying functions (`apply_fix_to_file`, `run_static_analysis`, `run_tests`) that are NOT changing:

| File | Reason |
|------|--------|
| `src/codewalk/review/fix_applier.py` | Already returns `dict` with `ok`, `validation`, `formatter` — no changes |
| `src/codewalk/tools/static_analysis.py` | Already returns `list[StaticIssue]` — no changes |
| `src/codewalk/tools/test_runner.py` | Already returns `ExecutionResult` — no changes |
| `src/codewalk/review/session_store.py` | Already has `load_session`, `load_findings`, `_session_dir` — no changes |
| `src/codewalk/review/renderers/markdown.py` | Already renders findings with `status`/`verifier_notes` — no changes |
| `src/codewalk/agent/tools.py` | LangGraph agent tools unchanged (separate HITL path) |
| `src/codewalk/agent/graph.py` | Agent graph unchanged (separate HITL path) |
| `src/codewalk/api/main.py` | API endpoints unchanged (backward compatible) |
| `src/codewalk/core/hitl.py` | HITL core unchanged |
| `src/codewalk/voice/router.py` | Voice routing unchanged |
| `tests/test_chat_hitl_e2e.py` | Tests agent HITL path — unrelated to MCP review flow |
| `tests/test_brutal_review_fixes.py` | Tests underlying fix_applier — no changes needed |
| `tests/test_review_engine.py` | Tests review orchestration — no changes needed |
| `tests/test_review_engine_fixes.py` | Tests fix application flow — no changes needed |
| `tests/test_fix_applier.py` | Tests underlying fix application — no changes needed |
| `mcp.json.example` | No review flow references |
| `mcp.json.local.example` | No review flow references |
| `blogs/` | No review flow references |
| `presentations/` | No review flow references |
| `wiki/` | No review flow references |

---

## Design Decisions

| Question | Decision | Rationale |
|----------|----------|-----------|
| Fail-fast or continue? | **Continue on error** — apply all, then verify | One failed fix shouldn't block others |
| Per-finding or batch verify? | **Batch verify** — run SA + tests once after all fixes | Per-finding would be N × test suite = too slow |
| Persist verification? | **Yes** — set `status` + `verifier_notes` on each applied finding | Enables tracking, re-review knows what was verified |
| Full suite or file-focused? | **File-focused** — pass modified file paths to SA + tests | Faster, more relevant. Full suite available via `verify_fix()` |
| Require approval token? | **No** — verdict is the approval | Consistent with current `apply_accepted` behavior |
| Keep existing tools? | **Yes** — `apply_accepted`, `verify_fix`, `approve_action`, `apply_fix` all remain | Backward compatibility, HITL path still works |
| Break existing tests? | **No** — all existing tests pass unchanged | New tests are additive only |

---

## Scope Summary

| Category | Count | Files |
|----------|-------|-------|
| New tool code | 1 | `server.py` (~80 lines new tool) |
| MCP instructions update | 1 | `server.py` (instructions string) |
| Documentation updates | 7 | `README.md`, `FULL_SETUP_GUIDE.md`, `details.md`, `docs/WHY_DUCKDB.md`, `codewalk_plan.md`, `.github/agents/codewalk.md`, `env.example.txt` |
| Context file updates | 3 | `mcp/context.md`, `review/context.md`, `agent/context.md` |
| Prompt updates | 1 | `agent/prompts.py` |
| New tests | 2 | `test_app_e2e.py` (4 tests), `test_review_engine_fixes.py` (4 tests) |
| **Total files changed** | **15** | |
| Existing files NOT changed | 17+ | All backward compatible |

---

## Implementation Order

1. **`server.py`** — new tool + instruction updates + docstring
2. **Tests** — `test_app_e2e.py` + `test_review_engine_fixes.py`
3. **Run tests** — verify all existing + new tests pass
4. **Documentation** — README, FULL_SETUP_GUIDE, details, WHY_DUCKDB, codewalk_plan
5. **Context/prompt files** — context.md files + prompts.py
6. **Agent config** — `.github/agents/codewalk.md`
7. **Env example** — `env.example.txt`
