"use client";

import React, { memo } from "react";
import { Handle, Position, type Node } from "@xyflow/react";
import { ChevronDown, ChevronRight, Folder } from "lucide-react";

export interface ContainerNodeData extends Record<string, unknown> {
  containerId: string;
  name: string;
  childCount: number;
  strategy: "folder" | "community";
  colorIndex: number;
  isExpanded: boolean;
  hasSearchHits: boolean;
  isDiffAffected: boolean;
  isFocusedViaChild: boolean;
  width?: number;
  height?: number;
  onToggle: (id: string) => void;
}

export type ContainerFlowNode = Node<ContainerNodeData, "container">;

function getClusterVar(index: number): string {
  return `var(--kg-cluster-${index % 8})`;
}

function ContainerNodeRaw({ id, data }: { id: string; data: ContainerNodeData }) {
  const color = getClusterVar(data.colorIndex);

  const containerStyle: React.CSSProperties = {
    borderColor: `color-mix(in srgb, ${color} 19%, transparent)`,
    width: data.isExpanded ? data.width ?? 260 : 240,
    height: data.isExpanded ? data.height : undefined,
    minWidth: data.isExpanded ? data.width ?? 260 : undefined,
    minHeight: data.isExpanded ? data.height ?? 140 : undefined,
  };

  return (
    <div
      className={[
        "rounded-xl border bg-kg-panel/60 overflow-hidden",
        data.isExpanded ? "" : "w-[240px]",
        data.isDiffAffected || data.isFocusedViaChild ? "ring-2 ring-kg-diff-affected/60" : "",
        data.hasSearchHits ? "ring-1 ring-kg-accent/50" : "",
      ].join(" ")}
      style={containerStyle}
    >
      <Handle type="target" position={Position.Top} className="!bg-transparent !border-0" />
      <div
        className="flex items-center gap-2 px-3 py-2 cursor-pointer select-none border-b"
        style={{ borderColor: `color-mix(in srgb, ${color} 12%, transparent)`, backgroundColor: `color-mix(in srgb, ${color} 6%, transparent)` }}
        onClick={() => data.onToggle(id)}
      >
        <Folder className="w-4 h-4" style={{ color }} />
        <div className="flex-1 min-w-0">
          <div className="text-xs font-semibold text-kg-text-primary truncate">{data.name}</div>
          <div className="text-[9px] text-kg-text-muted capitalize">
            {data.strategy} · {data.childCount} items
          </div>
        </div>
        {data.isExpanded ? (
          <ChevronDown className="w-4 h-4 text-kg-text-muted" />
        ) : (
          <ChevronRight className="w-4 h-4 text-kg-text-muted" />
        )}
      </div>
      <Handle type="source" position={Position.Bottom} className="!bg-transparent !border-0" />
    </div>
  );
}

export const ContainerNode = memo(ContainerNodeRaw);
