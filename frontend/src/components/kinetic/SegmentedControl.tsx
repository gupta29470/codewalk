"use client";

import { cn } from "@/lib/utils";

interface Option<T extends string> {
  value: T;
  label: string;
}

interface SegmentedControlProps<T extends string> {
  options: Option<T>[];
  value: T;
  onChange: (value: T) => void;
  className?: string;
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  className,
}: SegmentedControlProps<T>) {
  return (
    <div
      className={cn(
        "inline-flex items-center rounded-md border border-kinetic-border bg-kinetic-surface-container p-0.5",
        className,
      )}
    >
      {options.map((option) => (
        <button
          key={option.value}
          onClick={() => onChange(option.value)}
          className={cn(
            "relative rounded px-3 py-1 text-xs font-medium transition-colors",
            value === option.value
              ? "bg-kinetic-surface-container-high text-kinetic-on-surface"
              : "text-kinetic-on-surface-variant hover:text-kinetic-on-surface",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
