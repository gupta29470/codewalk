"use client";

import { Share2, Info, FolderOpen } from "lucide-react";

type Tab = "graph" | "info" | "files";

export function MobileBottomNav({
  active,
  onChange,
}: {
  active: Tab;
  onChange: (tab: Tab) => void;
}) {
  const tabs: { id: Tab; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
    { id: "graph", label: "Graph", icon: Share2 },
    { id: "info", label: "Info", icon: Info },
    { id: "files", label: "Files", icon: FolderOpen },
  ];

  return (
    <nav className="fixed bottom-0 left-0 right-0 z-40 bg-kg-surface border-t border-kg-border-subtle md:hidden">
      <div className="flex items-center justify-around">
        {tabs.map((tab) => {
          const isActive = active === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => onChange(tab.id)}
              className={`flex-1 flex flex-col items-center gap-1 py-2 transition-colors ${
                isActive ? "text-kg-accent" : "text-kg-text-muted"
              }`}
            >
              <tab.icon className="w-5 h-5" />
              <span className="text-[10px] font-medium">{tab.label}</span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}
