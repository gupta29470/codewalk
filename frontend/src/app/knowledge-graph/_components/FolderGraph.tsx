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
} from "@xyflow/react";
import { ArrowLeft, Folder } from "lucide-react";

import type { GraphNode, KnowledgeGraph } from "@/lib/kg/types";
import { useKgStore } from "@/lib/kg/store";
import { cn } from "@/lib/utils";
import { applyForceLayout } from "@/lib/kg/utils/layout";
import { KineticNode, type KineticNodeData } from "./KineticNode";

const FOLDER_NODE_WIDTH = 220;
const FOLDER_NODE_HEIGHT = 80;

type FolderNodeData = {
  id: string;
  name: string;
  fileCount: number;
} & Record<string, unknown>;

interface FolderGraphProps {
  graph: KnowledgeGraph;
  selectedNodeId: string | null;
  onSelectNode: (id: string | null) => void;
}

function folderOf(filePath?: string, module?: string): string {
  if (filePath) {
    const lastSlash = filePath.lastIndexOf("/");
    return lastSlash > 0 ? filePath.slice(0, lastSlash) : "/";
  }
  return module || "/";
}

function isInsideFolder(filePath: string, folder: string): boolean {
  return filePath === folder || filePath.startsWith(folder + "/");
}

function FolderNode({ data, selected }: { data: FolderNodeData; selected?: boolean }) {
  return (
    <div
      className={cn(
        "w-56 rounded-md border border-kinetic-node-file bg-kinetic-surface-container-low px-3 py-2 transition-all",
        selected ? "ring-2 ring-kinetic-primary ring-offset-2 ring-offset-kinetic-root" : "hover:brightness-110",
      )}
    >
      <div className="flex items-center gap-2">
        <Folder size={16} className="text-kinetic-tertiary" />
        <div className="min-w-0">
          <div className="truncate text-xs font-semibold text-kinetic-on-surface kinetic-font-mono">
            {data.name}
          </div>
          <div className="text-[10px] uppercase tracking-wide text-kinetic-on-surface-variant">
            {data.fileCount} file{data.fileCount === 1 ? "" : "s"}
          </div>
        </div>
      </div>
    </div>
  );
}

const folderNodeTypes = { folder: FolderNode };
const fileNodeTypes = { kinetic: KineticNode };

function FolderGraphInner({ graph, selectedNodeId, onSelectNode }: FolderGraphProps) {
  const { fitView, getEdges } = useReactFlow();
  const [drillFolder, setDrillFolder] = useState<string | null>(null);
  const changedNodeIds = useKgStore((s) => s.changedNodeIds);
  const affectedNodeIds = useKgStore((s) => s.affectedNodeIds);

  const fileGraph = useMemo(() => {
    const fileNodesMap = new Map<string, GraphNode>();
    const nodeToFileId = new Map<string, string>();

    for (const n of graph.nodes) {
      if (n.type === "file" && n.filePath) {
        fileNodesMap.set(n.id, n);
        nodeToFileId.set(n.id, n.id);
      }
    }

    for (const n of graph.nodes) {
      if (n.filePath && !fileNodesMap.has(n.id)) {
        const fileNid = `file:${n.filePath}`;
        if (fileNodesMap.has(fileNid)) {
          nodeToFileId.set(n.id, fileNid);
        }
      }
    }

    const fileEdgeCounts = new Map<string, number>();
    for (const e of graph.edges) {
      const srcFile = nodeToFileId.get(e.source);
      const tgtFile = nodeToFileId.get(e.target);
      if (srcFile && tgtFile && srcFile !== tgtFile) {
        const key = `${srcFile}|${tgtFile}`;
        fileEdgeCounts.set(key, (fileEdgeCounts.get(key) ?? 0) + 1);
      }
    }

    return { fileNodesMap, nodeToFileId, fileEdgeCounts };
  }, [graph]);

  const folderInitial = useMemo(() => {
    const { fileNodesMap, fileEdgeCounts } = fileGraph;
    const folderMap = new Map<string, number>();

    for (const n of Array.from(fileNodesMap.values())) {
      const dir = folderOf(n.filePath, n.module);
      folderMap.set(dir, (folderMap.get(dir) ?? 0) + 1);
    }

    const folderIds = Array.from(folderMap.keys());

    const folderEdgesMap = new Map<string, number>();
    for (const [key, weight] of Array.from(fileEdgeCounts.entries())) {
      const [srcId, tgtId] = key.split("|");
      const srcNode = fileNodesMap.get(srcId);
      const tgtNode = fileNodesMap.get(tgtId);
      if (!srcNode || !tgtNode) continue;
      const srcFolder = folderOf(srcNode.filePath, srcNode.module);
      const tgtFolder = folderOf(tgtNode.filePath, tgtNode.module);
      if (srcFolder === tgtFolder) continue;
      const folderKey = `${srcFolder}|${tgtFolder}`;
      folderEdgesMap.set(folderKey, (folderEdgesMap.get(folderKey) ?? 0) + weight);
    }

    const nodes: Node<FolderNodeData>[] = folderIds.map((id) => ({
      id,
      type: "folder",
      position: { x: 0, y: 0 },
      data: {
        id,
        name: id,
        fileCount: folderMap.get(id) ?? 0,
      },
    }));

    const edges: Edge[] = Array.from(folderEdgesMap.entries()).map(([key, weight], i) => {
      const [source, target] = key.split("|");
      return {
        id: `folder-edge-${i}`,
        source,
        target,
        type: "smoothstep",
        style: {
          stroke: "var(--kinetic-primary)",
          strokeWidth: Math.min(Math.max(weight, 1), 4),
          opacity: 0.6,
        },
      };
    });

    const dims = new Map<string, { width: number; height: number }>();
    nodes.forEach((n) => dims.set(n.id, { width: FOLDER_NODE_WIDTH, height: FOLDER_NODE_HEIGHT }));

    const { nodes: positioned } = applyForceLayout(nodes, edges, dims);
    return { nodes: positioned, edges };
  }, [fileGraph]);

  const fileInitial = useMemo(() => {
    if (!drillFolder) return { nodes: [] as Node<KineticNodeData>[], edges: [] as Edge[] };

    const { fileNodesMap, fileEdgeCounts } = fileGraph;

    const folderFileIds = new Set<string>();
    for (const [id, n] of Array.from(fileNodesMap.entries())) {
      if (n.filePath && isInsideFolder(n.filePath, drillFolder)) {
        folderFileIds.add(id);
      }
    }

    const visibleFileIds = new Set(folderFileIds);
    for (const [key] of Array.from(fileEdgeCounts.entries())) {
      const [srcId, tgtId] = key.split("|");
      if (folderFileIds.has(srcId) || folderFileIds.has(tgtId)) {
        visibleFileIds.add(srcId);
        visibleFileIds.add(tgtId);
      }
    }

    const statusOf = (id: string) =>
      changedNodeIds.has(id) ? "changed" : affectedNodeIds.has(id) ? "unchanged" : "analyzed";

    const nodes: Node<KineticNodeData>[] = Array.from(visibleFileIds).map((id) => {
      const n = fileNodesMap.get(id)!;
      return {
        id,
        type: "kinetic",
        position: { x: 0, y: 0 },
        data: {
          ...n,
          status: statusOf(id),
        },
      };
    });

    const edges: Edge[] = Array.from(fileEdgeCounts.entries())
      .filter(([key]) => {
        const [srcId, tgtId] = key.split("|");
        return visibleFileIds.has(srcId) && visibleFileIds.has(tgtId);
      })
      .map(([key, weight], i) => {
        const [source, target] = key.split("|");
        return {
          id: `file-edge-${i}`,
          source,
          target,
          type: "smoothstep",
          style: {
            stroke: "var(--kinetic-outline-variant)",
            strokeWidth: Math.min(Math.max(weight, 1), 2),
          },
        };
      });

    const dims = new Map<string, { width: number; height: number }>();
    nodes.forEach((n) => dims.set(n.id, { width: 220, height: 90 }));

    const { nodes: positioned } = applyForceLayout(nodes, edges, dims);
    return { nodes: positioned, edges };
  }, [fileGraph, drillFolder, changedNodeIds, affectedNodeIds]);

  const [folderNodes, , onFolderNodesChange] = useNodesState(folderInitial.nodes);
  const [folderEdges, , onFolderEdgesChange] = useEdgesState(folderInitial.edges);
  const [fileNodes, setFileNodes, onFileNodesChange] = useNodesState(fileInitial.nodes);
  const [fileEdges, setFileEdges, onFileEdgesChange] = useEdgesState(fileInitial.edges);

  useEffect(() => {
    if (drillFolder) {
      setFileNodes(fileInitial.nodes);
      setFileEdges(fileInitial.edges);
      window.setTimeout(() => fitView({ duration: 300, padding: 0.2 }), 50);
    }
  }, [drillFolder, fileInitial, setFileNodes, setFileEdges, fitView]);

  function handleFolderClick(folderId: string) {
    setDrillFolder(folderId);
    onSelectNode(null);
  }

  function handleFileClick(fileId: string) {
    onSelectNode(fileId === selectedNodeId ? null : fileId);
  }

  const highlightFilePath = useCallback(
    (fileId: string | null) => {
      const edges = getEdges();
      const connectedNodeIds = new Set<string>();
      const connectedEdgeIds = new Set<string>();
      if (fileId) {
        connectedNodeIds.add(fileId);
        edges.forEach((e, i) => {
          if (e.source === fileId || e.target === fileId) {
            connectedNodeIds.add(e.source);
            connectedNodeIds.add(e.target);
            connectedEdgeIds.add(`file-edge-${i}`);
          }
        });
      }

      setFileNodes((prev) =>
        prev.map((n) => {
          const isConnected = connectedNodeIds.has(n.id);
          return {
            ...n,
            selected: n.id === fileId,
            style: {
              ...n.style,
              opacity: fileId ? (isConnected ? 1 : 0.15) : 1,
              transition: "opacity 200ms ease",
            },
          };
        }),
      );

      setFileEdges((prev) =>
        prev.map((e, i) => {
          const id = `file-edge-${i}`;
          const isConnected = connectedEdgeIds.has(id);
          return {
            ...e,
            animated: isConnected,
            style: {
              ...e.style,
              opacity: fileId ? (isConnected ? 1 : 0.05) : 1,
              stroke: isConnected ? "var(--kinetic-primary)" : "var(--kinetic-outline-variant)",
              strokeWidth: isConnected ? 2 : 1,
              transition: "stroke 200ms ease, stroke-width 200ms ease, opacity 200ms ease",
            },
          };
        }),
      );
    },
    [getEdges, setFileNodes, setFileEdges],
  );

  useEffect(() => {
    highlightFilePath(selectedNodeId);
  }, [selectedNodeId, highlightFilePath]);

  if (drillFolder) {
    return (
      <div className="flex h-full w-full flex-col bg-kinetic-root">
        <div className="flex h-9 items-center gap-2 border-b border-kinetic-border bg-kinetic-surface-container-low px-4">
          <button
            onClick={() => setDrillFolder(null)}
            className="flex items-center gap-1 rounded px-2 py-1 text-xs font-medium text-kinetic-on-surface hover:bg-kinetic-surface-container-high"
          >
            <ArrowLeft size={14} />
            Back to folders
          </button>
          <span className="text-xs text-kinetic-on-surface-variant kinetic-font-mono">{drillFolder}</span>
        </div>
        <div className="flex-1 min-h-0">
          <ReactFlow
            className="kinetic-graph"
            nodes={fileNodes}
            edges={fileEdges}
            nodeTypes={fileNodeTypes}
            onNodesChange={onFileNodesChange}
            onEdgesChange={onFileEdgesChange}
            onNodeClick={(_, node) => handleFileClick(node.id)}
            onPaneClick={() => onSelectNode(null)}
            fitView
            fitViewOptions={{ padding: 0.2 }}
            minZoom={0.1}
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
        </div>
      </div>
    );
  }

  return (
    <div className="h-full w-full bg-kinetic-root">
      <ReactFlow
        className="kinetic-graph"
        nodes={folderNodes}
        edges={folderEdges}
        nodeTypes={folderNodeTypes}
        onNodesChange={onFolderNodesChange}
        onEdgesChange={onFolderEdgesChange}
        onNodeClick={(_, node) => handleFolderClick(node.id)}
        onPaneClick={() => onSelectNode(null)}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.1}
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
    </div>
  );
}

export function FolderGraph(props: FolderGraphProps) {
  return (
    <ReactFlowProvider>
      <FolderGraphInner {...props} />
    </ReactFlowProvider>
  );
}
