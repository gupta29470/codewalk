# Codewalk Web UI

Next.js frontend for the Codewalk codebase intelligence API (chat, overview, review flows).

## Getting Started

### Running the UI against a target repo

The easiest way to use the frontend is from the repo you want to explore:

```bash
# From the target repo
/path/to/codewalk/scripts/run-ui-for-repo.sh
```

This kills any process on ports `8000` and `3000`, starts the Codewalk API from the target repo (discovering `codewalk.yaml`), starts the Next.js frontend from the Codewalk checkout, and sets `CODEWALK_REPO_PATH` automatically. Then open [http://localhost:3000](http://localhost:3000).

You can override ports:

```bash
CODEWALK_API_PORT=8001 CODEWALK_FRONTEND_PORT=3001 /path/to/codewalk/scripts/run-ui-for-repo.sh
```

### Frontend-only development

From this directory:

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). The app expects the Codewalk API at `http://localhost:8000` unless configured otherwise.

To point the filesystem API routes (`/api/knowledge-graph`, `/api/file-content`, `/api/diff-overlay`) at a repo outside the Codewalk checkout:

```bash
CODEWALK_REPO_PATH=/path/to/target/repo npm run dev
```

### Restarting after code changes

If you see stale chunk 404s or client-side exceptions, start fresh with a clean build cache:

```bash
npm run dev:clean      # clears .next and restarts dev
npm run restart        # kills port 3000 and restarts dev
```

Or from the project root:

```bash
./scripts/restart-frontend.sh
```

## Related docs

- [README.md](../README.md) — API endpoints, MCP tools (42), index flows (`full_index_parallel` on API; MCP uses `index_from_paths_parallel` locally)
- [FULL_SETUP_GUIDE.md](../FULL_SETUP_GUIDE.md) — cloud deploy + local MCP; Phase 8 § Step 10.6 for review approve UI

## Learn More

- [Next.js Documentation](https://nextjs.org/docs)
