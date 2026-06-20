"use client";

import { useState } from "react";
import { useKgStore } from "@/lib/kg/store";
import { MobileBottomNav } from "./MobileBottomNav";
import { MobileDrawer } from "./MobileDrawer";
import { NodeInfo } from "./NodeInfo";
import { ProjectOverview } from "./ProjectOverview";
import { FileExplorer } from "./FileExplorer";
import { SearchBar } from "./SearchBar";
import { Menu } from "lucide-react";

type Tab = "graph" | "info" | "files";

export function MobileLayout({
  graphView,
  onOpenPathFinder,
  onOpenKeyboardHelp,
}: {
  graphView: React.ReactNode;
  onOpenPathFinder: () => void;
  onOpenKeyboardHelp: () => void;
}) {
  const [activeTab, setActiveTab] = useState<Tab>("graph");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const selectedNodeId = useKgStore((s) => s.selectedNodeId);
  const persona = useKgStore((s) => s.persona);
  const tourActive = useKgStore((s) => s.tourActive);

  const isLearnMode = tourActive || persona === "junior";

  return (
    <div className="h-screen w-screen flex flex-col bg-kg-root text-kg-text-primary md:hidden">
      {/* Mobile header */}
      <header className="flex items-center justify-between px-3 py-2 bg-kg-surface border-b border-kg-border-subtle shrink-0">
        <h1 className="font-heading text-sm truncate max-w-[180px]">
          {useKgStore((s) => s.graph?.project.name) ?? "CodeWalk"}
        </h1>
        <button
          onClick={() => setDrawerOpen(true)}
          className="p-1.5 rounded-lg bg-kg-elevated text-kg-text-secondary"
        >
          <Menu className="w-4 h-4" />
        </button>
      </header>

      <SearchBar />

      {/* Main panes */}
      <div className="flex-1 relative overflow-hidden pb-14">
        <div className={`absolute inset-0 ${activeTab === "graph" ? "block" : "hidden"}`}>
          {graphView}
        </div>
        <div
          className={`absolute inset-0 overflow-auto p-3 ${
            activeTab === "info" ? "block" : "hidden"
          }`}
        >
          {selectedNodeId && <NodeInfo />}
          {isLearnMode && <div className="text-sm text-kg-text-muted mt-4">Tour mode active</div>}
          {!selectedNodeId && !isLearnMode && <ProjectOverview />}
        </div>
        <div
          className={`absolute inset-0 overflow-auto p-2 ${
            activeTab === "files" ? "block" : "hidden"
          }`}
        >
          <FileExplorer />
        </div>
      </div>

      <MobileBottomNav active={activeTab} onChange={setActiveTab} />
      <MobileDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onOpenPathFinder={onOpenPathFinder}
        onOpenKeyboardHelp={onOpenKeyboardHelp}
      />
    </div>
  );
}
