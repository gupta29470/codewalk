import type { GraphEdge, GraphNode } from "../types";
import Graph from "graphology";
import louvain from "graphology-communities-louvain";

export interface DerivedContainer {
  id: string;
  name: string;
  nodeIds: string[];
  strategy: "folder" | "community";
  parentId?: string;
  depth: number;
}

function getFolderSegments(path?: string): string[] {
  if (!path) return [];
  return path.split("/").filter(Boolean).slice(0, -1);
}

function deriveFolderContainers(nodes: GraphNode[]): DerivedContainer[] {
  // Build folder tree: map folder path -> { fileIds, childFolders, parentPath }
  const folders = new Map<
    string,
    { fileIds: Set<string>; childFolders: Set<string>; parentPath: string | null }
  >();

  function ensureFolder(path: string, parentPath: string | null) {
    if (!folders.has(path)) {
      folders.set(path, { fileIds: new Set(), childFolders: new Set(), parentPath });
    }
  }

  for (const node of nodes) {
    if (!node.filePath) continue;
    const segs = getFolderSegments(node.filePath);
    if (segs.length === 0) continue;

    // Register every ancestor folder so we have a complete tree.
    for (let i = 0; i < segs.length; i++) {
      const path = segs.slice(0, i + 1).join("/");
      const parentPath = i > 0 ? segs.slice(0, i).join("/") : null;
      ensureFolder(path, parentPath);
    }

    // Assign the file to its deepest folder.
    const deepest = segs.join("/");
    folders.get(deepest)!.fileIds.add(node.id);

    // Register child folder relationships.
    for (let i = 0; i < segs.length - 1; i++) {
      const parentPath = segs.slice(0, i + 1).join("/");
      const childPath = segs.slice(0, i + 2).join("/");
      folders.get(parentPath)!.childFolders.add(childPath);
    }
  }

  const containers: DerivedContainer[] = [];
  for (const [path, data] of Array.from(folders.entries())) {
    containers.push({
      id: `container:folder:${path}`,
      name: path.split("/").pop() ?? path,
      nodeIds: [
        ...Array.from(data.childFolders).map((childPath) => `container:folder:${childPath}`),
        ...Array.from(data.fileIds),
      ],
      strategy: "folder",
      parentId: data.parentPath ? `container:folder:${data.parentPath}` : undefined,
      depth: path.split("/").length,
    });
  }
  return containers;
}

function deriveCommunityContainers(
  nodes: GraphNode[],
  edges: GraphEdge[],
): DerivedContainer[] {
  const graph = new Graph({ type: "directed", allowSelfLoops: false });
  const nodeIds = new Set(nodes.map((n) => n.id));

  for (const node of nodes) {
    graph.addNode(node.id);
  }

  for (const edge of edges) {
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) continue;
    if (!graph.hasEdge(edge.source, edge.target)) {
      graph.addEdge(edge.source, edge.target);
    }
  }

  const communities = louvain(graph, { resolution: 1.2 });
  const groups = new Map<string, string[]>();
  for (const [nodeId, community] of Array.from(Object.entries(communities))) {
    const key = String(community);
    const list = groups.get(key) ?? [];
    list.push(nodeId);
    groups.set(key, list);
  }

  const containers: DerivedContainer[] = [];
  let idx = 0;
  for (const [, nodeIds] of Array.from(groups.entries())) {
    if (nodeIds.length < 2) continue;
    idx++;
    containers.push({
      id: `container:community:${idx}`,
      name: `Cluster ${String.fromCharCode(64 + idx)}`,
      nodeIds,
      strategy: "community",
      depth: 1,
    });
  }
  return containers;
}

export function deriveContainers(
  nodes: GraphNode[],
  edges: GraphEdge[],
): { containers: DerivedContainer[]; ungrouped: string[] } {
  const folderContainers = deriveFolderContainers(nodes);
  const groupedIds = new Set<string>();
  for (const c of folderContainers) {
    for (const id of c.nodeIds) groupedIds.add(id);
  }

  // Prefer folder grouping whenever it covers a reasonable portion of the nodes,
  // even if one folder is large, because folder drill-down is the expected UX.
  const coverage = nodes.length > 0 ? groupedIds.size / nodes.length : 0;

  let containers: DerivedContainer[];
  if (coverage > 0.5) {
    containers = folderContainers;
  } else {
    containers = deriveCommunityContainers(nodes, edges);
    groupedIds.clear();
    for (const c of containers) {
      for (const id of c.nodeIds) groupedIds.add(id);
    }
  }

  const ungrouped = nodes.filter((n) => !groupedIds.has(n.id)).map((n) => n.id);
  return { containers, ungrouped };
}
