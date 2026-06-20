import type { GraphNode, KnowledgeGraph } from "../types";

export interface FuzzyResult {
  nodeId: string;
  score: number;
}

function normalize(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function scoreNode(query: string, node: GraphNode): number {
  const q = normalize(query);
  if (!q) return 0;

  const name = normalize(node.name);
  const summary = normalize(node.summary);
  const tags = node.tags.map(normalize).join(" ");
  const filePath = node.filePath ? normalize(node.filePath) : "";

  if (name === q) return 1;
  if (name.startsWith(q)) return 0.9;
  if (name.includes(q)) return 0.8;
  if (filePath.includes(q)) return 0.7;
  if (tags.includes(q)) return 0.6;
  if (summary.includes(q)) return 0.5;

  // Fuzzy-ish: count matched chars in order
  let qi = 0;
  for (const char of name) {
    if (char === q[qi]) qi++;
    if (qi >= q.length) break;
  }
  if (qi >= q.length) return 0.4;

  return 0;
}

export function fuzzySearch(
  graph: KnowledgeGraph,
  query: string,
  limit = 10,
): FuzzyResult[] {
  if (!query.trim()) return [];
  const results: FuzzyResult[] = [];
  for (const node of graph.nodes) {
    const score = scoreNode(query, node);
    if (score > 0) {
      results.push({ nodeId: node.id, score });
    }
  }
  results.sort((a, b) => b.score - a.score);
  return results.slice(0, limit);
}
