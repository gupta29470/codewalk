"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Folder, FileCode } from "lucide-react";
import { cn } from "@/lib/utils";
import type { GraphNode, KnowledgeGraph, NodeType } from "@/lib/kg/types";
import { useKgStore } from "@/lib/kg/store";
import { StatusBadge } from "@/components/kinetic/StatusBadge";

interface LayerTreeProps {
  graph: KnowledgeGraph;
  selectedNodeId: string | null;
  onSelectNode: (id: string | null) => void;
}

interface TreeNode {
  id: string;
  name: string;
  type: "folder" | "file";
  nodeType?: NodeType;
  children: TreeNode[];
  graphNode?: GraphNode;
}

function buildTree(graph: KnowledgeGraph): TreeNode {
  const root: TreeNode = {
    id: "__root__",
    name: graph.project.name || "Project",
    type: "folder",
    children: [],
  };

  const folders = new Map<string, TreeNode>();

  function getFolder(path: string): TreeNode {
    if (folders.has(path)) return folders.get(path)!;
    const node: TreeNode = {
      id: `folder:${path}`,
      name: path.split("/").pop() || path,
      type: "folder",
      children: [],
    };
    folders.set(path, node);
    const parentPath = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
    if (parentPath) {
      getFolder(parentPath).children.push(node);
    } else {
      root.children.push(node);
    }
    return node;
  }

  for (const node of graph.nodes) {
    const path = node.filePath || node.module || "";
    const dir = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
    const fileName = node.name;
    const fileNode: TreeNode = {
      id: node.id,
      name: fileName,
      type: "file",
      nodeType: node.type,
      graphNode: node,
      children: [],
    };
    if (dir) {
      getFolder(dir).children.push(fileNode);
    } else {
      root.children.push(fileNode);
    }
  }

  // Sort folders first, then files; alphabetically
  function sort(n: TreeNode) {
    n.children.sort((a, b) => {
      if (a.type === b.type) return a.name.localeCompare(b.name);
      return a.type === "folder" ? -1 : 1;
    });
    n.children.forEach(sort);
  }
  sort(root);

  return root;
}

function filterTree(node: TreeNode, query: string): TreeNode | null {
  const q = query.toLowerCase();
  const matchesSelf = node.name.toLowerCase().includes(q);
  const matchingChildren = node.children
    .map((c) => filterTree(c, query))
    .filter(Boolean) as TreeNode[];
  if (matchesSelf || matchingChildren.length > 0) {
    return { ...node, children: matchingChildren };
  }
  return null;
}

export function LayerTree({ graph, selectedNodeId, onSelectNode }: LayerTreeProps) {
  const rawTree = useMemo(() => buildTree(graph), [graph]);
  const searchQuery = useKgStore((s) => s.searchQuery);
  const tree = useMemo(() => {
    const q = searchQuery.trim();
    return q ? filterTree(rawTree, q) ?? rawTree : rawTree;
  }, [rawTree, searchQuery]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set([tree.id]));
  const changedNodeIds = useKgStore((s) => s.changedNodeIds);
  const affectedNodeIds = useKgStore((s) => s.affectedNodeIds);

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="h-full overflow-y-auto bg-kinetic-root p-4">
      <TreeItem
        node={tree}
        depth={0}
        expanded={expanded}
        selectedNodeId={selectedNodeId}
        changedNodeIds={changedNodeIds}
        affectedNodeIds={affectedNodeIds}
        onToggle={toggle}
        onSelect={onSelectNode}
      />
    </div>
  );
}

interface TreeItemProps {
  node: TreeNode;
  depth: number;
  expanded: Set<string>;
  selectedNodeId: string | null;
  changedNodeIds: Set<string>;
  affectedNodeIds: Set<string>;
  onToggle: (id: string) => void;
  onSelect: (id: string | null) => void;
}

function TreeItem({
  node,
  depth,
  expanded,
  selectedNodeId,
  changedNodeIds,
  affectedNodeIds,
  onToggle,
  onSelect,
}: TreeItemProps) {
  const isExpanded = expanded.has(node.id);
  const isSelected = node.graphNode && node.id === selectedNodeId;
  const hasChildren = node.children.length > 0;
  const status: "analyzed" | "changed" | "unchanged" = node.graphNode
    ? changedNodeIds.has(node.id)
      ? "changed"
      : affectedNodeIds.has(node.id)
        ? "unchanged"
        : "analyzed"
    : "analyzed";

  return (
    <div>
      <button
        onClick={() => {
          if (node.type === "folder" && hasChildren) onToggle(node.id);
          if (node.type === "file" && node.graphNode) onSelect(node.id);
        }}
        className={cn(
          "flex w-full items-center gap-2 rounded-md py-1.5 pr-2 text-left transition-colors",
          isSelected
            ? "bg-kinetic-primary/10 text-kinetic-primary"
            : "text-kinetic-on-surface hover:bg-kinetic-surface-container-low",
        )}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        {hasChildren ? (
          <span className="text-kinetic-on-surface-variant" onClick={(e) => { e.stopPropagation(); onToggle(node.id); }}>
            {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </span>
        ) : (
          <span className="w-[14px]" />
        )}

        {node.type === "folder" ? (
          <Folder size={14} className="text-kinetic-tertiary" />
        ) : (
          <FileCode size={14} className="text-kinetic-node-file" />
        )}

        <span className="flex-1 truncate text-xs kinetic-font-mono">{node.name}</span>

        {node.type === "file" && <StatusBadge status={status} />}
      </button>

      {isExpanded && hasChildren && (
        <div>
          {node.children.map((child) => (
            <TreeItem
              key={child.id}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              selectedNodeId={selectedNodeId}
              changedNodeIds={changedNodeIds}
              affectedNodeIds={affectedNodeIds}
              onToggle={onToggle}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}
