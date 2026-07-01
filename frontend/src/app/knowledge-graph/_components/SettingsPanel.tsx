"use client";

import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useKgStore } from "@/lib/kg/store";

type AccentTheme = "blue" | "purple" | "gold" | "green";

interface SettingsPanelProps {
  theme: AccentTheme;
  onChangeTheme: (theme: AccentTheme) => void;
  onClose: () => void;
}

const themes: { id: AccentTheme; name: string; color: string }[] = [
  { id: "blue", name: "Kinetic Blue", color: "#a2c9ff" },
  { id: "purple", name: "Service Purple", color: "#d8baff" },
  { id: "gold", name: "Document Gold", color: "#ffba42" },
  { id: "green", name: "Config Green", color: "#7ee787" },
];

export function SettingsPanel({ theme, onChangeTheme, onClose }: SettingsPanelProps) {
  const filters = useKgStore((s) => s.nodeTypeFilters);
  const resetFilters = () => {
    (Object.keys(filters) as Array<keyof typeof filters>).forEach((k) => {
      if (!filters[k]) useKgStore.getState().toggleNodeTypeFilter(k);
    });
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-12 items-center justify-between border-b border-kinetic-border px-4">
        <span className="text-sm font-semibold text-kinetic-on-surface">Settings</span>
        <button
          onClick={onClose}
          className="rounded p-1 text-kinetic-on-surface-variant hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface"
        >
          <X size={16} />
        </button>
      </div>

      <div className="flex-1 space-y-6 overflow-y-auto p-4">
        <section>
          <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-kinetic-on-surface-variant">
            Accent Color
          </h3>
          <div className="grid grid-cols-2 gap-2">
            {themes.map((t) => (
              <button
                key={t.id}
                onClick={() => onChangeTheme(t.id)}
                className={cn(
                  "flex items-center gap-2 rounded-md border px-3 py-2 text-left transition-colors",
                  theme === t.id
                    ? "border-kinetic-primary bg-kinetic-primary/10"
                    : "border-kinetic-border bg-kinetic-surface-container-low hover:bg-kinetic-surface-container-high",
                )}
              >
                <span
                  className="h-4 w-4 rounded-full border border-kinetic-border"
                  style={{ backgroundColor: t.color }}
                />
                <span className="text-xs text-kinetic-on-surface">{t.name}</span>
              </button>
            ))}
          </div>
        </section>

        <section>
          <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-kinetic-on-surface-variant">
            Graph Filters
          </h3>
          <button
            onClick={resetFilters}
            className="rounded-md border border-kinetic-border bg-kinetic-surface-container-low px-3 py-2 text-xs text-kinetic-on-surface hover:bg-kinetic-surface-container-high"
          >
            Reset node category filters
          </button>
        </section>

        <section>
          <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-kinetic-on-surface-variant">
            About
          </h3>
          <p className="text-xs leading-relaxed text-kinetic-on-surface-variant">
            Codewalk Kinetic Logic UI. Built for high-density codebase navigation with clean,
            utilitarian design.
          </p>
        </section>
      </div>
    </div>
  );
}
