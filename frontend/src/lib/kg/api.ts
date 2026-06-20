import type { KnowledgeGraph } from "./types";
import { validateGraph } from "./validate";

export interface LoadGraphResult {
  graph?: KnowledgeGraph;
  issues: { level: "auto-corrected" | "dropped" | "warning"; message: string }[];
  error?: string;
}

export async function loadKnowledgeGraph(repoPath?: string): Promise<LoadGraphResult> {
  const url = repoPath
    ? `/api/knowledge-graph?repoPath=${encodeURIComponent(repoPath)}`
    : "/api/knowledge-graph";

  try {
    const res = await fetch(url);
    if (!res.ok) {
      const text = await res.text();
      return { issues: [], error: text || `Failed to load graph (${res.status})` };
    }
    const data = (await res.json()) as unknown;
    const result = validateGraph(data);
    if (!result.success) {
      return { issues: result.issues, error: result.fatal };
    }
    return { graph: result.graph, issues: result.issues };
  } catch (err) {
    return {
      issues: [],
      error: err instanceof Error ? err.message : String(err),
    };
  }
}
