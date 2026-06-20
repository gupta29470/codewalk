"use client";

import { useRef, useEffect } from "react";
import { useKgStore } from "@/lib/kg/store";
import { Download } from "lucide-react";

export function ExportMenu() {
  const open = useKgStore((s) => s.exportMenuOpen);
  const setOpen = useKgStore((s) => s.setExportMenuOpen);
  const requestExport = useKgStore((s) => s.requestExport);
  const graph = useKgStore((s) => s.graph);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    if (open) document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open, setOpen]);

  const exportJson = () => {
    if (!graph) return;
    const blob = new Blob([JSON.stringify(graph, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "knowledge-graph.json";
    a.click();
    URL.revokeObjectURL(url);
    setOpen(false);
  };

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        className={`flex items-center gap-1.5 px-2 sm:px-3 py-1.5 rounded-lg text-sm transition-colors ${
          open ? "bg-kg-accent/20 text-kg-accent" : "bg-kg-elevated text-kg-text-secondary hover:text-kg-text-primary"
        }`}
      >
        <Download className="w-4 h-4" />
        <span className="hidden md:inline">Export</span>
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-2 w-44 kg-glass-heavy rounded-lg shadow-2xl z-50 py-1 animate-kg-fade-slide-in">
          <button
            onClick={exportJson}
            className="w-full text-left px-4 py-2 text-sm text-kg-text-secondary hover:text-kg-text-primary hover:bg-kg-elevated"
          >
            Export JSON
          </button>
          <button
            onClick={() => requestExport("svg")}
            className="w-full text-left px-4 py-2 text-sm text-kg-text-secondary hover:text-kg-text-primary hover:bg-kg-elevated"
          >
            Export SVG
          </button>
          <button
            onClick={() => requestExport("png")}
            className="w-full text-left px-4 py-2 text-sm text-kg-text-secondary hover:text-kg-text-primary hover:bg-kg-elevated"
          >
            Export PNG
          </button>
        </div>
      )}
    </div>
  );
}
