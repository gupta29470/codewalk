"use client";

import { X, Filter, Download, Route, HelpCircle } from "lucide-react";
import { useKgStore } from "@/lib/kg/store";

export function MobileDrawer({
  open,
  onClose,
  onOpenPathFinder,
  onOpenKeyboardHelp,
}: {
  open: boolean;
  onClose: () => void;
  onOpenPathFinder: () => void;
  onOpenKeyboardHelp: () => void;
}) {
  const toggleFilterPanel = useKgStore((s) => s.toggleFilterPanel);
  const requestExport = useKgStore((s) => s.requestExport);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 md:hidden">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="absolute right-0 top-0 bottom-0 w-64 bg-kg-surface border-l border-kg-border-subtle p-4 animate-kg-slide-up">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-heading text-lg">Tools</h2>
          <button onClick={onClose} className="text-kg-text-muted hover:text-kg-text-primary">
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="space-y-2">
          <button
            onClick={() => {
              toggleFilterPanel();
              onClose();
            }}
            className="w-full flex items-center gap-3 px-3 py-2 rounded-lg bg-kg-elevated text-kg-text-secondary hover:text-kg-text-primary"
          >
            <Filter className="w-4 h-4" /> Filters
          </button>
          <button
            onClick={() => {
              onOpenPathFinder();
              onClose();
            }}
            className="w-full flex items-center gap-3 px-3 py-2 rounded-lg bg-kg-elevated text-kg-text-secondary hover:text-kg-text-primary"
          >
            <Route className="w-4 h-4" /> Path Finder
          </button>
          <button
            onClick={() => {
              requestExport("svg");
              onClose();
            }}
            className="w-full flex items-center gap-3 px-3 py-2 rounded-lg bg-kg-elevated text-kg-text-secondary hover:text-kg-text-primary"
          >
            <Download className="w-4 h-4" /> Export SVG
          </button>
          <button
            onClick={() => {
              requestExport("png");
              onClose();
            }}
            className="w-full flex items-center gap-3 px-3 py-2 rounded-lg bg-kg-elevated text-kg-text-secondary hover:text-kg-text-primary"
          >
            <Download className="w-4 h-4" /> Export PNG
          </button>
          <button
            onClick={() => {
              onOpenKeyboardHelp();
              onClose();
            }}
            className="w-full flex items-center gap-3 px-3 py-2 rounded-lg bg-kg-elevated text-kg-text-secondary hover:text-kg-text-primary"
          >
            <HelpCircle className="w-4 h-4" /> Shortcuts
          </button>
        </div>
      </div>
    </div>
  );
}
