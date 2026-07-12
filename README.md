<p align="center">
  <h1 align="center">CODEWALK</h1>
   <p align="center" style="font-size:1.5em; font-weight:700;">
     <a href="https://www.codewalk.xyz/">Landing page & docs →</a>
   </p>
  <p align="center">
    <strong>AI-powered codebase intelligence tool</strong><br>
    Point it at any repo → understand the entire codebase in hours, not weeks
  </p>
</p>

<p align="center">
  <a href="#-features">Features</a> •
  <a href="#-demo">Demo</a> •
  <a href="#-frontend--knowledge-graph-ui">Frontend</a> •
  <a href="#%EF%B8%8F-setup">Local Setup</a> •
  <a href="#-cloud-deployment">Cloud</a> •
  <a href="#-mcp-integration">MCP</a> •
  <a href="#-api-reference">API</a> •
  <a href="#-architecture">Architecture</a> •
  <a href="#-contributing">Contributing</a>
</p>

---

## What is Codewalk?

Codewalk analyzes any codebase and gives you:

- **Module detection** — groups files into logical modules automatically
- **Dependency graph** — extracts every import/require → builds the full dependency map
- **Blast radius** — "if I change this file, what breaks?"
- **Reading order** — optimal file reading sequence (dependencies first)
- **Execution flow** — entry points, module-to-module and file-to-file dependency flow
- **AI chat** — ask anything about the code, powered by RAG + tool-calling agent
- **Code review** — review git diffs for bugs, security issues, and style (context-enriched, OWASP-focused)
- **Incremental reindex** — re-embed only changed files using content hash comparison
- **Graph intelligence** — DuckDB + igraph: symbol-level call graph, cycle detection, centrality analysis, import chain tracing
- **Corrective RAG** — distance-based chunk filtering + LLM answer grading + query rewriting for higher quality answers
- **Voice interface** — talk to your codebase hands-free: mic → transcribe → Copilot routes → speak answer

Three ways to use it locally, plus optional cloud indexing:

| Interface | Best for |
|-----------|----------|
| **Web UI** (Next.js) | Visual exploration — Knowledge Graph UI, diagrams, module browser, blast radius viewer |
| **MCP Server** | VS Code Copilot, Claude Code, Cursor — AI agents use tools directly |
| **REST API** | Scripts, CI/CD, custom integrations |

**Cloud (optional):** Push to GitHub → [Codewalk Cloud](https://api.codewalk.xyz) indexes on the server → MCP downloads the index and queries **locally**. See [Cloud Deployment](#-cloud-deployment).

> **🎙️ Voice** is available via both **MCP** (`codewalk_voice_ask` + `codewalk_speak`) and **REST API** (`POST /voice/ask`) — ask questions by speaking, hear answers read aloud.

---

## Why Codewalk?

| Scenario | How Codewalk helps |
|----------|-------------------|
| **New dev joins the team** | Point Codewalk at the repo → get an overview, module map, and reading order. Self-onboard in hours instead of weeks of "hey, can you explain this?" |
| **LLM token costs are high** | Without RAG, the LLM needs your entire codebase in context — slow and expensive. Codewalk embeds code into a vector DB and retrieves only the relevant chunks per query. Faster answers, fraction of the tokens. |
| **Senior dev switches modules** | You know the auth module but now need to work on payments. Get module info, blast radius, and execution flow without bugging the payments team. |
| **Before a refactor** | Check blast radius before touching shared code. "If I change `base_model.py`, what breaks?" — get the answer before you break prod. |
| **PR reviews** | Run `codewalk_run_review` (MCP) or `POST /review` (API) — automated multi-stage review with OWASP security checks, blast radius warnings, and team guidelines matching. MCP mode returns enriched context so the calling model (Claude/GPT) performs the review directly — no separate LLM needed. |
| **Documentation is outdated** | Codewalk analyzes the *actual code*, not stale wiki pages. Always up to date. |

---

## 🔬 Code Review — Powered by the Intelligence Layer

Codewalk's review engine is built on top of the codebase intelligence layer. It doesn't just lint — it understands your architecture, knows what files are risky, and reviews with full context.

### How it works

```
git diff → Static Analysis (graph risk, PageRank, cycles, blast radius)
         → Batch files (3-5 per batch, grouped by feature)
         → Host LLM reviews each batch with full context
         → Submit findings to disk per batch (JSON + Markdown, context stays clean)
         → Final summary: all findings + verdict
```

### What makes it different

| Capability | CodeRabbit / GitHub Copilot Review | Codewalk Review |
|---|---|---|
| **Architecture awareness** | ❌ No dependency graph | ✅ DuckDB + igraph: PageRank, fan-in, cycles, bottlenecks |
| **Blast radius** | ❌ | ✅ "This file has 23 callers — review with extra care" |
| **Works without indexing** | — | ✅ Just needs a git repo (graph enhances but isn't required) |
| **Batched for large PRs** | Dumps everything at once | ✅ 3-5 files per batch, sorted by risk, host LLM stays focused |
| **Custom rubrics** | Limited | ✅ Per-language + per-framework + team guidelines |
| **Fix application** | Suggests only | ✅ Accept/reject → apply atomically → verify with tests |
| **Severity levels** | varies | `blocker` · `error` · `suggestion` |

### Zero-setup review

Review runs on **any git repo** — no `codewalk_analyze_codebase` needed. The dependency graph is built automatically on first review (~5s) and cached:

| Component | Auto (graph-only) | Full index (after analyze) |
|-----------|-------------------|---------------------------|
| Git diff + file content | ✅ | ✅ |
| Rubrics + team guidelines | ✅ | ✅ |
| Stack detection | ✅ (from file extensions) | ✅ (cached) |
| Blast radius, PageRank, cycles | ✅ Built on-the-fly (~5s) | ✅ From cached DuckDB |
| Neighborhood (callers, tests) | ⚠️ Tests + interfaces only | ✅ Full (from ChromaDB) |
| Blast radius warnings | ⚠️ Not available | ✅ Transitive impact |

### Severity levels

| Level | Value | Meaning |
|-------|-------|---------|
| **Blocker** | `"blocker"` | Must fix before merge — blocks the PR |
| **Error** | `"error"` | Should fix — real bugs, logic errors, security risks |
| **Suggestion** | `"suggestion"` | Nice to have — style, naming, minor improvements |

### MCP review flow

1. `codewalk_run_review()` → session + first batch (reviews ALL changes by default: staged + unstaged + untracked; pass `target_branch='...'` to diff against a branch)
2. Host reviews batch → `codewalk_submit_batch_findings(session_id, [...])` → saved to disk as JSON; a hard-wrapped Markdown companion is also written for easy reading
3. `codewalk_review_next_batch(session_id)` → next batch (context window is clean)
4. Repeat until all batches done
5. `codewalk_get_review_summary(session_id)` → structured summary for final verdict
6. User edits `llm_findings.json` → sets `user_verdict` to `accepted`/`rejected` → `codewalk_apply_and_verify_fix` (apply + test in one step)
7. (Optional) `codewalk_re_review()` → fresh review that hides rejected findings

### API review flow

```bash
curl -X POST http://localhost:8000/review \
  -H "Content-Type: application/json" \
  -d '{}'
```

Runs: static analysis → batch review (parallel LLM) → dedup → verify → cluster → rank → verdict.

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🔍 **Module Detection** | Auto-groups files into packages/modules by directory structure |
| 🕸️ **Dependency Graph** | Parses imports across 15+ languages via tree-sitter |
| 💥 **Blast Radius** | BFS on reversed dependency graph → shows transitive impact of any change |
| 📖 **Reading Order** | Topological sort → "read config.py before embedder.py because embedder imports config" |
| 🔄 **Execution Flow** | Entry points, module/file dependency chains, D3/ELK diagrams |
| 🤖 **AI Chat** | LangGraph agent with 7 tools, multi-turn conversation with memory |
| 🔎 **Semantic Search** | ChromaDB vector search on embedded code chunks (RAG) |
| 🔬 **Code Review** | Multi-stage review pipeline: test coverage, blast radius, guidelines RAG, context-enriched deep analysis |
| 🔄 **Incremental Reindex** | Content hash comparison — only re-embeds changed files, skips unchanged |
| 🧩 **MCP Server** | 43 MCP tools for VS Code Copilot / Claude Code / Cursor / Codex |
| 🎙️ **Voice Interface** | Talk to your codebase — mic recording, local STT (faster-whisper), agent-driven routing (MCP + API), TTS response |
| 🔬 **Graph Intelligence** | DuckDB persistent graph + igraph C-speed traversal: cycle detection, centrality, import chain tracing |
| 🧬 **Corrective RAG** | Distance-based chunk filtering (free) + LLM answer grading + query rewriting for reliable answers |
| 📦 **Parent-Child Chunking** | Full functions stored as parents, sub-chunks searched — retrieve complete context on match |
| ⚡ **Parallel Embedding** | Producer-consumer pipeline — CPU chunking overlaps with GPU embedding |
| 🏗️ **Multi-Provider LLM** | Ollama (local), OpenAI, Anthropic, Groq, Gemini, OpenRouter, DeepSeek |
| 📚 **Doc Indexing** | Index team docs (.md, .pdf, .txt) — search and ask questions with source citations |
| 🔄 **Reflection** | Actor→Critic→Improve loop used by deep research to refine cross-cutting reports |
| 🧑‍💻 **Human-in-the-Loop** | Approval gate before any code/file modification — LangGraph checkpoint + interrupt |
| 🔬 **Deep Research** | Fan-out parallel search → merge → synthesize → reflect for complex cross-cutting questions |
| 🏗️ **Architecture Health** | Graph stats, bottleneck files (betweenness centrality), PageRank, cycle detection with fix suggestions |
| 🌐 **15+ Languages** | Python, JS, TS, Java, Go, Rust, Ruby, PHP, C#, C++, C, Dart, Kotlin, Swift, YAML |

### Supported Languages

| Language | Extensions | Tree-sitter Parsing | Import Extraction |
|----------|-----------|---------------------|-------------------|
| Python | `.py` | ✅ | ✅ |
| JavaScript | `.js`, `.jsx` | ✅ | ✅ |
| TypeScript | `.ts`, `.tsx` | ✅ | ✅ |
| Java | `.java` | ✅ | ✅ |
| Go | `.go` | ✅ | ✅ |
| Rust | `.rs` | ✅ | ✅ |
| Ruby | `.rb` | ✅ | ✅ |
| PHP | `.php` | ✅ | ✅ |
| C# | `.cs` | ✅ | ✅ |
| C++ | `.cpp` | ✅ | ✅ |
| C | `.c` | ✅ | ✅ |
| Kotlin | `.kt` | ✅ | ✅ |
| Swift | `.swift` | ✅ | ✅ |
| Dart | `.dart` | ✅ *(optional install)* | ✅ |
| YAML | `.yaml`, `.yml` | — | — |
| JSON | `.json` | — | — |
| TOML | `.toml` | — | — |
| Markdown | `.md` | — | — |

> **Tree-sitter parsing** = extracts functions, classes, and methods for accurate chunking and function explanations.  
> **Import extraction** = builds the dependency graph, blast radius, and reading order.  
> Languages without tree-sitter support still get indexed via text splitting — they work with semantic search and AI chat, just without function-level granularity.

---

## 🆚 Codewalk vs. alternatives

Codewalk is **not another AI autocomplete**. It is a codebase intelligence layer: it builds a persistent dependency graph, embeds your code, indexes your docs, and exposes that intelligence through a UI, an MCP server, and an API.

If you need deep cross-file reasoning, blast-radius analysis, or AI review inside your existing IDE agent, Codewalk fits where general-purpose assistants stop.

| Use case | Typical approach | What Codewalk does differently |
|---|---|---|
| **Explain this codebase** | Ask a generic chat model and paste files | Builds a live graph + RAG so answers are grounded and cite real files |
| **PR review** | Lint + human review | LLM review with blast-radius, architecture, and custom guidelines |
| **Refactor shared code** | Grep for imports | Dependency graph + blast radius showing transitive impact |
| **Onboard a new developer** | Read wiki pages | Reading order + module map generated from actual code |
| **Team knowledge** | Search Confluence/Notion | Index docs alongside code and ask with citations |
| **AI agent tooling** | Write custom scripts or prompts | 43 MCP tools the agent can call directly |

---

## 🎬 Demo

### Web UI

https://github.com/user-attachments/assets/1bc99516-b3f6-4059-b463-de3c72bc850e

### MCP with VS Code Copilot

https://github.com/user-attachments/assets/a1dfd347-1135-47d2-b01d-3d995d86208e

### REST API

> 🎥 **[Video coming soon]**

### Voice Interface

https://github.com/user-attachments/assets/51d41d48-970f-437e-8c50-e6a104d71e0e

---

## 🖥️ Frontend — Knowledge Graph UI

Codewalk ships with a Next.js frontend for visual codebase exploration.

### What you can do

- **Structural view** — explore the repo as a layered dependency graph: modules, files, classes, and functions laid out as an interactive path flow.
- **Knowledge view** — semantic graph of entities and relationships surfaced by the AI analysis.
- **Path Finder** — pick a source and target node and discover import/dependency paths between them.
- **Search** — fuzzy + semantic search across files, symbols, and concepts.
- **Blast Radius / Diff mode** — visually highlight changed and affected nodes.
- **Themes** — switch between presets (Dark Gold, Dark Ocean, Dark Forest, Dark Rose, Light Minimal), accent colors, and heading fonts; your choice is saved locally.
- **Info Panel** — unified node details, metrics, source preview, and project overview.
- **KineticShell navigation** — index-dependent tabs stay locked until `GET /index-status` reports `indexed: true`.
- **Cloud Admin** — visit `/admin` to register repos, list repos, trigger indexing, copy tokens, and check server health/version.

### Run it

#### Quick start from a target repo

If you already have the Codewalk repo cloned elsewhere, run the combined launcher from the repo you want to explore:

```bash
/path/to/codewalk/scripts/run-ui-for-repo.sh
```

This starts the API from your current directory (discovering `codewalk.yaml`) and the frontend from the Codewalk checkout, automatically setting `CODEWALK_REPO_PATH` so the UI reads `.codewalk/` from your repo.

#### Frontend-only development

```bash
cd frontend
npm install
npm run dev
```

If you change frontend code and see stale chunk 404s or client-side exceptions, restart with a clean build cache:

```bash
npm run dev:clean      # clears .next and starts fresh
npm run restart        # kills port 3000 and restarts dev
# or
./scripts/restart-frontend.sh
```

Set `NEXT_PUBLIC_API_URL` to point at the backend (e.g. `http://localhost:8000` or `https://api.codewalk.xyz`).

Then open `http://localhost:3000`, analyze a repo, and click **Knowledge Graph**.

### Demo

> 🎥 **[Video coming soon — add frontend walkthrough here]**

---

## ⚙️ Setup (local)

> **Production cloud server?** See **[FULL_SETUP_GUIDE.md](FULL_SETUP_GUIDE.md)** — step-by-step: Hetzner, `api.codewalk.xyz`, GitHub App, webhooks, MCP download.

### Prerequisites

| Tool | Version | Check |
|------|---------|-------|
| Python | 3.10+ | `python3 --version` |
| Node.js | 18+ | `node --version` |
| Git | Any | `git --version` |
| Ollama *(optional)* | Latest | `ollama --version` |

### 1. Clone the codewalk repo

```bash
git clone https://github.com/gupta29470/codewalk.git
cd codewalk
```

### 2. Backend setup in codewalk

```bash
# Create virtual environment
python3 -m venv .codewalk-env
source .codewalk-env/bin/activate    # macOS / Linux
# .codewalk-env\Scripts\activate     # Windows

# Install Python dependencies
pip install -r requirements.txt
```

<details>
<summary><strong>⚠️ VPN / Corporate Network / Private Network Issues</strong></summary>

If you're behind a **VPN, corporate proxy, or private network**, package installations and model downloads may fail due to blocked connections or SSL certificate errors.

**Recommended: Use a normal (non-VPN) network for first-time setup.**

Codewalk's setup downloads packages from PyPI, npm, and HuggingFace. These are one-time downloads — once installed, everything runs locally. If possible:

1. **Disconnect from VPN** temporarily
2. Run the setup steps (`pip install`, `npm install`, start the backend once to download the embedding model)
3. **Reconnect to VPN** — everything is cached locally, no more downloads needed

> After the first run, Codewalk works fully offline (with Ollama). The VPN/corporate network won't cause any issues.

</details>

<details>
<summary><strong>Optional: Dart/Flutter support (tree-sitter-dart)</strong></summary>

```bash
# If you get an SSH error, run this first:
git config --global url."https://github.com/".insteadOf "git@github.com:"

# Then install:
pip install "tree-sitter-dart @ git+https://github.com/UserNobody14/tree-sitter-dart.git"
```

Without this, Codewalk still works — Dart files just won't get tree-sitter parsing (falls back to text splitting).

</details>

### 3. Frontend setup in codewalk

```bash
cd frontend
npm install
cd ..
```

### 4. Configure environment in codewalk

Copy the template: `cp env.local.example.txt .env` then edit:

```env
# ─── LLM Configuration ──────────────────────────────────────
# Provider: ollama | openai | anthropic | gemini | groq | openrouter
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5-coder:7b

# ─── Embeddings ──────────────────────────────────────────────
EMBEDDING_MODEL=jinaai/jina-code-embeddings-1.5b

# ─── API Keys (only fill the one you're using) ──────────────
# GROQ_API_KEY=gsk_...
# OPENAI_API_KEY=sk-...
# ANTHROPIC_API_KEY=sk-ant-...
# GOOGLE_API_KEY=AI...
# OPENROUTER_API_KEY=sk-or-...
```

### 5. Pull an Ollama model (if using local LLM)

```bash
ollama pull qwen2.5-coder:7b
```

<details>
<summary><strong>Recommended models by size</strong></summary>

| Model | Size | Tool Calling | Best For |
|-------|------|-------------|----------|
| `qwen2.5-coder:7b` | 4.7 GB | ✅ | Code-focused, fast |
| `qwen3.5:latest` (8B) | 6.6 GB | ✅ | General + code |
| `qwen3.5:27b` | 17 GB | ✅ | Best accuracy |

</details>

---

## 🚀 Usage

### Option 1: Web UI

**Quick launch from a target repo (recommended for exploration):**

From the repo you want to explore, run the combined launcher in the Codewalk checkout:

```bash
/path/to/codewalk/scripts/run-ui-for-repo.sh
```

It kills any process on ports 8000/3000, starts the API from the target repo, starts the frontend from the Codewalk checkout, and sets `CODEWALK_REPO_PATH` automatically. Then open **http://localhost:3000**.

**Manual two-terminal setup (for frontend/backend development):**

Open **two terminals** in **codewalk**:

**Terminal 1 — Backend API**
```bash
source .codewalk-env/bin/activate
uvicorn src.codewalk.api.main:app --reload --port 8000
```

**Terminal 2 — Frontend**
```bash
cd frontend
npm run dev
```

If the frontend throws stale chunk 404s after pulling or editing code, restart it cleanly:

```bash
npm run dev:clean
# or from the project root
./scripts/restart-frontend.sh
```

Open **http://localhost:3000** → click **Analyze Codebase** (the repo is discovered from the working directory via `codewalk.yaml`).

Then explore:
- **Knowledge Graph** — interactive structural + knowledge graph, layer/module legend, node-category filters, detail-level toggle, persona selector, Path Finder, export menu, code viewer, file explorer, tour/onboarding, mobile layout, edge styling, and diff overlay
- **Overview** — tech stack, modules, dependency diagram, riskiest files
- **Modules** — browse all modules, click one for file list + dependencies
- **Blast Radius** — which files break if you change each file
- **Reading Order** — optimal file reading sequence with risk levels
- **Execution Flow** — D3/ELK diagram of module/file dependencies
- **Chat** — ask any question ("explain the authentication flow", "what does scanner.py do?")
- **Code Review** — review git diffs, review single files, load team guidelines
- **Voice** — click the mic, ask a question by speaking, hear the answer read aloud
- **Smart Reindex** — incremental re-embed with stats (skipped, changed, deleted)
- **Research** — deep cross-cutting reports with a structured architecture diagram card
- **Cloud Admin** — `/admin` page for repo registration, token management, and server health

### Option 2: MCP Server (VS Code Copilot / Claude Code / Cursor)

See [MCP Integration](#-mcp-integration) below.

### Option 3: REST API

```bash
# Start the backend
source .codewalk-env/bin/activate
uvicorn src.codewalk.api.main:app --reload --port 8000
```

**Step 1 — Analyze a codebase:**
```bash
# Run from inside the repo you want to analyze (repo is discovered from cwd via codewalk.yaml)
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"index_mode": "auto"}'
```

**Step 2 — Check index status and explore the results:**
```bash
# Check whether the current workspace is indexed
# Optional: ?repo_path=/path/to/repo (defaults to cwd discovery)
curl "http://localhost:8000/index-status" | python3 -m json.tool

# Project overview (tech stack, modules, riskiest files)
curl http://localhost:8000/overview | python3 -m json.tool

# List all modules
curl http://localhost:8000/modules | python3 -m json.tool

# Dive into a specific module
curl http://localhost:8000/modules/auth | python3 -m json.tool

# What breaks if I change files in the auth module?
curl http://localhost:8000/blast-radius/auth | python3 -m json.tool

# Optimal reading order
curl http://localhost:8000/reading-order | python3 -m json.tool

# Execution flow (entry points, dependency chains)
curl http://localhost:8000/execution-flow | python3 -m json.tool
```

**Step 3 — Chat with the agent:**
```bash
# Ask a question
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Explain this project", "thread_id": "thread-1"}'

# Follow-up (same thread_id = conversation memory)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What does the auth module do?", "thread_id": "thread-1"}'

# After code changes — refresh analysis without re-embedding
curl -X POST http://localhost:8000/refresh

# Incremental reindex — only re-embed changed files
curl -X POST http://localhost:8000/incremental-reindex

# Review current git diff for bugs, security, style
curl -X POST http://localhost:8000/review \
  -H "Content-Type: application/json" \
  -d '{"staged": false, "target_branch": "master"}'
```

See [API Reference](#-api-reference) for full request/response details on every endpoint.

---

## 🔌 MCP Integration

Codewalk runs as an MCP (Model Context Protocol) server, so any AI agent that speaks MCP can use it.

### Cloud MCP (index on server, query locally)

1. Cloud server indexes your repo on `git push` (GitHub App webhook)
2. Clone **codewalk** locally (MCP server code)
3. Open **target repo** in Cursor/VS Code (`${workspaceFolder}`)
4. Configure MCP with cloud URL + repo token:

```json
{
  "servers": {
    "codewalk": {
      "command": "/path/to/codewalk/.codewalk-env/bin/python",
      "args": [
        "-c",
        "import os, sys; sys.path.insert(0, os.environ['CODEWALK_PATH']); from src.codewalk.mcp.server import mcp; mcp.run(transport='stdio')"
      ],
      "cwd": "${workspaceFolder}",
      "env": {
        "CODEWALK_PATH": "/path/to/codewalk",
        "CODEWALK_SERVER_URL": "https://api.codewalk.xyz",
        "CODEWALK_REPO_NAME": "owner/repo",
        "CODEWALK_REPO_TOKEN": "cw_repo_xxxxxxxx"
      }
    }
  }
}
```

> `cwd` should be the **target repo** (where `codewalk.yaml` lives). `CODEWALK_PATH` tells Python where to find the Codewalk source package. Open the target repo in your editor so the server starts from that workspace.

Get `repo_token` after first index (on server):

```bash
docker compose exec postgres psql -U codewalk -d codewalk -c \
  "SELECT repo_token FROM repos WHERE full_name='owner/repo';"
```

Run **`codewalk_connect_repo`** in Cursor or let analyze auto-download the index. Cloud sync tools include `codewalk_pull_index`, `codewalk_connect_repo`, `codewalk_index_status`, `codewalk_check_version`, and `codewalk_show_knowledge_graph`.

> Every MCP tool is wrapped with a workspace-change guard (`_refresh_state_if_moved`) that re-discovers the current working directory and resets state if the workspace changes.

> ⚠️ **One repo per MCP server process.** Codewalk keeps runtime state (vector store, graph, repo path) in memory. Pointing the same running MCP server at multiple repos — or rapidly switching workspaces in the same process — can overwrite or corrupt that state. Use one editor window / one MCP connection per repo. The stdio transport is safe because each connection spawns a separate process, but do not route commands for different repos into the same server instance.

### Local-only MCP (index on your machine)

No cloud — index runs locally via `codewalk_analyze_codebase`. After `rebuild_analysis_cache`, MCP embeds with **`index_from_paths_parallel`** (same pipeline helpers as the API, but MCP scans via `scan_repo_files` + `codewalk.yaml` excludes rather than calling `full_index_parallel` directly).

| Surface | Local embed entrypoint | Notes |
|---------|------------------------|-------|
| **MCP** `codewalk_analyze_codebase` | `index_from_paths_parallel` | `rebuild_analysis_cache` → parallel chunk/embed → `write_manifest` |
| **API** `POST /analyze` (+ `/analyze/stream`) | `full_index_parallel` | Same Chroma output under `{repo}/.codewalk/` |

### Review & approve fixes (agent + MCP)

You talk to your **IDE agent**; the agent calls **Codewalk MCP tools**. Codewalk does not render UI — each host has its own approve/reject experience (Cursor approval cards, Copilot chat, Claude Code prompts, etc.). The agent must present each fix and wait for your approval through that host UI (or yes/no in chat).

1. Agent runs `codewalk_run_review` (returns enriched context for the host LLM to review) or `codewalk_review_file` (runs the full pipeline on one file)
2. User edits `llm_findings.json`: set `user_verdict` to `accepted` or `rejected` for each finding
3. Apply + verify accepted fixes: `codewalk_apply_and_verify_fix` applies every accepted finding and runs static analysis + tests in one call; or `codewalk_approve_action` → `codewalk_apply_fix(..., approval_token=<token>)` for a single manual fix
4. After edits: `codewalk_incremental_reindex`

Full agent rules: `src/codewalk/mcp/server.py` FastMCP `instructions` (sent on MCP connect).

**Example:** `@codewalk review my changes, then fix each issue only after I approve`

**Cloud re-download:** `codewalk_pull_index` / `codewalk_connect_repo` / auto-download on analyze all **replace** local `.codewalk/` (delete then extract). Force refresh: `rm -rf .codewalk` then pull.

### MCP tools — index requirements

| Tool | Index required? | Notes |
|------|-----------------|-------|
| `codewalk_analyze_codebase` | Builds/loads | Cloud download or local embed |
| `codewalk_generate_config` | No | Creates starter `codewalk.yaml` |
| Query tools (search, overview, modules, symbols, …) | Yes | `_require_index()` auto-loads disk |
| `codewalk_find_circular_dependencies` | Yes | Uses graph data |
| `codewalk_get_architecture_health` | Yes | Graph stats + cycles |
| `codewalk_incremental_reindex`, `codewalk_refresh_analysis` | Yes | |
| `codewalk_run_review`, `codewalk_review_file`, `codewalk_get_stack_info` | Soft / Yes | Better with index; `run_review` returns context for the host LLM |
| `codewalk_get_review_details` | Yes | Reads persisted session |
| `codewalk_approve_action` / `codewalk_apply_fix` | No / edits files | Token required for `apply_fix` |
| `codewalk_apply_and_verify_fix` | Yes | Applies accepted fixes + runs static analysis + tests in one step |
| `codewalk_run_static_analysis` | No | ruff/mypy/eslint/etc. |
| `codewalk_run_tests` | No | pytest/npm test/etc. |
| `codewalk_pull_index`, `codewalk_connect_repo`, `codewalk_index_status` | Cloud config | Replace `.codewalk/` on download |
| Docs / guidelines / voice / `check_version` / `show_knowledge_graph` | Varies | See MCP server `instructions` |

### Starting the MCP Server in VS Code

1. Open VS Code in the codewalk project
2. Press **`Cmd+Shift+P`** (macOS) or **`Ctrl+Shift+P`** (Windows/Linux)
3. Type **`MCP: List Servers`** and select it

   ![MCP: List Servers](assets/mcp-list-servers.png)

4. You'll see **`codewalk`** in the list

   ![Select codewalk server](assets/mcp-select-server.png)

5. Click **Start Server** next to codewalk

   ![Start Server](assets/mcp-start-server.png)

6. The server starts in the background (stdio transport)
7. Open Copilot Chat → type **`@codewalk`** → all Codewalk MCP tools are available

   ![MCP tools list](assets/mcp-tools-list.png)


### VS Code Copilot

Add to `.vscode/mcp.json` in your desired project:

> ⚠️ **Replace `/path/to/codewalk`** with the actual absolute path where you cloned codewalk.
> `cwd` (`${workspaceFolder}`) should be the target repo so the server discovers `codewalk.yaml`. `CODEWALK_PATH` must point at the cloned Codewalk repo so `src.codewalk.mcp.server` resolves.

```json
{
  "servers": {
    "codewalk": {
      "command": "/path/to/codewalk/.codewalk-env/bin/python",
      "args": [
        "-c",
        "import os, sys; sys.path.insert(0, os.environ['CODEWALK_PATH']); from src.codewalk.mcp.server import mcp; mcp.run(transport='stdio')"
      ],
      "cwd": "${workspaceFolder}",
      "env": {
        "CODEWALK_PATH": "/path/to/codewalk"
      }
    }
  }
}
```

> **Codewalk config (`codewalk.yaml`):** Put repo-specific settings in the repo root:
>
> ```yaml
> docs_path: team-docs
> code_guidelines: team-docs/code_guidelines.md
> indexing:
>   exclude:
>     - tests/**
>     - docs/**
>     - scripts/legacy/**
>     - "*.generated.*"
>   include:
>     - docs/architecture/**
> ```
>
> `indexing.exclude` is a list of paths/patterns skipped during scanning. `indexing.include` overrides exclusions (and the core safety net) for specific paths. These are checked at scan time. Generate a starter config with stack-specific excludes via `python -m src.codewalk.cli generate-config` or `@codewalk Run codewalk_generate_config`.

> **Docs & guidelines:**
> - `docs_path` indexes `.md`, `.pdf`, `.txt`, and `.rst` files for semantic search via `/docs/search` or `codewalk_search_docs`.
> - `code_guidelines` is an optional explicit path to the review-guidelines file. Codewalk loads guidelines in this priority order:
>   1. **Explicit file** — if `code_guidelines: path/to/file.md` is set in `codewalk.yaml`, read that file directly.
>   2. **ChromaDB doc collection** — search the indexed docs for a `code_guidelines` document (skipped in MCP review path to avoid cold-start latency).
>   3. **Disk scan** — scan `docs_path` for any file named `code_guidelines.md` / `.txt` / `.rst`.
>   4. **None** — no guidelines found; review proceeds without them.
> - For language/framework-specific review rubrics, place `.md` files in `.codewalk/rubrics/` (e.g. `.codewalk/rubrics/python.md`, `.codewalk/rubrics/python_fastapi.md`, `.codewalk/rubrics/core.md`). These override built-in rubrics.

> **Customizing file filters:** Codewalk uses a deterministic core safety net ([`src/codewalk/ingestion/file_filter.py`](src/codewalk/ingestion/file_filter.py)) — no LLM involved. It always skips universally bad content (`.git`, `node_modules`, dependency/build/cache dirs, binaries, media, secrets, lock files, generated suffixes). Repo- or framework-specific exclusions (e.g., `tools/`, `scripts/`, `cdk/`, `migrations/`, story files) belong in `codewalk.yaml` (often generated by `generate-config`). If a folder or file is **not being indexed** that you need, you have three options:
>
> 1. **`codewalk.yaml` `indexing.include`** — override exclusions for specific paths. Example: `["docs/architecture/**", "src/migrations/schema.py"]`.
> 2. **`codewalk.yaml` `indexing.exclude`** — repo-specific dirs/patterns. Example: `["tests/**", "docs/**", "*.generated.*"]`.
> 3. **`.codewalkignore` file** — gitignore-style patterns in the repo root (see below).
>
> You generally do **not** need to duplicate `node_modules`, `.git`, build dirs, etc. in `codewalk.yaml`; those are handled by the core safety net.

> **`.codewalkignore`** — Create a `.codewalkignore` file in the root of the repo you're analyzing to skip specific files/directories:
>
> ```
> # Skip test files
> tests/
> *_test.py
>
> # Skip specific directories
> data/
> wiki/
> blogs/
>
> # Skip specific file patterns
> *.config.js
> setup.py
> ```
>
> **Syntax** (gitignore-like):
> - `folder/` — skip any path containing this directory
> - `*.pattern` — glob match against full path or filename
> - `filename` — matches exact filename or path segment
> - `# comment` — ignored
> - blank lines — ignored
>
> Patterns are cached in `_codewalkignore_patterns` (loaded once per session). If you change the repo being analyzed, `reset_codewalkignore()` clears the cache so the next repo's `.codewalkignore` gets loaded.

Then in Copilot Chat: **`@codewalk`** → it will call `codewalk_analyze_codebase` automatically.

> **Note:** After adding or modifying `.vscode/mcp.json`, reload the VS Code window: **`Cmd+Shift+P`** → **`Developer: Reload Window`**.

### Claude Code

Add to `~/.claude/mcp.json`:

```json
{
  "mcpServers": {
    "codewalk": {
      "command": "/path/to/codewalk/.codewalk-env/bin/python",
      "args": [
        "-c",
        "import os, sys; sys.path.insert(0, os.environ['CODEWALK_PATH']); from src.codewalk.mcp.server import mcp; mcp.run(transport='stdio')"
      ],
      "cwd": "${workspaceFolder}",
      "env": {
        "CODEWALK_PATH": "/path/to/codewalk"
      }
    }
  }
}
```

### Cursor

Settings → MCP Servers → Add:

```json
{
  "codewalk": {
    "command": "/path/to/codewalk/.codewalk-env/bin/python",
    "args": ["-m", "src.codewalk.mcp.server"],
    "cwd": "${workspaceFolder}",
    "env": {
      "CODEWALK_PATH": "/path/to/codewalk"
    }
  }
}
```

> Exclusions now live in `codewalk.yaml` (`indexing.exclude`) or `.codewalkignore`, not in the `EXCLUDE_PATHS` env var.

### OpenAI Codex CLI

Add to `~/.codex/mcp.json`:

```json
{
  "mcpServers": {
    "codewalk": {
      "command": "/path/to/codewalk/.codewalk-env/bin/python",
      "args": [
        "-c",
        "import os, sys; sys.path.insert(0, os.environ['CODEWALK_PATH']); from src.codewalk.mcp.server import mcp; mcp.run(transport='stdio')"
      ],
      "cwd": "${workspaceFolder}",
      "env": {
        "CODEWALK_PATH": "/path/to/codewalk"
      }
    }
  }
}
```

### How It Works (First-Time Setup)

The **first time** you use Codewalk on a new codebase, it needs to index the files.  
You just tell the AI to analyze — **the AI handles the rest automatically**.

### Tool Calling Sequence

```
┌─────────────────────────────────────────────────────────────────────┐
│                    SETUP WORKFLOW (run once)                        │
│                                                                     │
│  Step 1 (only step)                                                 │
│  codewalk_analyze_codebase                                          │
│       │  scans files, builds dependency graph, detects modules,     │
│       │  filters with file_filter.py, chunks, embeds — all in one   │
│       ▼                                                             │
│  ✅ READY — all query tools unlocked                                │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                   QUERY TOOLS (use after setup)                     │
│                                                                     │
│  codewalk_get_overview          → project summary + dependency flow │
│  codewalk_search_codebase       → semantic code search              │
│  codewalk_lookup_symbol         → find symbols by name across repo  │
│  codewalk_get_module_info       → inspect a specific module         │
│  codewalk_explain_function      → AI-powered function explanation   │
│  codewalk_explain_class         → AI-powered class explanation      │
│  codewalk_get_blast_radius_map  → change risk analysis              │
│  codewalk_find_circular_dependencies → detect import cycles         │
│  codewalk_get_reading_order     → optimal file reading sequence     │
│  codewalk_get_execution_flow    → module/file dependency flow       │
│  codewalk_get_architecture_health → bottlenecks, cycles, key files  │
│  codewalk_call_chain(source, target) → trace import path between    │
│  codewalk_show_knowledge_graph  → export graph for visualization    │
│  codewalk_index_docs(docs_path) → index .md/.pdf/.txt docs          │
│  codewalk_search_docs(query)    → search indexed documents           │
│  codewalk_ask_docs(question)    → RAG answer grounded in docs        │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                   REVIEW & HITL TOOLS                               │
│                                                                     │
│  codewalk_run_review            → gather review context for host LLM│
│  codewalk_review_file           → full pipeline review of one file  │
│  codewalk_get_stack_info        → deterministic stack signals       │
│  codewalk_get_review_details    → retrieve a persisted review       │
│  codewalk_load_guidelines       → load team coding standards        │
│  codewalk_apply_and_verify_fix  → apply + static analysis + tests   │
│  codewalk_approve_action(text)  → HITL gate (returns approval_token)│
│  codewalk_apply_fix(..., token) → apply one fix after approval      │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                 MAINTENANCE (after code changes)                    │
│                                                                     │
│  codewalk_generate_config       → starter codewalk.yaml             │
│  codewalk_incremental_reindex   → re-embed only changed files       │
│  codewalk_refresh_analysis      → re-scan without re-embedding      │
│  codewalk_run_static_analysis   → ruff/mypy/eslint/etc. on files    │
│  codewalk_run_tests             → pytest/npm test/etc. on files     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                    CLOUD (when configured)                          │
│                                                                     │
│  codewalk_pull_index            → download latest server index      │
│  codewalk_connect_repo          → one-step cloud setup              │
│  codewalk_index_status          → local vs cloud version            │
│  codewalk_check_version         → server health/version             │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                      VOICE (hands-free)                             │
│                                                                     │
│  MCP:  codewalk_voice_ask  → mic → transcribe                       │
│        Copilot picks tool  → calls it → codewalk_speak(summary)     │
│                                                                     │
│  API:  POST /voice/ask     → mic → transcribe → agent invokes tool  │
│        agent answer        → format_voice_response() → MP3          │
└─────────────────────────────────────────────────────────────────────┘
```

> **💡 Before indexing:** Close unnecessary applications (browsers, Slack, Docker, etc.). Indexing loads the embedding model into memory and processes all files at once — freeing up RAM helps it run faster and avoids slowdowns.

**You type this in Copilot Chat:**
```
@codewalk analyze this codebase [auto(default) | reindex(update index) | full(delete existing index and generate new index)]
or
@codewalk_analyze_codebase [auto(default) | reindex(update index) | full(delete existing index and generate new index)]
```

**What happens behind the scenes (you don't need to do anything):**
1. The AI calls `codewalk_analyze_codebase` → scans all files, filters with `file_filter.py`, detects modules, builds the dependency graph, chunks and embeds everything in one call

**You'll see progress like:**
```
✓ Codebase analyzed and indexed successfully
  Files found: 142
  Files indexed: 121
  Chunks embedded: 380
  Modules found: api, analysis, embeddings, ingestion, rag

✅ Ready to answer questions — use query tools directly.
```

> **Note:** After indexing, the AI agent should automatically call these tools. If it doesn't, you can invoke them manually — the hints above tell you exactly which tools to run.

> **Note:** This only happens once. Next time you say `@codewalk analyze this codebase`, it detects a complete existing index and skips straight to "ready." If the index is partial (e.g., indexing was interrupted), it warns that the index is behind and tells you to run `codewalk_incremental_reindex`.

### ⚠️ If the AI Stops Mid-Workflow

The setup is now a single call — `codewalk_analyze_codebase` does everything. If the AI stops after that, just call any query tool yourself:

| AI stopped after... | You call next |
|---|---|
| `codewalk_analyze_codebase` (complete) | Any query tool — `codewalk_get_overview`, `codewalk_search_codebase`, etc. |
| `codewalk_analyze_codebase` (interrupted / partial index) | `codewalk_incremental_reindex` to resume/sync |

> **Tip:** Look for the **⏩ NEXT STEP** line at the bottom of each tool's output — it tells you exactly what to do.

---

### MCP Tools — What You Can Ask

After indexing is done, here's every tool you can use.  
You don't need to remember tool names — just ask naturally and the AI picks the right tool.

---

#### "Give me the big picture"

**Tool:** `codewalk_get_overview` — no parameters needed

You just joined a new team. You have no idea what this project does. Start here.

```
@codewalk give me an overview of this project
or
@codewalk_get_overview
```

**When to use:** Day 1 on a new project. You want to know what you're dealing with.

---

#### "What's in this module?"

**Tool:** `codewalk_get_module_info(module_name)` — pass the module name

You saw "auth" in the overview and want to dig into it.

```
@codewalk tell me about the auth module
or
@codewalk_get_module_info auth
```

**When to use:** You need to work on a specific module and want to see all its files, classes, and functions at a glance.

---

#### "Explain this function to me"

**Tool:** `codewalk_explain_function(function_name)` — pass the function or class name

Your tech lead mentioned `verify_request` in a PR review. You have no idea what it does.

```
@codewalk explain the verify_request function
or
@codewalk_explain_function verify_request function
```

**When to use:** You see a function name in code/PR/docs and want to understand exactly what it does without reading the whole file yourself.

---

#### "Search for something in the codebase"

**Tool:** `codewalk_search_codebase(query)` — pass any natural language question

You need to find where database connections are handled but don't know which file.

```
@codewalk how does this project handle database connections?
or 
@codewalk_search_codebase how does this project handle database connections?
```

**When to use:** You have a question about a concept ("error handling", "file upload", "caching") and don't know which files to look at. For every conceptual question, run 1-3 parallel calls with different phrasings and synthesize the returned chunks:

```
@codewalk_search_codebase how does this project handle database connections?
@codewalk_search_codebase database connection pool setup
@codewalk_search_codebase SQLAlchemy engine configuration
```

---

#### "What breaks if I change this?"

**Tool:** `codewalk_get_blast_radius_map(target)` — pass a module name, file name, or leave empty

You're about to refactor `models/base.py`. Before you touch it, you want to know the damage.

```
@codewalk what's the blast radius of base.py / auth?
or
@codewalk_get_blast_radius_map base.py / auth?
```

**When to use:** Before refactoring or making changes. "Is it safe to change this, or will half the project break?"

---

#### "Where should I start reading?"

**Tool:** `codewalk_get_reading_order(module_name)` — pass a module name or leave empty for entire repo

You want to understand the `agent` module but don't know which file to read first.

```
@codewalk what order should I read the agent module?
or 
@codewalk_get_reading_order 
```

**When to use:** You want to understand code without constantly jumping between files wondering "wait, what's this import?"

---

#### "How does the code flow?"

**Tool:** `codewalk_get_execution_flow(module_name)` — pass a module name or leave empty for module-level view

You want to understand how modules connect to each other.

```
@codewalk show me the execution flow
or 
@codewalk_get_execution_flow
```

**When to use:** You want to understand "what calls what" — the big picture of how code connects.

---

#### "I changed some code, refresh the analysis"

**Tool:** `codewalk_refresh_analysis` — no parameters needed

You added 3 new files and refactored a module. The analysis is now stale.

```
@codewalk refresh the analysis
or 
@codewalk_refresh_analysis
```

**When to use:** After you commit code changes and want updated blast radius / reading order / execution flow results.

---

#### "Some files changed, update the embeddings"

**Tool:** `codewalk_incremental_reindex` — no parameters needed

You changed a few files but don't want to re-embed the entire codebase.

```
@codewalk reindex changed files
or
@codewalk_incremental_reindex
```

**When to use:** After code changes when you want the vector search to reflect the latest code without a full re-index. Uses content hashes — only re-embeds what actually changed.

---

#### "Review my changes for bugs"

**Tool:** `codewalk_run_review` — reviews ALL changes by default; optional: `staged=true` (narrow), `target_branch="master"` (branch diff)

You're about to push a PR and want an automated code review.

```
@codewalk review my changes
or
@codewalk_run_review
@codewalk_run_review staged=true target_branch="master"
```

**When to use:** Before pushing a PR. `codewalk_run_review` gathers the full diff, neighborhood context, blast radius, and stack signals, then returns them to Copilot so it can perform the review directly using enriched context — no local LLM overhead, instant results.

---

#### "Review this specific file"

**Tool:** `codewalk_review_file(file_path)` — pass the file path

You want to check if a file follows the project's conventions.

```
@codewalk review src/codewalk/pipeline.py
or
@codewalk_review_file src/codewalk/pipeline.py
```

**When to use:** When you want to review any file — no git diff needed. Reads the file directly, enriches it with caller context (who imports it), security patterns from the vector store, similar code elsewhere in the codebase, and team guidelines. Copilot performs the review natively — no local LLM, instant results.

---

#### "Load our team's coding guidelines"

**Tool:** `codewalk_load_guidelines(docs_path)` — pass path to guidelines directory

Your team has coding standards in markdown files.

```
@codewalk load guidelines from docs/standards
or
@codewalk_load_guidelines docs/standards
```

**When to use:** Once per project. After loading, `codewalk_run_review` and `codewalk_review_file` automatically include your team's standards in their context.

---

#### "Talk to the codebase hands-free"

**Tools:** `codewalk_voice_ask` + `codewalk_speak` — no parameters needed

You want to ask a question by speaking instead of typing.

```
@codewalk_voice_ask
```

**What happens:**
1. 🔔 Beep — signals "start talking"
2. 🎙️ Records your voice (up to 30s, stops after 5s of silence)
3. 📝 Transcribes locally via faster-whisper
4. 🧠 Copilot reads the transcript and picks the right codewalk tool
5. ⚙️ Copilot calls the tool and gets the result
6. 🔊 Copilot calls `codewalk_speak(summary)` — speaks a 2-4 sentence summary aloud

**When to use:** Hands-free coding. You're reading code and want to ask "what does this function do?" without switching to the keyboard.

> **Note:** Routing is done by Copilot (full LLM), not a separate model — no Ollama required for MCP voice. The REST API (`POST /voice/ask`) sends the transcript directly to the chat agent, which picks the right tool natively.

---

#### "Is the architecture healthy?"

**Tool:** `codewalk_get_architecture_health` — no parameters needed

You want a health check: bottleneck files, circular dependencies, and the most important files.

```
@codewalk check the architecture health
or
@codewalk_get_architecture_health
```

**Returns:** Graph stats, bottleneck files (betweenness centrality), most important files (PageRank), circular dependencies with suggested fixes.

**When to use:** Before a refactor, code review, or whenever you suspect architectural issues.

---

#### "How does file A reach file B?"

**Tool:** `codewalk_call_chain(source, target)` — two file names

You want to trace the import chain between two files — "how does a change in config.py eventually affect server.py?"

```
@codewalk trace the import chain from config.py to server.py
or
@codewalk_call_chain config.py server.py
```

**Returns:** Shortest import path with hop count and full file paths.

**When to use:** Understanding how changes propagate, debugging import issues, or tracing dependency chains.

---

#### "Find circular dependencies"

**Tool:** `codewalk_find_circular_dependencies()` — no parameters

Detect import cycles that can cause brittle architecture or load-order bugs.

```
@codewalk find circular dependencies
or
@codewalk_find_circular_dependencies
```

**When to use:** Before a refactor or when investigating why two modules feel tightly coupled.

---

#### "Look up a symbol"

**Tool:** `codewalk_lookup_symbol(symbol_name)` — pass a function, class, or method name

Find every definition and key references of a named symbol across the repo.

```
@codewalk lookup symbol authenticate_user
or
@codewalk_lookup_symbol authenticate_user
```

**When to use:** You know a name and want its exact file, line, and callers without doing a semantic search.

---

#### "Generate a starter config"

**Tool:** `codewalk_generate_config()` — no parameters

Create a stack-specific `codewalk.yaml` with sensible excludes for your repo.

```
@codewalk generate a codewalk.yaml for this repo
or
@codewalk_generate_config
```

**When to use:** First-time setup, before the first analyze, to avoid indexing build artifacts and tests.

---

#### "Run static analysis"

**Tool:** `codewalk_run_static_analysis(file_paths)` — pass one or more files

Run language-appropriate linters/type-checkers (ruff, mypy, eslint, etc.) on the given files.

```
@codewalk run static analysis on src/auth.py
or
@codewalk_run_static_analysis src/auth.py
```

**When to use:** After applying a fix or editing files to catch style/type issues quickly.

---

#### "Run tests"

**Tool:** `codewalk_run_tests(file_paths)` — pass one or more files

Auto-detect and run the relevant test command (pytest, npm test, go test, cargo test, etc.).

```
@codewalk run tests for src/auth.py
or
@codewalk_run_tests src/auth.py
```

**When to use:** After a fix or refactor to confirm nothing broke.

---

### Quick Reference — What To Ask

| You want to... | Just say... |
|---|---|
| First-time setup | `@codewalk analyze this codebase`or `@codewalk_analyze_codebase` |
| Big picture overview | `@codewalk give me an overview` or `@codewalk_get_overview` |
| Understand a module | `@codewalk tell me about the auth module` or `@codewalk_get_module_info  auth` |
| Understand a function | `@codewalk explain the verify_request function` or `@codewalk_explain_function verify_request` |
| Find code by concept | `@codewalk how does error handling work?` or `@codewalk_search_codebase how does error handling work?` |
| Check change risk | `@codewalk what's the blast radius of config.py?` or `@codewalk_get_blast_radius_map config.py?` |
| Find riskiest files | `@codewalk show me the riskiest files` |
| Best reading order | `@codewalk what order should I read the agent module?` or `@codewalk_get_reading_order agent module` |
| See dependency flow | `@codewalk show me the execution flow` or `@codewalk_get_execution_flow` |
| After code changes | `@codewalk refresh the analysis` or `@codewalk_refresh_analysis` |
| Update embeddings | `@codewalk reindex changed files` or `@codewalk_incremental_reindex` |
| Review git diff | `@codewalk review my changes` or `@codewalk_run_review` |
| Review a file | `@codewalk review src/auth.py` or `@codewalk_review_file src/auth.py` |
| Get stack signals | `@codewalk what stack is this?` or `@codewalk_get_stack_info` |
| Load guidelines | `@codewalk load guidelines from docs/` or `@codewalk_load_guidelines docs/` |
| Architecture health | `@codewalk check architecture health` or `@codewalk_get_architecture_health` |
| Trace import chain | `@codewalk trace chain from config.py to server.py` or `@codewalk_call_chain config.py server.py` |
| Find circular dependencies | `@codewalk find circular dependencies` or `@codewalk_find_circular_dependencies` |
| Lookup a symbol | `@codewalk lookup symbol authenticate_user` or `@codewalk_lookup_symbol authenticate_user` |
| Run static analysis | `@codewalk run static analysis on src/auth.py` or `@codewalk_run_static_analysis src/auth.py` |
| Run tests | `@codewalk run tests for src/auth.py` or `@codewalk_run_tests src/auth.py` |
| Generate repo config | `@codewalk generate a codewalk.yaml` or `@codewalk_generate_config` |
| Search team docs | `@codewalk search docs for deployment` or `@codewalk_search_docs deployment` |
| Ask docs a question | `@codewalk how do we deploy?` or `@codewalk_ask_docs how do we deploy` |
| Deep research | `@codewalk research how error handling works across the codebase` |
| Accept/reject findings | User edits `llm_findings.json` → set `user_verdict` |
| Apply accepted fixes | `@codewalk apply and verify fixes` or `codewalk_apply_and_verify_fix` |
| Approve then apply one fix | `@codewalk approve apply fix to auth.py` → `@codewalk_approve_action` → `@codewalk_apply_fix` |
| Ask by speaking (hands-free) | `@codewalk_voice_ask` → Copilot calls tool → `@codewalk_speak` |

---

## 📡 API Reference

**Base URL**: `http://localhost:8000`

Start the server:
```bash
source .codewalk-env/bin/activate
uvicorn src.codewalk.api.main:app --reload --port 8000
```

### Analysis Endpoints

#### `POST /analyze` — Index a codebase

```bash
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "collection_name": "",
    "index_mode": "auto"
  }'
```

**Response:**
```json
{
  "status": "complete",
  "repo_path": "/Users/you/projects/my-app",
  "files_scanned": 142,
  "chunks_created": 380,
  "modules": ["api", "auth", "models", "utils", "frontend"],
  "message": ""
}
```

- The repo is discovered from the current working directory via `codewalk.yaml` (auto-created if missing). `repo_path` is optional in the request body and falls back to cwd discovery.
- `index_mode`: `"auto"` (full build if no index; load if complete; `status="behind"` if partial), `"reindex"` (smart update/resume), `"full"` (wipe & rebuild)
- `collection_name`: leave empty — reads `manifest.collection_name` if present, else repo folder name
- **`auto` + complete index** → load only (`load_scoped_analysis`), no re-embed — same idea as MCP `codewalk_analyze_codebase`
- **`auto` + partial index** → returns `status="behind"` and a `message` telling you to run `POST /incremental-reindex` or `/analyze` with `index_mode="reindex"`

**`status: "behind"` example:**
```json
{
  "status": "behind",
  "repo_path": "/Users/you/projects/my-app",
  "files_scanned": 75,
  "chunks_created": 210,
  "modules": ["api", "auth", "models"],
  "message": "Index is behind: 75/120 files indexed. Run incremental-reindex to sync."
}
```
- **No index** → `full_index_parallel` with `codewalk.yaml` excludes (local embed on API server)

#### `POST /analyze/stream` — Index with live progress (SSE)

```bash
curl -N -X POST http://localhost:8000/analyze/stream \
  -H "Content-Type: application/json" \
  -d '{"index_mode": "auto"}'
```

**Response (Server-Sent Events)** — `step` values from `analyze_stream()` in `main.py`:

| `step` | When |
|--------|------|
| `init` | Always first — checking existing index |
| `skip` | `index_mode: auto` + complete `.codewalk/` on disk (load only) |
| `behind` | `index_mode: auto` + partial `.codewalk/` (missing files) |
| `reindex` | `index_mode: reindex` — new/changed/deleted counts; also resumes a partial index |
| `store` | Full index or reindex — Chroma persist + manifest write |
| `analyze` | Dependency graph + module detection |
| `done` | Success (`result` object on final event when complete) |
| `error` | Exception message |

**`index_mode: auto` + complete `.codewalk/`** (fast path):
```
data: {"step": "init", "message": "Checking existing index..."}
data: {"step": "skip", "message": "Loaded existing index (380 chunks)"}
data: {"step": "done", "message": "Analysis complete!", "result": {...}}
```

**`index_mode: auto` + partial `.codewalk/`** (index behind):
```
data: {"step": "init", "message": "Checking existing index..."}
data: {"step": "behind", "message": "Index is behind: 75/120 files indexed. Run incremental-reindex to sync."}
data: {"step": "done", "message": "Analysis complete!", "result": {"status": "behind", ...}}
```

**`index_mode: full`** (or empty index):
```
data: {"step": "init", "message": "Checking existing index..."}
data: {"step": "store", "message": "Indexed 142 files (380 chunks)"}
data: {"step": "analyze", "message": "Building dependency graph..."}
data: {"step": "analyze", "message": "Detected 5 modules"}
data: {"step": "done", "message": "Analysis complete!", "result": {...}}
```

#### `GET /index-status` — Check whether the current workspace is indexed

```bash
# Check cwd-discovered repo
curl http://localhost:8000/index-status | python3 -m json.tool

# Optional: check a specific repo path
curl "http://localhost:8000/index-status?repo_path=/Users/you/projects/my-app" | python3 -m json.tool
```

**Response:**
```json
{
  "indexed": true,
  "repo_path": "/Users/you/projects/my-app"
}
```

The frontend `KineticShell` uses this endpoint to lock index-dependent tabs until `indexed: true`.

### API endpoints — index requirements (parity with MCP)

All query endpoints call `state.require_index()` — auto-loads `.codewalk/` from disk after server restart (same as MCP `_require_index()`).

| Endpoint | Index required? | MCP equivalent | Notes |
|----------|-----------------|----------------|-------|
| `POST /analyze` | Builds or loads | `codewalk_analyze_codebase` | API: `full_index_parallel`; MCP local: `index_from_paths_parallel` |
| `POST /analyze/stream` | Builds or loads | same (SSE progress) | Steps: `init`→`skip`/`behind`/`reindex`/`store`→`analyze`→`done` |
| `POST /chat`, `/chat/stream` | Yes | agent + tools | API HITL via `POST /chat/approve` |
| `GET /overview` | Yes | `codewalk_get_overview` | |
| `GET /modules`, `/modules/{name}` | Yes | `codewalk_get_module_info` | |
| `GET /blast-radius`, `/blast-radius/{m}` | Yes | `codewalk_get_blast_radius_map` | |
| `GET /reading-order` | Yes | `codewalk_get_reading_order` | |
| `GET /execution-flow` | Yes | `codewalk_get_execution_flow` | |
| `GET /architecture`, `/cycles` | Yes | `codewalk_get_architecture_health` | |
| `POST /semantic-search` | Yes | `codewalk_search_codebase` | Chroma semantic search endpoint |
| `POST /rag/expand-query` | Yes | — | LLM query expansion for RAG |
| `POST /rag/rerank` | Yes | — | LLM chunk reranking |
| `POST /rag/symbol-lookup` | Yes | `codewalk_lookup_symbol` | DuckDB symbol lookup |
| `POST /tools/static-analysis` | No | `codewalk_run_static_analysis` | ruff/mypy/eslint/etc. on files |
| `POST /tools/run-tests` | No | `codewalk_run_tests` | pytest/npm test/etc. on files |
| `GET /version` | No | `codewalk_check_version` | Codewalk version + commit info |
| `GET /staleness` | Yes | — | Local vs cloud index staleness |
| `POST /refresh` | Yes | `codewalk_refresh_analysis` | No re-embed |
| `POST /incremental-reindex` | Yes | `codewalk_incremental_reindex` | `codewalk_config` + manifest collection |
| `POST /review` | Soft (better with index) | `codewalk_run_review` | Works with partial context |
| `POST /review/stream` | Soft (better with index) | — | SSE progress events |
| `POST /review/cancel` | Yes | — | Cancel a running review |
| `POST /review/file` | Yes | `codewalk_review_file` | |
| `POST /review/guidelines` | No | `codewalk_load_guidelines` | Guidelines Chroma only |
| `POST /review/apply-accepted` | Yes | `codewalk_apply_and_verify_fix` | Apply accepted fixes + verify |
| `POST /review/apply` | No (repo path only) | `codewalk_apply_fix` | Caller approves in UI; no token gate |
| `POST /docs/index`, `/docs/search`, `/docs/ask` | Doc index only | `codewalk_index_docs` etc. | |
| `POST /chat/approve` | Yes | — | Resume/reject interrupted agent |
| `POST /voice/ask` | Yes | `codewalk_voice_ask` | |
| `POST /research` | Yes | deep research | |
| `GET /health` | No | — | |
| Cloud `GET /indexes/...` | Download only | `codewalk_pull_index` | Blocked when cloud-only mode |

**Cloud API server** (`DATABASE_URL` set): query endpoints above return 400 — indexes are built on server, queried locally via MCP download.

#### `POST /refresh` — Re-scan without re-embedding

```bash
curl -X POST http://localhost:8000/refresh
```

**Response:**
```json
{
  "status": "refreshed",
  "repo_path": "/Users/you/projects/my-app",
  "files": 142,
  "modules": ["api", "auth", "models", "utils", "frontend"]
}
```

---

### Chat Endpoint

#### `POST /chat` — Ask the AI agent a question

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Explain how authentication works in this project", "thread_id": "thread-1"}'
```

**Response:**
```json
{
  "answer": "The authentication flow starts in auth/middleware.py which checks JWT tokens on every request. The token validation logic is in auth/jwt.py which uses the python-jose library...",
  "thread_id": "thread-1"
}
```

Multi-turn conversation — use the same `thread_id`:
```bash
# Follow-up question
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What happens if the token expires?", "thread_id": "thread-1"}'
```

---

### View Endpoints

#### `GET /overview` — Project overview

```bash
curl http://localhost:8000/overview
```

**Response:**
```json
{
  "tech_stack": ["Python", "FastAPI", "React"],
  "total_files": 142,
  "total_modules": 5,
  "modules": [
    {"name": "api", "file_count": 12, "depends_on": ["auth", "models"]},
    {"name": "auth", "file_count": 5, "depends_on": ["models"]}
  ],
  "diagram": null,
  "overview_text": "## Project Overview\nTech stack: Python, FastAPI...",
  "riskiest_files": [
    {"file": "models/base.py", "risk_level": "high", "affected_files": 23}
  ]
}
```

`diagram` is optional (`str | None`) and currently always `null`.

#### `GET /modules` — List all modules

```bash
curl http://localhost:8000/modules
```

**Response:**
```json
{
  "modules": [
    {"name": "api", "file_count": 12, "languages": ["python"]},
    {"name": "auth", "file_count": 5, "languages": ["python"]},
    {"name": "frontend", "file_count": 34, "languages": ["typescript", "css"]}
  ],
  "total": 5
}
```

#### `GET /modules/{name}` — Module details

```bash
curl http://localhost:8000/modules/auth
```

**Response:**
```json
{
  "name": "auth",
  "file_count": 5,
  "files": ["auth/middleware.py", "auth/jwt.py", "auth/permissions.py", "auth/models.py", "auth/__init__.py"],
  "languages": {"python": 5},
  "depends_on": ["models"],
  "depended_by": ["api"],
  "blast_radius": [
    {"file": "auth/middleware.py", "risk_level": "moderate", "affected_files": 8}
  ],
  "module_risk": "moderate"
}
```

#### `GET /blast-radius` — Top 15 riskiest files

```bash
curl http://localhost:8000/blast-radius
```

**Response:**
```json
{
  "module": null,
  "module_risk": "high",
  "total_files": 15,
  "files": [
    {
      "file": "models/base.py",
      "risk_level": "high",
      "affected_files": 23,
      "direct": ["api/routes.py", "auth/models.py"],
      "transitive": ["api/views.py", "auth/middleware.py"]
    }
  ]
}
```

#### `GET /blast-radius/{module}` — Blast radius for a module

```bash
curl http://localhost:8000/blast-radius/auth
```

#### `GET /reading-order` — Recommended reading order

```bash
curl http://localhost:8000/reading-order
```

**Response:**
```json
{
  "order": [
    {
      "file": "config.py",
      "position": 1,
      "why": "No internal dependencies",
      "risk_level": "moderate",
      "affected_files": 12,
      "direct": ["embedder.py", "chain.py"],
      "transitive": ["pipeline.py"]
    },
    {
      "file": "models/base.py",
      "position": 2,
      "why": "No internal dependencies | Used by: routes.py, views.py",
      "risk_level": "high",
      "affected_files": 23
    }
  ]
}
```

#### `GET /execution-flow` — Execution flow diagram

```bash
curl http://localhost:8000/execution-flow
```

**Response:**
```json
{
  "flow": "## Execution Flow — Module Level\nEntry modules: api, cli\nTotal modules: 5\n\n### Module Dependencies\n  api (12 files) → depends on: auth, models\n  auth (5 files) → depends on: models\n  models (8 files) → (standalone)\n  utils (6 files) → (standalone)\n  frontend (34 files) → (standalone)"
}
```

---

### Maintenance Endpoints

#### `POST /incremental-reindex` — Re-embed only changed files

```bash
curl -X POST http://localhost:8000/incremental-reindex
```

**Response:**
```json
{
  "repo_path": "/Users/you/projects/my-app",
  "files_on_disk": 142,
  "files_skipped": 138,
  "files_reindexed": 3,
  "files_deleted": 1,
  "chunks_embedded": 12,
  "total_time": "2.3s"
}
```

**How it works:** Incremental reindex first performs a Chroma incremental update (only changed files are embedded/deleted). It then fully rebuilds DuckDB and `knowledge-graph.json` from all Chroma chunks, and re-indexes docs/guidelines. The manifest (`{repo}/.codewalk/manifest.json`) is updated every write with an incremented `index_version` and a `chunk_count` reflecting total Chroma chunks.

---

### Review Endpoints

#### `POST /review` — Review git diff

```bash
curl -X POST http://localhost:8000/review \
  -H "Content-Type: application/json" \
  -d '{"staged": false, "target_branch": "master"}'
```

**Response:**
```json
{
  "verdict": "request_changes",
  "verdict_reason": "Critical security issue found that must be fixed before merge.",
  "issues": [
    {
      "severity": "blocker",
      "category": "security",
      "file_path": "src/auth/jwt.py",
      "line_number": 42,
      "title": "JWT secret hardcoded",
      "explanation": "The JWT signing secret is hardcoded in the source file.",
      "suggestion": "Move the secret to an environment variable.",
      "code_snippet": "SECRET = 'my-secret-key'",
      "blocking": true,
      "confidence": "high"
    }
  ],
  "summary": "Found 1 critical issue in 3 files (+45 / -12 lines)",
  "narrative_summary": "",
  "files_reviewed": 3,
  "lines_added": 45,
  "lines_removed": 12,
  "session_id": "25-June-2026-143052-feature-x-to-main",
  "architecture_flags": {},
  "schema_version": "2.0",
  "merge_blockers": ["JWT secret hardcoded"],
  "clusters": [],
  "fixed_count": 0,
  "new_count": 1,
  "still_present_count": 0
}
```

- `staged`: If `true`, review ONLY staged changes (narrow mode). Default: `false` (reviews staged + unstaged + untracked).
- `target_branch`: Diff working tree against a branch (e.g. `"master"`). Shows committed + staged + unstaged + untracked. Default: `null` (local changes since last commit).
- `incremental`: Carry forward previous findings when `true`. Default: `false`.
- `narrative_summary`: Set `true` for an LLM-written narrative summary (slower). Default: `false`.

#### `POST /review/file` — Review a single file

```bash
curl -X POST http://localhost:8000/review/file \
  -H "Content-Type: application/json" \
  -d '{"file_path": "src/codewalk/pipeline.py"}'
```

**Response:**
```json
{
  "verdict": "approve_with_nits",
  "verdict_reason": "Non-critical issues found. Fix recommended but not blocking.",
  "issues": [
    {
      "severity": "suggestion",
      "category": "style",
      "file_path": "src/codewalk/pipeline.py",
      "line_number": 120,
      "title": "Consider extracting helper function",
      "explanation": "The inline loop is repeated in two places.",
      "suggestion": "Move the loop body into a private helper.",
      "code_snippet": "for chunk in chunks:"
    }
  ],
  "summary": "Clean change with one minor style suggestion.",
  "file_path": "src/codewalk/pipeline.py"
}
```

#### `POST /review/guidelines` — Load coding guidelines

```bash
curl -X POST http://localhost:8000/review/guidelines \
  -H "Content-Type: application/json" \
  -d '{"docs_path": "/path/to/guidelines"}'
```

**Response:**
```json
{
  "status": "loaded",
  "chunks": 24,
  "path": "/path/to/guidelines"
}
```

---

### Voice Endpoint

#### `POST /voice/ask` — Voice-in, voice-out Q&A

Upload an audio file (webm/mp3/wav from browser mic). Codewalk transcribes it, sends it to the chat agent (which picks the right tool natively), and returns both the text answer and a spoken MP3 response.

```bash
curl -X POST http://localhost:8000/voice/ask \
  -F "audio=@question.webm" \
  -F "thread_id=voice"
```

**Response:**
```json
{
  "question": "what does the auth module do?",
  "answer": "The auth module contains 5 files handling JWT validation...",
  "speech": "The auth module handles JWT validation and permissions.",
  "audio_base64": "SUQzBAAAAAAAI1RTU0UAAAA..."
}
```

- `audio` *(required)*: Audio file upload (webm, mp3, wav)
- `thread_id` *(optional)*: Conversation thread ID. Default: `"voice"`
- `audio_base64`: Base64-encoded MP3 of the spoken answer — decode and play in the browser

**Pipeline:** audio upload → faster-whisper STT → chat agent (picks tool natively) → summarize → edge-tts → MP3 response

---

#### `GET /health` — Health check

```bash
curl http://localhost:8000/health
```

**Response:**
```json
{
  "status": "ok"
}
```

---

## ☁️ Cloud Deployment

Production API: **`https://api.codewalk.xyz`** (indexing + webhooks + index download).  
Marketing site (optional): **`https://codewalk.xyz`**

### Architecture

```
git push → GitHub App webhook → api.codewalk.xyz
              ↓
         build in .incoming.{commit}/ → atomic_swap → active index
              ↓
Local MCP → GET /indexes/{owner}/{repo} → query locally
```

**Indexing is server-side only** — the cloud API does not serve `/analyze` or `/chat` for indexed repos. MCP downloads the index tarball and queries locally.

### Cloud indexing lifecycle

| Event | What happens |
|-------|----------------|
| **First `git push`** | Auto-registers repo, incremental index, `index_status: ready` |
| **Later pushes** | Incremental re-index; cloud Postgres `index_version` bumps and is written to the downloaded `.codewalk/manifest.json` |
| **Push during indexing** | Older run superseded; newest commit wins |
| **Deploy / API restart** | Orphan jobs cancelled; catch-up re-indexes stale/pending repos (~15s) |
| **Stale `codewalk_version`** | Catch-up full re-index after semver deploy |
| **Crash mid-write** | Atomic swap — active index unchanged until publish succeeds |

**Laptop after server index updates:** `codewalk_pull_index` (not `codewalk_analyze_codebase` when cloud is configured).

**Staleness banners (MCP):** `[Cloud]` → pull index / wait for server catch-up; `[Local]` → `codewalk_analyze_codebase`. See [deploy/SERVER_OPS.md](deploy/SERVER_OPS.md) §6.

**Cloud Admin UI:** The frontend includes an `/admin` page to register repos, list repos, trigger indexing, copy per-repo tokens, and check server health/version. Production API base is configured via `NEXT_PUBLIC_API_URL`.

**Local-ahead safety:** `codewalk_pull_index` and `codewalk_connect_repo` warn and require `force=True` when the local `.codewalk/manifest.json` `index_version` is ahead of the cloud Postgres row.

### Architecture (components)

| Component | Where | Role |
|-----------|-------|------|
| **Cloud server** | Hetzner + Docker | Index on push only |
| **GitHub Actions** | GitHub | Build image + deploy server |
| **GitHub App** | GitHub | Send `push` webhooks (must **install** app on repos) |
| **Local MCP** | Your laptop | Download index, run queries |

### Quick start

1. Follow **[FULL_SETUP_GUIDE.md](FULL_SETUP_GUIDE.md)** (complete step-by-step)
2. Server `.env`: `cp env.server.example.txt` → `/opt/codewalk/.env`
3. GitHub App webhook: `https://api.codewalk.xyz/webhooks/github`
4. **Install App** on each repo to index (creating the app is not enough)
5. **`git push`** to that repo — this registers it and starts indexing (install alone is not enough)
6. Verify: `POST /admin/repos` with `X-Admin-Key` → `index_status: ready`
7. Get `repo_token` from DB → set `CODEWALK_REPO_TOKEN` in MCP config

**Day-to-day server ops:** [deploy/SERVER_OPS.md](deploy/SERVER_OPS.md) — health, SQL, logs, [reset-repo.sh](deploy/reset-repo.sh) (prepare/reset/delete, `--dry-run`).

### Indexing a repo (checklist)

| Step | Required for indexing? |
|------|------------------------|
| Server running + cloud `.env` (App ID, PEM, webhook secret) | Yes |
| GitHub Actions secrets (`HETZNER_*`) | No — deploy only |
| Install GitHub App on the repo | Yes |
| `git push` to the repo | Yes — triggers register + index |
| `repo_token` in local MCP | Yes — for downloading the index |

> GitHub Actions **deploys the server**. **Indexing** is triggered by **GitHub App `push` webhooks**, not Actions.

### Per-repo codewalk config (`codewalk.yaml`)

Each indexed repo can have a `codewalk.yaml` at its root:

```yaml
indexing:
  branches:          # only these branches trigger indexing (fnmatch)
    - master
    - release/**
  exclude:
    # Repo-specific dirs/files (the core safety net already skips
    # node_modules, build artifacts, binaries, secrets, lock files, etc.)
    - frontend/**
    - docs/**
  include:
    # Override an exclusion for a specific path
    - docs/architecture/**
```

Generate a starter config with stack-specific excludes:

```bash
python -m src.codewalk.cli generate-config
```

Or via MCP: `@codewalk Run codewalk_generate_config`.

Cloud reads `codewalk.yaml` on every index. Pushes to other branches are ignored. See [FULL_SETUP_GUIDE.md § Phase 7](FULL_SETUP_GUIDE.md#9-phase-7--codewalk-config-codewalkyaml).

### Config templates

| File | Use |
|------|-----|
| [FULL_SETUP_GUIDE.md](FULL_SETUP_GUIDE.md) | Complete A→Z setup |
| [deploy/DEPLOY.md](deploy/DEPLOY.md) | Deployment guide |
| [deploy/SERVER_OPS.md](deploy/SERVER_OPS.md) | Server ops — health, indexing, SQL, [reset-repo.sh](deploy/reset-repo.sh) |
| [env.server.example.txt](env.server.example.txt) | Hetzner `/opt/codewalk/.env` |
| [env.local.example.txt](env.local.example.txt) | Local dev `.env` |
| [mcp.json.example](mcp.json.example) | MCP config → `.vscode/mcp.json` |
| [env.example.txt](env.example.txt) | All env vars index |

### CI/CD (GitHub Actions)

Push to `master` → build image → GHCR → deploy to Hetzner (`deploy-server.sh` syncs compose + Caddyfile).

**Secrets:** `HETZNER_HOST`, `HETZNER_USER`, `HETZNER_SSH_KEY`

> GitHub Actions **deploys the server**. **Indexing** is triggered by **GitHub App push webhooks**, not Actions.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      INTERFACES                         │
│                                                         │
│   Next.js Web UI (:3000)    MCP Server    REST API      │
│   ├── Overview              (stdio)       (:8000)       │
│   ├── Modules                  │             │          │
│   ├── Blast Radius             │             │          │
│   ├── Reading Order        Voice Interface   │          │
│   ├── Execution Flow       (mic → speak)     │          │
│   ├── Code Review              │             │          │
│   ├── Smart Reindex            │             │          │
│   └── Chat ──────────────────┐ │             │          │
│                              ▼ ▼             ▼          │
├──────────────────────────────────────────────────────────┤
│                     AGENT LAYER                          │
│                                                          │
│   LangGraph StateGraph ─── LLM (bind_tools) ───┐        │
│          │                                      │        │
│          ▼                                      ▼        │
│   ┌─ 11 Agent Tools ─────────────────────────────┐       │
│   │ search_codebase     get_overview             │       │
│   │ get_module_info     get_blast_radius_map     │       │
│   │ explain_function    get_reading_order        │       │
│   │ get_execution_flow  get_architecture_health  │       │
│   │ load_guidelines     apply_fix                │       │
│   └──────────────────────────────────────────────┘       │
├──────────────────────────────────────────────────────────┤
│                   INGESTION LAYER                         │
│                                                          │
│   scanner.py ──► file_filter.py ──► tech_detect.py       │
│   (file enum         (skip rules       (language/        │
│    & hashing)         & safety net)     framework id)     │
├──────────────────────────────────────────────────────────┤
│                    ANALYSIS LAYER                         │
│                                                          │
│   code_parser.py ──► dependency_graph.py ──► module_     │
│   (tree-sitter       (import extraction    detector      │
│    15+ langs)         → graph)                            │
│                         │                                │
│                         ▼                                │
│   blast_radius.py   reading_order.py                     │
│   (BFS reverse       (topological                         │
│    graph)             sort)                                │
├──────────────────────────────────────────────────────────┤
│                    GRAPH LAYER                           │
│                                                          │
│   graph/store.py ──► graph/runtime.py                    │
│   (DuckDB 10-table    (igraph C-speed                    │
│    persistent          traversal: cycles,                 │
│    graph)              centrality, paths)                 │
│                                                          │
│   .codewalk/graph.duckdb  ◄── files, imports, symbols,   │
│                               symbol_calls, chunks,       │
│                               modules, module_deps        │
├──────────────────────────────────────────────────────────┤
│                    REVIEW LAYER                          │
│                                                          │
│   diff_parser.py → reviewers/ → pipeline/ → engine.py    │
│   (git diff         (pluggable         (cluster/rank/    │
│    parsing)          reviewers)         verify/summary)   │
│                                                          │
│   report.py ────────► fix_applier.py                     │
│   (Finding dataclasses)  (approved fix application)       │
├──────────────────────────────────────────────────────────┤
│                   EMBEDDING LAYER                        │
│                                                          │
│   chunker.py ──► embedder.py ──► vector_store.py         │
│   (smart code     (Jina 1.5B     (ChromaDB               │
│    chunks)         MPS/CUDA)      persistent)             │
├──────────────────────────────────────────────────────────┤
│                     VOICE LAYER                          │
│                                                          │
│   ┌── mic ──► stt.py ──► router.py ──► tool exec ──┐    │
│   │  sounddevice   faster-whisper   get_llm()          │    │
│   │  (record)      (transcribe)     (route to tool) │    │
│   │                                                 │    │
│   │            ┌─ content tool? ─┐                   │    │
│   │            │  YES            │ NO (admin)        │    │
│   │            ▼                 ▼                   │    │
│   │   main LLM (get_llm())   return text only       │    │
│   │   raw result → speech    (no TTS)               │    │
│   │         │                                       │    │
│   │   tts.py ◄── speech                             │    │
│   │   edge-tts (speak answer)                       │    │
│   └──────────────────────────────────────────────    │
│                                                          │
│   Voice Flow:                                            │
│   🔔 beep → 🎙️ record (30s max, 5s silence stop)        │
│   → 📝 transcribe (faster-whisper, local)                │
│   → 🧠 route (configured LLM picks the right tool + args) │
│   → ⚙️ execute tool                                      │
│   → 🔇 admin tool? → text result only (silent)           │
│   → 🔊 content tool? → main LLM → speech → edge-tts     │
├──────────────────────────────────────────────────────────┤
│                     CORE LAYER (v2.4–v2.7)               │
│                                                          │
│   core/reflect.py   → Actor→Critic→Improve loop         │
│   core/hitl.py      → LangGraph interrupts + SQLite/AsyncSqliteSaver checkpoint │
│   core/fanout.py    → Parallel fan-out/fan-in graphs (`build_fanout_graph`)    │
│                                                          │
│   Used by: agent (hitl), research (reflect + fanout)    │
│   — generic, composable, zero duplication                │
├──────────────────────────────────────────────────────────┤
│                     LLM LAYER                            │
│                                                          │
│   config.py ──► get_llm() factory                        │
│   Ollama │ OpenAI │ Anthropic │ Gemini │ Groq │ ...      │
└──────────────────────────────────────────────────────────┘
```

### Directory Structure

```
codewalk/
├── src/codewalk/
│   ├── config.py                  # Settings + LLM provider factory
│   ├── pipeline.py                # Orchestration (parallel embed)
│   ├── ingestion/                 # File scanning & tech detection
│   │   ├── scanner.py             #   File enumeration
│   │   ├── file_filter.py         #   Skip rules (node_modules, etc.)
│   │   └── tech_detect.py         #   Language/framework detection
│   ├── analysis/                  # Code parsing & dependency analysis
│   │   ├── code_parser.py         #   Tree-sitter (15+ languages)
│   │   ├── dependency_graph.py    #   Import extraction → graph
│   │   ├── module_detector.py     #   Auto-grouping into modules
│   │   ├── blast_radius.py        #   Change impact (BFS)
│   │   └── reading_order.py       #   Topological sort
│   ├── graph/                     # Graph intelligence layer
│   │   ├── graph_store.py         #   DuckDB 10-table schema + stable hash IDs
│   │   └── graph_runtime.py       #   igraph: cycles, centrality, shortest path
│   ├── embeddings/                # Vectorization
│   │   ├── chunker.py             #   Code → chunks
│   │   ├── embedder.py            #   Chunks → vectors
│   │   └── vector_store.py        #   ChromaDB storage
│   ├── agent/                     # LangGraph chat agent
│   │   ├── graph.py               #   StateGraph + fallback parser
│   │   ├── tools.py               #   11 tool functions
│   │   └── prompts.py             #   System prompt
│   ├── rag/                       # RAG pipeline
│   │   ├── chain.py               #   ask() + ask_corrective() (corrective RAG)
│   │   ├── retrieval_quality.py   #   Distance-based chunk filtering (free)
│   │   ├── answer_grader.py       #   LLM answer quality grading
│   │   └── query_rewriter.py      #   LLM query reformulation
│   ├── review/                    # Code review pipeline
│   │   ├── engine.py              #   Main review orchestrator (run_review)
│   │   ├── report.py              #   Finding, ReviewReport, Severity, Category
│   │   ├── diff_parser.py         #   git diff → parsed DiffFile objects
│   │   ├── fix_applier.py         #   Apply approved fixes safely
│   │   ├── finding_store.py       #   Persist review findings
│   │   ├── session_store.py       #   Persist review sessions
│   │   ├── reviewers/             #   Pluggable reviewers (generic, security, …)
│   │   ├── pipeline/              #   Post-processing (cluster/rank/verify/summary)
│   │   └── renderers/             #   Output formatters (markdown/cli/api)
│   ├── api/                       # FastAPI REST
│   │   ├── main.py                #   35+ endpoints
│   │   ├── models.py              #   Pydantic schemas
│   │   ├── state.py               #   Singleton app state + restart resilience
│   │   └── cloud.py               #   Cloud mode (GitHub App + webhooks)
│   ├── voice/                     # Voice interface
│   │   ├── stt.py                 #   Mic recording + faster-whisper transcription
│   │   ├── tts.py                 #   edge-tts speech synthesis (thread-safe)
│   │   ├── router.py              #   LLM-based tool routing (via get_llm)
│   │   ├── backends.py            #   Tool execution bridge
│   │   └── companion.py           #   Standalone voice loop
│   ├── core/                      # Reusable LangGraph patterns
│   │   ├── reflect.py             #   Actor→Critic→Improve loop
│   │   ├── hitl.py                #   Human-in-the-loop interrupts
│   │   └── fanout.py              #   Parallel fan-out/fan-in graphs
│   ├── research/                  # Deep research mode
│   │   ├── deep_research.py       #   End-to-end deep research entry point
│   │   ├── researcher.py          #   Parallel search + synthesis
│   │   ├── planner.py             #   Decompose question into sub-questions
│   │   ├── synthesizer.py         #   Merge parallel findings into report
│   │   └── diagram_generator.py   #   Structured research architecture diagrams
│   ├── generation/                # Explanation / diagram generation
│   │   ├── overview_generator.py  #   Project overview text
│   │   ├── module_explainer.py    #   Module-level explanations
│   │   └── flow_generator.py      #   Execution flow diagrams
│   ├── doc_knowledge/             # Docs & guidelines indexing
│   │   ├── doc_parser.py          #   Parse .md/.pdf/.txt/.rst
│   │   └── doc_store.py           #   ChromaDB doc collection wrapper
│   ├── services/                  # Deterministic service wrappers
│   │   ├── search_service.py      #   retrieval pipeline wrapper
│   │   └── symbol_service.py      #   symbol lookup wrapper
│   ├── tools/                     # Agent / MCP tool implementations
│   │   ├── static_analysis.py     #   Static analysis runner
│   │   ├── test_runner.py         #   Test execution runner
│   │   └── tool_runner.py         #   Generic tool dispatch
│   ├── worker/                    # Background cloud indexing worker
│   │   ├── indexer.py             #   Poll Postgres jobs, build indexes
│   │   ├── github_app.py          #   GitHub App token retrieval
│   │   └── atomic_store.py        #   Atomic index directory swap
│   ├── eval/                      # Evaluation & benchmarking
│   │   ├── evaluator.py           #   RAGAS RAG evaluation
│   │   ├── experiments.py         #   A/B parameter sweeps
│   │   └── generate_multilang_review_fixtures.py # Review eval fixtures
│   ├── debug/                     # Development/debug utilities
│   │   └── fanout_agent.py        #   Fan-out graph experiments
│   ├── cli.py                     #   Command-line interface
│   └── mcp/                       # Model Context Protocol
│       └── server.py              #   43 MCP tools (stdio)
│
├── frontend/                      # Next.js 14 web UI
│   └── src/app/
│       ├── page.tsx               #   Home (analyze form)
│       ├── chat/page.tsx          #   AI chat interface
│       ├── overview/page.tsx      #   Project overview
│       ├── modules/page.tsx       #   Module browser
│       ├── modules/[name]/page.tsx#   Single module detail
│       ├── blast-radius/page.tsx  #   Change impact viewer
│       ├── reading-order/page.tsx #   Reading order viewer
│       ├── execution-flow/page.tsx#   Flow diagram viewer
│       ├── knowledge-graph/page.tsx#  Interactive graph explorer
│       ├── review/page.tsx        #   Code review (diff/file/guidelines)
│       ├── voice/page.tsx         #   Voice assistant (mic → transcribe → speak)
│       ├── admin/page.tsx         #   Admin dashboard
│       ├── architecture/page.tsx  #   Architecture health viewer
│       ├── docs/page.tsx          #   Team docs search & ask
│       ├── research/page.tsx      #   Deep research interface
│       └── incremental-reindex/   #   Smart reindex page
│           └── page.tsx
│
├── <target-repo>/.codewalk/
│   ├── chroma/                    # ChromaDB persistent storage (per repo)
│   ├── graph.duckdb               # DuckDB graph database (relationships)
│   ├── knowledge-graph.json       # Serialized knowledge graph entities/relationships
│   └── manifest.json              # Version tracking + index metadata (index_version, chunk_count)
│
├── deploy/                        # Production deployment
│   ├── Dockerfile                 #   Multi-stage Python 3.11 build
│   ├── docker-compose.yml         #   Postgres + API + Caddy orchestration
│   ├── Caddyfile                  #   Reverse proxy config (IP or domain mode)
│   ├── hetzner-setup.sh           #   One-click Hetzner VPS provisioning
│   ├── DEPLOY.md                  #   Full deployment guide
│   └── SERVER_OPS.md              #   Health, indexing, logs, SQL commands
│
├── .github/workflows/             # CI/CD
│   └── deploy.yml                 #   Build Docker image → GHCR → deploy
│
├── requirements.txt               # Python dependencies
├── codewalk.yaml                  # Per-repo config (branches, excludes)
├── env.example.txt                # Environment variable index
├── env.server.example.txt         # Hetzner server .env template
├── env.local.example.txt          # Local dev .env template
├── FULL_SETUP_GUIDE.md            # Complete cloud + MCP setup (step by step)
├── .env                           # Configuration (gitignored)
└── .vscode/mcp.json               # MCP server config
```

---

## 🔧 Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `ollama` | LLM backend: `ollama`, `openai`, `anthropic`, `gemini`, `groq`, `openrouter` |
| `LLM_MODEL` | `qwen3.5:27b` | Model name (must match provider) |
| `EMBEDDING_MODEL` | `jinaai/jina-code-embeddings-1.5b` | Sentence-transformer model for code embeddings |
| `GROQ_API_KEY` | — | Groq API key |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `ANTHROPIC_API_KEY` | — | Anthropic API key |
| `GOOGLE_API_KEY` | — | Google Gemini API key |
| `OPENROUTER_API_KEY` | — | OpenRouter API key |
| `POSTGRES_PASSWORD` | — | Postgres password (for Docker/server deployment) |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins (e.g. `https://yourdomain.com`) |
| `RATE_LIMIT_REQUESTS` | `60` | Max requests per IP per window |
| `RATE_LIMIT_WINDOW` | `60` | Rate limit window in seconds |
| `INDEX_STORAGE_PATH` | `/var/codewalk` | Path for ChromaDB/DuckDB data (Docker default) |
| `GITHUB_APP_ID` | — | GitHub App ID (server cloud mode) |
| `GITHUB_APP_PRIVATE_KEY_PATH` | — | PEM path inside container, e.g. `/var/codewalk/secrets/key.pem` |
| `GITHUB_WEBHOOK_SECRET` | — | Must match GitHub App webhook secret |
| `ADMIN_API_KEY` | — | `X-Admin-Key` header for `/admin/*` routes |
| `CODEWALK_SERVER_URL` | — | MCP: cloud API URL, e.g. `https://api.codewalk.xyz` |
| `CODEWALK_REPO_NAME` | — | MCP: `owner/repo` slug |
| `CODEWALK_REPO_TOKEN` | — | MCP: per-repo download token (`cw_repo_...`) |
| `CODEWALK_REPO_PATH` | — | Frontend dev mode: path to the target repo for filesystem API routes (`/api/knowledge-graph`, `/api/file-content`, `/api/diff-overlay`). Set automatically by `scripts/run-ui-for-repo.sh` |

---

## 🤖 Supported LLM Providers

| Provider | Set `LLM_PROVIDER=` | API Key | Notes |
|----------|---------------------|---------|-------|
| **Ollama** | `ollama` | None | Fully local, no internet. Run `ollama serve` first |
| **OpenAI** | `openai` | `OPENAI_API_KEY` | GPT models, etc. |
| **Anthropic** | `anthropic` | `ANTHROPIC_API_KEY` | Claude models |
| **Google Gemini** | `gemini` | `GOOGLE_API_KEY` | Gemini models |
| **Groq** | `groq` | `GROQ_API_KEY` | Groq models |
| **OpenRouter** | `openrouter` | `OPENROUTER_API_KEY` | Access to 100+ models |
| **DeepSeek** | `deepseek` | `DEEPSEEK_API_KEY` | DeepSeek V3, R1 models |

---

## 🧹 Clearing the Index (Reset ChromaDB)

To wipe all indexed data and start fresh, delete the `.codewalk/chroma/` directory inside the target repo:

```bash
# From the target repo root:
rm -rf .codewalk/chroma/
```

This removes all embedded chunks and collections. Next time you run `codewalk_analyze_codebase` (MCP) or `POST /analyze` (API), it will re-index from scratch.

> **When to do this:**
> - You switched to a different repo and want a clean index
> - Embeddings seem stale or corrupted
> - You changed the embedding model and need to re-embed everything
> - You want to use `index_mode: "full"` but it's still picking up old data

### Adding `.codewalk/` to `.gitignore`

Codewalk stores its index data inside each target repo at `.codewalk/` (ChromaDB embeddings, DuckDB graph, version metadata). This directory should **not** be committed to version control.

Add this to each target repo's `.gitignore`:

```gitignore
# Codewalk index (auto-generated)
.codewalk/
```

> This is only needed in the **target repo** you're analyzing, not in the codewalk repo itself.

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.10+, FastAPI, Uvicorn |
| **Agent** | LangGraph, LangChain |
| **Vector DB** | ChromaDB (persistent, per-repo at `.codewalk/chroma/`) |
| **Graph DB** | DuckDB (persistent, per-repo at `.codewalk/graph.duckdb`) — [Why DuckDB over SQLite?](docs/WHY_DUCKDB.md) |
| **Graph Runtime** | igraph (C-speed traversal, in-memory from DuckDB) |
| **Voice STT** | faster-whisper (local, small model, int8) |
| **Voice TTS** | edge-tts (free, en-US-AriaNeural) |
| **Voice Router** | User's configured LLM (via get_llm()) |
| **Embeddings** | Jina Code Embeddings 1.5B (768-dim, Ollama/MPS) |
| **Code Parsing** | Tree-sitter (15+ language grammars) |
| **Frontend** | Next.js 14, React 18, TypeScript 5 |
| **Styling** | Tailwind CSS, shadcn/ui, Kinetic design system (`kinetic-*` CSS tokens, `KineticShell`, `StatusBadge`, `SegmentedControl`) |
| **Diagrams** | D3 + ELK (execution flow / architecture), React Flow-style research diagram, custom SVG/canvas for knowledge graph |
| **MCP** | Model Context Protocol (stdio transport) |

---

## ⚠️ Known Limitations

### Single-repo state (no concurrent multi-repo)

Codewalk holds **one repo's state in memory at a time** (vector store, dependency graph, module map, repo path). Using more than one repo in the same process can override and corrupt that state — index files, cached graph handles, and the active repo path can end up pointing at the wrong workspace.

| Interface | Multi-repo behavior |
|-----------|-------------------|
| **MCP (stdio)** | ✅ **Safe *per connection*.** Each MCP connection spawns a separate Python process, so two repos in two editor windows are isolated. However, do **not** point the same running MCP server at multiple repos or switch workspaces rapidly within the same process — that will corrupt in-memory state. |
| **FastAPI (REST)** | ⚠️ **Not safe.** Two concurrent `/analyze` calls for different repos will race — whoever finishes last overwrites the shared globals. Only one repo at a time. |
| **Web UI** | ⚠️ **Same as REST.** The browser hits the FastAPI backend. Analyze one repo, explore it, then analyze another. Don't run two analyses in parallel from different browser tabs. |

**This is by design, not a bug.** Codewalk is optimized for the common case: one developer, one repo at a time. If you need concurrent multi-repo support on the API side, it would require a `dict[repo_path, SessionState]` architecture — contributions welcome.

> **MCP users:** You're already safe as long as each repo gets its own MCP server process (one VS Code window / Claude Code session / Cursor instance per repo). Do not route commands for different repos into the same stdio connection or reuse one server process across workspaces.

---

## 🤝 Contributing

1. **Fork** this repo
2. **Clone** your fork: `git clone https://github.com/<your-username>/codewalk.git`
3. **Create a branch**: `git checkout -b feat/my-feature`
4. **Make your changes** and test them
5. **Commit**: `git commit -m "feat: add my feature"`
6. **Push**: `git push origin feat/my-feature`
7. **Open a Pull Request** against `master`

> All contributions welcome — bug fixes, new language support, UI improvements, docs, anything.

> **Found a bug?** [Open an issue](https://github.com/gupta29470/codewalk/issues/new) with screenshots, error logs, or references — it helps us fix it faster.

---

## 📜 License

[MIT](LICENSE)

---

<p align="center">
  ⭐ If you find Codewalk useful, give it a star — it helps others discover it!
</p>

<p align="center">
  Built by <a href="https://github.com/gupta29470">gupta29470</a>
  <br>
  <a href="https://www.linkedin.com/in/aakash98gupta/">LinkedIn</a> · <a href="https://x.com/fosla98">Twitter/X</a>
</p>
