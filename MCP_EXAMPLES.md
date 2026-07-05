# Codewalk MCP — Example Prompts & Usage Guide

This guide shows how to phrase questions so the IDE agent routes them to the right Codewalk MCP tool and gets the best answer. Copy the examples verbatim or swap in your own module/file/function names.

---

## Quick tips

1. **Be specific about names.** `Button` is better than "the button component". Use full file names when you know them: `packages/Button/src/Button.tsx`.
2. **Use the action verb the tool is built for:** overview, explain, search, blast radius, reading order, execution flow, review.
3. **Mention the domain for sensitive changes.** For payments/auth/crypto/PII, explicitly ask about validation, token handling, error paths, and backward compatibility.
4. **If the answer looks off, ask again with the tool name**, e.g. `Use codewalk_get_blast_radius_map to tell me what breaks if I change packages/Common/src/index.ts`.

---

## 1. Setup & indexing

### `codewalk_analyze_codebase`
Run once per repo per workspace. `mode="auto"` does a full build if no index exists, loads a complete index, or warns (`status="behind"`) if the index is partial.

```text
@codewalk Analyze this repo.
@codewalk Index the current workspace.
@codewalk Run codewalk_analyze_codebase.
```

### `codewalk_generate_config`
Create a starter `codewalk.yaml` with stack-specific exclusions. Run this before the first analyze if your repo does not have a config yet.

```text
@codewalk Generate a codewalk.yaml for this repo.
@codewalk Create a starter config with stack-specific excludes.
@codewalk Run codewalk_generate_config.
```

To overwrite an existing config:

```text
@codewalk Regenerate codewalk.yaml from scratch.
@codewalk Run codewalk_generate_config with force=true.
```

### `codewalk_incremental_reindex`
After you edit files, or to resume/sync a partial/interrupted index.

```text
@codewalk Reindex only the files that changed.
@codewalk Run an incremental reindex.
@codewalk Resume the partial index.
```

### `codewalk_refresh_analysis`
Rebuild deps/modules without re-embedding.

```text
@codewalk Refresh the dependency graph.
@codewalk Rebuild analysis cache without reindexing embeddings.
```

---

## 2. High-level understanding

### `codewalk_get_overview`
Big-picture summary, tech stack, riskiest files.

```text
@codewalk Give me an overview of this codebase.
@codewalk What is the tech stack and repo structure?
@codewalk What are the most fragile files in this repo?
@codewalk Show me the project overview.
```

### `codewalk_get_module_info`
One module’s files, symbols, imports, exports.

```text
@codewalk What's in the packages/Common module?
@codewalk Show me module info for checkout-ui.
@codewalk List the public exports of packages/Button.
@codewalk What does packages/Datalayer contain?
```

---

## 3. Search & explanation

### `codewalk_search_codebase`
Conceptual search by meaning, not just text. Returns relevant code chunks for analysis.

For every conceptual question, run 1-3 parallel calls with different phrasings and synthesize the chunks:

```text
@codewalk_search_codebase how does authentication work
@codewalk_search_codebase authentication login flow
@codewalk_search_codebase verify user credentials
```

```text
@codewalk How is authentication handled?
@codewalk Search for error handling in the checkout flow.
@codewalk Find code related to Apple Pay session validation.
@codewalk Where are GraphQL product queries defined?
@codewalk How does optimistic cart updates work?
@codewalk Find styled-components theme usage.
```

### `codewalk_explain_function`
Line-by-line explanation of a named function or class.

```text
@codewalk Explain the function useAddToCartActions.
@codewalk Explain ApplePayV2 component.
@codewalk Walk me through packages/Common/src/hooks/useAuth.ts.
@codewalk What does processPayment in apps/checkout-ui do?
```

### `codewalk_call_chain`
Shortest import/call path between two symbols/files.

```text
@codewalk Show the call chain from Header to useAuth.
@codewalk Trace how AddToCart reaches the cart API.
@codewalk What is the path from apps/home-ui to packages/Datalayer?
```

---

## 4. Dependencies, blast radius & architecture

### `codewalk_get_blast_radius_map`
What breaks if you change a file or module.

```text
@codewalk What breaks if I change packages/Button?
@codewalk Blast radius of packages/Common/src/index.ts.
@codewalk What is the impact of changing config.ts?
@codewalk Show the top 30 riskiest files.
@codewalk Who depends on the Flybuys component?
```

### `codewalk_find_circular_dependencies`
Detect import cycles.

```text
@codewalk Are there circular dependencies in this repo?
@codewalk Find import cycles involving packages/Common.
@codewalk What circular deps exist between Datalayer and Common?
@codewalk Show me cycle groups and how to break them.
```

### `codewalk_get_reading_order`
Optimal sequence to read files in a module.

```text
@codewalk Where should I start reading packages/checkout-ui?
@codewalk Give me the reading order for the Button module.
@codewalk Show files in dependency order for apps/account-ui.
```

### `codewalk_get_execution_flow`
Module-to-module or file-to-file dependency flow.

```text
@codewalk Show the dependency flow for the whole repo.
@codewalk How do files inside packages/Checkout connect?
@codewalk Show execution flow for product-discovery-ui.
```

### `codewalk_get_architecture_health`
Bottlenecks, centrality, cycles, refactoring priorities.

```text
@codewalk What is the architecture health of this repo?
@codewalk What are the bottleneck files?
@codewalk Which files have the highest PageRank?
@codewalk Suggest refactoring priorities.
```

---

## 5. Code review

### `codewalk_run_review`
Gather raw diff + blast radius + caller context for a review. The host LLM (Copilot/Claude) writes the final review from the returned context.

```text
@codewalk Get review context for the current diff.
@codewalk Review context for PR feat/KOSM-2650.
@codewalk Show me review context for these changes.
@codewalk Run review for the diff between main and this branch.
```

### `codewalk_review_file`
Review a single file against conventions.

```text
@codewalk Review packages/Button/src/Button.tsx.
@codewalk Check apps/checkout-ui/src/main.tsx for issues.
@codewalk Review this file: packages/Common/src/index.ts.
```

### `codewalk_get_stack_info`
Get the repository file tree and a structured prompt for stack detection. Use this when `codewalk_run_review` returns "Stack Context Required".

```text
@codewalk What stack is this repo using?
@codewalk Show me stack info for the current diff.
@codewalk Run codewalk_get_stack_info.
```

### `codewalk_save_stack_context`
Save the project's stack context to `.codewalk/stack_context.json` after analyzing the file tree from `codewalk_get_stack_info`. This persists across commits.

```text
@codewalk Save stack context: {"languages": ["typescript"], "frameworks": ["typescript_nextjs"], "architecture": "layered feature modules", "state_management": "zustand", "data_layer": "prisma", "testing": "jest + RTL", "api_style": "REST with zod"}.
@codewalk Run codewalk_save_stack_context with the JSON above.
```

### `codewalk_review_next_batch`
Get the next batch of files from an active review session after submitting findings for the current batch.

```text
@codewalk Get the next review batch.
@codewalk Run codewalk_review_next_batch('abc123...').
```

### `codewalk_submit_batch_findings`
Save the host LLM's findings for the current batch to disk. Call this before `codewalk_review_next_batch`.

```text
@codewalk Submit these findings for batch 1.
@codewalk Run codewalk_submit_batch_findings('abc123...', findings=[...]).
```

### `codewalk_get_review_summary`
Combine Layer 0 architectural warnings and all submitted LLM findings into a final summary after all batches are done.

```text
@codewalk Show me the review summary.
@codewalk Run codewalk_get_review_summary('abc123...').
```

### Review fixes / human-in-the-loop

```text
@codewalk Propose a fix for issue #1.
@codewalk Accept finding 2 and apply the accepted fixes.
@codewalk Approve applying the fix to packages/Button/src/Button.tsx.
@codewalk Verify the fix by running tests.
```

---

## 6. Local tool runners

### `codewalk_lookup_symbol`
Find a symbol by name across the repo.

```text
@codewalk Lookup symbol authenticate_user.
@codewalk Where is useAddToCartActions defined?
@codewalk Run codewalk_lookup_symbol processPayment.
```

### `codewalk_run_static_analysis`
Run language-appropriate static analysis on given files.

```text
@codewalk Run static analysis on src/auth/jwt.py.
@codewalk Check packages/Common/src/index.ts for lint issues.
@codewalk Run codewalk_run_static_analysis on the changed files.
```

### `codewalk_run_tests`
Auto-detect and run tests for the given files.

```text
@codewalk Run tests for src/auth/test_jwt.py.
@codewalk Test packages/Button/src/Button.test.tsx.
@codewalk Run codewalk_run_tests on the files I just modified.
```

---

## 7. Documentation search

### `codewalk_index_docs`
Index a folder of docs.

```text
@codewalk Index the docs folder.
@codewalk Index docs and guidelines.
```

### `codewalk_search_docs`
Semantic search over indexed docs.

```text
@codewalk Search docs for commit conventions.
@codewalk Find docs about deployment.
```

### `codewalk_ask_docs`
Question-answering over docs.

```text
@codewalk Ask docs: what is the PR title format?
@codewalk Ask docs: how do we handle environment secrets?
```

---

## 8. Cloud index (optional)

### `codewalk_connect_repo`
Link local workspace to a Codewalk server project.

```text
@codewalk Connect this repo to the cloud project owner/repo.
@codewalk Setup cloud index for ko-ui-lib.
```

### `codewalk_pull_index`
Download a pre-built index from the server.

```text
@codewalk Pull the latest cloud index.
@codewalk Download the index from the Codewalk server.
```

---

## 9. Visualization & voice

### `codewalk_show_knowledge_graph`
Open the interactive knowledge graph in a browser. This tool kills any existing Codewalk frontend on the chosen port and starts the pre-built production server (`npm start`).

```text
@codewalk Open the knowledge graph.
@codewalk Show me the knowledge graph for this repo.
```

### `codewalk_voice_ask`
Voice-based query (transcribes mic, then routes to a tool).

```text
@codewalk Voice: explain the checkout flow.
@codewalk Voice: what's the blast radius of Button?
```

---

## 10. Combining tools for complex questions

When a question spans multiple concerns, the agent can call tools in parallel. Phrase the request so it knows what to gather:

```text
@codewalk For packages/Button/src/Button.tsx, explain what it does, list its dependents, and review it for issues.
@codewalk I'm changing packages/Common/src/index.ts. What's the blast radius, are there circular deps, and what should I test?
@codewalk Review this Apple Pay PR for security, backward compatibility with V1, and any missing tests.
@codewalk Summarize the checkout-ui architecture, show me the reading order, and flag any circular dependencies.
```

---

## 11. Troubleshooting prompts

If the agent picks the wrong tool or gives a weak answer, be explicit:

```text
@codewalk Use codewalk_get_blast_radius_map on packages/Datalayer, not search.
@codewalk Don't search — read packages/Common/src/index.ts directly and summarize.
@codewalk Focus on security issues only in the review.
@codewalk Exclude test files from the blast radius analysis.
```

---

## One-liner cheat sheet

| I want to... | Say... |
|---|---|
| Index the repo | `@codewalk analyze this repo` |
| Understand structure | `@codewalk overview` |
| Find code | `@codewalk search for <concept>` |
| Explain code | `@codewalk explain <function/class>` |
| See impact | `@codewalk blast radius of <file/module>` |
| Find cycles | `@codewalk circular dependencies` |
| Review changes | `@codewalk review this diff` |
| Review file | `@codewalk review <file>` |
| Read docs | `@codewalk ask docs: <question>` |
| Visualize | `@codewalk show knowledge graph` |
