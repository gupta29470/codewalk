"use client";

import { useEffect, useState } from "react";
import { api, ReadingOrderResponse } from "@/lib/api";
import { useAnalyze } from "@/lib/analyze-context";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RiskBadge } from "@/components/RiskBadge";
import { Separator } from "@/components/ui/separator";
import { Loader2, Star, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";

export default function ReadingOrderPage() {
    const { cache, setCache } = useAnalyze();
    const [order, setOrder] = useState<ReadingOrderResponse | null>(cache.readingOrder);
    const [loading, setLoading] = useState(!cache.readingOrder);
    const [error, setError] = useState("");

    function fetchData() {
        setLoading(true);
        setError("");
        api
            .getReadingOrder()
            .then((res) => {
                setOrder(res);
                setCache("readingOrder", res);
            })
            .catch((err) => setError(err.message))
            .finally(() => setLoading(false));
    }

    useEffect(() => {
        if (cache.readingOrder) return;
        fetchData();
    }, []);

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-full p-6">
                <Loader2 className="h-8 w-8 animate-spin text-kinetic-primary" />
            </div>
        );
    }

    if (error) {
        return (
            <div className="p-6">
                <div className="p-4 bg-kinetic-error/10 text-kinetic-error rounded-md border border-kinetic-error/20">
                    {error}
                </div>
            </div>
        );
    }

    return (
        <div className="p-6 space-y-6 max-w-6xl">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-kinetic-on-surface">Code Reading Guide</h1>
                    <p className="text-kinetic-on-surface-variant mt-1">
                        Start here, read in this order
                    </p>
                </div>
                <Button
                    variant="outline"
                    size="sm"
                    onClick={fetchData}
                    disabled={loading}
                    className="border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface"
                >
                    <RefreshCw className={`h-4 w-4 mr-1 ${loading ? "animate-spin" : ""}`} />
                    Refresh
                </Button>
            </div>

            {/* Reading Order */}
            <Card className="border-kinetic-border bg-kinetic-surface-container-low">
                <CardHeader>
                    <CardTitle className="text-kinetic-on-surface">Recommended Reading Order</CardTitle>
                </CardHeader>
                <CardContent className="space-y-0">
                    {order?.order.map((item, idx) => (
                        <div key={item.file}>
                            <div className="flex items-start gap-3 py-3">
                                <div className="flex items-center gap-2 mt-0.5">
                                    {item.priority === "must-read" && (
                                        <Star className="h-4 w-4 text-kinetic-tertiary fill-kinetic-tertiary" />
                                    )}
                                    <span className="text-sm font-medium text-kinetic-on-surface-variant w-6">
                                        {idx + 1}.
                                    </span>
                                </div>
                                <div className="flex-1 space-y-1">
                                    <div className="flex items-center gap-2">
                                        <span className="font-mono text-sm font-medium text-kinetic-on-surface">
                                            {item.file}
                                        </span>
                                        {item.risk_level && <RiskBadge level={item.risk_level} />}
                                    </div>
                                    <p className="text-sm text-kinetic-on-surface-variant">{item.reason}</p>
                                    {item.direct && item.direct.length > 0 && (
                                        <p className="text-xs text-kinetic-on-surface-variant">
                                            Used by: {item.direct.join(", ")}
                                        </p>
                                    )}
                                </div>
                                {item.affected_files !== undefined && (
                                    <span className="text-xs text-kinetic-on-surface-variant whitespace-nowrap">
                                        {item.affected_files} dependents
                                    </span>
                                )}
                            </div>
                            {idx < (order?.order.length ?? 0) - 1 && <Separator className="bg-kinetic-border" />}
                        </div>
                    ))}
                </CardContent>
            </Card>
        </div>
    );
}
