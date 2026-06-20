"use client";

import { useRef, useEffect } from "react";
import { useKgStore } from "@/lib/kg/store";
import { Filter, X } from "lucide-react";
import type { NodeCategory } from "@/lib/kg/types";

const CATEGORIES: { key: NodeCategory; label: string }[] = [
  { key: "code", label: "Code" },
  { key: "config", label: "Config" },
  { key: "docs", label: "Docs" },
  { key: "infra", label: "Infrastructure" },
  { key: "data", label: "Data" },
  { key: "domain", label: "Domain" },
  { key: "knowledge", label: "Knowledge" },
];

export function FilterPanel() {
  const open = useKgStore((s) => s.filterPanelOpen);
  const setOpen = useKgStore((s) => s.setFilterPanelOpen);
  const filters = useKgStore((s) => s.nodeTypeFilters);
  const toggle = useKgStore((s) => s.toggleNodeTypeFilter);
  const detailLevel = useKgStore((s) => s.detailLevel);
  const setDetailLevel = useKgStore((s) => s.setDetailLevel);
  const showFunctions = useKgStore((s) => s.showFunctionsInClassView);
  const toggleFunctions = useKgStore((s) => s.toggleShowFunctionsInClassView);

  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    if (open) document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open, setOpen]);

  const activeCount = Object.values(filters).filter((v) => v === false).length;

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        className={`flex items-center gap-1.5 px-2 sm:px-3 py-1.5 rounded-lg text-sm transition-colors ${
          activeCount > 0 || open
            ? "bg-kg-accent/20 text-kg-accent"
            : "bg-kg-elevated text-kg-text-secondary hover:text-kg-text-primary"
        }`}
        title="Filters"
      >
        <Filter className="w-4 h-4" />
        <span className="hidden md:inline">Filter</span>
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-72 kg-glass-heavy rounded-lg shadow-2xl z-50 p-4 animate-kg-fade-slide-in">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-heading text-sm">Filters</h3>
            <button onClick={() => setOpen(false)} className="text-kg-text-muted hover:text-kg-text-primary">
              <X className="w-4 h-4" />
            </button>
          </div>

          <div className="space-y-3">
            <div>
              <h4 className="text-[10px] uppercase tracking-wider text-kg-text-muted mb-2">Node categories</h4>
              <div className="space-y-1.5">
                {CATEGORIES.map((cat) => (
                  <label
                    key={cat.key}
                    className="flex items-center justify-between text-sm text-kg-text-secondary hover:text-kg-text-primary cursor-pointer"
                  >
                    <span>{cat.label}</span>
                    <input
                      type="checkbox"
                      checked={filters[cat.key] !== false}
                      onChange={() => toggle(cat.key)}
                      className="accent-kg-accent"
                    />
                  </label>
                ))}
              </div>
            </div>

            <div className="border-t border-kg-border-subtle pt-3">
              <h4 className="text-[10px] uppercase tracking-wider text-kg-text-muted mb-2">Detail level</h4>
              <div className="flex items-center bg-kg-elevated rounded-lg p-0.5">
                <button
                  onClick={() => setDetailLevel("file")}
                  className={`flex-1 px-3 py-1 text-xs rounded-md transition-colors ${
                    detailLevel === "file"
                      ? "bg-kg-accent/20 text-kg-accent"
                      : "text-kg-text-muted hover:text-kg-text-secondary"
                  }`}
                >
                  File
                </button>
                <button
                  onClick={() => setDetailLevel("class")}
                  className={`flex-1 px-3 py-1 text-xs rounded-md transition-colors ${
                    detailLevel === "class"
                      ? "bg-kg-accent/20 text-kg-accent"
                      : "text-kg-text-muted hover:text-kg-text-secondary"
                  }`}
                >
                  Class
                </button>
              </div>
              {detailLevel === "class" && (
                <label className="flex items-center gap-2 mt-2 text-xs text-kg-text-secondary cursor-pointer">
                  <input type="checkbox" checked={showFunctions} onChange={toggleFunctions} className="accent-kg-accent" />
                  Show functions
                </label>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
