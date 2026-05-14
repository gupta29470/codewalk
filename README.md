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
  <a href="#-architecture">Architecture</a>
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

Three ways to use it:
| Interface | Best for |
|-----------|----------|
| **Web UI** (Next.js) | Visual exploration — diagrams, module browser, blast radius viewer |
| **MCP Server** | VS Code Copilot, Claude Code, Cursor, Codex — AI agents use tools directly |
| **REST API** | Scripts, CI/CD, custom integrations |

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
| 🧩 **MCP Server** | 12 tools for VS Code Copilot / Claude Code / Cursor / Codex |
| ⚡ **Parallel Embedding** | Producer-consumer pipeline — CPU chunking overlaps with GPU embedding |
| 🏗️ **Multi-Provider LLM** | Ollama (local), OpenAI, Anthropic, Groq, Gemini, OpenRouter |
| 🌐 **15+ Languages** | Python, JS, TS, Java, Go, Rust, Ruby, PHP, C#, C++, C, Dart, Kotlin, Swift, YAML |

---

## 🎬 Demo

### Web UI

<!-- Replace with your video -->
> 🎥 **[Video coming soon]**

### MCP with VS Code Copilot

<!-- Replace with your video -->
> 🎥 **[Video coming soon]**

### REST API

<!-- Replace with your video -->
> 🎥 **[Video coming soon]**

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
```

See [API Reference](#-api-reference) for full request/response details on every endpoint.

---

## 🔌 MCP Integration

Codewalk runs as an MCP (Model Context Protocol) server, so any AI agent that speaks MCP can use it.

### Starting the MCP Server in VS Code

1. Open VS Code in the codewalk project
2. Press **`Cmd+Shift+P`** (macOS) or **`Ctrl+Shift+P`** (Windows/Linux)
3. Type **`MCP: List Servers`** and select it
4. You'll see **`codewalk`** in the list
5. Click **Start Server** next to codewalk
6. The server starts in the background (stdio transport)
7. Open Copilot Chat → type **`@codewalk`** → start using the tools


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
        "REPO_PATH": "${workspaceFolder}"
      }
    }
  }
}
```

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
        "REPO_PATH": "/path/to/target/repo"
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
      "REPO_PATH": "/path/to/target/repo"
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
        "REPO_PATH": "/path/to/target/repo"
      }
    }
  }
}
```

### How It Works (First-Time Setup)

The **first time** you use Codewalk on a new codebase, it needs to index the files.  
You just tell the AI to analyze — **the AI handles the rest automatically**.

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

Ready! You can now ask me anything about this codebase.
```

> **Note:** This only happens once. Next time you say `@codewalk analyze this codebase`, it detects the existing index and skips straight to "ready."

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
│   ├── Reading Order            │             │          │
│   ├── Execution Flow           │             │          │
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
│                   EMBEDDING LAYER                        │
│                                                          │
│   chunker.py ──► embedder.py ──► vector_store.py         │
│   (smart code     (Jina 1.5B     (ChromaDB               │
│    chunks)         MPS/CUDA)      persistent)             │
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
│   ├── embeddings/                # Vectorization
│   │   ├── chunker.py             #   Code → chunks
│   │   ├── embedder.py            #   Chunks → vectors
│   │   └── vector_store.py        #   ChromaDB storage
│   ├── agent/                     # LangGraph chat agent
│   │   ├── graph.py               #   StateGraph + fallback parser
│   │   ├── tools.py               #   7 tool functions
│   │   └── prompts.py             #   System prompt
│   ├── api/                       # FastAPI REST
│   │   ├── main.py                #   12 endpoints
│   │   ├── models.py              #   Pydantic schemas
│   │   └── state.py               #   Singleton app state
│   └── mcp/                       # Model Context Protocol
│       └── server.py              #   12 MCP tools (stdio)
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
│       └── execution-flow/page.tsx#   Flow diagram viewer
│
├── data/
│   └── chroma/                    # ChromaDB persistent storage
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
| `GROQ_API_KEY` | — | Groq API key |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `ANTHROPIC_API_KEY` | — | Anthropic API key |
| `GOOGLE_API_KEY` | — | Google Gemini API key |
| `OPENROUTER_API_KEY` | — | OpenRouter API key |

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

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.10+, FastAPI, Uvicorn |
| **Agent** | LangGraph, LangChain |
| **Vector DB** | ChromaDB (persistent, local) |
| **Embeddings** | Jina Code Embeddings 1.5B (1536-dim, MPS/CUDA) |
| **Code Parsing** | Tree-sitter (15+ language grammars) |
| **Frontend** | Next.js 14, React 18, TypeScript 5 |
| **Styling** | Tailwind CSS, shadcn/ui |
| **Diagrams** | Mermaid.js |
| **MCP** | Model Context Protocol (stdio transport) |

---

## 📜 License

MIT

---

<p align="center">
  Built by <a href="https://github.com/gupta29470">gupta29470</a>
</p>
