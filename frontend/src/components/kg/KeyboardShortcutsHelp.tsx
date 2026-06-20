"use client";

import { X } from "lucide-react";
import type { KeyboardShortcut } from "./useKeyboardShortcuts";
import { formatKey } from "./useKeyboardShortcuts";

export default function KeyboardShortcutsHelp({
  shortcuts,
  onClose,
}: {
  shortcuts: KeyboardShortcut[];
  onClose: () => void;
}) {
  const byCategory = shortcuts.reduce((acc, s) => {
    if (!acc[s.category]) acc[s.category] = [];
    acc[s.category].push(s);
    return acc;
  }, {} as Record<string, KeyboardShortcut[]>);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 backdrop-blur-sm p-4">
      <div className="w-full max-w-md kg-glass-heavy rounded-lg border border-kg-border-medium shadow-2xl p-5 animate-kg-fade-slide-in">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-heading text-lg">Keyboard Shortcuts</h2>
          <button onClick={onClose} className="text-kg-text-muted hover:text-kg-text-primary">
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="space-y-4">
          {Object.entries(byCategory).map(([category, items]) => (
            <div key={category}>
              <h3 className="text-[10px] uppercase tracking-wider text-kg-text-muted mb-2">{category}</h3>
              <div className="space-y-1.5">
                {items.map((s, i) => (
                  <div key={i} className="flex items-center justify-between text-sm">
                    <span className="text-kg-text-secondary">{s.description}</span>
                    <kbd className="px-2 py-0.5 rounded bg-kg-elevated border border-kg-border-subtle text-kg-text-primary text-xs font-mono">
                      {formatKey(s)}
                    </kbd>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
