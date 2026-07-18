"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard,
  Package,
  FileSearch,
  Zap,
  BookOpen,
  GitBranch,
  MessageCircle,
  Home,
  ShieldCheck,
  RefreshCw,
  Mic,
  Cloud,
  Network,
  Search,
  FileText,
  Share2,
} from "lucide-react";
import { useAnalyze } from "@/lib/analyze-context";

interface NavItem {
  href: string;
  label: string;
  icon: React.ElementType;
  requiresIndex: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "Home", icon: Home, requiresIndex: false },
  { href: "/overview", label: "Overview", icon: LayoutDashboard, requiresIndex: true },
  { href: "/architecture", label: "Architecture", icon: Network, requiresIndex: true },
  { href: "/knowledge-graph", label: "Knowledge Graph", icon: Share2, requiresIndex: false },
  { href: "/modules", label: "Modules", icon: Package, requiresIndex: true },
  { href: "/module", label: "Module Detail", icon: FileSearch, requiresIndex: true },
  { href: "/blast-radius", label: "Blast Radius", icon: Zap, requiresIndex: true },
  { href: "/reading-order", label: "Reading Order", icon: BookOpen, requiresIndex: true },
  { href: "/execution-flow", label: "Execution Flow", icon: GitBranch, requiresIndex: true },
  { href: "/docs", label: "Docs", icon: FileText, requiresIndex: true },
  { href: "/research", label: "Research", icon: Search, requiresIndex: true },
  { href: "/chat", label: "Chat", icon: MessageCircle, requiresIndex: true },
  { href: "/voice", label: "Voice", icon: Mic, requiresIndex: true },
  { href: "/review", label: "Code Review", icon: ShieldCheck, requiresIndex: false },
  { href: "/incremental-reindex", label: "Smart Reindex", icon: RefreshCw, requiresIndex: true },
  { href: "/admin", label: "Cloud Admin", icon: Cloud, requiresIndex: false },
];

export interface KineticShellProps {
  children: React.ReactNode;
  header?: React.ReactNode;
  headerRight?: React.ReactNode;
  detailPanel?: React.ReactNode;
  showDetailPanel?: boolean;
  className?: string;
}

export function KineticShell({
  children,
  header,
  headerRight,
  detailPanel,
  showDetailPanel = false,
  className,
}: KineticShellProps) {
  const pathname = usePathname();
  const { hasIndex } = useAnalyze();

  return (
    <div
      className={cn(
        "flex h-screen w-screen overflow-hidden bg-kinetic-root text-kinetic-on-surface",
        className
      )}
    >
      {/* Left sidebar */}
      <nav className="flex h-full w-56 flex-col border-r border-kinetic-border bg-kinetic-surface-container-low flex-shrink-0">
        <div className="flex h-12 w-full items-center px-4 border-b border-kinetic-border">
          <Link
            href="/"
            className="text-sm font-bold tracking-wider text-kinetic-on-surface hover:text-kinetic-primary transition-colors"
          >
            CODEWALK
          </Link>
        </div>
        <div className="flex flex-1 flex-col gap-0.5 overflow-y-auto overflow-x-hidden py-2 px-2">
          {NAV_ITEMS.map((item) => {
            const isActive =
              item.href === "/"
                ? pathname === "/"
                : pathname === item.href || pathname.startsWith(item.href + "/");
            const locked = item.requiresIndex && !hasIndex;

            return (
              <Link
                key={item.href}
                href={locked ? "#" : item.href}
                title={locked ? "Analyze a codebase first to unlock" : item.label}
                className={cn(
                  "relative flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors",
                  isActive
                    ? "bg-kinetic-primary/15 text-kinetic-primary"
                    : locked
                      ? "text-kinetic-on-surface-variant/40 cursor-not-allowed"
                      : "text-kinetic-on-surface-variant hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface"
                )}
              >
                <item.icon size={18} />
                <span className="truncate">{item.label}</span>
                {isActive && (
                  <span className="absolute left-0 top-1/2 -translate-y-1/2 h-4 w-0.5 rounded-r bg-kinetic-primary" />
                )}
              </Link>
            );
          })}
        </div>
      </nav>

      {/* Main area */}
      <div className="flex flex-1 flex-col min-w-0">
        {/* Top bar */}
        <header className="flex h-12 items-center justify-between border-b border-kinetic-border bg-kinetic-surface-container-low px-4 flex-shrink-0">
          <div className="flex items-center gap-2 text-sm min-w-0 overflow-hidden">
            <span className="font-semibold text-kinetic-on-surface">CodeWalk</span>
            {header && (
              <>
                <span className="text-kinetic-outline">/</span>
                <div className="flex items-center gap-2 min-w-0 truncate">{header}</div>
              </>
            )}
          </div>
          {headerRight && (
            <div className="flex items-center gap-3 flex-shrink-0">{headerRight}</div>
          )}
        </header>

        {/* Stage + optional detail panel */}
        <div className="relative flex flex-1 min-h-0">
          <main className="flex-1 min-w-0 overflow-auto">{children}</main>
          {detailPanel && showDetailPanel && (
            <aside className="hidden lg:flex w-[380px] flex-shrink-0 overflow-y-auto border-l border-kinetic-border bg-kinetic-surface-container-low">
              {detailPanel}
            </aside>
          )}
        </div>
      </div>
    </div>
  );
}
