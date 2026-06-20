"use client";

import { useKgStore } from "@/lib/kg/store";
import { GraduationCap, User, Users } from "lucide-react";

const OPTIONS = [
  { value: "experienced", label: "Experienced", icon: User },
  { value: "junior", label: "Junior", icon: GraduationCap },
  { value: "non-technical", label: "Non-Technical", icon: Users },
] as const;

export function PersonaSelector() {
  const persona = useKgStore((s) => s.persona);
  const setPersona = useKgStore((s) => s.setPersona);

  return (
    <div className="flex items-center bg-kg-elevated rounded-lg p-0.5">
      {OPTIONS.map((opt) => {
        const active = persona === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => setPersona(opt.value)}
            className={`flex items-center gap-1 px-2 py-1 text-[10px] sm:text-xs font-medium rounded-md transition-colors ${
              active
                ? "bg-kg-accent/20 text-kg-accent"
                : "text-kg-text-muted hover:text-kg-text-secondary"
            }`}
            title={opt.label}
          >
            <opt.icon className="w-3 h-3" />
            <span className="hidden sm:inline">{opt.label}</span>
          </button>
        );
      })}
    </div>
  );
}
