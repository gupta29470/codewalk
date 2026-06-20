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

  // Resolve relative to project root. Reject absolute paths outside repo.
  const cwd = process.cwd();
  const candidates = [join(cwd, "..", path), resolve(cwd, "..", path), join(cwd, path)];
  const repoRoot = resolve(cwd, "..");

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
