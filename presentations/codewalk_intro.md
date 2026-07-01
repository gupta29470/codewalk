---
marp: true
theme: default
paginate: true
size: 16:9
header: 'Codewalk — Internal Tech Talk'
footer: 'Codewalk | AI-Powered Codebase Intelligence'
style: |
  section {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }
  h1 {
    color: #1a202c;
  }
  strong {
    color: #2563eb;
  }
  blockquote {
    border-left: 5px solid #2563eb;
    padding-left: 1rem;
    color: #4a5568;
    font-style: italic;
  }
---

<!-- _class: lead -->
<!-- _paginate: false -->

# Codewalk

## AI-Powered Codebase Intelligence

Understand any repo in hours, not weeks

**Presenter:** Your Name  
**Date:** {{date}}

<!--
Opening hook: every team struggles with the same problem — new codebase, weeks of onboarding, risky refactors, inconsistent reviews.
-->

---

# The Problem We All Face

- **New engineer onboarding** takes weeks of "hey, can you explain this?"
- **Refactors break unexpected things** because we grep, not graph
- **PR reviews are inconsistent** and miss cross-file impact
- **LLM context windows are too small** to dump the whole repo
- **Docs go stale**, but the code is always current

> *"The hardest part of software engineering is understanding the system you're changing."*

<!--
Make it relatable: ask the room how long it took them to feel productive in the last new repo they touched.
-->

---

# What is Codewalk?

A single codebase intelligence layer that turns any repository into a queryable model:

- **Modules** — auto-grouped by structure and imports
- **Dependencies** — full file/symbol-level import graph
- **Embeddings** — semantic search over code and docs
- **Risk analysis** — blast radius, centrality, cycles
- **AI assistant** — grounded answers, not hallucinated guesses

### Three ways to use it

| Web UI | MCP Server | REST API |
|--------|-----------|----------|
| Visual exploration | Cursor / Copilot / Claude Code | Scripts & CI/CD |

<!--
Position Codewalk as infrastructure, not a toy: it sits between the repo and every AI tool we use.
-->

---

# Core Capabilities

<div class="columns">
<div>

**Understand**
- Module detection
- Dependency graph
- Reading order
- Execution flow

**Search**
- Semantic code search
- Symbol lookup
- Doc & guideline indexing
- Corrective RAG

</div>
<div>

**Analyze**
- Blast radius
- Architecture health
- Circular dependencies
- PageRank / centrality

**Review**
- Multi-stage diff review
- OWASP security checks
- Team guideline matching
- Atomic fix application

</div>
</div>

<!--
This is the feature map. Emphasize that these are not siloed tools — they share one graph + one vector index.
-->

---

# Architecture at a Glance

```text
Repo files
    ↓
ingestion/    → scan + filter (tree-sitter, 15+ languages)
    ↓
analysis/     → dependency graph + modules
    ↓
graph/        → DuckDB persistent store + igraph runtime
    ↓
embeddings/   → chunks + Jina embeddings + ChromaDB
    ↓
rag/ agent/ review/ → AI layer
```

**Storage per repo:** `.codewalk/`
- `chroma/` — vector embeddings
- `graph.duckdb` — structured graph
- `manifest.json` — version metadata

<!--
The key message: two databases because each is best at its job. Chroma for vectors, DuckDB for graph queries.
-->

---

# How Teams Use It

**New dev onboarding**
- Project overview + reading order in minutes

**Before a refactor**
- "If I change `base_model.py`, what breaks?" → blast radius

**PR review**
- Multi-stage review with architecture context and security checks

**Architecture health**
- Bottleneck files, circular dependencies, riskiest modules

**Team knowledge**
- Index docs & guidelines alongside code

<!--
Pick 1-2 examples from your own teams if possible. The more concrete, the better.
-->

---

# Integration Options

**Next.js Web UI** (`localhost:3000`)
- Knowledge graph, blast radius viewer, chat, review

**MCP Server** — `@codewalk` in your IDE
- 42 tools for Cursor, Copilot, Claude Code
- No context switching; agent calls Codewalk directly

**REST API** (`localhost:8000`)
- `/analyze`, `/chat`, `/review`, `/semantic-search`, `/blast-radius`

```bash
# Example: review current diff
curl -X POST http://localhost:8000/review \
  -d '{"target_branch": "main"}'
```

<!--
Stress MCP: most engineers already live in their IDE. Codewalk meets them there.
-->

---

# Live Demo

**Scenario:** Explore the Codewalk repo itself

1. Open the Web UI → **Analyze Codebase**
2. **Overview** — tech stack, modules, riskiest files
3. **Knowledge Graph** — zoom into `rag/`, `review/`, `agent/`
4. **Blast Radius** — click `pipeline.py`, see dependents
5. **Chat** — ask "How does corrective RAG work?"
6. **Review** — run diff review on a sample branch

Or in your IDE:
```text
@codewalk analyze this codebase
@codewalk show me the execution flow
@codewalk review my changes
```

<!--
Decide live vs screenshot based on risk. If demoing live, have a repo already indexed to avoid waiting on embeddings.
-->

---

# Roadmap & Adoption

**Today**
- Local indexing + MCP + Web UI
- Multi-provider LLM support (Ollama, OpenAI, Anthropic, etc.)

**Next**
- Team-wide guideline templates
- CI/CD review hooks
- Cloud index sharing (optional)

**How to evaluate**
- Time to first useful answer for a new repo
- Review false-positive rate vs manual review
- Refactor incidents caught before merge

> **Call to action:** Pick one repo, run `codewalk analyze`, and try the MCP tools for a week.

<!--
End with a concrete ask. Adoption is easier when it's a time-boxed experiment on one repo.
-->

---

<!-- _class: lead -->
<!-- _paginate: false -->

# Thank You

## Questions?

**Resources**
- README: `README.md`
- Context doc: `local_docs/codewalk_context.md`
- This deck: `presentations/codewalk_intro.md`

**Export this deck**
```bash
npx @marp-team/marp-cli@latest presentations/codewalk_intro.md \
  -o presentations/codewalk_intro.pptx
```

<!--
Leave the export command on the final slide so anyone can regenerate the PPTX later.
-->
