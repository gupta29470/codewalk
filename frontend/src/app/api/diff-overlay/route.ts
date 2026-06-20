import { readFile } from "fs/promises";
import { NextResponse } from "next/server";
import { existsSync } from "fs";
import { join, resolve } from "path";
import type { NextRequest } from "next/server";

function candidatePaths(repoPath?: string | null): string[] {
  if (repoPath) {
    return [join(repoPath, ".codewalk", "diff-overlay.json")];
  }
  const cwd = process.cwd();
  return [
    join(cwd, ".codewalk", "diff-overlay.json"),
    join(cwd, "..", ".codewalk", "diff-overlay.json"),
    resolve(cwd, "..", ".codewalk", "diff-overlay.json"),
  ];
}

export async function GET(request: NextRequest) {
  const repoPath = request.nextUrl.searchParams.get("repoPath");
  for (const filePath of candidatePaths(repoPath)) {
    if (!existsSync(filePath)) continue;
    try {
      const content = await readFile(filePath, "utf-8");
      return new NextResponse(content, { headers: { "Content-Type": "application/json" } });
    } catch {
      return NextResponse.json({ error: "Failed to read diff overlay" }, { status: 500 });
    }
  }
  return NextResponse.json(
    { changedNodeIds: [], affectedNodeIds: [] },
    { status: 404 },
  );
}
