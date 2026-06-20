# Codewalk Web UI

Next.js frontend for the Codewalk codebase intelligence API (chat, overview, review flows).

## Getting Started

From this directory:

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). The app expects the Codewalk API at `http://localhost:8000` unless configured otherwise.

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

- [README.md](../README.md) — API endpoints, MCP tools (33), index flows (`full_index_parallel` on API; MCP uses `index_from_paths_parallel` locally)
- [FULL_SETUP_GUIDE.md](../FULL_SETUP_GUIDE.md) — cloud deploy + local MCP; Phase 8 § Step 10.6 for review approve UI

## Learn More

- [Next.js Documentation](https://nextjs.org/docs)
