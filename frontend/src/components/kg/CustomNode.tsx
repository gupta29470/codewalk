"use client";

import { memo } from "react";
import { Handle, Position, type Node } from "@xyflow/react";
import { getNodeColor } from "@/lib/kg/types";

export interface CustomNodeData extends Record<string, unknown> {
  label: string;
  nodeType: string;
  summary?: string;
  complexity?: string;
  tags?: string[];
  isHighlighted: boolean;
  searchScore?: number;
  isSelected: boolean;
  isTourHighlighted: boolean;
  isDiffChanged: boolean;
  isDiffAffected: boolean;
  isDiffFaded: boolean;
  isNeighbor: boolean;
  isSelectionFaded: boolean;
  onNodeClick: (nodeId: string) => void;
  incomingCount?: number;
}

export type CustomFlowNode = Node<CustomNodeData, "custom">;

function CustomNodeRaw({ id, data }: { id: string; data: CustomNodeData }) {
  const color = getNodeColor(data.nodeType as never);

  const containerClasses = [
    "relative rounded-lg border bg-kg-surface overflow-hidden cursor-pointer select-none",
    "transition-[box-shadow,outline,opacity,filter] duration-200",
    "shadow-[0_2px_8px_rgba(0,0,0,0.3)]",
    data.isSelected ? "ring-2 ring-kg-accent shadow-[0_0_16px_rgba(var(--kg-accent-rgb),0.35)]" : "ring-0",
    data.isTourHighlighted ? "ring-2 ring-kg-accent animate-kg-accent-pulse" : "",
    data.isDiffChanged ? "ring-2 ring-kg-diff-changed shadow-[0_0_12px_rgba(224,82,82,0.4)]" : "",
    !data.isDiffChanged && data.isDiffAffected ? "ring-2 ring-kg-diff-affected shadow-[0_0_12px_rgba(var(--kg-accent-rgb),0.4)]" : "",
    data.isSelectionFaded ? "opacity-25 saturate-[0.3]" : "",
  ].join(" ");

  const topBarStyle: React.CSSProperties = {
    backgroundColor: color,
    opacity: data.isSelectionFaded ? 0.4 : 1,
  };

  return (
    <div className={containerClasses} onClick={() => data.onNodeClick(id)}>
      <Handle type="target" position={Position.Top} className="!bg-transparent !border-0" />
      <div className="h-1.5 w-full" style={topBarStyle} />
      <div className="p-2.5">
        <div className="flex items-start justify-between gap-2">
          <h3 className="text-xs font-semibold text-kg-text-primary leading-tight line-clamp-2">
            {data.label}
          </h3>
        </div>
        {data.summary && (
          <p className="mt-1 text-[10px] text-kg-text-secondary line-clamp-2 leading-snug">
            {data.summary}
          </p>
        )}
        <div className="mt-2 flex items-center gap-2">
          <span
            className="text-[9px] uppercase tracking-wider px-1 py-0.5 rounded border"
            style={{ color, borderColor: `${color}40`, backgroundColor: `${color}10` }}
          >
            {data.nodeType}
          </span>
          {data.complexity && (
            <span className="text-[9px] text-kg-text-muted uppercase tracking-wider">
              {data.complexity}
            </span>
          )}
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} className="!bg-transparent !border-0" />
    </div>
  );
}

export const CustomNode = memo(CustomNodeRaw, (prev, next) => {
  const a = prev.data;
  const b = next.data;
  return (
    a.label === b.label &&
    a.nodeType === b.nodeType &&
    a.summary === b.summary &&
    a.isHighlighted === b.isHighlighted &&
    a.searchScore === b.searchScore &&
    a.isSelected === b.isSelected &&
    a.isTourHighlighted === b.isTourHighlighted &&
    a.isDiffChanged === b.isDiffChanged &&
    a.isDiffAffected === b.isDiffAffected &&
    a.isDiffFaded === b.isDiffFaded &&
    a.isNeighbor === b.isNeighbor &&
    a.isSelectionFaded === b.isSelectionFaded
  );
});
