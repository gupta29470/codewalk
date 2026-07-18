import { readFile } from "fs/promises";
import { NextResponse } from "next/server";
import { existsSync } from "fs";
import { dirname, basename, join, resolve } from "path";
import type { NextRequest } from "next/server";

const API_BASE = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function candidatePaths(repoPath?: string | null): string[] {
  // When a repoPath is explicitly provided (query param or env var), only look
  // in that repo. This prevents showing the wrong repo's graph when the target
  // repo isn't indexed yet, and lets the UI be launched from the Codewalk
  // checkout while reading indexes from the user's repo.
  const explicitRepo = repoPath || process.env.CODEWALK_REPO_PATH;
  if (explicitRepo) {
    return [join(explicitRepo, ".codewalk", "knowledge-graph.json")];
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

  // Local graph JSON is missing — ask the FastAPI backend to build it on-the-fly.
  try {
    const backendUrl = repoPath
      ? `${API_BASE}/knowledge-graph?repo_path=${encodeURIComponent(repoPath)}`
      : `${API_BASE}/knowledge-graph`;
    const backendRes = await fetch(backendUrl);
    if (!backendRes.ok) {
      const text = await backendRes.text();
      return NextResponse.json(
        {
          error: "Knowledge graph build failed",
          message: text || `Backend returned ${backendRes.status}`,
          details: errors.join("; ") || "No local graph and backend build failed.",
          repoPath: repoPath || undefined,
        },
        { status: backendRes.status },
      );
    }
    const graph = (await backendRes.json()) as Record<string, unknown>;
    return NextResponse.json(graph);
  } catch {
    return NextResponse.json(
      {
        error: "Knowledge graph not found",
        message:
          "No .codewalk/knowledge-graph.json found and the backend could not build it on-the-fly.",
        details: errors.join("; ") || "No candidate paths matched an existing file.",
        repoPath: repoPath || undefined,
      },
      { status: 404 },
    );
  }
}
