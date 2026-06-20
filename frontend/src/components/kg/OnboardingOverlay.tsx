"use client";

import { useState } from "react";
import { X, ChevronRight, ChevronLeft } from "lucide-react";

const STEPS = [
  {
    title: "Welcome to CodeWalk",
    body: "Explore your codebase as an interactive graph. Pan, zoom, and click nodes to inspect files, classes, and functions.",
  },
  {
    title: "Layers & Containers",
    body: "The overview shows architecture layers. Drill into a layer to see files grouped into expandable containers.",
  },
  {
    title: "Search & Path Finder",
    body: "Use the search bar or press / to find nodes. Use Path Finder to trace dependency paths between two nodes.",
  },
  {
    title: "Ready",
    body: "Press ? anytime for keyboard shortcuts. Enjoy exploring your codebase!",
  },
];

export default function OnboardingOverlay({
  onDismiss,
}: {
  onDismiss: (remember: boolean) => void;
}) {
  const [step, setStep] = useState(0);
  const [remember, setRemember] = useState(true);

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="w-full max-w-md kg-glass-heavy rounded-xl border border-kg-border-medium shadow-2xl p-6 animate-kg-fade-slide-in">
        <button
          onClick={() => onDismiss(false)}
          className="absolute top-4 right-4 text-kg-text-muted hover:text-kg-text-primary"
        >
          <X className="w-5 h-5" />
        </button>

        <h2 className="font-heading text-xl mb-3">{STEPS[step].title}</h2>
        <p className="text-sm text-kg-text-secondary leading-relaxed mb-6">{STEPS[step].body}</p>

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {STEPS.map((_, i) => (
              <div
                key={i}
                className={`w-2 h-2 rounded-full transition-colors ${
                  i === step ? "bg-kg-accent" : "bg-kg-elevated"
                }`}
              />
            ))}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setStep((s) => Math.max(0, s - 1))}
              disabled={step === 0}
              className="p-2 rounded-lg text-kg-text-secondary hover:bg-kg-elevated disabled:opacity-30"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            {step < STEPS.length - 1 ? (
              <button
                onClick={() => setStep((s) => Math.min(STEPS.length - 1, s + 1))}
                className="flex items-center gap-1 px-4 py-2 rounded-lg bg-kg-accent/15 text-kg-accent hover:bg-kg-accent/25 text-sm font-medium"
              >
                Next <ChevronRight className="w-4 h-4" />
              </button>
            ) : (
              <button
                onClick={() => onDismiss(remember)}
                className="px-4 py-2 rounded-lg bg-kg-accent/15 text-kg-accent hover:bg-kg-accent/25 text-sm font-medium"
              >
                Get started
              </button>
            )}
          </div>
        </div>

        <label className="flex items-center gap-2 mt-4 text-xs text-kg-text-muted cursor-pointer">
          <input
            type="checkbox"
            checked={remember}
            onChange={(e) => setRemember(e.target.checked)}
            className="accent-kg-accent"
          />
          Don&apos;t show again
        </label>
      </div>
    </div>
  );
}
