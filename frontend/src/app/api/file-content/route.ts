import { readFile } from "fs/promises";
import { NextResponse } from "next/server";
import { existsSync } from "fs";
import { join, resolve } from "path";
import type { NextRequest } from "next/server";

export async function GET(request: NextRequest) {
  const path = request.nextUrl.searchParams.get("path");
  if (!path) {
    return NextResponse.json({ error: "Missing path" }, { status: 400 });
  }

  // Prefer an explicit repo path (query param or env var), otherwise fall back
  // to the legacy cwd-based discovery for the standard dev layout.
  const explicitRepo = request.nextUrl.searchParams.get("repoPath") || process.env.CODEWALK_REPO_PATH;
  const cwd = process.cwd();
  const repoRoot = explicitRepo ? resolve(explicitRepo) : resolve(cwd, "..");
  const candidates = explicitRepo
    ? [join(repoRoot, path), resolve(repoRoot, path)]
    : [join(cwd, "..", path), resolve(cwd, "..", path), join(cwd, path)];

  for (const filePath of candidates) {
    if (!filePath.startsWith(repoRoot) && !filePath.startsWith(cwd)) continue;
    if (!existsSync(filePath)) continue;
    try {
      const content = await readFile(filePath, "utf-8");
      return new NextResponse(content, { headers: { "Content-Type": "text/plain" } });
    } catch (error) {
      return NextResponse.json(
        { error: error instanceof Error ? error.message : String(error) },
        { status: 500 },
      );
    }
  }

  return NextResponse.json({ error: "File not found" }, { status: 404 });
}
