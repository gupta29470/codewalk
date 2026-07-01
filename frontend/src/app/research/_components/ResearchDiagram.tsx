"use client";

import { useEffect, useState } from "react";
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
  Position,
  Handle,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { FileCode, FunctionSquare, Boxes, Box, Maximize2, Minimize2, X, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ResearchDiagram, ResearchDiagramNode, ResearchDiagramEdge } from "@/lib/api";
import { cn } from "@/lib/utils";

interface NodeData extends Record<string, unknown> {
  label: string;
  fullPath: string;
  type: string;
  startLine?: number;
  endLine?: number;
}

const typeIcons: Record<string, React.ReactNode> = {
  file: <FileCode className="h-4 w-4" />,
  function: <FunctionSquare className="h-3.5 w-3.5" />,
  class: <Boxes className="h-4 w-4" />,
  method: <Box className="h-3.5 w-3.5" />,
};

const HEADER_H = 38;
const PAD = 14;
const CHILD_W = 170;
const CHILD_H = 32;
const GAP_Y = 10;
const FILE_MIN_W = 210;
const FILE_MIN_H = 70;
const CLASS_MIN_W = 180;
const CLASS_MIN_H = 56;

function FileNode({ data }: { data: NodeData }) {
  return (
    <div className="w-full h-full rounded-xl border-2 border-kinetic-primary/50 bg-kinetic-primary/10 px-3 py-2 flex items-center gap-2 text-kinetic-on-surface">
      <Handle type="target" position={Position.Left} className="!bg-kinetic-outline" />
      {typeIcons.file}
      <div className="flex flex-col min-w-0">
        <span className="font-semibold text-sm truncate">{data.label}</span>
        <span className="text-[10px] opacity-70 truncate">{data.fullPath.split("/").pop()}</span>
      </div>
      <Handle type="source" position={Position.Right} className="!bg-kinetic-outline" />
    </div>
  );
}

function ClassNode({ data }: { data: NodeData }) {
  return (
    <div className="w-full h-full rounded-lg border border-kinetic-tertiary/50 bg-kinetic-tertiary/10 px-2 py-1 flex items-center gap-1.5 text-kinetic-on-surface">
      <Handle type="target" position={Position.Left} className="!bg-kinetic-outline" />
      {typeIcons.class}
      <span className="font-medium text-xs truncate">{data.label}</span>
      <Handle type="source" position={Position.Right} className="!bg-kinetic-outline" />
    </div>
  );
}

function FunctionNode({ data }: { data: NodeData }) {
  return (
    <div className="w-full h-full rounded-md border border-kinetic-secondary/40 bg-kinetic-secondary/10 px-2 py-1 flex items-center gap-1.5 text-kinetic-on-surface">
      <Handle type="target" position={Position.Left} className="!bg-kinetic-outline" />
      {typeIcons.function}
      <span className="text-xs truncate">{data.label}</span>
      <Handle type="source" position={Position.Right} className="!bg-kinetic-outline" />
    </div>
  );
}

function MethodNode({ data }: { data: NodeData }) {
  return (
    <div className="w-full h-full rounded-md border border-kinetic-border bg-kinetic-surface-container-high px-2 py-1 flex items-center gap-1.5 text-kinetic-on-surface-variant">
      <Handle type="target" position={Position.Left} className="!bg-kinetic-outline" />
      {typeIcons.method}
      <span className="text-xs truncate">{data.label}</span>
      <Handle type="source" position={Position.Right} className="!bg-kinetic-outline" />
    </div>
  );
}

const nodeTypes = {
  file: FileNode,
  class: ClassNode,
  function: FunctionNode,
  method: MethodNode,
};

function toFlowNode(node: ResearchDiagramNode): Node<NodeData> {
  return {
    id: node.id,
    type: node.type,
    position: { x: node.x ?? 0, y: node.y ?? 0 },
    data: {
      label: node.name,
      fullPath: node.full_path,
      type: node.type,
      startLine: node.start_line,
      endLine: node.end_line,
    },
    parentId: node.parentId,
    extent: "parent",
  };
}

function toFlowEdge(edge: ResearchDiagramEdge, index: number): Edge {
  const edgeStyles: Record<string, { stroke: string; strokeDasharray?: string }> = {
    imports: { stroke: "var(--kinetic-primary)", strokeDasharray: "4 4" },
    calls: { stroke: "var(--kinetic-secondary)" },
  };

  return {
    id: `${edge.source}-${edge.target}-${index}`,
    source: edge.source,
    target: edge.target,
    type: "smoothstep",
    style: { strokeWidth: 1.5, ...edgeStyles[edge.type] },
    animated: edge.type === "calls",
  };
}

interface LayoutNode {
  id: string;
  type: string;
  width: number;
  height: number;
  children: LayoutNode[];
  level: number;
}

function buildLayoutTree(nodes: ResearchDiagramNode[]): Map<string, LayoutNode> {
  const map = new Map<string, LayoutNode>();

  // First pass: create nodes.
  nodes.forEach((n) => {
    map.set(n.id, {
      id: n.id,
      type: n.type,
      width: n.type === "file" ? FILE_MIN_W : n.type === "class" ? CLASS_MIN_W : CHILD_W,
      height: n.type === "file" ? FILE_MIN_H : n.type === "class" ? CLASS_MIN_H : CHILD_H,
      children: [],
      level: n.level ?? 0,
    });
  });

  // Second pass: link children to parents.
  const roots: LayoutNode[] = [];
  nodes.forEach((n) => {
    const node = map.get(n.id)!;
    if (n.parentId && map.has(n.parentId)) {
      map.get(n.parentId)!.children.push(node);
    } else {
      roots.push(node);
    }
  });

  // Sort children by type then name for stable layout.
  map.forEach((node) => {
    node.children.sort((a, b) => {
      const typeOrder = (t: string) => (t === "class" ? 0 : t === "function" ? 1 : 2);
      if (typeOrder(a.type) !== typeOrder(b.type)) return typeOrder(a.type) - typeOrder(b.type);
      return a.id.localeCompare(b.id);
    });
  });

  return map;
}

function computeSizes(node: LayoutNode): { width: number; height: number } {
  if (node.children.length === 0) {
    return { width: node.width, height: node.height };
  }

  let childrenHeight = PAD;
  let maxChildWidth = 0;

  for (const child of node.children) {
    const childSize = computeSizes(child);
    child.width = childSize.width;
    child.height = childSize.height;
    childrenHeight += child.height + GAP_Y;
    maxChildWidth = Math.max(maxChildWidth, child.width);
  }

  const contentWidth = maxChildWidth + PAD * 2;
  const contentHeight = childrenHeight + PAD;

  node.width = Math.max(node.width, contentWidth);
  node.height = Math.max(node.height, contentHeight + HEADER_H);

  return { width: node.width, height: node.height };
}

function layoutTree(
  roots: LayoutNode[],
  edges: ResearchDiagramEdge[]
): Map<string, { x: number; y: number; width: number; height: number }> {
  const positions = new Map<string, { x: number; y: number; width: number; height: number }>();

  // Compute import graph levels for files.
  const fileRoots = roots.filter((r) => r.type === "file");
  const fileIds = new Set(fileRoots.map((r) => r.id));
  const inDegree = new Map<string, number>();
  const outgoing = new Map<string, string[]>();

  fileRoots.forEach((f) => {
    inDegree.set(f.id, 0);
    outgoing.set(f.id, []);
  });

  edges.forEach((e) => {
    if (e.type === "imports" && fileIds.has(e.source) && fileIds.has(e.target)) {
      outgoing.get(e.source)!.push(e.target);
      inDegree.set(e.target, (inDegree.get(e.target) || 0) + 1);
    }
  });

  const queue = fileRoots.filter((f) => (inDegree.get(f.id) || 0) === 0).map((f) => f.id);
  const levelMap = new Map<string, number>();
  queue.forEach((id) => levelMap.set(id, 0));

  const processed = new Set<string>();
  while (queue.length > 0) {
    const source = queue.shift()!;
    processed.add(source);
    for (const target of outgoing.get(source) || []) {
      levelMap.set(target, Math.max(levelMap.get(target) || 0, levelMap.get(source)! + 1));
      const newDegree = (inDegree.get(target) || 0) - 1;
      inDegree.set(target, newDegree);
      if (newDegree === 0) queue.push(target);
    }
  }

  // Handle cycles.
  fileRoots.forEach((f) => {
    if (!processed.has(f.id)) {
      let predLevel = -1;
      edges.forEach((e) => {
        if (e.type === "imports" && e.target === f.id && fileIds.has(e.source)) {
          predLevel = Math.max(predLevel, levelMap.get(e.source) || 0);
        }
      });
      levelMap.set(f.id, predLevel + 1);
    }
  });

  // Group files by level and sort.
  const cols = new Map<number, LayoutNode[]>();
  fileRoots.forEach((f) => {
    const lvl = levelMap.get(f.id) || 0;
    if (!cols.has(lvl)) cols.set(lvl, []);
    cols.get(lvl)!.push(f);
  });

  cols.forEach((nodes) => nodes.sort((a, b) => a.id.localeCompare(b.id)));

  const colGap = 320;
  const rowGap = 40;

  cols.forEach((colNodes, lvl) => {
    let currentY = 0;
    colNodes.forEach((node) => {
      const x = lvl * colGap;
      const y = currentY;
      positions.set(node.id, { x, y, width: node.width, height: node.height });
      layoutChildren(node, x, y, positions);
      currentY += node.height + rowGap;
    });
  });

  return positions;
}

function layoutChildren(
  parent: LayoutNode,
  parentX: number,
  parentY: number,
  positions: Map<string, { x: number; y: number; width: number; height: number }>
) {
  let childY = HEADER_H + PAD;

  for (const child of parent.children) {
    const childX = PAD;
    positions.set(child.id, {
      x: childX,
      y: childY,
      width: child.width,
      height: child.height,
    });
    layoutChildren(child, parentX + childX, parentY + childY, positions);
    childY += child.height + GAP_Y;
  }
}

function applyLayout(nodes: Node<NodeData>[], positions: Map<string, { x: number; y: number; width: number; height: number }>): Node<NodeData>[] {
  return nodes.map((node) => {
    const pos = positions.get(node.id);
    if (!pos) return node;
    return {
      ...node,
      position: { x: pos.x, y: pos.y },
      style: { width: pos.width, height: pos.height },
    };
  });
}

function ResearchDiagramGraphInner({
  data,
  heightClass = "h-[60vh]",
  expanded = false,
}: {
  data: ResearchDiagram;
  heightClass?: string;
  expanded?: boolean;
}) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<NodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const { fitView } = useReactFlow();

  useEffect(() => {
    const flowNodes = data.nodes.map(toFlowNode);
    const flowEdges = data.edges.map(toFlowEdge);

    const tree = buildLayoutTree(data.nodes);
    const roots = Array.from(tree.values()).filter((n) => !flowNodes.find((fn) => fn.id === n.id)?.parentId);
    roots.forEach((r) => computeSizes(r));
    const positions = layoutTree(roots, data.edges);
    const laidOutNodes = applyLayout(flowNodes, positions);

    setNodes(laidOutNodes);
    setEdges(flowEdges);
    if (!expanded) {
      window.setTimeout(() => fitView({ padding: 0.12 }), 50);
    }
  }, [data, setNodes, setEdges, fitView, expanded]);

  return (
    <div className={cn("kinetic-graph w-full rounded-md border border-kinetic-border bg-kinetic-root", heightClass)}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        fitView={!expanded}
        minZoom={0.1}
        maxZoom={2}
        panOnScroll={expanded}
        zoomOnScroll={expanded}
        panOnDrag
        defaultViewport={expanded ? { x: 40, y: 40, zoom: 0.75 } : undefined}
        attributionPosition="bottom-right"
        proOptions={{ hideAttribution: true }}
      >
        <Background color="var(--kinetic-outline-variant)" gap={20} size={1} />
        <Controls />
      </ReactFlow>
    </div>
  );
}

function ResearchDiagramGraph({
  data,
  heightClass = "h-[60vh]",
  expanded = false,
}: {
  data: ResearchDiagram;
  heightClass?: string;
  expanded?: boolean;
}) {
  return (
    <ReactFlowProvider>
      <ResearchDiagramGraphInner data={data} heightClass={heightClass} expanded={expanded} />
    </ReactFlowProvider>
  );
}

export function ResearchDiagramCard({
  diagram,
  onRefresh,
  loading,
}: {
  diagram: ResearchDiagram;
  onRefresh?: () => void;
  loading?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <>
      <Card className="border-kinetic-border bg-kinetic-surface-container-low">
        <CardHeader>
          <CardTitle className="flex items-center justify-between text-kinetic-on-surface">
            <span>Architecture Diagram</span>
            <div className="flex items-center gap-2">
              {onRefresh && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={onRefresh}
                  disabled={loading}
                  className="border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface"
                >
                  <RefreshCw className={`h-4 w-4 mr-1 ${loading ? "animate-spin" : ""}`} />
                  Refresh
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                onClick={() => setExpanded(true)}
                className="border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface"
              >
                <Maximize2 className="h-4 w-4 mr-1" />
                Expand
              </Button>
            </div>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ResearchDiagramGraph data={diagram} />
        </CardContent>
      </Card>

      {expanded && (
        <div className="fixed inset-0 z-50 flex flex-col bg-kinetic-root">
          <div className="flex h-12 items-center justify-between border-b border-kinetic-border bg-kinetic-surface-container-low px-4">
            <h2 className="text-sm font-semibold text-kinetic-on-surface">Architecture Diagram</h2>
            <div className="flex items-center gap-2">
              {onRefresh && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={onRefresh}
                  disabled={loading}
                  className="border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface"
                >
                  <RefreshCw className={`h-4 w-4 mr-1 ${loading ? "animate-spin" : ""}`} />
                  Refresh
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                onClick={() => setExpanded(false)}
                className="border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface"
              >
                <Minimize2 className="h-4 w-4 mr-1" />
                Close
              </Button>
              <button
                onClick={() => setExpanded(false)}
                className="rounded p-1 text-kinetic-on-surface-variant hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface"
              >
                <X size={18} />
              </button>
            </div>
          </div>
          <div className="flex-1 overflow-hidden p-4">
            <ResearchDiagramGraph data={diagram} heightClass="h-full" expanded />
          </div>
        </div>
      )}
    </>
  );
}
