"use client";

import { FileCode } from "lucide-react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";

export interface FlowNodeData extends Record<string, unknown> {
  label: string;
  fullPath: string;
}

type FlowNodeType = Node<FlowNodeData>;

export function FlowNode({ data }: NodeProps<FlowNodeType>) {
  return (
    <div className="group relative flex min-w-[140px] max-w-[180px] items-center gap-2 rounded-md border border-kinetic-border bg-kinetic-surface-container px-3 py-2 shadow-sm transition-colors hover:border-kinetic-primary hover:bg-kinetic-surface-container-high">
      <Handle type="target" position={Position.Left} className="!bg-kinetic-primary !w-2 !h-2" />
      <FileCode className="h-4 w-4 flex-shrink-0 text-kinetic-primary" />
      <div className="min-w-0">
        <p className="truncate text-xs font-medium text-kinetic-on-surface">{data.label}</p>
        <p className="truncate text-[10px] text-kinetic-on-surface-variant kinetic-font-mono">
          {data.fullPath}
        </p>
      </div>
      <Handle type="source" position={Position.Right} className="!bg-kinetic-primary !w-2 !h-2" />
    </div>
  );
}
