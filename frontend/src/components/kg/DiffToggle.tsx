"use client";

import { useKgStore } from "@/lib/kg/store";
import { GitCompare } from "lucide-react";

export function DiffToggle() {
  const diffMode = useKgStore((s) => s.diffMode);
  const toggleDiffMode = useKgStore((s) => s.toggleDiffMode);
  const changed = useKgStore((s) => s.changedNodeIds.size);
  const affected = useKgStore((s) => s.affectedNodeIds.size);

  if (changed === 0 && affected === 0) return null;

  return (
    <button
      onClick={toggleDiffMode}
      className={`flex items-center gap-1.5 px-2 sm:px-3 py-1.5 rounded-lg text-xs transition-colors ${
        diffMode
          ? "bg-kg-diff-changed/20 text-kg-diff-changed border border-kg-diff-changed/30"
          : "bg-kg-elevated text-kg-text-secondary hover:text-kg-text-primary"
      }`}
      title="Toggle diff overlay"
    >
      <GitCompare className="w-3.5 h-3.5" />
      <span className="hidden sm:inline">Diff</span>
      {diffMode && (
        <span className="ml-1 text-[10px]">
          {changed}+{affected}
        </span>
      )}
    </button>
  );
}
