"use client";

import React, { useEffect, useMemo, useRef, useState, useCallback } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  useNodes,
  useReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  useViewport,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { CustomNode, type CustomNodeData, type CustomFlowNode } from "./CustomNode";
import { LayerClusterNode, type LayerClusterFlowNode } from "./LayerClusterNode";
import { ContainerNode, type ContainerFlowNode } from "./ContainerNode";
import { Breadcrumb } from "./Breadcrumb";
import { useKgStore } from "@/lib/kg/store";
import type { GraphNode, KnowledgeGraph } from "@/lib/kg/types";
import { NODE_TYPE_TO_CATEGORY } from "@/lib/kg/types";
import {
  NODE_WIDTH,
  NODE_HEIGHT,
  LAYER_CLUSTER_WIDTH,
  LAYER_CLUSTER_HEIGHT,
  CONTAINER_HEADER_HEIGHT,
  CONTAINER_PADDING,
  mergeElkPositions,
  applyForceLayout,
} from "@/lib/kg/utils/layout";
import { applyElkLayoutWorker } from "@/lib/kg/utils/elk-worker-client";
import type { ElkChild, ElkEdge, ElkInput } from "@/lib/kg/utils/layout";
import { aggregateContainerEdges, aggregateLayerEdges } from "@/lib/kg/utils/edgeAggregation";
import { deriveContainers } from "@/lib/kg/utils/containers";
import type { DerivedContainer } from "@/lib/kg/utils/containers";
import { computeLayerStats } from "@/lib/kg/utils/layerStats";
import { buildSvgFromNodes, downloadSvg, downloadPng } from "@/lib/kg/utils/export";
import { Loader2 } from "lucide-react";

const nodeTypes = {
  custom: CustomNode,
  "layer-cluster": LayerClusterNode,
  container: ContainerNode,
};

const OVERVIEW_LAYOUT_OPTIONS = {
  "elk.algorithm": "layered",
  "elk.direction": "DOWN",
  "elk.spacing.nodeNodeBetweenLayers": "80",
  "elk.spacing.nodeNode": "60",
  "elk.layered.spacing.nodeNodeBetweenLayers": "80",
  "elk.edgeRouting": "ORTHOGONAL",
  "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
};

const LAYER_LAYOUT_OPTIONS = {
  "elk.algorithm": "layered",
  "elk.direction": "DOWN",
  "elk.spacing.nodeNodeBetweenLayers": "60",
  "elk.spacing.nodeNode": "50",
  "elk.layered.spacing.nodeNodeBetweenLayers": "60",
  "elk.edgeRouting": "ORTHOGONAL",
  "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
};

const TREE_LAYOUT_OPTIONS = {
  "elk.algorithm": "layered",
  "elk.direction": "DOWN",
  "elk.spacing.nodeNodeBetweenLayers": "70",
  "elk.spacing.nodeNode": "40",
  "elk.layered.spacing.nodeNodeBetweenLayers": "70",
  "elk.edgeRouting": "ORTHOGONAL",
  "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
  "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
};

function hasBackendPositions(
  graph: KnowledgeGraph | null,
): graph is KnowledgeGraph & { nodes: (GraphNode & { x: number; y: number })[] } {
  return !!graph && graph.nodes.length > 0 && graph.nodes.every(
    (n) => typeof n.x === "number" && typeof n.y === "number",
  );
}

function buildBackendPositionMap(graph: { nodes: GraphNode[] }): Map<string, { x: number; y: number }> {
  const map = new Map<string, { x: number; y: number }>();
  for (const n of graph.nodes) {
    map.set(n.id, { x: n.x!, y: n.y! });
  }
  return map;
}

function computeContainerPositionFromBackend(
  container: DerivedContainer,
  backendMap: Map<string, { x: number; y: number }>,
  fallbackIndex: number,
): { x: number; y: number } {
  const leafPositions: { x: number; y: number }[] = [];
  for (const id of container.nodeIds) {
    if (id.startsWith("container:")) continue;
    const pos = backendMap.get(id);
    if (pos) leafPositions.push(pos);
  }
  if (leafPositions.length === 0) {
    const spacing = 600;
    const cols = 4;
    return {
      x: (fallbackIndex % cols) * spacing,
      y: Math.floor(fallbackIndex / cols) * spacing,
    };
  }
  const sum = leafPositions.reduce((acc, p) => ({ x: acc.x + p.x, y: acc.y + p.y }), { x: 0, y: 0 });
  return { x: sum.x / leafPositions.length, y: sum.y / leafPositions.length };
}

function useOverviewGraph() {
  const graph = useKgStore((s) => s.graph);
  const nodesById = useKgStore((s) => s.nodesById);
  const nodeIdToLayerId = useKgStore((s) => s.nodeIdToLayerId);
  const searchResults = useKgStore((s) => s.searchResults);

  const built = useMemo(() => {
    if (!graph) return null;
    const layers = graph.layers ?? [];
    if (layers.length === 0) return null;

    const searchMatchByLayer = new Map<string, number>();
    for (const result of searchResults) {
      const lid = nodeIdToLayerId.get(result.nodeId);
      if (lid) {
        searchMatchByLayer.set(lid, (searchMatchByLayer.get(lid) ?? 0) + 1);
      }
    }

    const clusterNodes: LayerClusterFlowNode[] = layers.map((layer, i) => {
      const stats = computeLayerStats(layer, nodesById);
      return {
        id: layer.id,
        type: "layer-cluster",
        position: { x: 0, y: 0 },
        data: {
          layerId: layer.id,
          layerName: layer.name,
          layerDescription: layer.description,
          fileCount: layer.nodeIds.length,
          aggregateComplexity: stats.aggregateComplexity,
          layerColorIndex: i,
          searchMatchCount: searchMatchByLayer.get(layer.id),
        },
      };
    });

    const aggregated = aggregateLayerEdges(graph);
    const flowEdges: Edge[] = aggregated.map((agg, i) => ({
      id: `le-${i}`,
      source: agg.sourceLayerId,
      target: agg.targetLayerId,
      style: {
        stroke: "var(--kg-accent)",
        strokeWidth: Math.min(2 + Math.log2(agg.count + 1), 6),
      },
    }));

    const dims = new Map<string, { width: number; height: number }>();
    for (const n of clusterNodes) {
      dims.set(n.id, { width: LAYER_CLUSTER_WIDTH, height: LAYER_CLUSTER_HEIGHT });
    }

    return { clusterNodes, flowEdges, dims };
  }, [graph, nodesById, nodeIdToLayerId, searchResults]);

  const [overview, setOverview] = useState<{ nodes: Node[]; edges: Edge[] }>({ nodes: [], edges: [] });

  useEffect(() => {
    if (!built) {
      setOverview({ nodes: [], edges: [] });
      return;
    }
    let cancelled = false;
    const { clusterNodes, flowEdges, dims } = built;
    const baseNodes = clusterNodes as unknown as Node[];
    const elkInput: ElkInput = {
      id: "overview",
      layoutOptions: OVERVIEW_LAYOUT_OPTIONS,
      children: baseNodes.map((n) => {
        const dim = dims.get(n.id)!;
        return { id: n.id, width: dim.width, height: dim.height };
      }),
      edges: flowEdges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
    };
    applyElkLayoutWorker(elkInput)
      .then(({ positioned }) => {
        if (cancelled) return;
        setOverview({ nodes: mergeElkPositions(baseNodes, positioned), edges: flowEdges });
      })
      .catch((err) => {
        console.error("[overview ELK]", err);
        if (cancelled) return;
        // Fallback: keep structural view usable even if ELK worker/layout fails.
        const cols = Math.max(1, Math.ceil(Math.sqrt(baseNodes.length)));
        const fallbackNodes = baseNodes.map((n, idx) => ({
          ...n,
          position: {
            x: (idx % cols) * (LAYER_CLUSTER_WIDTH + 80),
            y: Math.floor(idx / cols) * (LAYER_CLUSTER_HEIGHT + 80),
          },
        }));
        setOverview({ nodes: fallbackNodes, edges: flowEdges });
      });
    return () => {
      cancelled = true;
    };
  }, [built]);

  return overview;
}

interface LayerDetailTopology {
  nodes: Node[];
  edges: Edge[];
  filteredNodes: GraphNode[];
  containers: DerivedContainer[];
  nodeToContainer: Map<string, string>;
}

const EMPTY_TOPOLOGY: LayerDetailTopology = {
  nodes: [],
  edges: [],
  filteredNodes: [],
  containers: [],
  nodeToContainer: new Map(),
};

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function useLayerDetailTopology(): LayerDetailTopology {
  const graph = useKgStore((s) => s.graph);
  const nodesById = useKgStore((s) => s.nodesById);
  const activeLayerId = useKgStore((s) => s.activeLayerId);
  const selectNode = useKgStore((s) => s.selectNode);
  const persona = useKgStore((s) => s.persona);
  const nodeTypeFilters = useKgStore((s) => s.nodeTypeFilters);
  const detailLevel = useKgStore((s) => s.detailLevel);
  const showFunctionsInClassView = useKgStore((s) => s.showFunctionsInClassView);
  const containerSizeMemory = useKgStore((s) => s.containerSizeMemory);

  const handleNodeSelect = useCallback(
    (nodeId: string) => selectNode(nodeId),
    [selectNode],
  );

  const handleContainerToggle = useCallback(
    (id: string) => useKgStore.getState().toggleContainer(id),
    [],
  );

  const built = useMemo(() => {
    if (!graph || !activeLayerId) return null;
    const activeLayer = graph.layers.find((l) => l.id === activeLayerId);
    if (!activeLayer) return null;

    const layerNodeIds = new Set(activeLayer.nodeIds);
    const expandedLayerNodeIds = new Set(layerNodeIds);
    if (detailLevel !== "file") {
      for (const edge of graph.edges) {
        if (edge.type === "contains" && layerNodeIds.has(edge.source)) {
          const child = nodesById.get(edge.target);
          if (!child) continue;
          if (child.type === "class") {
            expandedLayerNodeIds.add(edge.target);
          } else if (child.type === "function" && showFunctionsInClassView) {
            expandedLayerNodeIds.add(edge.target);
          }
        }
      }
    }

    const allVisibleTypes = new Set([
      "file", "module", "concept", "config", "document", "service", "table",
      "endpoint", "pipeline", "schema", "resource", "domain", "flow", "step",
      "function", "class",
    ]);
    const subFileTypes = new Set(["function", "class"]);

    const filteredNodes = graph.nodes.filter((n) => {
      if (!expandedLayerNodeIds.has(n.id)) return false;
      if (!allVisibleTypes.has(n.type)) return false;
      if (persona === "non-technical" && subFileTypes.has(n.type)) return false;
      const category = NODE_TYPE_TO_CATEGORY[n.type] ?? "code";
      return nodeTypeFilters[category] !== false;
    });

    const filteredNodeIds = new Set(filteredNodes.map((n) => n.id));
    const filteredEdges = graph.edges.filter(
      (e) => filteredNodeIds.has(e.source) && filteredNodeIds.has(e.target),
    );

    const { containers, ungrouped } = deriveContainers(filteredNodes, filteredEdges);
    const ungroupedSet = new Set(ungrouped);

    // Map each graph node to its immediate folder container.
    const nodeToContainer = new Map<string, string>();
    for (const c of containers) {
      for (const id of c.nodeIds) {
        if (!id.startsWith("container:")) {
          nodeToContainer.set(id, c.id);
        }
      }
    }

    // Build a container lookup and compute root atom for aggregation (walk up nested parents).
    const containerById = new Map(containers.map((c) => [c.id, c]));
    function getRootAtom(id: string): string {
      if (ungroupedSet.has(id)) return id;
      const immediate = nodeToContainer.get(id);
      if (!immediate) return id;
      let current = immediate;
      while (true) {
        const parent = containerById.get(current)?.parentId;
        if (!parent) return current;
        current = parent;
      }
    }
    const nodeToRootAtom = new Map<string, string>();
    for (const id of Array.from(filteredNodeIds)) {
      nodeToRootAtom.set(id, getRootAtom(id));
    }
    const { interContainerAggregated } = aggregateContainerEdges(filteredEdges, nodeToRootAtom);

    const rootContainers = containers.filter((c) => !c.parentId);

    const sizeMemory = containerSizeMemory;
    const buildContainerNode = (c: DerivedContainer, idx: number): ContainerFlowNode => {
      const memo = sizeMemory.get(c.id);
      const estimate = Math.sqrt(c.nodeIds.length) * NODE_WIDTH * 1.2;
      return {
        id: c.id,
        type: "container",
        position: { x: 0, y: 0 },
        width: memo?.width ?? Math.min(800, Math.max(NODE_WIDTH, estimate)),
        height: memo?.height ?? Math.min(600, Math.max(NODE_HEIGHT, estimate)),
        data: {
          containerId: c.id,
          name: c.name,
          childCount: c.nodeIds.length,
          strategy: c.strategy,
          colorIndex: idx % 12,
          isExpanded: false,
          hasSearchHits: false,
          isDiffAffected: false,
          isFocusedViaChild: false,
          width: memo?.width ?? Math.min(800, Math.max(NODE_WIDTH, estimate)),
          height: memo?.height ?? Math.min(600, Math.max(NODE_HEIGHT, estimate)),
          onToggle: handleContainerToggle,
        },
      };
    };

    const containerNodes: ContainerFlowNode[] = rootContainers.map((c, idx) => buildContainerNode(c, idx));

    const ungroupedNodes: CustomFlowNode[] = filteredNodes
      .filter((n) => ungroupedSet.has(n.id))
      .map((node) => ({
        id: node.id,
        type: "custom",
        position: { x: 0, y: 0 },
        data: {
          label: node.name ?? node.filePath?.split("/").pop() ?? node.id,
          nodeType: node.type,
          summary: node.summary,
          complexity: node.complexity,
          tags: node.tags,
          isHighlighted: false,
          searchScore: undefined,
          isSelected: false,
          isTourHighlighted: false,
          isDiffChanged: false,
          isDiffAffected: false,
          isDiffFaded: false,
          isNeighbor: false,
          isSelectionFaded: false,
          onNodeClick: handleNodeSelect,
        },
      }));

    const aggEdges: Edge[] = interContainerAggregated.map((agg, i) => ({
      id: `agg-${i}`,
      source: agg.sourceContainerId,
      target: agg.targetContainerId,
      label: String(agg.count),
      style: {
        stroke: "var(--kg-accent)",
        strokeWidth: Math.min(2 + Math.log2(agg.count + 1), 6),
      },
      labelStyle: { fill: "var(--kg-text-secondary)", fontSize: 11, fontWeight: 600 },
    }));

    return {
      containers,
      nodeToContainer,
      filteredNodes,
      containerNodes,
      ungroupedNodes,
      aggEdges,
    };
  }, [
    graph,
    nodesById,
    activeLayerId,
    persona,
    nodeTypeFilters,
    detailLevel,
    showFunctionsInClassView,
    handleNodeSelect,
    handleContainerToggle,
    containerSizeMemory,
  ]);

  const [topology, setTopology] = useState<LayerDetailTopology>(EMPTY_TOPOLOGY);

  useEffect(() => {
    if (!built) {
      setTopology(EMPTY_TOPOLOGY);
      return;
    }
    let cancelled = false;
    const {
      containers,
      nodeToContainer,
      filteredNodes,
      containerNodes,
      ungroupedNodes,
      aggEdges,
    } = built;

    // Use backend-precomputed positions when available to skip ELK layout.
    if (hasBackendPositions(graph)) {
      const backendMap = buildBackendPositionMap(graph);
      const positionedContainers = containerNodes.map((cn, idx) => {
        const derived = containers.find((c) => c.id === cn.id);
        return {
          ...cn,
          position: derived
            ? computeContainerPositionFromBackend(derived, backendMap, idx)
            : { x: 0, y: 0 },
        };
      }) as unknown as Node[];
      const positionedUngrouped = ungroupedNodes.map((un) => ({
        ...un,
        position: backendMap.get(un.id) ?? { x: 0, y: 0 },
      })) as unknown as Node[];
      setTopology({
        nodes: [...positionedContainers, ...positionedUngrouped],
        edges: [...aggEdges],
        filteredNodes,
        containers,
        nodeToContainer,
      });
      return () => {
        cancelled = true;
      };
    }

    const children: ElkChild[] = [
      ...containerNodes.map((cn) => ({
        id: cn.id,
        width: cn.width ?? NODE_WIDTH,
        height: cn.height ?? NODE_HEIGHT,
      })),
      ...ungroupedNodes.map((un) => ({ id: un.id, width: NODE_WIDTH, height: NODE_HEIGHT })),
    ];
    const edges: ElkEdge[] = [...aggEdges].map((e) => ({
      id: e.id,
      sources: [e.source],
      targets: [e.target],
    }));

    const elkInput: ElkInput = {
      id: "layer",
      layoutOptions: LAYER_LAYOUT_OPTIONS,
      children,
      edges,
    };

    applyElkLayoutWorker(elkInput)
      .then(({ positioned, issues }) => {
        if (cancelled) return;
        if (issues.length > 0) {
          useKgStore.getState().appendLayoutIssues(issues);
        }
        const allBase: Node[] = [
          ...(containerNodes as unknown as Node[]),
          ...(ungroupedNodes as unknown as Node[]),
        ];
        setTopology({
          nodes: mergeElkPositions(allBase, positioned),
          edges: [...aggEdges],
          filteredNodes,
          containers,
          nodeToContainer,
        });
      })
      .catch((err) => console.error("[layer-detail ELK]", err));
    return () => {
      cancelled = true;
    };
  }, [built, graph]);

  return topology;
}

function buildCustomFlowNode(
  node: GraphNode,
  opts: {
    isSelected: boolean;
    isNeighbor: boolean;
    isSelectionFaded: boolean;
    searchScore?: number;
    isTourHighlighted: boolean;
    onNodeClick: (id: string) => void;
  },
): CustomFlowNode {
  return {
    id: node.id,
    type: "custom",
    position: { x: 0, y: 0 },
    data: {
      label: node.name ?? node.filePath?.split("/").pop() ?? node.id,
      nodeType: node.type,
      summary: node.summary,
      complexity: node.complexity,
      tags: node.tags,
      isHighlighted: opts.searchScore !== undefined,
      searchScore: opts.searchScore,
      isSelected: opts.isSelected,
      isTourHighlighted: opts.isTourHighlighted,
      isDiffChanged: false,
      isDiffAffected: false,
      isDiffFaded: false,
      isNeighbor: opts.isNeighbor,
      isSelectionFaded: opts.isSelectionFaded,
      onNodeClick: opts.onNodeClick,
    },
  };
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function useLayerDetailGraph(topo: LayerDetailTopology) {
  const selectedNodeId = useKgStore((s) => s.selectedNodeId);
  const searchResults = useKgStore((s) => s.searchResults);
  const tourHighlightedNodeIds = useKgStore((s) => s.tourHighlightedNodeIds);
  const expandedContainers = useKgStore((s) => s.expandedContainers);
  const containerLayoutCache = useKgStore((s) => s.containerLayoutCache);
  const selectNode = useKgStore((s) => s.selectNode);
  const diffMode = useKgStore((s) => s.diffMode);
  const changedNodeIds = useKgStore((s) => s.changedNodeIds);
  const affectedNodeIds = useKgStore((s) => s.affectedNodeIds);

  const handleNodeSelect = useCallback((nodeId: string) => selectNode(nodeId), [selectNode]);

  const handleContainerToggle = useCallback(
    (id: string) => {
      const container = topo.containers.find((c) => c.id === id);
      if (!container) {
        useKgStore.getState().toggleContainer(id);
        return;
      }
      const descendants: string[] = [];
      function walk(parent: DerivedContainer) {
        for (const childId of parent.nodeIds) {
          if (!childId.startsWith("container:")) continue;
          descendants.push(childId);
          const child = topo.containers.find((c) => c.id === childId);
          if (child) walk(child);
        }
      }
      walk(container);
      useKgStore.getState().toggleContainerRecursive(id, descendants);
    },
    [topo.containers],
  );

  const searchMap = useMemo(
    () => new Map(searchResults.map((r) => [r.nodeId, r.score])),
    [searchResults],
  );
  const tourSet = useMemo(() => new Set(tourHighlightedNodeIds), [tourHighlightedNodeIds]);

  const output = useMemo(() => {
    if (!topo.filteredNodes.length) return { nodes: [] as Node[], edges: [] as Edge[] };

    const nodeById = new Map(topo.filteredNodes.map((n) => [n.id, n]));
    const activeId = selectedNodeId;
    const neighborIds = new Set<string>();
    if (activeId) {
      for (const edge of topo.edges) {
        if (edge.source === activeId) neighborIds.add(edge.target);
        if (edge.target === activeId) neighborIds.add(edge.source);
      }
    }

    const baseNodes = topo.nodes.map((n) => {
      if (n.type === "container") {
        const data = n.data as { childCount: number; isDiffAffected?: boolean };
        let isDiffAffected = false;
        if (diffMode) {
          const container = topo.containers.find((c) => c.id === n.id);
          if (container) {
            isDiffAffected = container.nodeIds.some(
              (id) => changedNodeIds.has(id) || affectedNodeIds.has(id),
            );
          }
        }
        return {
          ...n,
          data: {
            ...data,
            isExpanded: expandedContainers.has(n.id),
            isDiffAffected,
          },
        };
      }
      if (n.type !== "custom") return n;
      const data = n.data as CustomNodeData;
      const isSelected = n.id === selectedNodeId;
      const isNeighbor = neighborIds.has(n.id);
      const isSelectionFaded = !!activeId && !isSelected && !isNeighbor;
      const searchScore = searchMap.get(n.id);
      const isTourHighlighted = tourSet.has(n.id);
      const isDiffChanged = diffMode && changedNodeIds.has(n.id);
      const isDiffAffected = diffMode && affectedNodeIds.has(n.id);
      const isDiffFaded = diffMode && !isDiffChanged && !isDiffAffected;
      return {
        ...n,
        data: {
          ...data,
          isSelected,
          isNeighbor,
          isSelectionFaded,
          searchScore,
          isHighlighted: searchScore !== undefined,
          isTourHighlighted,
          isDiffChanged,
          isDiffAffected,
          isDiffFaded,
        },
      };
    });

    const containerIndexById = new Map(topo.containers.map((c, i) => [c.id, i]));
    const sizeMemory = useKgStore.getState().containerSizeMemory;

    const childNodes: Node[] = [];
    const expandedSorted = Array.from(expandedContainers).sort((a, b) => {
      const da = topo.containers.find((c) => c.id === a)?.depth ?? 0;
      const db = topo.containers.find((c) => c.id === b)?.depth ?? 0;
      return da - db;
    });

    for (const containerId of expandedSorted) {
      const cache = containerLayoutCache.get(containerId);
      const container = topo.containers.find((c) => c.id === containerId);
      if (!cache || !container) continue;
      for (const childId of container.nodeIds) {
        const pos = cache.childPositions.get(childId);
        if (!pos) continue;

        if (childId.startsWith("container:")) {
          const childContainer = topo.containers.find((c) => c.id === childId);
          if (!childContainer) continue;
          const memo = sizeMemory.get(childContainer.id);
          const estimate = Math.sqrt(childContainer.nodeIds.length) * NODE_WIDTH * 1.2;
          const idx = containerIndexById.get(childContainer.id) ?? 0;
          childNodes.push({
            id: childContainer.id,
            type: "container",
            position: pos,
            width: memo?.width ?? Math.min(800, Math.max(NODE_WIDTH, estimate)),
            height: memo?.height ?? Math.min(600, Math.max(NODE_HEIGHT, estimate)),
            parentId: containerId,
            extent: "parent",
            expandParent: false,
            data: {
              containerId: childContainer.id,
              name: childContainer.name,
              childCount: childContainer.nodeIds.length,
              strategy: childContainer.strategy,
              colorIndex: idx % 12,
              isExpanded: expandedContainers.has(childContainer.id),
              hasSearchHits: false,
              isDiffAffected: false,
              isFocusedViaChild: false,
              width: memo?.width ?? Math.min(800, Math.max(NODE_WIDTH, estimate)),
              height: memo?.height ?? Math.min(600, Math.max(NODE_HEIGHT, estimate)),
              onToggle: handleContainerToggle,
            },
          });
          continue;
        }

        const node = nodeById.get(childId);
        if (!node) continue;
        const isSelected = childId === selectedNodeId;
        const isNeighbor = neighborIds.has(childId);
        const isSelectionFaded = !!activeId && !isSelected && !isNeighbor;
        const searchScore = searchMap.get(childId);
        const isTourHighlighted = tourSet.has(childId);
        const isDiffChanged = diffMode && changedNodeIds.has(childId);
        const isDiffAffected = diffMode && affectedNodeIds.has(childId);
        const isDiffFaded = diffMode && !isDiffChanged && !isDiffAffected;
        childNodes.push({
          ...buildCustomFlowNode(node, {
            isSelected,
            isNeighbor,
            isSelectionFaded,
            searchScore,
            isTourHighlighted,
            onNodeClick: handleNodeSelect,
          }),
          position: pos,
          parentId: containerId,
          extent: "parent",
          expandParent: false,
          data: {
            ...buildCustomFlowNode(node, {
              isSelected,
              isNeighbor,
              isSelectionFaded,
              searchScore,
              isTourHighlighted,
              onNodeClick: handleNodeSelect,
            }).data,
            isDiffChanged,
            isDiffAffected,
            isDiffFaded,
          },
        });
      }
    }

    const edges = topo.edges.map((e) => {
      const isConnected = activeId && (e.source === activeId || e.target === activeId);
      return {
        ...e,
        style: isConnected
          ? { ...e.style, strokeWidth: Math.max(3, (e.style?.strokeWidth as number) ?? 2), opacity: 1 }
          : activeId
            ? { ...e.style, opacity: 0.12 }
            : e.style,
      };
    });

    return { nodes: [...baseNodes, ...childNodes], edges };
  }, [topo, selectedNodeId, searchMap, tourSet, expandedContainers, containerLayoutCache, handleNodeSelect, handleContainerToggle, diffMode, changedNodeIds, affectedNodeIds]);

  return { ...output, containers: topo.containers };
}

function centerNodes(nodes: Node[]): Node[] {
  if (nodes.length === 0) return nodes;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const n of nodes) {
    const w = (n.width ?? NODE_WIDTH);
    const h = (n.height ?? NODE_HEIGHT);
    minX = Math.min(minX, n.position.x);
    minY = Math.min(minY, n.position.y);
    maxX = Math.max(maxX, n.position.x + w);
    maxY = Math.max(maxY, n.position.y + h);
  }
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  return nodes.map((n) => ({
    ...n,
    position: { x: n.position.x - cx, y: n.position.y - cy },
  }));
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function useLayerTreeGraph() {
  const graph = useKgStore((s) => s.graph);
  const nodesById = useKgStore((s) => s.nodesById);
  const activeLayerId = useKgStore((s) => s.activeLayerId);
  const selectedNodeId = useKgStore((s) => s.selectedNodeId);
  const searchResults = useKgStore((s) => s.searchResults);
  const tourHighlightedNodeIds = useKgStore((s) => s.tourHighlightedNodeIds);
  const nodeTypeFilters = useKgStore((s) => s.nodeTypeFilters);
  const selectNode = useKgStore((s) => s.selectNode);

  const handleNodeSelect = useCallback(
    (nodeId: string) => selectNode(nodeId === selectedNodeId ? null : nodeId),
    [selectNode, selectedNodeId],
  );

  const built = useMemo(() => {
    if (!graph || !activeLayerId) return null;
    const activeLayer = graph.layers.find((l) => l.id === activeLayerId);
    if (!activeLayer) return null;

    const layerNodeIds = new Set(activeLayer.nodeIds);
    const allVisibleTypes = new Set([
      "file", "module", "concept", "config", "document", "service", "table",
      "endpoint", "pipeline", "schema", "resource", "domain", "flow", "step",
      "function", "class",
    ]);

    const filteredNodes = graph.nodes.filter((n) => {
      if (!layerNodeIds.has(n.id)) return false;
      if (!allVisibleTypes.has(n.type)) return false;
      const category = NODE_TYPE_TO_CATEGORY[n.type] ?? "code";
      return nodeTypeFilters[category] !== false;
    });

    interface TreeItem {
      id: string;
      name: string;
      type: "folder" | "file";
      nodeId?: string;
      children: TreeItem[];
    }

    const root: TreeItem = { id: "tree:root", name: activeLayer.name, type: "folder", children: [] };
    const itemByPath = new Map<string, TreeItem>();
    itemByPath.set("", root);

    for (const node of filteredNodes) {
      if (node.filePath) {
        const parts = node.filePath.split("/").filter(Boolean);
        let currentPath = "";
        for (let i = 0; i < parts.length; i++) {
          const part = parts[i];
          const parentPath = currentPath;
          currentPath = currentPath ? `${currentPath}/${part}` : part;
          const isFile = i === parts.length - 1;
          let item = itemByPath.get(currentPath);
          if (!item) {
            item = {
              id: isFile ? node.id : `tree:folder:${currentPath}`,
              name: part,
              type: isFile ? "file" : "folder",
              nodeId: isFile ? node.id : undefined,
              children: [],
            };
            itemByPath.set(currentPath, item);
            const parent = itemByPath.get(parentPath)!;
            parent.children.push(item);
          }
        }
      } else {
        root.children.push({
          id: node.id,
          name: node.name,
          type: "file",
          nodeId: node.id,
          children: [],
        });
      }
    }

    const searchMap = new Map(searchResults.map((r) => [r.nodeId, r.score]));
    const tourSet = new Set(tourHighlightedNodeIds);

    const flowNodes: Node[] = [];
    const flowEdges: Edge[] = [];

    function walk(item: TreeItem, parentId?: string) {
      if (item.id === "tree:root") {
        for (const child of item.children) walk(child);
        return;
      }

      if (item.type === "folder") {
        flowNodes.push({
          id: item.id,
          type: "custom",
          position: { x: 0, y: 0 },
          data: {
            label: item.name,
            nodeType: "folder",
            summary: `${item.children.length} items`,
            isHighlighted: false,
            isSelected: false,
            isTourHighlighted: false,
            isDiffChanged: false,
            isDiffAffected: false,
            isDiffFaded: false,
            isNeighbor: false,
            isSelectionFaded: false,
            onNodeClick: () => {},
          } as CustomNodeData,
        });
      } else {
        const graphNode = nodesById.get(item.nodeId!);
        const searchScore = graphNode ? searchMap.get(graphNode.id) : undefined;
        const isTourHighlighted = graphNode ? tourSet.has(graphNode.id) : false;
        const isSelected = item.nodeId === selectedNodeId;
        if (graphNode) {
          flowNodes.push(
            buildCustomFlowNode(graphNode, {
              isSelected,
              isNeighbor: false,
              isSelectionFaded: false,
              searchScore,
              isTourHighlighted,
              onNodeClick: handleNodeSelect,
            }) as unknown as Node,
          );
        } else {
          flowNodes.push({
            id: item.id,
            type: "custom",
            position: { x: 0, y: 0 },
            data: {
              label: item.name,
              nodeType: "file",
              isHighlighted: false,
              isSelected,
              isTourHighlighted,
              isDiffChanged: false,
              isDiffAffected: false,
              isDiffFaded: false,
              isNeighbor: false,
              isSelectionFaded: false,
              onNodeClick: handleNodeSelect,
            } as CustomNodeData,
          });
        }
      }

      if (parentId && parentId !== "tree:root") {
        flowEdges.push({
          id: `tree-${parentId}-${item.id}`,
          source: parentId,
          target: item.id,
          style: { stroke: "var(--kg-border-subtle)", strokeWidth: 1 },
        });
      }

      for (const child of item.children) walk(child, item.id);
    }

    walk(root);

    const dims = new Map<string, { width: number; height: number }>();
    for (const n of flowNodes) {
      dims.set(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
    }

    return { flowNodes, flowEdges, dims };
  }, [
    graph,
    nodesById,
    activeLayerId,
    selectedNodeId,
    searchResults,
    tourHighlightedNodeIds,
    nodeTypeFilters,
    handleNodeSelect,
  ]);

  const [output, setOutput] = useState<{ nodes: Node[]; edges: Edge[] }>({ nodes: [], edges: [] });

  useEffect(() => {
    if (!built) {
      setOutput({ nodes: [], edges: [] });
      return;
    }
    let cancelled = false;
    const { flowNodes, flowEdges, dims } = built;
    const baseNodes = flowNodes;
    const elkInput: ElkInput = {
      id: "layer-tree",
      layoutOptions: TREE_LAYOUT_OPTIONS,
      children: baseNodes.map((n) => ({
        id: n.id,
        width: dims.get(n.id)!.width,
        height: dims.get(n.id)!.height,
      })),
      edges: flowEdges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
    };

    if (hasBackendPositions(graph)) {
      const backendMap = buildBackendPositionMap(graph);
      const positionedNodes = baseNodes.map((n) => ({
        ...n,
        position: backendMap.get(n.id) ?? { x: 0, y: 0 },
      }));
      setOutput({ nodes: centerNodes(positionedNodes), edges: flowEdges });
      return () => {
        cancelled = true;
      };
    }

    applyElkLayoutWorker(elkInput)
      .then(({ positioned }) => {
        if (cancelled) return;
        setOutput({ nodes: centerNodes(mergeElkPositions(baseNodes, positioned)), edges: flowEdges });
      })
      .catch((err) => {
        console.error("[layer-tree ELK]", err);
        if (cancelled) return;
        const cols = Math.max(1, Math.ceil(Math.sqrt(baseNodes.length)));
        const fallbackNodes = baseNodes.map((n, idx) => ({
          ...n,
          position: {
            x: (idx % cols) * (NODE_WIDTH + 80),
            y: Math.floor(idx / cols) * (NODE_HEIGHT + 80),
          },
        }));
        setOutput({ nodes: centerNodes(fallbackNodes), edges: flowEdges });
      });
    return () => {
      cancelled = true;
    };
  }, [built, graph]);

  return output;
}

function getNodeDimensions(edgeCount: number): { width: number; height: number } {
  const scale = Math.min(1.5, Math.max(0.85, 0.85 + edgeCount * 0.03));
  return {
    width: Math.round(NODE_WIDTH * scale),
    height: Math.round(NODE_HEIGHT * scale),
  };
}

function computeLayerKnowledgeLayout(graph: KnowledgeGraph) {
  const edgeCounts = new Map<string, number>();
  for (const edge of graph.edges) {
    edgeCounts.set(edge.source, (edgeCounts.get(edge.source) ?? 0) + 1);
    edgeCounts.set(edge.target, (edgeCounts.get(edge.target) ?? 0) + 1);
  }

  const dims = new Map<string, { width: number; height: number }>();
  for (const node of graph.nodes) {
    dims.set(node.id, getNodeDimensions(edgeCounts.get(node.id) ?? 0));
  }

  const positionMap = new Map<string, { x: number; y: number }>();
  const hasPrecomputedPositions = graph.nodes.length > 0 && graph.nodes.every(
    (n) => typeof n.x === "number" && typeof n.y === "number",
  );

  if (hasPrecomputedPositions) {
    for (const node of graph.nodes) {
      positionMap.set(node.id, { x: node.x!, y: node.y! });
    }
  } else {
    const tmpNodes: Node[] = graph.nodes.map((node) => ({
      id: node.id,
      type: "custom" as const,
      position: { x: 0, y: 0 },
      data: {},
    }));

    const tmpEdges: Edge[] = graph.edges.map((e, i) => ({
      id: `ke-${i}`,
      source: e.source,
      target: e.target,
    }));

    const communityMap = new Map<string, number>();
    graph.layers.forEach((layer, i) => {
      for (const nodeId of layer.nodeIds) {
        communityMap.set(nodeId, i);
      }
    });

    const { nodes: layoutedNodes } = applyForceLayout(tmpNodes, tmpEdges, dims, communityMap);
    for (const n of layoutedNodes) {
      positionMap.set(n.id, n.position);
    }
  }

  return { positionMap, edgeCounts };
}

function useLayerKnowledgeGraph(layerId: string | null) {
  const graph = useKgStore((s) => s.graph);
  const selectedNodeId = useKgStore((s) => s.selectedNodeId);
  const focusNodeId = useKgStore((s) => s.focusNodeId);
  const searchResultsRaw = useKgStore((s) => s.searchResults);
  const tourHighlightedNodeIds = useKgStore((s) => s.tourHighlightedNodeIds);
  const nodeTypeFilters = useKgStore((s) => s.nodeTypeFilters);
  const diffMode = useKgStore((s) => s.diffMode);
  const changedNodeIds = useKgStore((s) => s.changedNodeIds);
  const affectedNodeIds = useKgStore((s) => s.affectedNodeIds);
  const selectNode = useKgStore((s) => s.selectNode);

  const searchResults = useMemo(
    () => new Map(searchResultsRaw.map((r) => [r.nodeId, r.score])),
    [searchResultsRaw],
  );
  const tourSet = useMemo(() => new Set(tourHighlightedNodeIds), [tourHighlightedNodeIds]);

  const filteredGraph = useMemo((): KnowledgeGraph | null => {
    if (!graph || !layerId) return null;
    const activeLayer = graph.layers.find((l) => l.id === layerId);
    if (!activeLayer) return null;
    const layerNodeIds = new Set(activeLayer.nodeIds);

    const filteredNodes = graph.nodes.filter((n) => {
      if (!layerNodeIds.has(n.id)) return false;
      const category = NODE_TYPE_TO_CATEGORY[n.type] ?? "code";
      return nodeTypeFilters[category] !== false;
    });
    const filteredNodeIds = new Set(filteredNodes.map((n) => n.id));
    const filteredEdges = graph.edges.filter(
      (e) => filteredNodeIds.has(e.source) && filteredNodeIds.has(e.target),
    );
    return { ...graph, nodes: filteredNodes, edges: filteredEdges };
  }, [graph, layerId, nodeTypeFilters]);

  const { positionMap } = useMemo(() => {
    if (!filteredGraph) return { positionMap: new Map(), edgeCounts: new Map() };
    return computeLayerKnowledgeLayout(filteredGraph);
  }, [filteredGraph]);

  const { nodes, edges } = useMemo(() => {
    if (!filteredGraph) return { nodes: [] as Node[], edges: [] as Edge[] };

    const neighborIds = new Set<string>();
    const focusId = focusNodeId ?? selectedNodeId;
    if (focusId) {
      for (const edge of filteredGraph.edges) {
        if (edge.source === focusId) neighborIds.add(edge.target);
        if (edge.target === focusId) neighborIds.add(edge.source);
      }
    }

    const rfNodes: Node[] = filteredGraph.nodes.map((node) => {
      const isSelected = node.id === selectedNodeId;
      const isNeighbor = neighborIds.has(node.id);
      const isSelectionFaded = !!focusId && !isSelected && !isNeighbor;
      const searchScore = searchResults.get(node.id);
      const isTourHighlighted = tourSet.has(node.id);

      const flowNode = buildCustomFlowNode(node, {
        isSelected,
        isNeighbor,
        isSelectionFaded,
        searchScore,
        isTourHighlighted,
        onNodeClick: (id: string) => selectNode(id),
      }) as unknown as Node;
      flowNode.position = positionMap.get(node.id) ?? { x: 0, y: 0 };

      const data = flowNode.data as CustomNodeData;
      data.isDiffChanged = diffMode && changedNodeIds.has(node.id);
      data.isDiffAffected = diffMode && affectedNodeIds.has(node.id);
      data.isDiffFaded = diffMode && !data.isDiffChanged && !data.isDiffAffected;

      return flowNode;
    });

    const rfEdges: Edge[] = filteredGraph.edges.map((e, i) => {
      const connected = selectedNodeId && (e.source === selectedNodeId || e.target === selectedNodeId);
      return {
        id: `ke-${i}`,
        source: e.source,
        target: e.target,
        label: e.type,
        style: {
          stroke: "var(--kg-accent)",
          strokeWidth: connected ? 3 : 2,
          opacity: selectedNodeId ? (connected ? 1 : 0.12) : 1,
        },
        labelStyle: { fill: "var(--kg-text-secondary)", fontSize: 10 },
        labelShowBg: true,
        labelBgStyle: { fill: "var(--kg-root)", fillOpacity: 0.8 },
      };
    });

    return { nodes: rfNodes, edges: rfEdges };
  }, [
    filteredGraph,
    selectedNodeId,
    focusNodeId,
    searchResults,
    tourSet,
    positionMap,
    selectNode,
    diffMode,
    changedNodeIds,
    affectedNodeIds,
  ]);

  return { nodes, edges };
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function useStage2Layout(topo: LayerDetailTopology) {
  const graph = useKgStore((s) => s.graph);
  const activeLayerId = useKgStore((s) => s.activeLayerId);
  const expandedContainers = useKgStore((s) => s.expandedContainers);
  const containerLayoutCache = useKgStore((s) => s.containerLayoutCache);
  const setContainerLayout = useKgStore((s) => s.setContainerLayout);

  useEffect(() => {
    if (!graph || !activeLayerId || topo.containers.length === 0) return;
    const toCompute = Array.from(expandedContainers).filter(
      (id) => !containerLayoutCache.has(id),
    );
    if (toCompute.length === 0) return;

    let cancelled = false;

    // Fast path: use backend-precomputed positions for expanded container children.
    if (hasBackendPositions(graph)) {
      const backendMap = buildBackendPositionMap(graph);
      const results = toCompute.map((containerId) => {
        const container = topo.containers.find((c) => c.id === containerId);
        const containerNode = topo.nodes.find((n) => n.id === containerId);
        if (!container || !containerNode) return null;
        const parentPos = containerNode.position;
        const childPositions = new Map<string, { x: number; y: number }>();
        let maxX = 0;
        let maxY = 0;
        for (const id of container.nodeIds) {
          let globalPos = backendMap.get(id);
          if (!globalPos && id.startsWith("container:")) {
            const nestedNode = topo.nodes.find((n) => n.id === id);
            globalPos = nestedNode?.position;
          }
          if (!globalPos) continue;
          const x = globalPos.x - parentPos.x + CONTAINER_PADDING / 2;
          const y = globalPos.y - parentPos.y + CONTAINER_HEADER_HEIGHT + CONTAINER_PADDING / 2;
          childPositions.set(id, { x, y });
          maxX = Math.max(maxX, x + NODE_WIDTH);
          maxY = Math.max(maxY, y + NODE_HEIGHT);
        }
        return {
          containerId,
          childPositions,
          actualSize: {
            width: Math.max(maxX + CONTAINER_PADDING / 2, 260),
            height: Math.max(maxY + CONTAINER_PADDING / 2, 140),
          },
        };
      });
      for (const r of results) {
        if (!r) continue;
        setContainerLayout(r.containerId, r.childPositions, r.actualSize);
      }
      return () => {
        cancelled = true;
      };
    }

    Promise.all(
      toCompute.map(async (containerId) => {
        const container = topo.containers.find((c) => c.id === containerId);
        if (!container) return null;
        const childIds = new Set(container.nodeIds);
        const childEdges = (graph.edges ?? []).filter(
          (e) =>
            childIds.has(e.source) &&
            childIds.has(e.target) &&
            !e.source.startsWith("container:") &&
            !e.target.startsWith("container:"),
        );
        const sizeMemory = useKgStore.getState().containerSizeMemory;
        const children: ElkChild[] = container.nodeIds.map((id) => {
          if (id.startsWith("container:")) {
            const childContainer = topo.containers.find((c) => c.id === id);
            const memo = childContainer ? sizeMemory.get(childContainer.id) : undefined;
            const estimate = childContainer
              ? Math.sqrt(childContainer.nodeIds.length) * NODE_WIDTH * 1.2
              : NODE_WIDTH;
            return {
              id,
              width: memo?.width ?? Math.min(800, Math.max(NODE_WIDTH, estimate)),
              height: memo?.height ?? Math.min(600, Math.max(NODE_HEIGHT, estimate)),
            };
          }
          return { id, width: NODE_WIDTH, height: NODE_HEIGHT };
        });
        const edges: ElkEdge[] = childEdges.map((e, i) => ({
          id: `${containerId}-e${i}`,
          sources: [e.source],
          targets: [e.target],
        }));
        const input: ElkInput = {
          id: containerId,
          layoutOptions: LAYER_LAYOUT_OPTIONS,
          children,
          edges,
        };
        try {
          const { positioned } = await applyElkLayoutWorker(input);
          if (cancelled) return null;
          const childPositions = new Map<string, { x: number; y: number }>();
          let maxX = 0;
          let maxY = 0;
          for (const ch of positioned.children ?? []) {
            const x = (ch.x ?? 0) + CONTAINER_PADDING / 2;
            const y = (ch.y ?? 0) + CONTAINER_HEADER_HEIGHT + CONTAINER_PADDING / 2;
            const w = ch.width ?? NODE_WIDTH;
            const h = ch.height ?? NODE_HEIGHT;
            childPositions.set(ch.id, { x, y });
            if (x + w > maxX) maxX = x + w;
            if (y + h > maxY) maxY = y + h;
          }
          return {
            containerId,
            childPositions,
            actualSize: {
              width: Math.max(maxX + CONTAINER_PADDING / 2, 260),
              height: Math.max(maxY + CONTAINER_PADDING / 2, 140),
            },
          };
        } catch (err) {
          console.error(`[Stage 2 ${containerId}]`, err);
          // Synchronous grid fallback so children are still visible if ELK fails/times out.
          const childPositions = new Map<string, { x: number; y: number }>();
          let maxX = 0;
          let maxY = 0;
          const gap = 20;
          const cols = Math.max(1, Math.ceil(Math.sqrt(children.length)));
          for (let i = 0; i < children.length; i++) {
            const ch = children[i];
            const col = i % cols;
            const row = Math.floor(i / cols);
            const x = col * ((ch.width ?? NODE_WIDTH) + gap) + CONTAINER_PADDING / 2;
            const y = row * ((ch.height ?? NODE_HEIGHT) + gap) + CONTAINER_HEADER_HEIGHT + CONTAINER_PADDING / 2;
            childPositions.set(ch.id, { x, y });
            maxX = Math.max(maxX, x + (ch.width ?? NODE_WIDTH));
            maxY = Math.max(maxY, y + (ch.height ?? NODE_HEIGHT));
          }
          return {
            containerId,
            childPositions,
            actualSize: {
              width: Math.max(maxX + CONTAINER_PADDING / 2, 260),
              height: Math.max(maxY + CONTAINER_PADDING / 2, 140),
            },
          };
        }
      }),
    ).then((results) => {
      if (cancelled) return;
      for (const r of results) {
        if (!r) continue;
        setContainerLayout(r.containerId, r.childPositions, r.actualSize);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [
    graph,
    activeLayerId,
    expandedContainers,
    containerLayoutCache,
    setContainerLayout,
    topo,
  ]);
}

function useGraphExport() {
  const pendingFormat = useKgStore((s) => s.pendingExportFormat);
  const clearPendingExport = useKgStore((s) => s.clearPendingExport);
  const { getNodes, getEdges } = useReactFlow();

  useEffect(() => {
    if (!pendingFormat) return;
    const nodes = getNodes();
    const edges = getEdges();
    const svg = buildSvgFromNodes(nodes, edges);
    if (pendingFormat === "svg") {
      downloadSvg(svg);
    } else {
      downloadPng(svg);
    }
    clearPendingExport();
  }, [pendingFormat, clearPendingExport, getNodes, getEdges]);
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function useZoomAutoExpand() {
  const { zoom } = useViewport();
  const navigationLevel = useKgStore((s) => s.navigationLevel);
  const expandedContainers = useKgStore((s) => s.expandedContainers);
  const expandContainer = useKgStore((s) => s.expandContainer);
  const nodes = useNodes();

  useEffect(() => {
    if (navigationLevel !== "layer-detail" || zoom <= 1) return;
    const containerIds = nodes.filter((n) => n.type === "container").map((n) => n.id);
    const toExpand = containerIds.filter((id) => !expandedContainers.has(id));
    for (const id of toExpand) {
      expandContainer(id);
    }
  }, [zoom, navigationLevel, nodes, expandedContainers, expandContainer]);
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function FitViewOnMount() {
  const { fitView } = useReactFlow();
  const nodes = useNodes();
  const didFit = useRef(false);

  useEffect(() => {
    if (!didFit.current && nodes.length > 0) {
      didFit.current = true;
      fitView({ padding: 0.15, duration: 500 });
    }
  }, [nodes, fitView]);

  return null;
}

function arraysEqual(a: string[], b: string[]) {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function useTourFitView() {
  const tourActive = useKgStore((s) => s.tourActive);
  const tourHighlightedNodeIds = useKgStore((s) => s.tourHighlightedNodeIds);
  const { fitView } = useReactFlow();
  const nodes = useNodes();
  const lastFittedRef = useRef<string[]>([]);

  useEffect(() => {
    if (!tourActive || tourHighlightedNodeIds.length === 0) return;
    if (arraysEqual(lastFittedRef.current, tourHighlightedNodeIds)) return;

    let attempts = 0;
    const maxAttempts = 30;
    const interval = setInterval(() => {
      const nodeIdSet = new Set(nodes.map((n) => n.id));
      const allPresent = tourHighlightedNodeIds.every((id) => nodeIdSet.has(id));
      if (allPresent || attempts >= maxAttempts) {
        clearInterval(interval);
        if (allPresent) {
          fitView({
            nodes: tourHighlightedNodeIds.map((id) => ({ id })),
            duration: 800,
            padding: 0.25,
          });
          lastFittedRef.current = [...tourHighlightedNodeIds];
        }
      }
      attempts++;
    }, 100);

    return () => clearInterval(interval);
  }, [tourActive, tourHighlightedNodeIds, fitView, nodes]);
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function useFitViewOnNavigation() {
  const { fitView } = useReactFlow();
  const navigationLevel = useKgStore((s) => s.navigationLevel);
  const viewMode = useKgStore((s) => s.viewMode);
  const lastFittedRef = useRef<string>("");

  useEffect(() => {
    const key = `${navigationLevel}-${viewMode}`;
    if (lastFittedRef.current === key) return;
    lastFittedRef.current = key;
    const timer = setTimeout(() => {
      fitView({ padding: 0.15, duration: 500 });
    }, 150);
    return () => clearTimeout(timer);
  }, [navigationLevel, viewMode, fitView]);
}

function GraphViewInner() {
  const graph = useKgStore((s) => s.graph);
  const navigationLevel = useKgStore((s) => s.navigationLevel);
  const activeLayerId = useKgStore((s) => s.activeLayerId);
  const selectNode = useKgStore((s) => s.selectNode);
  const selectedNodeId = useKgStore((s) => s.selectedNodeId);

  const overview = useOverviewGraph();
  const layerDetail = useLayerKnowledgeGraph(activeLayerId);

  const isLayoutPending = useMemo(() => {
    if (navigationLevel === "overview") {
      return overview.nodes.length === 0;
    }
    return layerDetail.nodes.length === 0;
  }, [navigationLevel, overview.nodes.length, layerDetail.nodes.length]);

  // Handle export requests.
  useGraphExport();

  const nodes = navigationLevel === "overview" ? overview.nodes : layerDetail.nodes;
  const edges = navigationLevel === "overview" ? overview.edges : layerDetail.edges;

  const onNodeClick = useCallback(
    (_event: React.MouseEvent, node: Node) => {
      if (node.type === "layer-cluster") {
        const data = node.data as { layerId: string };
        useKgStore.getState().drillIntoLayer(data.layerId);
      } else if (node.type === "portal") {
        const data = node.data as { targetLayerId: string };
        useKgStore.getState().drillIntoLayer(data.targetLayerId);
      } else {
        selectNode(node.id === selectedNodeId ? null : node.id);
      }
    },
    [selectNode, selectedNodeId],
  );

  const onPaneClick = useCallback(() => {
    selectNode(null);
  }, [selectNode]);

  if (!graph) {
    return (
      <div className="h-full flex items-center justify-center text-kg-text-muted text-sm">
        No knowledge graph available.
      </div>
    );
  }

  // Check if this graph has no structural layers (pure knowledge graph)
  if (!graph.layers || graph.layers.length === 0) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-center space-y-4 max-w-md">
          <h2 className="text-lg font-semibold text-kg-text-primary">Knowledge Graph View</h2>
          <p className="text-sm text-kg-text-secondary">
            This is a pure knowledge graph without structural layers. Switch to <span className="text-kg-accent font-medium">Knowledge</span> view to explore it.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full w-full relative">
      <Breadcrumb />
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        minZoom={0.05}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        fitView
        fitViewOptions={{ padding: 0.15 }}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={20}
          size={1}
          color="var(--kg-border-subtle)"
        />
        <Controls />
      </ReactFlow>
      {isLayoutPending && (
        <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 bg-kg-root/60 backdrop-blur-sm">
          <Loader2 className="w-8 h-8 animate-spin text-kg-accent" />
          <span className="text-sm text-kg-text-secondary">Building structural layout…</span>
        </div>
      )}
    </div>
  );
}

export default function GraphView() {
  return (
    <ReactFlowProvider>
      <GraphViewInner />
    </ReactFlowProvider>
  );
}
