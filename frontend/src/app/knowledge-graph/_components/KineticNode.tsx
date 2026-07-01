"use client";

import { memo } from "react";
import { Handle, Position, type NodeProps, type Node } from "@xyflow/react";
import { FileCode, Settings, Server, FileText, Radio } from "lucide-react";
import { cn } from "@/lib/utils";
import type { GraphNode, NodeType } from "@/lib/kg/types";
import { StatusBadge } from "@/components/kinetic/StatusBadge";

type KineticFlowNode = Node<KineticNodeData, "kinetic">;

const typeIcons: Partial<Record<NodeType, React.ReactNode>> = {
  file: <FileCode size={14} />,
  service: <Server size={14} />,
  config: <Settings size={14} />,
  document: <FileText size={14} />,
  endpoint: <Radio size={14} />,
};

const typeColors: Record<string, string> = {
  file: "border-kinetic-node-file text-kinetic-node-file",
  function: "border-kinetic-node-function text-kinetic-node-function",
  class: "border-kinetic-node-class text-kinetic-node-class",
  module: "border-kinetic-node-module text-kinetic-node-module",
  config: "border-kinetic-node-config text-kinetic-node-config",
  document: "border-kinetic-node-document text-kinetic-node-document",
  service: "border-kinetic-node-service text-kinetic-node-service",
  endpoint: "border-kinetic-node-endpoint text-kinetic-node-endpoint",
  table: "border-kinetic-node-endpoint text-kinetic-node-endpoint",
  pipeline: "border-kinetic-node-service text-kinetic-node-service",
  concept: "border-kinetic-node-class text-kinetic-node-class",
};

export type KineticNodeData = GraphNode & {
  status?: "analyzed" | "changed" | "unchanged" | "error";
  borderColor?: string;
} & Record<string, unknown>;

export const KineticNode = memo(function KineticNode({ data, selected }: NodeProps<KineticFlowNode>) {
  const defaultColorClass = typeColors[data.type] || "border-kinetic-outline text-kinetic-on-surface-variant";
  const colorClass = data.borderColor ? "" : defaultColorClass;
  const Icon = typeIcons[data.type] || <FileCode size={14} />;
  const accentStyle = data.borderColor
    ? { borderColor: data.borderColor, color: data.borderColor }
    : undefined;

  return (
    <div
      style={accentStyle}
      className={cn(
        "group w-52 rounded-md border bg-kinetic-surface-container-low px-3 py-2 transition-all",
        colorClass,
        selected
          ? "ring-2 ring-kinetic-primary ring-offset-2 ring-offset-kinetic-root"
          : "hover:brightness-110",
      )}
    >
      <Handle type="target" position={Position.Top} className="!bg-kinetic-outline !w-2 !h-2" />
      <div className="flex items-center gap-2">
        <span className={cn("flex-shrink-0", colorClass)}>{Icon}</span>
        <div className="min-w-0">
          <div className="truncate text-xs font-medium text-kinetic-on-surface kinetic-font-mono">
            {data.name}
          </div>
          <div className="truncate text-[10px] uppercase tracking-wide text-kinetic-on-surface-variant">
            {data.type}
          </div>
        </div>
      </div>
      {data.status && (
        <div className="mt-2 flex justify-end">
          <StatusBadge status={data.status} />
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="!bg-kinetic-outline !w-2 !h-2" />
    </div>
  );
});
