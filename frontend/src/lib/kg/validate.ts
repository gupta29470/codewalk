import type {
  Complexity,
  EdgeDirection,
  EdgeType,
  GraphEdge,
  GraphIssue,
  GraphNode,
  GraphStats,
  KnowledgeGraph,
  Layer,
  NodeType,
  ProjectMeta,
  TourStep,
} from "./types";
import { NODE_TYPES, EDGE_TYPES } from "./types";

const COMPLEXITIES: Complexity[] = ["simple", "moderate", "complex"];
const DIRECTIONS: EdgeDirection[] = ["forward", "backward", "bidirectional"];

function coerceNodeType(value: unknown): NodeType {
  if (typeof value === "string" && NODE_TYPES.includes(value as NodeType)) {
    return value as NodeType;
  }
  return "file";
}

function coerceEdgeType(value: unknown): EdgeType {
  if (typeof value === "string" && EDGE_TYPES.includes(value as EdgeType)) {
    return value as EdgeType;
  }
  return "related";
}

function coerceComplexity(value: unknown): Complexity {
  if (typeof value === "string" && COMPLEXITIES.includes(value as Complexity)) {
    return value as Complexity;
  }
  return "moderate";
}

function coerceDirection(value: unknown): EdgeDirection {
  if (typeof value === "string" && DIRECTIONS.includes(value as EdgeDirection)) {
    return value as EdgeDirection;
  }
  return "forward";
}

function coerceString(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

function coerceStringArray(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.filter((v): v is string => typeof v === "string");
  }
  return [];
}

function coerceNumber(value: unknown, fallback = 0.5): number {
  const n = typeof value === "string" ? Number(value) : Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function coerceProjectMeta(value: unknown): ProjectMeta {
  const raw = (value ?? {}) as Record<string, unknown>;
  return {
    name: coerceString(raw.name, "Unknown project"),
    repoPath: coerceString(raw.repoPath, "."),
    languages: coerceStringArray(raw.languages),
    frameworks: coerceStringArray(raw.frameworks),
    description: coerceString(raw.description, ""),
    analyzedAt: coerceString(raw.analyzedAt, new Date().toISOString()),
    gitCommitHash: raw.gitCommitHash ? String(raw.gitCommitHash) : undefined,
    branch: raw.branch ? String(raw.branch) : undefined,
  };
}

function validateNode(raw: unknown, issues: GraphIssue[]): GraphNode | null {
  if (!raw || typeof raw !== "object") {
    issues.push({ level: "dropped", message: "Node is not an object" });
    return null;
  }
  const n = raw as Record<string, unknown>;
  const id = coerceString(n.id, "");
  if (!id) {
    issues.push({ level: "dropped", message: "Node missing id" });
    return null;
  }

  const originalType = n.type;
  const type = coerceNodeType(originalType);
  if (originalType !== type) {
    issues.push({
      level: "auto-corrected",
      message: `Node ${id}: invalid type "${String(originalType)}" → "${type}"`,
    });
  }

  const complexity = coerceComplexity(n.complexity);
  if (n.complexity && n.complexity !== complexity) {
    issues.push({
      level: "auto-corrected",
      message: `Node ${id}: invalid complexity "${String(n.complexity)}" → "${complexity}"`,
    });
  }

  const tags = coerceStringArray(n.tags);
  if (!Array.isArray(n.tags)) {
    issues.push({
      level: "auto-corrected",
      message: `Node ${id}: missing tags → []`,
    });
  }

  const summary = coerceString(n.summary, "");
  if (!summary) {
    issues.push({
      level: "auto-corrected",
      message: `Node ${id}: missing summary → name`,
    });
  }

  const metrics = n.metrics && typeof n.metrics === "object" ? n.metrics : {};

  return {
    id,
    type,
    name: coerceString(n.name, id),
    filePath: n.filePath ? String(n.filePath) : undefined,
    qualifiedName: n.qualifiedName ? String(n.qualifiedName) : undefined,
    lineRange: Array.isArray(n.lineRange) && n.lineRange.length === 2
      ? [Number(n.lineRange[0]), Number(n.lineRange[1])]
      : undefined,
    summary: summary || coerceString(n.name, id),
    tags,
    complexity,
    language: n.language ? String(n.language) : undefined,
    module: n.module ? String(n.module) : undefined,
    x: typeof n.x === "number" ? n.x : undefined,
    y: typeof n.y === "number" ? n.y : undefined,
    metrics: {
      sizeBytes: coerceNumber((metrics as Record<string, unknown>).sizeBytes, 0),
      importCount: coerceNumber((metrics as Record<string, unknown>).importCount, 0),
      importerCount: coerceNumber((metrics as Record<string, unknown>).importerCount, 0),
      pageRank: coerceNumber((metrics as Record<string, unknown>).pageRank, 0),
      betweenness: coerceNumber((metrics as Record<string, unknown>).betweenness, 0),
      inDegree: coerceNumber((metrics as Record<string, unknown>).inDegree, 0),
      outDegree: coerceNumber((metrics as Record<string, unknown>).outDegree, 0),
      startLine: coerceNumber((metrics as Record<string, unknown>).startLine, 0),
      endLine: coerceNumber((metrics as Record<string, unknown>).endLine, 0),
      fileCount: coerceNumber((metrics as Record<string, unknown>).fileCount, 0),
    },
  };
}

function validateEdge(
  raw: unknown,
  nodeIds: Set<string>,
  issues: GraphIssue[],
): GraphEdge | null {
  if (!raw || typeof raw !== "object") {
    issues.push({ level: "dropped", message: "Edge is not an object" });
    return null;
  }
  const e = raw as Record<string, unknown>;
  const source = coerceString(e.source, "");
  const target = coerceString(e.target, "");
  if (!source || !target) {
    issues.push({ level: "dropped", message: "Edge missing source or target" });
    return null;
  }
  if (!nodeIds.has(source) || !nodeIds.has(target)) {
    issues.push({
      level: "dropped",
      message: `Edge ${source} → ${target} references missing node`,
    });
    return null;
  }

  const originalType = e.type;
  const type = coerceEdgeType(originalType);
  if (originalType !== type) {
    issues.push({
      level: "auto-corrected",
      message: `Edge ${source} → ${target}: invalid type "${String(originalType)}" → "${type}"`,
    });
  }

  const direction = coerceDirection(e.direction);
  if (e.direction && e.direction !== direction) {
    issues.push({
      level: "auto-corrected",
      message: `Edge ${source} → ${target}: invalid direction "${String(e.direction)}" → "${direction}"`,
    });
  }

  return {
    source,
    target,
    type,
    direction,
    weight: coerceNumber(e.weight, 0.5),
    description: e.description ? String(e.description) : undefined,
    line: e.line ? Number(e.line) : undefined,
  };
}

function validateLayer(raw: unknown, nodeIds: Set<string>, issues: GraphIssue[]): Layer | null {
  if (!raw || typeof raw !== "object") return null;
  const l = raw as Record<string, unknown>;
  const id = coerceString(l.id, "");
  if (!id) return null;
  const nodeIdsRaw = coerceStringArray(l.nodeIds);
  const validNodeIds = nodeIdsRaw.filter((nid) => {
    if (nodeIds.has(nid)) return true;
    issues.push({
      level: "dropped",
      message: `Layer ${id}: dangling node id "${nid}"`,
    });
    return false;
  });
  return {
    id,
    name: coerceString(l.name, id),
    description: coerceString(l.description, ""),
    nodeIds: validNodeIds,
  };
}

function validateTourStep(raw: unknown, nodeIds: Set<string>, issues: GraphIssue[]): TourStep | null {
  if (!raw || typeof raw !== "object") return null;
  const t = raw as Record<string, unknown>;
  const nodeIdsRaw = coerceStringArray(t.nodeIds);
  const validNodeIds = nodeIdsRaw.filter((nid) => {
    if (nodeIds.has(nid)) return true;
    issues.push({
      level: "dropped",
      message: `Tour step "${String(t.title)}": dangling node id "${nid}"`,
    });
    return false;
  });
  return {
    order: Number(t.order) || 0,
    title: coerceString(t.title, ""),
    description: coerceString(t.description, ""),
    nodeIds: validNodeIds,
  };
}

export function validateGraph(data: unknown): {
  success: boolean;
  graph?: KnowledgeGraph;
  issues: GraphIssue[];
  fatal?: string;
} {
  const issues: GraphIssue[] = [];

  if (!data || typeof data !== "object") {
    return { success: false, issues, fatal: "Graph data is not an object" };
  }
  const d = data as Record<string, unknown>;

  if (!Array.isArray(d.nodes)) {
    return { success: false, issues, fatal: "Graph nodes must be an array" };
  }
  if (!Array.isArray(d.edges)) {
    return { success: false, issues, fatal: "Graph edges must be an array" };
  }
  if (!Array.isArray(d.layers)) {
    return { success: false, issues, fatal: "Graph layers must be an array" };
  }

  const nodes: GraphNode[] = [];
  for (const raw of d.nodes) {
    const node = validateNode(raw, issues);
    if (node) nodes.push(node);
  }
  const nodeIds = new Set(nodes.map((n) => n.id));

  const edges: GraphEdge[] = [];
  for (const raw of d.edges) {
    const edge = validateEdge(raw, nodeIds, issues);
    if (edge) edges.push(edge);
  }

  const layers: Layer[] = [];
  for (const raw of d.layers) {
    const layer = validateLayer(raw, nodeIds, issues);
    if (layer) layers.push(layer);
  }

  const tour: TourStep[] = [];
  if (Array.isArray(d.tour)) {
    for (const raw of d.tour) {
      const step = validateTourStep(raw, nodeIds, issues);
      if (step) tour.push(step);
    }
  }

  const graph: KnowledgeGraph = {
    version: coerceString(d.version, "1.0.0"),
    kind: d.kind === "knowledge" ? "knowledge" : "codebase",
    project: coerceProjectMeta(d.project),
    nodes,
    edges,
    layers,
    tour,
    stats: d.stats && typeof d.stats === "object"
      ? (d.stats as GraphStats)
      : undefined,
  };

  return { success: true, graph, issues };
}
