"use client";

import { useEffect, useState } from "react";
import { api, StalenessStatus } from "@/lib/api";

export function StalenessBanner() {
    const [status, setStatus] = useState<StalenessStatus | null>(null);

    useEffect(() => {
        api.getStaleness()
            .then(setStatus)
            .catch(() => setStatus(null));
    }, []);

    if (!status?.has_updates) {
        return null;
    }

    return (
        <div className="mb-4 space-y-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm">
            {status.alerts.map((alert) => (
                <div key={alert.kind}>
                    <p className="font-medium text-amber-200">
                        {alert.context === "cloud" ? "[Cloud] " : alert.context === "local" ? "[Local] " : ""}
                        {alert.title}
                    </p>
                    <p className="text-muted-foreground">{alert.message}</p>
                    <p className="mt-1 text-amber-100/90">→ {alert.action_api}</p>
                </div>
            ))}
            <p className="text-xs text-muted-foreground">
                Running Codewalk v{status.version.codewalk_version} ({status.version.commit_sha_short})
            </p>
        </div>
    );
}
