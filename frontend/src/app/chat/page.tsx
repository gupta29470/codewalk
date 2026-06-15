"use client";

import { useState, useRef, useEffect } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Card } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Loader2, Send, Bot, User, Wifi, WifiOff, CheckCircle2, XCircle, AlertTriangle } from "lucide-react";
import ReactMarkdown from "react-markdown";

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
        <div className="flex flex-col h-[calc(100vh-3rem)]">
            <div className="flex items-center justify-between mb-4">
                <h1 className="text-2xl font-bold">Ask Codewalk</h1>
                <div className="flex items-center gap-2 text-sm">
                    {connected ? (
                        <><Wifi className="h-4 w-4 text-green-500" /><span className="text-muted-foreground">Connected</span></>
                    ) : (
                        <><WifiOff className="h-4 w-4 text-red-500" /><span className="text-red-500">Backend offline — run `uvicorn src.codewalk.api.main:app`</span></>
                    )}
                </div>
            </div>

            {/* Messages */}
            <Card className="flex-1 overflow-hidden">
                <ScrollArea className="h-full p-4" ref={scrollRef}>
                    <div className="space-y-4">
                        {messages.map((msg, idx) => (
                            <div
                                key={idx}
                                className={`flex gap-3 ${msg.role === "user" ? "justify-end" : "justify-start"
                                    }`}
                            >
                                {msg.role === "assistant" && (
                                    <div className="h-8 w-8 rounded-full bg-primary flex items-center justify-center flex-shrink-0">
                                        <Bot className="h-4 w-4 text-primary-foreground" />
                                    </div>
                                )}
                                <div
                                    className={`max-w-[75%] rounded-lg px-4 py-2 text-sm ${msg.role === "user"
                                        ? "bg-primary text-primary-foreground"
                                        : "bg-muted"
                                        }`}
                                >
                                    {msg.role === "assistant" ? (
                                        <div className="prose prose-sm dark:prose-invert max-w-none">
                                            <ReactMarkdown>{msg.content}</ReactMarkdown>
                                        </div>
                                    ) : (
                                        <pre className="whitespace-pre-wrap font-sans">
                                            {msg.content}
                                        </pre>
                                    )}
                                </div>
                                {msg.role === "user" && (
                                    <div className="h-8 w-8 rounded-full bg-muted flex items-center justify-center flex-shrink-0">
                                        <User className="h-4 w-4" />
                                    </div>
                                )}
                            </div>
                        ))}

                        {/* HITL Approval Card */}
                        {hitlPending && (
                            <div className="flex gap-3">
                                <div className="h-8 w-8 rounded-full bg-primary flex items-center justify-center flex-shrink-0">
                                    <Bot className="h-4 w-4 text-primary-foreground" />
                                </div>
                                <Card className="bg-amber-50 dark:bg-amber-950 border-amber-200 dark:border-amber-800 p-4 max-w-[80%]">
                                    <div className="flex items-start gap-2 mb-3">
                                        <AlertTriangle className="h-5 w-5 text-amber-600 dark:text-amber-400 flex-shrink-0 mt-0.5" />
                                        <div>
                                            <p className="text-sm font-medium text-amber-800 dark:text-amber-200">
                                                The agent wants to apply a code fix
                                            </p>
                                            <p className="text-xs text-amber-700 dark:text-amber-300 mt-1 font-mono">
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
                                            className="bg-green-600 hover:bg-green-700"
                                        >
                                            <CheckCircle2 className="h-4 w-4 mr-1" />
                                            Approve
                                        </Button>
                                        <Button
                                            size="sm"
                                            variant="outline"
                                            onClick={() => handleHitl("reject")}
                                            disabled={hitlLoading}
                                            className="border-red-300 text-red-600 hover:bg-red-50 hover:text-red-700"
                                        >
                                            <XCircle className="h-4 w-4 mr-1" />
                                            Reject
                                        </Button>
                                    </div>
                                </Card>
                            </div>
                        )}

                        {loading && (
                            <div className="flex gap-3">
                                <div className="h-8 w-8 rounded-full bg-primary flex items-center justify-center flex-shrink-0">
                                    <Bot className="h-4 w-4 text-primary-foreground" />
                                </div>
                                <div className="bg-muted rounded-lg px-4 py-2 text-sm flex items-center gap-2">
                                    <Loader2 className="h-4 w-4 animate-spin flex-shrink-0" />
                                    {activeToolName && (
                                        <span className="text-muted-foreground">
                                            {activeToolName.replace(/_/g, " ")}…
                                        </span>
                                    )}
                                </div>
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
                            className="text-xs"
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
                    className="resize-none"
                    disabled={hitlPending}
                />
                <Button onClick={() => handleSend()} disabled={loading || !input.trim() || hitlPending}>
                    <Send className="h-4 w-4" />
                </Button>
            </div>
        </div>
    );
}
