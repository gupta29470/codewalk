<p align="center">
  <h1 align="center">CODEWALK</h1>
  <p align="center">
    <strong>AI-powered codebase onboarding tool</strong><br>
    Point it at any repo → understand the entire codebase in hours, not weeks
  </p>
</p>

<p align="center">
  <a href="#-features">Features</a> •
  <a href="#-demo">Demo</a> •
  <a href="#%EF%B8%8F-setup">Setup</a> •
  <a href="#-usage">Usage</a> •
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

Four ways to use it:
| Interface | Best for |
|-----------|----------|
| **Web UI** (Next.js) | Visual exploration — diagrams, module browser, blast radius viewer |
| **MCP Server** | VS Code Copilot, Claude Code, Cursor, Codex — AI agents use tools directly |
| **REST API** | Scripts, CI/CD, custom integrations |

> **🎙️ Voice** is available via both **MCP** (`codewalk_voice_ask` + `codewalk_speak`) and **REST API** (`POST /voice/ask`) — ask questions by speaking, hear answers read aloud.

---

## Why Codewalk?

| Scenario | How Codewalk helps |
|----------|-------------------|
| **New dev joins the team** | Point Codewalk at the repo → get an overview, module map, and reading order. Self-onboard in hours instead of weeks of "hey, can you explain this?" |
| **LLM token costs are high** | Without RAG, the LLM needs your entire codebase in context — slow and expensive. Codewalk embeds code into a vector DB and retrieves only the relevant chunks per query. Faster answers, fraction of the tokens. |
| **Senior dev switches modules** | You know the auth module but now need to work on payments. Get module info, blast radius, and execution flow without bugging the payments team. |
| **Before a refactor** | Check blast radius before touching shared code. "If I change `base_model.py`, what breaks?" — get the answer before you break prod. |
| **PR reviews** | Run `codewalk_review_diff` or `POST /review` — automated multi-stage review with OWASP security checks, test coverage detection, blast radius warnings, and team guidelines matching. MCP mode leverages the calling model (Claude/GPT) directly — no separate LLM needed. |
| **Documentation is outdated** | Codewalk analyzes the *actual code*, not stale wiki pages. Always up to date. |

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🔍 **Module Detection** | Auto-groups files into packages/modules by directory structure |
| 🕸️ **Dependency Graph** | Parses imports across 15+ languages via tree-sitter |
| 💥 **Blast Radius** | BFS on reversed dependency graph → shows transitive impact of any change |
| 📖 **Reading Order** | Topological sort → "read config.py before embedder.py because embedder imports config" |
| 🔄 **Execution Flow** | Entry points, module/file dependency chains, Mermaid diagrams |
| 🤖 **AI Chat** | LangGraph agent with 7 tools, multi-turn conversation with memory |
| 🔎 **Semantic Search** | ChromaDB vector search on embedded code chunks (RAG) |
| 🔬 **Code Review** | Multi-stage review pipeline: test coverage, blast radius, guidelines RAG, context-enriched deep analysis |
| 🔄 **Incremental Reindex** | Content hash comparison — only re-embeds changed files, skips unchanged |
| 🧩 **MCP Server** | 20 tools for VS Code Copilot / Claude Code / Cursor / Codex |
| 🎙️ **Voice Interface** | Talk to your codebase — mic recording, local STT (faster-whisper), Copilot-driven routing (MCP) / Ollama routing (API), TTS response |
| 🔬 **Graph Intelligence** | DuckDB persistent graph + igraph C-speed traversal: cycle detection, centrality, import chain tracing |
| 🧬 **Corrective RAG** | Distance-based chunk filtering (free) + LLM answer grading + query rewriting for reliable answers |
| 📦 **Parent-Child Chunking** | Full functions stored as parents, sub-chunks searched — retrieve complete context on match |
| ⚡ **Parallel Embedding** | Producer-consumer pipeline — CPU chunking overlaps with GPU embedding |
| 🏗️ **Multi-Provider LLM** | Ollama (local), OpenAI, Anthropic, Groq, Gemini, OpenRouter |
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

## ⚙️ Setup

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

Create a `.env` file in the project root:

```env
# ─── LLM Configuration ──────────────────────────────────────
# Provider: ollama | openai | anthropic | gemini | groq | openrouter
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5-coder:7b

# ─── Embeddings ──────────────────────────────────────────────
EMBEDDING_MODEL=jinaai/jina-code-embeddings-1.5b

# ─── Repository to Analyze ──────────────────────────────────
# Relative path (self-analysis): src/codewalk
# Absolute path (any repo):      /Users/you/projects/my-app/src
REPO_PATH=src/codewalk

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

Open **http://localhost:3000** → enter a repo path → click **Analyze Codebase**.

Then explore:
- **Overview** — tech stack, modules, dependency diagram, riskiest files
- **Modules** — browse all modules, click one for file list + dependencies
- **Blast Radius** — which files break if you change each file
- **Reading Order** — optimal file reading sequence with risk levels
- **Execution Flow** — Mermaid diagram of module/file dependencies
- **Chat** — ask any question ("explain the authentication flow", "what does scanner.py do?")
- **Code Review** — review git diffs, review single files, load team guidelines
- **Voice** — click the mic, ask a question by speaking, hear the answer read aloud
- **Smart Reindex** — incremental re-embed with stats (skipped, changed, deleted)

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
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"repo_path": "/path/to/your/repo", "index_mode": "auto"}'
```

**Step 2 — Explore the results:**
```bash
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
  -d '{"staged": false, "target_branch": "main"}'
```

See [API Reference](#-api-reference) for full request/response details on every endpoint.

---

## 🔌 MCP Integration

Codewalk runs as an MCP (Model Context Protocol) server, so any AI agent that speaks MCP can use it.

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
7. Open Copilot Chat → type **`@codewalk`** → all 20 tools are available

   ![MCP tools list](assets/mcp-tools-list.png)


### VS Code Copilot

Add to `.vscode/mcp.json` in your desired project:

> ⚠️ **Replace `/path/to/codewalk`** with the actual absolute path where you cloned codewalk.

```json
{
  "servers": {
    "codewalk": {
      "command": "/path/to/codewalk/.codewalk-env/bin/python",
      "args": ["-m", "src.codewalk.mcp.server"],
      "cwd": "/path/to/codewalk",
      "env": {
        "REPO_PATH": "${workspaceFolder}",
        "EXCLUDE_PATHS": ""
      }
    }
  }
}
```

> **`EXCLUDE_PATHS`** — comma-separated list of paths/patterns to skip during scanning. Example: `"tests,docs,scripts/legacy,*.generated.*"`

> **Customizing file filters:** Codewalk ships with a built-in skip list (binary files, lock files, `node_modules/`, etc.). If you want to **remove** a predefined skip rule (e.g., to index `.md` or `.css` files), edit [`src/codewalk/ingestion/file_filter.py`](src/codewalk/ingestion/file_filter.py).

Then in Copilot Chat: **`@codewalk`** → follow the scan → filter → index workflow.

> **Note:** After adding or modifying `.vscode/mcp.json`, reload the VS Code window: **`Cmd+Shift+P`** → **`Developer: Reload Window`**.

### Claude Code

Add to `~/.claude/mcp.json`:

```json
{
  "mcpServers": {
    "codewalk": {
      "command": "python",
      "args": ["-m", "src.codewalk.mcp.server"],
      "cwd": "/path/to/codewalk",
      "env": {
        "REPO_PATH": "/path/to/target/repo",
        "EXCLUDE_PATHS": ""
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
    "command": "python",
    "args": ["-m", "src.codewalk.mcp.server"],
    "cwd": "/path/to/codewalk",
    "env": {
      "REPO_PATH": "/path/to/target/repo",
      "EXCLUDE_PATHS": ""
    }
  }
}
```

### OpenAI Codex CLI

Add to `~/.codex/mcp.json`:

```json
{
  "mcpServers": {
    "codewalk": {
      "command": "python",
      "args": ["-m", "src.codewalk.mcp.server"],
      "cwd": "/path/to/codewalk",
      "env": {
        "REPO_PATH": "/path/to/target/repo",
        "EXCLUDE_PATHS": ""
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
│  Step 1                                                             │
│  codewalk_analyze_codebase                                          │
│       │  scans files, builds dependency graph, detects modules      │
│       ▼                                                             │
│  Step 2                                                             │
│  codewalk_scan_files(batch=1)                                       │
│       │  returns ~100 file paths for review                         │
│       ▼                                                             │
│  Step 3                                                             │
│  codewalk_submit_filtered_files(paths=[...])                        │
│       │  submit relevant source files from this batch               │
│       ▼                                                             │
│  ┌─── More batches? ───┐                                            │
│  │ YES                 │ NO                                         │
│  │ Go to Step 2        │                                            │
│  │ (batch=2, 3, ...)   ▼                                            │
│  └─────────────┐  Step 4                                            │
│                │  codewalk_index_filtered_files                      │
│                │       │  chunks + embeds all submitted files        │
│                │       ▼                                             │
│                │  ✅ READY — all query tools unlocked                │
│                └────────────────────────────────────────             │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                   QUERY TOOLS (use after setup)                     │
│                                                                     │
│  codewalk_get_overview          → project summary + dependency flow │
│  codewalk_search_codebase       → semantic code search              │
│  codewalk_get_module_info       → inspect a specific module         │
│  codewalk_explain_function      → AI-powered function explanation   │
│  codewalk_get_blast_radius_map  → change risk analysis              │
│  codewalk_get_reading_order     → optimal file reading sequence     │
│  codewalk_get_execution_flow    → module/file dependency flow       │
│  codewalk_get_architecture_health → bottlenecks, cycles, key files  │
│  call_chain(source, target)     → trace import path between files   │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                 MAINTENANCE (after code changes)                    │
│                                                                     │
│  codewalk_incremental_reindex   → re-embed only changed files       │
│  codewalk_refresh_analysis      → re-scan without re-embedding      │
│  codewalk_review_diff           → review git diff (context + checks) │
│  codewalk_review_file           → review file vs codebase patterns  │
│  codewalk_load_guidelines       → load team coding standards        │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                      VOICE (hands-free)                            │
│                                                                     │
│  MCP:  codewalk_voice_ask  → mic → transcribe                       │
│        Copilot picks tool  → calls it → codewalk_speak(summary)     │
│                                                                     │
│  API:  POST /voice/ask     → mic → transcribe → Ollama route        │
│        execute_direct()    → format_voice_response() → MP3          │
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
1. The AI calls `codewalk_analyze_codebase` → scans all files, detects modules, builds the dependency graph
2. The AI calls `codewalk_scan_files(batch=1)` → gets a batch of file paths
3. The AI reviews the paths — keeps source code (`.py`, `.ts`, `.js`), skips junk (`node_modules/`, `__pycache__/`, test files, images)
4. The AI calls `codewalk_submit_filtered_files(file_paths=[...])` → submits the good files
5. Steps 2-4 repeat for each batch until all files are processed
6. The AI calls `codewalk_index_filtered_files` → embeds everything into the vector database

**You'll see progress like:**
```
✓ Codebase analyzed — 142 files, 5 modules detected
✓ Scanning batch 1 of 2... submitted 87 source files
✓ Scanning batch 2 of 2... submitted 34 source files (LAST BATCH)
✓ Indexed 121 files → 380 chunks embedded

Ready! You can now use these tools:
  - codewalk_get_overview (if LLM didn't call — run manually for project summary)
  - codewalk_search_codebase (if LLM didn't call — search code by concept)
  - codewalk_get_module_info (if LLM didn't call — inspect a specific module)
  - codewalk_explain_function (if LLM didn't call — explain any function/class)
  - codewalk_get_blast_radius_map (if LLM didn't call — check change risk)
  - codewalk_get_reading_order (if LLM didn't call — optimal file reading order)
  - codewalk_get_execution_flow (if LLM didn't call — dependency flow diagram)
```

> **Note:** After indexing, the AI agent should automatically call these tools. If it doesn't, you can invoke them manually — the hints above tell you exactly which tools to run.

> **Note:** This only happens once. Next time you say `@codewalk analyze this codebase`, it detects the existing index and skips straight to "ready."

### ⚠️ If the AI Stops Mid-Workflow

Some LLMs stop after one tool call instead of continuing the full workflow. **Each tool's output tells you exactly what to call next.** If the AI stops, just call the next tool yourself:

| AI stopped after... | You call next |
|---|---|
| `codewalk_analyze_codebase` | `codewalk_scan_files(batch=1)` |
| `codewalk_scan_files` | `codewalk_submit_filtered_files` with the listed paths |
| `codewalk_submit_filtered_files` | `codewalk_scan_files(batch=<next>)` or `codewalk_index_filtered_files` if last batch |
| `codewalk_index_filtered_files` | Any query tool — `codewalk_get_overview`, `codewalk_search_codebase`, etc. |

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

**When to use:** You have a question about a concept ("error handling", "file upload", "caching") and don't know which files to look at.

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

**Tool:** `codewalk_review_diff` — optional: `staged=true`, `target_branch="main"`

You're about to push a PR and want an automated code review.

```
@codewalk review my changes
or
@codewalk_review_diff
@codewalk_review_diff staged=true target_branch="main"
```

**When to use:** Before pushing a PR. Catches security vulnerabilities (OWASP), bugs, missing test coverage, and style issues. In MCP mode, Copilot performs the review directly using enriched context (full file contents, dependency graph, vector store patterns) — no local LLM overhead, instant results.

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

**When to use:** Once per project. After loading, `codewalk_review_diff` automatically checks code against your team's standards.

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

> **Note:** Routing is done by Copilot (full LLM), not a separate model — no Ollama required for MCP voice. The REST API (`POST /voice/ask`) uses Ollama routing for the web UI where Copilot isn't available.

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

**Tool:** `call_chain(source, target)` — two file names

You want to trace the import chain between two files — "how does a change in config.py eventually affect server.py?"

```
@codewalk trace the import chain from config.py to server.py
or
@call_chain config.py server.py
```

**Returns:** Shortest import path with hop count and full file paths.

**When to use:** Understanding how changes propagate, debugging import issues, or tracing dependency chains.

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
| Review git diff | `@codewalk review my changes` or `@codewalk_review_diff` |
| Review a file | `@codewalk review src/auth.py` or `@codewalk_review_file src/auth.py` |
| Load guidelines | `@codewalk load guidelines from docs/` or `@codewalk_load_guidelines docs/` |
| Architecture health | `@codewalk check architecture health` or `@codewalk_get_architecture_health` |
| Trace import chain | `@codewalk trace chain from config.py to server.py` or `@call_chain config.py server.py` |
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
    "repo_path": "/Users/you/projects/my-app",
    "collection_name": "",
    "index_mode": "auto"
  }'
```

**Response:**
```json
{
  "status": "success",
  "repo_path": "/Users/you/projects/my-app",
  "files_scanned": 142,
  "chunks_created": 380,
  "modules": ["api", "auth", "models", "utils", "frontend"]
}
```

- `index_mode`: `"auto"` (skip if indexed), `"reindex"` (smart update), `"full"` (wipe & rebuild)
- `collection_name`: leave empty — auto-derived from repo path (e.g. `my-app`)

#### `POST /analyze/stream` — Index with live progress (SSE)

```bash
curl -N -X POST http://localhost:8000/analyze/stream \
  -H "Content-Type: application/json" \
  -d '{"repo_path": "/Users/you/projects/my-app", "index_mode": "auto"}'
```

**Response (Server-Sent Events):**
```
data: {"step": "scan", "message": "Scanning files..."}
data: {"step": "scan", "message": "Found 142 files"}
data: {"step": "deps", "message": "Building dependency graph..."}
data: {"step": "modules", "message": "Detected 5 modules"}
data: {"step": "embed", "message": "Embedding 142 files → 380 chunks"}
data: {"step": "done", "message": "Analysis complete!"}
```

#### `POST /refresh` — Re-scan without re-embedding

```bash
curl -X POST http://localhost:8000/refresh
```

**Response:**
```json
{
  "status": "refreshed",
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
  "diagram": "graph TD\n    api --> auth\n    api --> models\n    auth --> models",
  "overview_text": "## Project Overview\nTech stack: Python, FastAPI...",
  "riskiest_files": [
    {"file": "models/base.py", "risk_level": "high", "affected_files": 23}
  ]
}
```

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

---

### Review Endpoints

#### `POST /review` — Review git diff

```bash
curl -X POST http://localhost:8000/review \
  -H "Content-Type: application/json" \
  -d '{"staged": false, "target_branch": "main"}'
```

**Response:**
```json
{
  "issues": [
    {
      "severity": "critical",
      "category": "security",
      "file_path": "src/auth/jwt.py",
      "line_number": 42,
      "title": "JWT secret hardcoded",
      "explanation": "The JWT signing secret is hardcoded in the source file.",
      "suggestion": "Move the secret to an environment variable.",
      "code_snippet": "SECRET = 'my-secret-key'"
    }
  ],
  "summary": "Found 1 critical issue in 3 files (+45 / -12 lines)",
  "files_reviewed": 3,
  "lines_added": 45,
  "lines_removed": 12
}
```

- `staged`: If `true`, review only staged changes (`--staged`). Default: `false`.
- `target_branch`: Diff against a branch (e.g. `"main"` for full PR review). Default: `null` (unstaged changes).

#### `POST /review/file` — Review a single file

```bash
curl -X POST http://localhost:8000/review/file \
  -H "Content-Type: application/json" \
  -d '{"file_path": "src/codewalk/pipeline.py"}'
```

**Response:**
```json
{
  "review": "## File Review: pipeline.py\n\n### Consistency...\n",
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

Upload an audio file (webm/mp3/wav from browser mic). Codewalk transcribes it, routes to the right tool, executes it, and returns both the text answer and a spoken MP3 response.

```bash
curl -X POST http://localhost:8000/voice/ask \
  -F "audio=@question.webm" \
  -F "thread_id=voice"
```

**Response:**
```json
{
  "question": "what does the auth module do?",
  "tool": "codewalk_get_module_info",
  "answer": "The auth module contains 5 files handling JWT validation...",
  "speech": "The auth module handles JWT validation and permissions.",
  "audio_base64": "SUQzBAAAAAAAI1RTU0UAAAA..."
}
```

- `audio` *(required)*: Audio file upload (webm, mp3, wav)
- `thread_id` *(optional)*: Conversation thread ID. Default: `"voice"`
- `audio_base64`: Base64-encoded MP3 of the spoken answer — decode and play in the browser

**Pipeline:** audio upload → faster-whisper STT → LLM router → tool execution → summarize → edge-tts → MP3 response

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
│   ┌─ 7 Agent Tools ──────────────────────────────┐       │
│   │ search_codebase    get_overview              │       │
│   │ get_module_info    get_blast_radius_map       │       │
│   │ explain_function   get_reading_order          │       │
│   │                    get_execution_flow         │       │
│   └──────────────────────────────────────────────┘       │
├──────────────────────────────────────────────────────────┤
│                    ANALYSIS LAYER                         │
│                                                          │
│   scanner.py ──► dependency_graph.py ──► module_detector │
│                         │                                │
│                         ▼                                │
│   blast_radius.py   reading_order.py   code_parser.py    │
│   (BFS reverse       (topological      (tree-sitter      │
│    graph)             sort)              15+ langs)       │
├──────────────────────────────────────────────────────────┤
│                    GRAPH LAYER                           │
│                                                          │
│   graph/store.py ──► graph/runtime.py                    │
│   (DuckDB 7-table     (igraph C-speed                    │
│    persistent          traversal: cycles,                 │
│    graph)              centrality, paths)                 │
│                                                          │
│   .codewalk/graph.duckdb  ◄── files, imports, symbols,   │
│                               symbol_calls, chunks,       │
│                               modules, module_deps        │
├──────────────────────────────────────────────────────────┤
│                    REVIEW LAYER                          │
│                                                          │
│   diff_parser.py → test_coverage.py → reviewer.py        │
│   (git diff         (11-lang test       (8-step          │
│    parsing)          detection)          pipeline)        │
│                                                          │
│   guidelines_loader.py → review_prompts.py               │
│   (team standards       (OWASP security                  │
│    RAG search)           checklist)                       │
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
│   │  sounddevice   faster-whisper   qwen2.5:1.5b     │    │
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
│   → 🧠 route (qwen2.5:1.5b picks the right tool + args) │
│   → ⚙️ execute tool                                      │
│   → 🔇 admin tool? → text result only (silent)           │
│   → 🔊 content tool? → main LLM → speech → edge-tts     │
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
│   │   ├── graph_store.py         #   DuckDB 7-table schema + stable hash IDs
│   │   └── graph_runtime.py       #   igraph: cycles, centrality, shortest path
│   ├── embeddings/                # Vectorization
│   │   ├── chunker.py             #   Code → chunks
│   │   ├── embedder.py            #   Chunks → vectors
│   │   └── vector_store.py        #   ChromaDB storage
│   ├── agent/                     # LangGraph chat agent
│   │   ├── graph.py               #   StateGraph + fallback parser
│   │   ├── tools.py               #   7 tool functions
│   │   └── prompts.py             #   System prompt
│   ├── rag/                       # RAG pipeline
│   │   ├── chain.py               #   ask() + ask_corrective() (corrective RAG)
│   │   ├── retrieval_quality.py   #   Distance-based chunk filtering (free)
│   │   ├── answer_grader.py       #   LLM answer quality grading
│   │   └── query_rewriter.py      #   LLM query reformulation
│   ├── review/                    # Code review pipeline
│   │   ├── models.py              #   Issue, ReviewResult, Severity, Category
│   │   ├── diff_parser.py         #   git diff → parsed DiffFile objects
│   │   ├── test_coverage.py       #   Missing test detection (11 languages)
│   │   ├── guidelines_loader.py   #   Load team coding standards (RAG)
│   │   ├── review_prompts.py      #   System + user prompts (OWASP checklist)
│   │   └── reviewer.py            #   8-step review pipeline orchestrator
│   ├── api/                       # FastAPI REST
│   │   ├── main.py                #   18 endpoints
│   │   ├── models.py              #   Pydantic schemas
│   │   └── state.py               #   Singleton app state
│   ├── voice/                     # Voice interface
│   │   ├── stt.py                 #   Mic recording + faster-whisper transcription
│   │   ├── tts.py                 #   edge-tts speech synthesis (thread-safe)
│   │   ├── router.py              #   LLM-based tool routing (qwen2.5:1.5b)
│   │   ├── backends.py            #   Tool execution bridge
│   │   └── companion.py           #   Standalone voice loop
│   └── mcp/                       # Model Context Protocol
│       └── server.py              #   20 MCP tools (stdio)
│
├── frontend/                      # Next.js 14 web UI
│   └── src/app/
│       ├── page.tsx               #   Home (analyze form)
│       ├── chat/page.tsx          #   AI chat interface
│       ├── overview/page.tsx      #   Project overview
│       ├── modules/page.tsx       #   Module browser
│       ├── module/page.tsx        #   Single module detail
│       ├── blast-radius/page.tsx  #   Change impact viewer
│       ├── reading-order/page.tsx #   Reading order viewer
│       ├── execution-flow/page.tsx#   Flow diagram viewer
│       ├── review/page.tsx        #   Code review (diff/file/guidelines)
│       ├── voice/page.tsx         #   Voice assistant (mic → transcribe → speak)
│       └── incremental-reindex/   #   Smart reindex page
│           └── page.tsx
│
├── <target-repo>/.codewalk/
│   ├── chroma/                    # ChromaDB persistent storage (per repo)
│   ├── graph.duckdb               # DuckDB graph database (relationships)
│   └── meta.json                  # Version tracking + index metadata
│
├── requirements.txt               # Python dependencies
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
| `REPO_PATH` | `src/codewalk` | Default repository path to analyze |
| `EXCLUDE_PATHS` | — | Comma-separated paths to exclude from scanning (e.g. `tests,docs,*.generated.*`) |
| `USE_LLM_FILTER` | `true` | `true` = LLM decides which files to embed (smarter, slower). `false` = pattern matching only (faster) |
| `GROQ_API_KEY` | — | Groq API key |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `ANTHROPIC_API_KEY` | — | Anthropic API key |
| `GOOGLE_API_KEY` | — | Google Gemini API key |
| `OPENROUTER_API_KEY` | — | OpenRouter API key |
| `REVIEW_GUIDELINES_PATH` | — | Path to directory with team coding guidelines (.md, .txt, .rst) |

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
| **Graph DB** | DuckDB (persistent, per-repo at `.codewalk/graph.duckdb`) |
| **Graph Runtime** | igraph (C-speed traversal, in-memory from DuckDB) |
| **Voice STT** | faster-whisper (local, small model, int8) |
| **Voice TTS** | edge-tts (free, en-US-AriaNeural) |
| **Voice Router** | Ollama qwen2.5:1.5b  (local, ~300MB) |
| **Embeddings** | Jina Code Embeddings 1.5B (1536-dim, MPS/CUDA) |
| **Code Parsing** | Tree-sitter (15+ language grammars) |
| **Frontend** | Next.js 14, React 18, TypeScript 5 |
| **Styling** | Tailwind CSS, shadcn/ui |
| **Diagrams** | Mermaid.js |
| **MCP** | Model Context Protocol (stdio transport) |

---

## ⚠️ Known Limitations

### Single-repo state (no concurrent multi-repo)

Codewalk holds **one repo's state in memory at a time** (vector store, dependency graph, module map, repo path). This means:

| Interface | Multi-repo behavior |
|-----------|-------------------|
| **MCP (stdio)** | ✅ **Safe.** Each MCP connection spawns a separate Python process. Two repos = two processes = completely isolated memory. No conflicts. |
| **FastAPI (REST)** | ⚠️ **Not safe.** Two concurrent `/analyze` calls for different repos will race — whoever finishes last overwrites the shared globals. Only one repo at a time. |
| **Web UI** | ⚠️ **Same as REST.** The browser hits the FastAPI backend. Analyze one repo, explore it, then analyze another. Don't run two analyses in parallel from different browser tabs. |

**This is by design, not a bug.** Codewalk is optimized for the common case: one developer, one repo at a time. If you need concurrent multi-repo support on the API side, it would require a `dict[repo_path, SessionState]` architecture — contributions welcome.

> **MCP users:** You're already safe. Each VS Code window / Claude Code session / Cursor instance gets its own MCP server process via stdio transport. Analyze as many repos as you want across different windows — they never share state.

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
