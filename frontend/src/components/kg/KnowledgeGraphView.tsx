"use client";

import React, { useMemo, useCallback, useEffect, useRef } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  useNodes,
  Background,
  BackgroundVariant,
  Controls,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { CustomNode, type CustomFlowNode } from "./CustomNode";
import { useKgStore } from "@/lib/kg/store";
import { applyForceLayout, NODE_WIDTH, NODE_HEIGHT } from "@/lib/kg/utils/layout";
import { buildSvgFromNodes, downloadSvg, downloadPng } from "@/lib/kg/utils/export";
import type { KnowledgeGraph } from "@/lib/kg/types";

const nodeTypes = { custom: CustomNode };

function getNodeDimensions(edgeCount: number): { width: number; height: number } {
  const scale = Math.min(1.5, Math.max(0.85, 0.85 + edgeCount * 0.03));
  return {
    width: Math.round(NODE_WIDTH * scale),
    height: Math.round(NODE_HEIGHT * scale),
  };
}

function computeLayout(graph: KnowledgeGraph) {
  const edgeCounts = new Map<string, number>();
  for (const edge of graph.edges) {
    edgeCounts.set(edge.source, (edgeCounts.get(edge.source) ?? 0) + 1);
    edgeCounts.set(edge.target, (edgeCounts.get(edge.target) ?? 0) + 1);
  }

  const communityMap = new Map<string, number>();
  graph.layers.forEach((layer, i) => {
    for (const nodeId of layer.nodeIds) {
      communityMap.set(nodeId, i);
    }
  });

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

    const { nodes: layoutedNodes } = applyForceLayout(tmpNodes, tmpEdges, dims, communityMap);
    for (const n of layoutedNodes) {
      positionMap.set(n.id, n.position);
    }
  }

  return { positionMap, edgeCounts, communityMap };
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

function KnowledgeGraphViewInner() {
  const graph = useKgStore((s) => s.graph);
  const selectedNodeId = useKgStore((s) => s.selectedNodeId);
  const focusNodeId = useKgStore((s) => s.focusNodeId);
  const selectNode = useKgStore((s) => s.selectNode);
  const searchResultsRaw = useKgStore((s) => s.searchResults);
  const tourHighlightedNodeIds = useKgStore((s) => s.tourHighlightedNodeIds);
  const nodeTypeFilters = useKgStore((s) => s.nodeTypeFilters);
  const knowledgeViewFilter = useKgStore((s) => s.knowledgeViewFilter);
  const diffMode = useKgStore((s) => s.diffMode);
  const changedNodeIds = useKgStore((s) => s.changedNodeIds);
  const affectedNodeIds = useKgStore((s) => s.affectedNodeIds);
  const activeLayerId = useKgStore((s) => s.activeLayerId);

  useGraphExport();
  useTourFitView();

  const onNodeClick = useCallback(
    (_event: React.MouseEvent, node: Node) => {
      selectNode(node.id === selectedNodeId ? null : node.id);
    },
    [selectNode, selectedNodeId],
  );

  const searchResults = useMemo(
    () => new Map(searchResultsRaw.map((r) => [r.nodeId, r.score])),
    [searchResultsRaw],
  );
  const tourSet = useMemo(() => new Set(tourHighlightedNodeIds), [tourHighlightedNodeIds]);

  const filteredGraph = useMemo((): KnowledgeGraph | null => {
    if (!graph) return null;
    const activeLayer = activeLayerId ? graph.layers.find((l) => l.id === activeLayerId) : null;
    const layerNodeIds = activeLayer ? new Set(activeLayer.nodeIds) : null;

    const codeFilters = new Set<string>();
    if (knowledgeViewFilter === "files" || knowledgeViewFilter === "both") {
      codeFilters.add("file");
    }
    if (knowledgeViewFilter === "functions" || knowledgeViewFilter === "both") {
      codeFilters.add("function");
      codeFilters.add("method");
      codeFilters.add("class");
    }

    const filteredNodes = graph.nodes.filter((n) => {
      if (layerNodeIds && !layerNodeIds.has(n.id)) return false;
      if (["article", "entity", "topic", "claim", "source"].includes(n.type)) {
        return nodeTypeFilters.knowledge !== false;
      }
      return codeFilters.has(n.type);
    });
    const filteredNodeIds = new Set(filteredNodes.map((n) => n.id));
    const filteredEdges = graph.edges.filter(
      (e) => filteredNodeIds.has(e.source) && filteredNodeIds.has(e.target),
    );
    return { ...graph, nodes: filteredNodes, edges: filteredEdges };
  }, [graph, activeLayerId, nodeTypeFilters, knowledgeViewFilter]);

  const { positionMap, edgeCounts } = useMemo(() => {
    if (!filteredGraph) return { positionMap: new Map(), edgeCounts: new Map() };
    return computeLayout(filteredGraph);
  }, [filteredGraph]);

  const { nodes, edges } = useMemo(() => {
    if (!filteredGraph) return { nodes: [], edges: [] };

    const neighborIds = new Set<string>();
    const focusId = focusNodeId ?? selectedNodeId;
    if (focusId) {
      for (const edge of filteredGraph.edges) {
        if (edge.source === focusId) neighborIds.add(edge.target);
        if (edge.target === focusId) neighborIds.add(edge.source);
      }
    }

    const rfNodes: CustomFlowNode[] = filteredGraph.nodes.map((node) => {
      const isSelected = node.id === selectedNodeId;
      const isFocused = node.id === focusNodeId;
      const isNeighbor = neighborIds.has(node.id);
      const isSelectionFaded = !!focusId && !isSelected && !isFocused && !isNeighbor;
      const searchScore = searchResults.get(node.id);
      const isTourHighlighted = tourSet.has(node.id);
      const isDiffChanged = diffMode && changedNodeIds.has(node.id);
      const isDiffAffected = diffMode && affectedNodeIds.has(node.id);
      const isDiffFaded = diffMode && !isDiffChanged && !isDiffAffected;

      return {
        id: node.id,
        type: "custom",
        position: positionMap.get(node.id) ?? { x: 0, y: 0 },
        data: {
          label: node.name,
          nodeType: node.type,
          summary: node.summary,
          complexity: node.complexity,
          tags: node.tags,
          isHighlighted: searchScore !== undefined,
          searchScore,
          isSelected,
          isTourHighlighted,
          isDiffChanged,
          isDiffAffected,
          isDiffFaded,
          isNeighbor,
          isSelectionFaded: !!isSelectionFaded,
          onNodeClick: (id: string) => selectNode(id),
          incomingCount: edgeCounts.get(node.id) ?? 0,
        },
      };
    });

    const rfEdges: Edge[] = filteredGraph.edges.map((e, i) => {
      const connected = selectedNodeId && (e.source === selectedNodeId || e.target === selectedNodeId);
      return {
        id: `ke-${i}`,
        source: e.source,
        target: e.target,
        label: e.type,
        animated: !!connected,
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
    edgeCounts,
    selectNode,
    diffMode,
    changedNodeIds,
    affectedNodeIds,
  ]);

  if (!graph) {
    return (
      <div className="h-full flex items-center justify-center text-kg-text-muted text-sm">
        No knowledge graph available.
      </div>
    );
  }

  return (
    <div className="h-full w-full relative">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        minZoom={0.05}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
        onNodeClick={onNodeClick}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={20}
          size={1}
          color="var(--kg-border-subtle)"
        />
        <Controls />
      </ReactFlow>
    </div>
  );
}

export default function KnowledgeGraphView() {
  return (
    <ReactFlowProvider>
      <KnowledgeGraphViewInner />
    </ReactFlowProvider>
  );
}
