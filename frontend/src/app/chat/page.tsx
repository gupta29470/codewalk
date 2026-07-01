"use client";

import { useState, useRef, useEffect } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Card } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Loader2, Send, Bot, User, Wifi, WifiOff, CheckCircle2, XCircle, AlertTriangle } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface Message {
    role: "user" | "assistant";
    content: string;
}

const SUGGESTED_QUESTIONS = [
    "Give me an overview of this project",
    "What are the riskiest files to change?",
    "Explain the main entry point",
    "What modules exist?",
];

export default function ChatPage() {
    const [messages, setMessages] = useState<Message[]>([
        {
            role: "assistant",
            content:
                "Welcome! I've analyzed your codebase. Ask me anything about the code.",
        },
    ]);
    const [input, setInput] = useState("");
    const [loading, setLoading] = useState(false);
    const [activeToolName, setActiveToolName] = useState<string | null>(null);
    const [threadId] = useState(() => `thread-${Date.now()}`);
    const [connected, setConnected] = useState(true);
    const [hitlPending, setHitlPending] = useState(false);
    const [hitlAction, setHitlAction] = useState("");
    const [hitlLoading, setHitlLoading] = useState(false);
    const scrollRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [messages, hitlPending]);

    // Connection check on mount + periodic polling
    useEffect(() => {
        const checkConnection = async () => {
            try {
                await api.health();
                setConnected(true);
            } catch {
                setConnected(false);
            }
        };
        checkConnection();
        const interval = setInterval(checkConnection, 10000);
        return () => clearInterval(interval);
    }, []);

    async function handleSend(message?: string) {
        const trimmed = (message || input).trim();
        if (!trimmed || loading) return;

        const userMsg: Message = { role: "user", content: trimmed };
        setMessages((prev) => [...prev, userMsg]);
        setInput("");
        setLoading(true);
        setActiveToolName(null);
        setHitlPending(false);
        setHitlAction("");

        // Add a streaming assistant message (starts empty, tokens appended live)
        setMessages((prev) => [...prev, { role: "assistant", content: "" }]);

        try {
            await api.streamChat(trimmed, threadId, (event) => {
                if (event.type === "token" && event.content) {
                    // Append each token to the last assistant message
                    setMessages((prev) => {
                        const updated = [...prev];
                        updated[updated.length - 1] = {
                            role: "assistant",
                            content: updated[updated.length - 1].content + event.content,
                        };
                        return updated;
                    });
                } else if (event.type === "tool_start" && event.name) {
                    setActiveToolName(event.name);
                } else if (event.type === "tool_end") {
                    setActiveToolName(null);
                } else if (event.type === "done") {
                    setLoading(false);
                    setActiveToolName(null);
                } else if (event.type === "interrupted") {
                    setLoading(false);
                    setActiveToolName(null);
                    setHitlPending(true);
                    setHitlAction(event.proposed_action || "unknown action");
                } else if (event.type === "error") {
                    setMessages((prev) => {
                        const updated = [...prev];
                        updated[updated.length - 1] = {
                            role: "assistant",
                            content: `Error: ${event.message ?? "Something went wrong"}`,
                        };
                        return updated;
                    });
                    setLoading(false);
                    setActiveToolName(null);
                }
            });
        } catch (err) {
            setMessages((prev) => {
                const updated = [...prev];
                updated[updated.length - 1] = {
                    role: "assistant",
                    content: `Error: ${err instanceof Error ? err.message : "Something went wrong"}`,
                };
                return updated;
            });
        } finally {
            setLoading(false);
            setActiveToolName(null);
        }
    }

    async function handleHitl(action: "approve" | "reject") {
        setHitlLoading(true);
        try {
            const res = await api.chatApprove(threadId, action);

            if (res.status === "rejected") {
                setMessages((prev) => [
                    ...prev,
                    { role: "assistant", content: res.result || res.message || "Action rejected." },
                ]);
            } else if (res.status === "completed") {
                setMessages((prev) => [
                    ...prev,
                    { role: "assistant", content: typeof res.result === "string" ? res.result : "Action completed." },
                ]);
            } else if (res.status === "interrupted") {
                setMessages((prev) => [
                    ...prev,
                    { role: "assistant", content: "Agent requires another approval." },
                ]);
            }
        } catch (err) {
            setMessages((prev) => [
                ...prev,
                { role: "assistant", content: `Approval failed: ${err instanceof Error ? err.message : "Unknown error"}` },
            ]);
        } finally {
            setHitlLoading(false);
            setHitlPending(false);
            setHitlAction("");
        }
    }

    function handleKeyDown(e: React.KeyboardEvent) {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    }

    const showSuggestions = messages.length <= 1 && !loading && !hitlPending;

    return (
        <div className="flex flex-col h-full p-6">
            <div className="flex items-center justify-between mb-4">
                <h1 className="text-2xl font-bold text-kinetic-on-surface">Ask Codewalk</h1>
                <div className="flex items-center gap-2 text-sm">
                    {connected ? (
                        <><Wifi className="h-4 w-4 text-kinetic-node-config" /><span className="text-kinetic-on-surface-variant">Connected</span></>
                    ) : (
                        <><WifiOff className="h-4 w-4 text-kinetic-error" /><span className="text-kinetic-error">Backend offline — run `uvicorn src.codewalk.api.main:app`</span></>
                    )}
                </div>
            </div>

            {/* Messages */}
            <Card className="flex-1 overflow-hidden border-kinetic-border bg-kinetic-surface-container-low">
                <ScrollArea className="h-full p-4" ref={scrollRef}>
                    <div className="space-y-4">
                        {messages.map((msg, idx) => (
                            <div
                                key={idx}
                                className={`flex gap-3 ${msg.role === "user" ? "justify-end" : "justify-start"
                                    }`}
                            >
                                {msg.role === "assistant" && (
                                    <div className="h-8 w-8 rounded-full bg-kinetic-primary flex items-center justify-center flex-shrink-0">
                                        <Bot className="h-4 w-4 text-kinetic-on-primary" />
                                    </div>
                                )}
                                <div
                                    className={`max-w-[75%] rounded-md px-4 py-2 text-sm ${msg.role === "user"
                                        ? "bg-kinetic-primary text-kinetic-on-primary"
                                        : "bg-kinetic-surface-container text-kinetic-on-surface border border-kinetic-border"
                                        }`}
                                >
                                    {msg.role === "assistant" ? (
                                        msg.content === "" && loading ? (
                                            <div className="flex items-center gap-2 text-kinetic-on-surface">
                                                <Loader2 className="h-4 w-4 animate-spin flex-shrink-0 text-kinetic-primary" />
                                                {activeToolName && (
                                                    <span className="text-kinetic-on-surface-variant">
                                                        {activeToolName.replace(/_/g, " ")}…
                                                    </span>
                                                )}
                                            </div>
                                        ) : (
                                            <div className="prose prose-sm prose-invert max-w-none chat-message">
                                                <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                                            </div>
                                        )
                                    ) : (
                                        <pre className="whitespace-pre-wrap font-sans">
                                            {msg.content}
                                        </pre>
                                    )}
                                </div>
                                {msg.role === "user" && (
                                    <div className="h-8 w-8 rounded-full bg-kinetic-surface-container flex items-center justify-center flex-shrink-0 border border-kinetic-border">
                                        <User className="h-4 w-4 text-kinetic-on-surface" />
                                    </div>
                                )}
                            </div>
                        ))}

                        {/* HITL Approval Card */}
                        {hitlPending && (
                            <div className="flex gap-3">
                                <div className="h-8 w-8 rounded-full bg-kinetic-primary flex items-center justify-center flex-shrink-0">
                                    <Bot className="h-4 w-4 text-kinetic-on-primary" />
                                </div>
                                <Card className="bg-kinetic-tertiary/10 border-kinetic-tertiary/30 p-4 max-w-[80%]">
                                    <div className="flex items-start gap-2 mb-3">
                                        <AlertTriangle className="h-5 w-5 text-kinetic-tertiary flex-shrink-0 mt-0.5" />
                                        <div>
                                            <p className="text-sm font-medium text-kinetic-tertiary">
                                                The agent wants to apply a code fix
                                            </p>
                                            <p className="text-xs text-kinetic-on-surface-variant mt-1 font-mono">
                                                {hitlAction}
                                            </p>
                                        </div>
                                    </div>
                                    <div className="flex gap-2">
                                        <Button
                                            size="sm"
                                            variant="default"
                                            onClick={() => handleHitl("approve")}
                                            disabled={hitlLoading}
                                            className="bg-kinetic-node-config text-kinetic-on-primary hover:bg-kinetic-node-config/90"
                                        >
                                            <CheckCircle2 className="h-4 w-4 mr-1" />
                                            Approve
                                        </Button>
                                        <Button
                                            size="sm"
                                            variant="outline"
                                            onClick={() => handleHitl("reject")}
                                            disabled={hitlLoading}
                                            className="border-kinetic-error text-kinetic-error hover:bg-kinetic-error/10 hover:text-kinetic-error"
                                        >
                                            <XCircle className="h-4 w-4 mr-1" />
                                            Reject
                                        </Button>
                                    </div>
                                </Card>
                            </div>
                        )}


                    </div>
                </ScrollArea>
            </Card>

            {/* Input */}
            {showSuggestions && (
                <div className="flex flex-wrap gap-2 mt-4">
                    {SUGGESTED_QUESTIONS.map((q) => (
                        <Button
                            key={q}
                            variant="outline"
                            size="sm"
                            className="text-xs border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface-variant hover:bg-kinetic-surface-container-high hover:text-kinetic-on-surface"
                            onClick={() => handleSend(q)}
                        >
                            {q}
                        </Button>
                    ))}
                </div>
            )}
            <div className="flex gap-2 mt-4">
                <Textarea
                    placeholder="Type your question..."
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={handleKeyDown}
                    rows={1}
                    className="resize-none border-kinetic-border bg-kinetic-surface-container text-kinetic-on-surface placeholder:text-kinetic-on-surface-variant focus-visible:ring-kinetic-primary"
                    disabled={hitlPending}
                />
                <Button
                    onClick={() => handleSend()}
                    disabled={loading || !input.trim() || hitlPending}
                    className="bg-kinetic-primary text-kinetic-on-primary hover:bg-kinetic-primary/90"
                >
                    <Send className="h-4 w-4" />
                </Button>
            </div>
        </div>
    );
}
