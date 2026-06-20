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

const FILE_FLOW_LAYOUT_OPTIONS = {
  "elk.algorithm": "layered",
  "elk.direction": "DOWN",
  "elk.spacing.nodeNodeBetweenLayers": "90",
  "elk.spacing.nodeNode": "60",
  "elk.layered.spacing.nodeNodeBetweenLayers": "90",
  "elk.edgeRouting": "ORTHOGONAL",
  "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
  "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
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
  const drillIntoLayer = useKgStore((s) => s.drillIntoLayer);

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
          onDrillIn: drillIntoLayer,
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
  }, [graph, nodesById, nodeIdToLayerId, searchResults, drillIntoLayer]);

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
function useLayerDetailGraph() {
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
  const topo = useLayerDetailTopology();

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
          data: { ...data, isDiffAffected },
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

function useLayerFileFlowGraph() {
  const graph = useKgStore((s) => s.graph);
  const nodesById = useKgStore((s) => s.nodesById);
  const activeLayerId = useKgStore((s) => s.activeLayerId);
  const selectedNodeId = useKgStore((s) => s.selectedNodeId);
  const searchResults = useKgStore((s) => s.searchResults);
  const tourHighlightedNodeIds = useKgStore((s) => s.tourHighlightedNodeIds);
  const nodeTypeFilters = useKgStore((s) => s.nodeTypeFilters);
  const detailLevel = useKgStore((s) => s.detailLevel);
  const showFunctionsInClassView = useKgStore((s) => s.showFunctionsInClassView);
  const selectNode = useKgStore((s) => s.selectNode);
  const diffMode = useKgStore((s) => s.diffMode);
  const changedNodeIds = useKgStore((s) => s.changedNodeIds);
  const affectedNodeIds = useKgStore((s) => s.affectedNodeIds);

  const handleNodeSelect = useCallback(
    (nodeId: string) => {
      const current = useKgStore.getState().selectedNodeId;
      selectNode(current === nodeId ? null : nodeId);
    },
    [selectNode],
  );

  const built = useMemo(() => {
    if (!graph || !activeLayerId) return null;
    const activeLayer = graph.layers.find((l) => l.id === activeLayerId);
    if (!activeLayer) return null;

    const layerNodeIds = new Set(activeLayer.nodeIds);
    const expandedIds = new Set(layerNodeIds);
    if (detailLevel !== "file") {
      for (const edge of graph.edges) {
        if (edge.type === "contains" && layerNodeIds.has(edge.source)) {
          const child = nodesById.get(edge.target);
          if (!child) continue;
          if (child.type === "class") {
            expandedIds.add(edge.target);
          } else if (child.type === "function" && showFunctionsInClassView) {
            expandedIds.add(edge.target);
          }
        }
      }
    }

    const allVisibleTypes = new Set([
      "file", "module", "concept", "config", "document", "service", "table",
      "endpoint", "pipeline", "schema", "resource", "domain", "flow", "step",
      "function", "class",
    ]);

    const filteredNodes = graph.nodes.filter((n) => {
      if (!expandedIds.has(n.id)) return false;
      if (!allVisibleTypes.has(n.type)) return false;
      const category = NODE_TYPE_TO_CATEGORY[n.type] ?? "code";
      return nodeTypeFilters[category] !== false;
    });

    const filteredNodeIds = new Set(filteredNodes.map((n) => n.id));
    const filteredEdges = graph.edges.filter(
      (e) =>
        filteredNodeIds.has(e.source) &&
        filteredNodeIds.has(e.target) &&
        e.type !== "contains",
    );

    const searchMap = new Map(searchResults.map((r) => [r.nodeId, r.score]));
    const tourSet = new Set(tourHighlightedNodeIds);

    const flowNodes: CustomFlowNode[] = filteredNodes.map((node) => {
      const searchScore = searchMap.get(node.id);
      const isTourHighlighted = tourSet.has(node.id);
      const flowNode = buildCustomFlowNode(node, {
        isSelected: false,
        isNeighbor: false,
        isSelectionFaded: false,
        searchScore,
        isTourHighlighted,
        onNodeClick: handleNodeSelect,
      });
      flowNode.data.label = node.filePath ?? node.name;
      return flowNode;
    });

    flowNodes.forEach((n) => {
      n.data.isDiffChanged = diffMode && changedNodeIds.has(n.id);
      n.data.isDiffAffected = diffMode && affectedNodeIds.has(n.id);
      n.data.isDiffFaded = diffMode && !n.data.isDiffChanged && !n.data.isDiffAffected;
    });

    const flowEdges: Edge[] = filteredEdges.map((e, i) => ({
      id: `fe-${i}`,
      source: e.source,
      target: e.target,
      style: {
        stroke: "var(--kg-accent)",
        strokeWidth: 2,
      },
    }));

    const dims = new Map<string, { width: number; height: number }>();
    for (const n of flowNodes) {
      dims.set(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
    }

    return { flowNodes, flowEdges, dims };
  }, [
    graph,
    nodesById,
    activeLayerId,
    searchResults,
    tourHighlightedNodeIds,
    nodeTypeFilters,
    detailLevel,
    showFunctionsInClassView,
    handleNodeSelect,
    diffMode,
    changedNodeIds,
    affectedNodeIds,
  ]);

  const [baseOutput, setBaseOutput] = useState<{ nodes: Node[]; edges: Edge[] }>({ nodes: [], edges: [] });

  useEffect(() => {
    if (!built) {
      setBaseOutput({ nodes: [], edges: [] });
      return;
    }
    let cancelled = false;
    const { flowNodes, flowEdges, dims } = built;
    const baseNodes = flowNodes as unknown as Node[];
    const elkInput: ElkInput = {
      id: "file-flow",
      layoutOptions: FILE_FLOW_LAYOUT_OPTIONS,
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
      setBaseOutput({ nodes: positionedNodes, edges: flowEdges });
      return () => {
        cancelled = true;
      };
    }

    applyElkLayoutWorker(elkInput)
      .then(({ positioned }) => {
        if (cancelled) return;
        setBaseOutput({ nodes: mergeElkPositions(baseNodes, positioned), edges: flowEdges });
      })
      .catch((err) => {
        console.error("[file-flow ELK]", err);
        if (cancelled) return;
        const cols = Math.max(1, Math.ceil(Math.sqrt(baseNodes.length)));
        const fallbackNodes = baseNodes.map((n, idx) => ({
          ...n,
          position: {
            x: (idx % cols) * (NODE_WIDTH + 80),
            y: Math.floor(idx / cols) * (NODE_HEIGHT + 80),
          },
        }));
        setBaseOutput({ nodes: fallbackNodes, edges: flowEdges });
      });
    return () => {
      cancelled = true;
    };
  }, [built, graph]);

  const output = useMemo(() => {
    if (baseOutput.nodes.length === 0) return baseOutput;

    const neighborIds = new Set<string>();
    if (selectedNodeId) {
      for (const edge of baseOutput.edges) {
        if (edge.source === selectedNodeId) neighborIds.add(edge.target);
        if (edge.target === selectedNodeId) neighborIds.add(edge.source);
      }
    }

    const nodes = baseOutput.nodes.map((n) => {
      if (n.type !== "custom") return n;
      const isSelected = n.id === selectedNodeId;
      const isNeighbor = neighborIds.has(n.id);
      const isSelectionFaded = !!selectedNodeId && !isSelected && !isNeighbor;
      return {
        ...n,
        data: {
          ...n.data,
          isSelected,
          isNeighbor,
          isSelectionFaded,
        },
      };
    });

    const edges = baseOutput.edges.map((e) => {
      const isConnected = !!selectedNodeId && (e.source === selectedNodeId || e.target === selectedNodeId);
      return {
        ...e,
        animated: isConnected,
        style: {
          stroke: "var(--kg-accent)",
          strokeWidth: isConnected ? 3 : 2,
          opacity: selectedNodeId ? (isConnected ? 1 : 0.12) : 1,
        },
      };
    });

    return { nodes, edges };
  }, [baseOutput, selectedNodeId]);

  return output;
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function useStage2Layout() {
  const graph = useKgStore((s) => s.graph);
  const activeLayerId = useKgStore((s) => s.activeLayerId);
  const expandedContainers = useKgStore((s) => s.expandedContainers);
  const containerLayoutCache = useKgStore((s) => s.containerLayoutCache);
  const setContainerLayout = useKgStore((s) => s.setContainerLayout);

  const topo = useLayerDetailTopology();

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
          return null;
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

function useFitViewOnNavigation() {
  const { fitView } = useReactFlow();
  const navigationLevel = useKgStore((s) => s.navigationLevel);
  const viewMode = useKgStore((s) => s.viewMode);
  const nodes = useNodes();

  useEffect(() => {
    if (nodes.length === 0) return;
    const timer = setTimeout(() => {
      fitView({ padding: 0.15, duration: 500 });
    }, 150);
    return () => clearTimeout(timer);
  }, [navigationLevel, viewMode, nodes.length, fitView]);
}

function GraphViewInner() {
  const graph = useKgStore((s) => s.graph);
  const navigationLevel = useKgStore((s) => s.navigationLevel);
  const selectNode = useKgStore((s) => s.selectNode);
  const selectedNodeId = useKgStore((s) => s.selectedNodeId);

  const overview = useOverviewGraph();
  const fileFlow = useLayerFileFlowGraph();

  const isLayoutPending = useMemo(() => {
    if (navigationLevel === "overview") {
      return overview.nodes.length === 0;
    }
    return fileFlow.nodes.length === 0;
  }, [navigationLevel, overview.nodes.length, fileFlow.nodes.length]);

  // Handle export requests.
  useGraphExport();

  // Follow tour highlights with the viewport.
  useTourFitView();

  // Re-fit the viewport whenever the user navigates or switches views.
  useFitViewOnNavigation();

  const nodes = navigationLevel === "overview" ? overview.nodes : fileFlow.nodes;
  const edges = navigationLevel === "overview" ? overview.edges : fileFlow.edges;

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
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={20}
          size={1}
          color="var(--kg-border-subtle)"
        />
        <Controls />
        <FitViewOnMount />
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
