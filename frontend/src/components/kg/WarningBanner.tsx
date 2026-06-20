"use client";

import { useState } from "react";
import { AlertTriangle, X } from "lucide-react";

export function WarningBanner({
  issues,
}: {
  issues: { level: "auto-corrected" | "dropped" | "warning" | "error"; message: string }[];
}) {
  const [dismissed, setDismissed] = useState(false);
  if (dismissed || issues.length === 0) return null;

  const dropped = issues.filter((i) => i.level === "dropped" || i.level === "error").length;
  const warnings = issues.length - dropped;

  return (
    <div className="px-4 py-2 bg-amber-900/20 border-b border-amber-700/40 text-amber-200 text-xs flex items-center justify-between">
      <div className="flex items-center gap-2">
        <AlertTriangle className="w-4 h-4 text-amber-500" />
        <span>
          Graph loaded with {issues.length} issue{issues.length !== 1 ? "s" : ""}
          {dropped > 0 ? ` (${dropped} dropped)` : ""}
          {warnings > 0 ? ` (${warnings} warnings)` : ""}
        </span>
      </div>
      <button onClick={() => setDismissed(true)} className="text-amber-200/70 hover:text-amber-200">
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}
