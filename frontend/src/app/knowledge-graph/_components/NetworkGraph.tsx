"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  useReactFlow,
  type Node,
  type Edge,
  type Connection,
  addEdge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Loader2, Search, Settings } from "lucide-react";

import { cn } from "@/lib/utils";
import type { GraphNode, KnowledgeGraph, NodeType } from "@/lib/kg/types";
import { NODE_TYPE_TO_CATEGORY } from "@/lib/kg/types";
import { useKgStore, type KnowledgeViewFilter } from "@/lib/kg/store";
import { NODE_HEIGHT, NODE_WIDTH } from "@/lib/kg/utils/layout";
import { KineticNode, type KineticNodeData } from "./KineticNode";
import { LayerDetail } from "./LayerDetail";
import { DependencyView } from "./DependencyView";
import { SegmentedControl } from "@/components/kinetic/SegmentedControl";

const nodeTypes = { kinetic: KineticNode };

const FILE_LIKE_TYPES: Set<NodeType> = new Set<NodeType>([
  "file",
  "module",
  "config",
  "document",
  "service",
  "endpoint",
  "table",
  "pipeline",
  "resource",
]);

const FUNC_LIKE_TYPES: Set<NodeType> = new Set<NodeType>([
  "function",
  "class",
  "method",
  "concept",
]);

interface NetworkGraphProps {
  graph: KnowledgeGraph;
  selectedNodeId: string | null;
  onSelectNode: (id: string | null) => void;
  onOpenSettings?: () => void;
}

type ViewTab = "network" | "layer" | "dependency";

function buildFlowNodes(
  graph: KnowledgeGraph,
  changed: Set<string>,
  affected: Set<string>,
): Node<KineticNodeData>[] {
  return graph.nodes.map((n) => {
    const status: "analyzed" | "changed" | "unchanged" = changed.has(n.id)
      ? "changed"
      : affected.has(n.id)
        ? "unchanged"
        : "analyzed";
    return {
      id: n.id,
      type: "kinetic",
      position: { x: n.x ?? 0, y: n.y ?? 0 },
      data: {
        ...n,
        status,
      },
    };
  });
}

function buildFlowEdges(graph: KnowledgeGraph): Edge[] {
  return graph.edges.map((e, i) => ({
    id: `${e.source}-${e.target}-${i}`,
    source: e.source,
    target: e.target,
    type: "smoothstep",
    style: { stroke: "var(--kinetic-outline-variant)", strokeWidth: 1 },
    animated: false,
  }));
}

function NetworkGraphInner({ graph, selectedNodeId, onSelectNode, onOpenSettings }: NetworkGraphProps) {
  const [tab, setTab] = useState<ViewTab>("network");
  const [layouting, setLayouting] = useState(false);
  const changedNodeIds = useKgStore((s) => s.changedNodeIds);
  const affectedNodeIds = useKgStore((s) => s.affectedNodeIds);
  const searchQuery = useKgStore((s) => s.searchQuery);
  const setSearchQuery = useKgStore((s) => s.setSearchQuery);
  const nodeTypeFilters = useKgStore((s) => s.nodeTypeFilters);
  const toggleNodeTypeFilter = useKgStore((s) => s.toggleNodeTypeFilter);
  const knowledgeViewFilter = useKgStore((s) => s.knowledgeViewFilter);
  const setKnowledgeViewFilter = useKgStore((s) => s.setKnowledgeViewFilter);

  const [debouncedQuery, setDebouncedQuery] = useState(searchQuery);
  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedQuery(searchQuery), 200);
    return () => window.clearTimeout(t);
  }, [searchQuery]);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node<KineticNodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const { fitView } = useReactFlow();

  // Run force layout in a Web Worker so the UI stays responsive.
  useEffect(() => {
    setLayouting(true);
    const layoutNodes: Node[] = graph.nodes.map((n) => ({
      id: n.id,
      type: "kinetic",
      position: { x: 0, y: 0 },
      data: {},
    }));
    const layoutEdges = buildFlowEdges(graph);
    const dimsArray: [string, { width: number; height: number }][] = graph.nodes.map((n) => [
      n.id,
      { width: NODE_WIDTH, height: NODE_HEIGHT },
    ]);

    const worker = new Worker(new URL("./layout.worker.ts", import.meta.url));

    worker.postMessage({ nodes: layoutNodes, edges: layoutEdges, dimsArray });

    worker.onmessage = (event: MessageEvent<{ positioned: Node[] }>) => {
      const { positioned } = event.data;
      const positionMap = new Map(positioned.map((n) => [n.id, n.position]));
      const fullNodes = buildFlowNodes(graph, changedNodeIds, affectedNodeIds).map((n) => {
        const data = n.data as unknown as GraphNode;
        const category = NODE_TYPE_TO_CATEGORY[data.type];
        const filteredByCategory = !nodeTypeFilters[category];
        const filteredByLevel =
          knowledgeViewFilter === "files"
            ? !FILE_LIKE_TYPES.has(data.type)
            : knowledgeViewFilter === "functions"
              ? !FUNC_LIKE_TYPES.has(data.type)
              : false;
        return {
          ...n,
          position: positionMap.get(n.id) ?? n.position,
          hidden: filteredByCategory || filteredByLevel,
        };
      });
      setNodes(fullNodes);
      setEdges(layoutEdges);
      setLayouting(false);
      window.setTimeout(() => fitView({ duration: 300, padding: 0.05 }), 100);
    };

    worker.onerror = () => {
      setLayouting(false);
    };

    return () => worker.terminate();
    // Filters are applied to the result without re-running layout.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, changedNodeIds, affectedNodeIds, setNodes, setEdges, fitView]);

  // Update statuses when diff overlay arrives without re-layouting.
  useEffect(() => {
    setNodes((prev) =>
      prev.map((n) => {
        const status: "analyzed" | "changed" | "unchanged" = changedNodeIds.has(n.id)
          ? "changed"
          : affectedNodeIds.has(n.id)
            ? "unchanged"
            : "analyzed";
        return {
          ...n,
          data: { ...n.data, status },
        };
      }),
    );
  }, [changedNodeIds, affectedNodeIds, setNodes]);

  useEffect(() => {
    const connectedNodeIds = new Set<string>();
    const connectedEdgeIds = new Set<string>();
    if (selectedNodeId) {
      connectedNodeIds.add(selectedNodeId);
      graph.edges.forEach((e, i) => {
        if (e.source === selectedNodeId || e.target === selectedNodeId) {
          connectedNodeIds.add(e.source);
          connectedNodeIds.add(e.target);
          connectedEdgeIds.add(`${e.source}-${e.target}-${i}`);
        }
      });
    }

    setNodes((prev) =>
      prev.map((n) => {
        const isConnected = connectedNodeIds.has(n.id);
        return {
          ...n,
          selected: n.id === selectedNodeId,
          style: {
            ...n.style,
            opacity: selectedNodeId ? (isConnected ? 1 : 0.15) : 1,
            transition: "opacity 200ms ease",
          },
        };
      }),
    );

    setEdges((prev) =>
      prev.map((e, i) => {
        const id = `${e.source}-${e.target}-${i}`;
        const isConnected = connectedEdgeIds.has(id);
        return {
          ...e,
          animated: isConnected,
          style: {
            ...e.style,
            opacity: selectedNodeId ? (isConnected ? 1 : 0.05) : 1,
            stroke: isConnected ? "var(--kinetic-primary)" : "var(--kinetic-outline-variant)",
            strokeWidth: isConnected ? 2 : 1,
            transition: "stroke 200ms ease, stroke-width 200ms ease, opacity 200ms ease",
          },
        };
      }),
    );

    if (selectedNodeId) {
      window.setTimeout(() => {
        fitView({ nodes: [{ id: selectedNodeId }], duration: 300, padding: 0.3 });
      }, 50);
    }
  }, [selectedNodeId, graph.edges, setNodes, setEdges, fitView]);

  // Filter by category and by files/funcs/both view level.
  useEffect(() => {
    setNodes((prev) =>
      prev.map((n) => {
        const data = n.data as unknown as GraphNode;
        const category = NODE_TYPE_TO_CATEGORY[data.type];
        const filteredByCategory = !nodeTypeFilters[category];
        const filteredByLevel =
          knowledgeViewFilter === "files"
            ? !FILE_LIKE_TYPES.has(data.type)
            : knowledgeViewFilter === "functions"
              ? !FUNC_LIKE_TYPES.has(data.type)
              : false;
        return {
          ...n,
          hidden: filteredByCategory || filteredByLevel,
        };
      }),
    );
  }, [nodeTypeFilters, knowledgeViewFilter, setNodes]);

  // Hide edges that connect to hidden nodes.
  useEffect(() => {
    const visible = new Set(nodes.filter((n) => !n.hidden).map((n) => n.id));
    setEdges((prev) =>
      prev.map((e) => ({
        ...e,
        hidden: !(visible.has(e.source) && visible.has(e.target)),
      })),
    );
  }, [nodes, setEdges]);

  useEffect(() => {
    const q = debouncedQuery.trim().toLowerCase();
    if (!q) {
      onSelectNode(null);
      return;
    }
    const match = graph.nodes.find((n) => {
      const hay = [n.name, n.qualifiedName, n.filePath].filter(Boolean).join(" ").toLowerCase();
      return hay.includes(q);
    });
    if (match) {
      onSelectNode(match.id);
    } else {
      onSelectNode(null);
    }
  }, [debouncedQuery, graph.nodes, onSelectNode]);

  const onConnect = useCallback(
    (params: Connection) => setEdges((eds) => addEdge(params, eds)),
    [setEdges],
  );

  const visibleNodeCount = useMemo(
    () => nodes.filter((n) => !n.hidden).length,
    [nodes],
  );
  const visibleEdgeCount = useMemo(
    () => edges.filter((e) => !e.hidden).length,
    [edges],
  );

  return (
    <div className="flex h-full w-full flex-col">
      <div className="flex min-h-11 flex-wrap items-center justify-between gap-2 border-b border-kinetic-border bg-kinetic-surface-container-low px-4 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <SegmentedControl<ViewTab>
            options={[
              { value: "network", label: "Network Graph" },
              { value: "layer", label: "Layer Detail" },
              { value: "dependency", label: "Dependency" },
            ]}
            value={tab}
            onChange={setTab}
          />
          {tab === "network" && (
            <>
              <SegmentedControl<KnowledgeViewFilter>
                options={[
                  { value: "files", label: "Files" },
                  { value: "functions", label: "Funcs" },
                  { value: "both", label: "Both" },
                ]}
                value={knowledgeViewFilter}
                onChange={setKnowledgeViewFilter}
              />
              <div className="hidden lg:flex items-center gap-1">
                {(Object.keys(nodeTypeFilters) as Array<keyof typeof nodeTypeFilters>).map((key) => (
                  <button
                    key={key}
                    onClick={() => toggleNodeTypeFilter(key)}
                    className={cn(
                      "rounded px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider transition-colors",
                      nodeTypeFilters[key]
                        ? "bg-kinetic-primary/15 text-kinetic-primary"
                        : "bg-kinetic-surface-container-high text-kinetic-on-surface-variant line-through",
                    )}
                  >
                    {key}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
        <div className="flex items-center gap-2">
          <div className="relative hidden sm:flex items-center gap-2 rounded-md border border-kinetic-border bg-kinetic-surface-container px-2 py-1">
            <Search size={14} className="text-kinetic-outline flex-shrink-0" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setSearchQuery("");
              }}
              placeholder="Search nodes..."
              className="w-28 lg:w-40 bg-transparent text-xs text-kinetic-on-surface placeholder:text-kinetic-on-surface-variant outline-none kinetic-font-mono"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery("")}
                className="text-[10px] text-kinetic-on-surface-variant hover:text-kinetic-on-surface flex-shrink-0"
              >
                clear
              </button>
            )}
          </div>
          {onOpenSettings && (
            <button
              onClick={onOpenSettings}
              className="rounded-md border border-kinetic-border p-1.5 text-kinetic-on-surface-variant hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface"
              title="Settings"
            >
              <Settings size={16} />
            </button>
          )}
          <span className="text-xs text-kinetic-on-surface-variant hidden xl:inline whitespace-nowrap">
            {visibleNodeCount} / {graph.nodes.length} nodes · {visibleEdgeCount} / {graph.edges.length} edges
          </span>
        </div>
      </div>

      <div className="relative flex-1 min-h-0 bg-kinetic-root">
        {tab === "network" ? (
          <>
            <ReactFlow
              className="kinetic-graph"
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              onNodeClick={(_, node) =>
                onSelectNode(node.id === selectedNodeId ? null : node.id)
              }
              onPaneClick={() => onSelectNode(null)}
              fitViewOptions={{ padding: 0.05 }}
              minZoom={0.05}
              maxZoom={2}
              onlyRenderVisibleElements
              selectNodesOnDrag={false}
              selectionKeyCode={null}
              multiSelectionKeyCode={null}
              deleteKeyCode={null}
              proOptions={{ hideAttribution: true }}
            >
              <Background color="var(--kinetic-outline-variant)" gap={24} size={1} />
              <Controls className="!bg-kinetic-surface-container !border-kinetic-border !text-kinetic-on-surface-variant" />
            </ReactFlow>
            {layouting && (
              <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 bg-kinetic-root/80 text-kinetic-on-surface-variant">
                <Loader2 size={24} className="animate-spin text-kinetic-primary" />
                <span className="text-xs">Computing layout…</span>
              </div>
            )}
          </>
        ) : tab === "layer" ? (
          <LayerDetail
            graph={graph}
            selectedNodeId={selectedNodeId}
            onSelectNode={onSelectNode}
          />
        ) : (
          <DependencyView
            graph={graph}
            selectedNodeId={selectedNodeId}
            onSelectNode={onSelectNode}
          />
        )}
      </div>
    </div>
  );
}

export function NetworkGraph(props: NetworkGraphProps) {
  return (
    <ReactFlowProvider>
      <NetworkGraphInner {...props} />
    </ReactFlowProvider>
  );
}
