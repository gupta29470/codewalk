"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
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
} from "lucide-react";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
    { href: "/", label: "Home", icon: Home },
    { href: "/overview", label: "Overview", icon: LayoutDashboard },
    { href: "/architecture", label: "Architecture", icon: Network },
    { href: "/modules", label: "Modules", icon: Package },
    { href: "/module", label: "Module Detail", icon: FileSearch },
    { href: "/blast-radius", label: "Blast Radius", icon: Zap },
    { href: "/reading-order", label: "Reading Order", icon: BookOpen },
    { href: "/execution-flow", label: "Execution Flow", icon: GitBranch },
    { href: "/docs", label: "Docs", icon: FileText },
    { href: "/research", label: "Research", icon: Search },
    { href: "/chat", label: "Chat", icon: MessageCircle },
    { href: "/voice", label: "Voice", icon: Mic },
    { href: "/review", label: "Code Review", icon: ShieldCheck },
    { href: "/incremental-reindex", label: "Smart Reindex", icon: RefreshCw },
    { href: "/admin", label: "Cloud Admin", icon: Cloud },
];

export function Sidebar() {
    const pathname = usePathname();

    return (
        <aside className="w-56 border-r bg-card flex flex-col h-screen sticky top-0">
            <div className="p-4 border-b">
                <Link href="/" className="flex items-center gap-2">
                    <span className="text-xl font-bold tracking-tight">CODEWALK</span>
                </Link>
                <p className="text-xs text-muted-foreground mt-1">
                    Codebase onboarding tool
                </p>
            </div>

            <nav className="flex-1 p-2 space-y-1">
                {NAV_ITEMS.map((item) => {
                    const isActive =
                        item.href === "/"
                            ? pathname === "/"
                            : pathname === item.href || pathname.startsWith(item.href + "/");

                    const className = cn(
                        "flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors",
                        isActive
                            ? "bg-primary text-primary-foreground"
                            : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                    );

                    return (
                        <Link
                            key={item.href}
                            href={item.href}
                            className={className}
                        >
                            <item.icon className="h-4 w-4" />
                            {item.label}
                        </Link>
                    );
                })}
            </nav>
        </aside>
    );
}
