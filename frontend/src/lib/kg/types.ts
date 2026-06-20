export type Complexity = "simple" | "moderate" | "complex";

export const NODE_TYPES = [
  "file",
  "function",
  "class",
  "method",
  "module",
  "config",
  "document",
  "resource",
  "table",
  "service",
  "endpoint",
  "schema",
  "pipeline",
  "concept",
  "domain",
  "flow",
  "step",
  "article",
  "entity",
  "topic",
  "claim",
  "source",
] as const;

export type NodeType = (typeof NODE_TYPES)[number];

export const EDGE_TYPES = [
  "imports",
  "exports",
  "contains",
  "extends",
  "calls",
  "module_dep",
  "related",
  "depends_on",
  "implements",
  "cites",
  "contradicts",
  "builds_on",
  "exemplifies",
  "categorized_under",
  "authored_by",
] as const;

export type EdgeType = (typeof EDGE_TYPES)[number];

export type EdgeDirection = "forward" | "backward" | "bidirectional";

export type NodeCategory =
  | "code"
  | "config"
  | "docs"
  | "infra"
  | "data"
  | "domain"
  | "knowledge";

export interface NodeMetrics {
  sizeBytes?: number;
  importCount?: number;
  importerCount?: number;
  pageRank?: number;
  betweenness?: number;
  inDegree?: number;
  outDegree?: number;
  startLine?: number;
  endLine?: number;
  fileCount?: number;
}

export interface GraphNode {
  id: string;
  type: NodeType;
  name: string;
  filePath?: string;
  qualifiedName?: string;
  lineRange?: [number, number];
  summary: string;
  tags: string[];
  complexity: Complexity;
  language?: string;
  module?: string;
  metrics?: NodeMetrics;
  /** Pre-computed layout coordinates from the backend. */
  x?: number;
  y?: number;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: EdgeType;
  direction: EdgeDirection;
  weight: number;
  description?: string;
  line?: number;
}

export interface Layer {
  id: string;
  name: string;
  description: string;
  nodeIds: string[];
}

export interface TourStep {
  order: number;
  title: string;
  description: string;
  nodeIds: string[];
}

export interface ProjectMeta {
  name: string;
  repoPath: string;
  languages: string[];
  frameworks: string[];
  description: string;
  analyzedAt: string;
  gitCommitHash?: string;
  branch?: string;
}

export interface GraphStats {
  files: number;
  imports: number;
  symbols: number;
  symbol_calls: number;
  chunks: number;
  modules: number;
  nodeCount: number;
  edgeCount: number;
  layerCount: number;
  moduleDepCount: number;
}

export interface KnowledgeGraph {
  version: string;
  kind?: "codebase" | "knowledge";
  project: ProjectMeta;
  nodes: GraphNode[];
  edges: GraphEdge[];
  layers: Layer[];
  stats?: GraphStats;
  tour?: TourStep[];
}

export interface GraphIssue {
  level: "auto-corrected" | "dropped" | "warning";
  message: string;
}

export interface ValidatedGraph {
  graph: KnowledgeGraph;
  issues: GraphIssue[];
}

export const NODE_TYPE_TO_CATEGORY: Record<NodeType, NodeCategory> = {
  file: "code",
  function: "code",
  class: "code",
  method: "code",
  module: "code",
  concept: "code",
  config: "config",
  document: "docs",
  service: "infra",
  resource: "infra",
  pipeline: "infra",
  table: "data",
  endpoint: "data",
  schema: "data",
  domain: "domain",
  flow: "domain",
  step: "domain",
  article: "knowledge",
  entity: "knowledge",
  topic: "knowledge",
  claim: "knowledge",
  source: "knowledge",
};

export const CATEGORY_NODE_COLORS: Record<NodeCategory, string> = {
  code: "var(--kg-node-file)",
  config: "var(--kg-node-config)",
  docs: "var(--kg-node-document)",
  infra: "var(--kg-node-service)",
  data: "var(--kg-node-table)",
  domain: "var(--kg-node-domain)",
  knowledge: "var(--kg-node-article)",
};

export function getNodeColor(type: NodeType): string {
  const map: Record<string, string> = {
    file: "var(--kg-node-file)",
    function: "var(--kg-node-function)",
    class: "var(--kg-node-class)",
    method: "var(--kg-node-method)",
    module: "var(--kg-node-module)",
    config: "var(--kg-node-config)",
    document: "var(--kg-node-document)",
    resource: "var(--kg-node-resource)",
    table: "var(--kg-node-table)",
    service: "var(--kg-node-service)",
    endpoint: "var(--kg-node-table)",
    schema: "var(--kg-node-table)",
    pipeline: "var(--kg-node-service)",
    concept: "var(--kg-node-concept)",
    domain: "var(--kg-node-domain)",
    flow: "var(--kg-node-flow)",
    step: "var(--kg-node-step)",
    article: "var(--kg-node-article)",
    entity: "var(--kg-node-entity)",
    topic: "var(--kg-node-topic)",
    claim: "var(--kg-node-claim)",
    source: "var(--kg-node-source)",
  };
  return map[type] ?? "var(--kg-accent)";
}
