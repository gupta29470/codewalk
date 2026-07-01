"use client";

import { KineticShell } from "@/components/KineticShell";

export function RootShell({ children }: { children: React.ReactNode }) {
  return <KineticShell>{children}</KineticShell>;
}
