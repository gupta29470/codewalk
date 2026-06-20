import type { GraphEdge, KnowledgeGraph, Layer } from "../types";

export interface LayerAggregation {
  sourceLayerId: string;
  targetLayerId: string;
  count: number;
}

export interface ContainerAggregation {
  sourceContainerId: string;
  targetContainerId: string;
  count: number;
}

export interface PortalInfo {
  layerId: string;
  layerName: string;
  connectionCount: number;
}

export function aggregateLayerEdges(graph: KnowledgeGraph): LayerAggregation[] {
  const layerMap = new Map<string, Layer>();
  for (const layer of graph.layers) {
    layerMap.set(layer.id, layer);
  }

  const nodeToLayer = new Map<string, string>();
  for (const layer of graph.layers) {
    for (const nodeId of layer.nodeIds) {
      nodeToLayer.set(nodeId, layer.id);
    }
  }

  const counts = new Map<string, number>();
  for (const edge of graph.edges) {
    const sourceLayer = nodeToLayer.get(edge.source);
    const targetLayer = nodeToLayer.get(edge.target);
    if (!sourceLayer || !targetLayer || sourceLayer === targetLayer) continue;
    const key = `${sourceLayer}|${targetLayer}`;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }

  const result: LayerAggregation[] = [];
  for (const [key, count] of Array.from(counts.entries())) {
    const [sourceLayerId, targetLayerId] = key.split("|");
    result.push({ sourceLayerId, targetLayerId, count });
  }
  return result;
}

export function aggregateContainerEdges(
  edges: GraphEdge[],
  nodeToContainer: Map<string, string>,
): {
  intraContainer: GraphEdge[];
  interContainerAggregated: ContainerAggregation[];
} {
  const intraContainer: GraphEdge[] = [];
  const interCounts = new Map<string, number>();

  for (const edge of edges) {
    const sourceAtom = nodeToContainer.get(edge.source) ?? edge.source;
    const targetAtom = nodeToContainer.get(edge.target) ?? edge.target;
    if (sourceAtom === targetAtom) {
      intraContainer.push(edge);
      continue;
    }
    const key = `${sourceAtom}|${targetAtom}`;
    interCounts.set(key, (interCounts.get(key) ?? 0) + 1);
  }

  const interContainerAggregated: ContainerAggregation[] = [];
  for (const [key, count] of Array.from(interCounts.entries())) {
    const [sourceContainerId, targetContainerId] = key.split("|");
    interContainerAggregated.push({ sourceContainerId, targetContainerId, count });
  }

  return { intraContainer, interContainerAggregated };
}

export function computePortals(
  graph: KnowledgeGraph,
  activeLayerId: string,
): PortalInfo[] {
  const activeLayer = graph.layers.find((l) => l.id === activeLayerId);
  if (!activeLayer) return [];

  const layerByNode = new Map<string, string>();
  for (const layer of graph.layers) {
    for (const nodeId of layer.nodeIds) {
      layerByNode.set(nodeId, layer.id);
    }
  }

  const counts = new Map<string, number>();
  for (const edge of graph.edges) {
    const sourceLayer = layerByNode.get(edge.source);
    const targetLayer = layerByNode.get(edge.target);
    if (sourceLayer === activeLayerId && targetLayer && targetLayer !== activeLayerId) {
      counts.set(targetLayer, (counts.get(targetLayer) ?? 0) + 1);
    } else if (targetLayer === activeLayerId && sourceLayer && sourceLayer !== activeLayerId) {
      counts.set(sourceLayer, (counts.get(sourceLayer) ?? 0) + 1);
    }
  }

  const result: PortalInfo[] = [];
  for (const [layerId, connectionCount] of Array.from(counts.entries())) {
    const layer = graph.layers.find((l) => l.id === layerId);
    if (layer) {
      result.push({ layerId, layerName: layer.name, connectionCount });
    }
  }
  return result;
}

export function findCrossLayerFileNodes(
  graph: KnowledgeGraph,
  activeLayerId: string,
  targetLayerId: string,
): string[] {
  const activeLayer = graph.layers.find((l) => l.id === activeLayerId);
  const targetLayer = graph.layers.find((l) => l.id === targetLayerId);
  if (!activeLayer || !targetLayer) return [];

  const activeNodeIds = new Set(activeLayer.nodeIds);
  const targetNodeIds = new Set(targetLayer.nodeIds);
  const fileIds = new Set<string>();

  for (const edge of graph.edges) {
    if (activeNodeIds.has(edge.source) && targetNodeIds.has(edge.target)) {
      fileIds.add(edge.source);
    } else if (targetNodeIds.has(edge.source) && activeNodeIds.has(edge.target)) {
      fileIds.add(edge.target);
    }
  }

  return Array.from(fileIds);
}

export function buildAdjacencyList(graph: KnowledgeGraph): {
  outgoing: Map<string, string[]>;
  incoming: Map<string, string[]>;
} {
  const outgoing = new Map<string, string[]>();
  const incoming = new Map<string, string[]>();
  for (const node of graph.nodes) {
    outgoing.set(node.id, []);
    incoming.set(node.id, []);
  }
  for (const edge of graph.edges) {
    outgoing.get(edge.source)?.push(edge.target);
    incoming.get(edge.target)?.push(edge.source);
  }
  return { outgoing, incoming };
}
