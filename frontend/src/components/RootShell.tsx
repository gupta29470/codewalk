"use client";

import { usePathname } from "next/navigation";
import { Sidebar } from "@/components/Sidebar";

export function RootShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const hideSidebar = pathname === "/knowledge-graph" || pathname?.startsWith("/knowledge-graph/");

  return (
    <div className={hideSidebar ? "" : "flex min-h-screen"}>
      {!hideSidebar && <Sidebar />}
      <main className={hideSidebar ? "" : "flex-1 p-6 overflow-auto"}>
        {children}
      </main>
    </div>
  );
}
