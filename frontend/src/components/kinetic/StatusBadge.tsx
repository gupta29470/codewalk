"use client";

import { cn } from "@/lib/utils";

type Status = "analyzed" | "changed" | "unchanged" | "error";

interface StatusBadgeProps {
  status: Status;
  className?: string;
}

const statusStyles: Record<Status, string> = {
  analyzed: "bg-kinetic-status-analyzed text-kinetic-status-analyzed-text",
  changed: "bg-kinetic-status-changed text-kinetic-status-changed-text",
  unchanged: "bg-kinetic-status-unchanged text-kinetic-status-unchanged-text",
  error: "bg-kinetic-status-error text-kinetic-status-error-text",
};

export function StatusBadge({ status, className }: StatusBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider kinetic-font-mono",
        statusStyles[status],
        className,
      )}
    >
      {status}
    </span>
  );
}
