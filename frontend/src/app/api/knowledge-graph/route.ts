import { readFile } from "fs/promises";
import { NextResponse } from "next/server";
import { existsSync } from "fs";
import { dirname, basename, join, resolve } from "path";
import type { NextRequest } from "next/server";

function candidatePaths(repoPath?: string | null): string[] {
  // When a repoPath is explicitly provided, only look in that repo.
  // This prevents showing the wrong repo's graph when the target repo isn't indexed yet.
  if (repoPath) {
    return [join(repoPath, ".codewalk", "knowledge-graph.json")];
  }

  const cwd = process.cwd();
  const candidates: string[] = [
    join(cwd, ".codewalk", "knowledge-graph.json"),
    // When running `npm run dev` from inside frontend/, check the parent repo root.
    join(cwd, "..", ".codewalk", "knowledge-graph.json"),
    resolve(cwd, "..", ".codewalk", "knowledge-graph.json"),
  ];

  return candidates;
}

function deriveRepo(filePath: string): { name: string; repoPath: string } {
  // The repo root is the directory that contains the .codewalk folder.
  const repoRoot = dirname(dirname(filePath));
  return { name: basename(repoRoot), repoPath: repoRoot };
}

export async function GET(request: NextRequest) {
  const repoPath = request.nextUrl.searchParams.get("repoPath");
  const errors: string[] = [];

  for (const filePath of candidatePaths(repoPath)) {
    if (!existsSync(filePath)) continue;
    try {
      const content = await readFile(filePath, "utf-8");
      const graph = JSON.parse(content) as Record<string, unknown>;
      const project = (graph.project as Record<string, unknown> | undefined) ?? {};
      // Use the actual repo directory as the source of truth so the UI can detect
      // which repo is being displayed even when the bundle is cached.
      const repo = deriveRepo(filePath);
      project.name = repo.name;
      project.repoPath = repo.repoPath;
      graph.project = project;
      return NextResponse.json(graph);
    } catch (error) {
      errors.push(`${filePath}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  return NextResponse.json(
    {
      error: "Knowledge graph not found",
      message:
        "No .codewalk/knowledge-graph.json found. Run `codewalk analyze` locally, or run `@codewalk analyze this codebase` in MCP/cloud if you have cloud indexing set up.",
      details: errors.join("; ") || "No candidate paths matched an existing file.",
      repoPath: repoPath || undefined,
    },
    { status: 404 },
  );
}
