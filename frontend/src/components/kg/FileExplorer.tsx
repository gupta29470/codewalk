"use client";

import { useMemo } from "react";
import { useKgStore } from "@/lib/kg/store";
import { FileCode } from "lucide-react";

interface TreeNode {
  name: string;
  path: string;
  children: Map<string, TreeNode>;
  nodeIds: string[];
}

export function FileExplorer() {
  const graph = useKgStore((s) => s.graph);
  const nodesById = useKgStore((s) => s.nodesById);
  const selectNode = useKgStore((s) => s.selectNode);
  const navigateToNodeInLayer = useKgStore((s) => s.navigateToNodeInLayer);
  const openCodeViewer = useKgStore((s) => s.openCodeViewer);

  const tree = useMemo(() => {
    const root: TreeNode = { name: "", path: "", children: new Map(), nodeIds: [] };
    if (!graph) return root;
    for (const node of graph.nodes) {
      if (!node.filePath) continue;
      const parts = node.filePath.split("/").filter(Boolean);
      let current = root;
      let builtPath = "";
      for (let i = 0; i < parts.length; i++) {
        const part = parts[i];
        builtPath = builtPath ? `${builtPath}/${part}` : part;
        if (!current.children.has(part)) {
          current.children.set(part, {
            name: part,
            path: builtPath,
            children: new Map(),
            nodeIds: [],
          });
        }
        current = current.children.get(part)!;
        if (i === parts.length - 1) {
          current.nodeIds.push(node.id);
        }
      }
    }
    return root;
  }, [graph]);

  const handleNodeClick = (nodeId: string, openSource: boolean) => {
    selectNode(nodeId);
    navigateToNodeInLayer(nodeId);
    if (openSource) {
      setTimeout(() => openCodeViewer(nodeId), 100);
    }
  };

  return (
    <div className="p-2 text-sm">
      <TreeBranch
        node={tree}
        depth={-1}
        nodesById={nodesById}
        onSelect={handleNodeClick}
      />
    </div>
  );
}

function TreeBranch({
  node,
  depth,
  nodesById,
  onSelect,
}: {
  node: TreeNode;
  depth: number;
  nodesById: Map<string, { id: string; name: string; type: string }>;
  onSelect: (id: string, openSource: boolean) => void;
}) {
  const sortedChildren = Array.from(node.children.values()).sort((a, b) => {
    const aIsDir = a.children.size > 0;
    const bIsDir = b.children.size > 0;
    if (aIsDir !== bIsDir) return aIsDir ? -1 : 1;
    return a.name.localeCompare(b.name);
  });

  return (
    <>
      {depth >= 0 && node.nodeIds.length > 0 && (
        <div className="space-y-0.5">
          {node.nodeIds.map((id) => {
            const n = nodesById.get(id);
            if (!n) return null;
            return (
              <button
                key={id}
                onClick={() => onSelect(id, false)}
                onDoubleClick={() => onSelect(id, true)}
                className="w-full text-left flex items-center gap-1.5 px-2 py-1 rounded text-xs text-kg-text-secondary hover:text-kg-text-primary hover:bg-kg-elevated transition-colors"
                style={{ paddingLeft: `${depth * 12 + 8}px` }}
              >
                <FileCode className="w-3 h-3 text-kg-accent" />
                <span className="truncate">{n.name}</span>
              </button>
            );
          })}
        </div>
      )}
      {sortedChildren.map((child) => (
        <div key={child.path}>
          {depth >= 0 && child.nodeIds.length === 0 && (
            <div
              className="px-2 py-1 text-xs text-kg-text-muted font-medium"
              style={{ paddingLeft: `${depth * 12 + 8}px` }}
            >
              {child.name}
            </div>
          )}
          <TreeBranch
            node={child}
            depth={depth + 1}
            nodesById={nodesById}
            onSelect={onSelect}
          />
        </div>
      ))}
    </>
  );
}
