"use client";

import { useState, useRef, useEffect } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Card } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Loader2, Send, Bot, User, Wifi, WifiOff } from "lucide-react";
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
    const [threadId] = useState(() => `thread-${Date.now()}`);
    const [connected, setConnected] = useState(true);
    const scrollRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [messages]);

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

        try {
            const res = await api.chat(trimmed, threadId);
            setMessages((prev) => [
                ...prev,
                { role: "assistant", content: res.answer },
            ]);
        } catch (err) {
            setMessages((prev) => [
                ...prev,
                {
                    role: "assistant",
                    content: `Error: ${err instanceof Error ? err.message : "Something went wrong"}`,
                },
            ]);
        } finally {
            setLoading(false);
        }
    }

    function handleKeyDown(e: React.KeyboardEvent) {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    }

    const showSuggestions = messages.length <= 1 && !loading;

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

                        {loading && (
                            <div className="flex gap-3">
                                <div className="h-8 w-8 rounded-full bg-primary flex items-center justify-center flex-shrink-0">
                                    <Bot className="h-4 w-4 text-primary-foreground" />
                                </div>
                                <div className="bg-muted rounded-lg px-4 py-2">
                                    <Loader2 className="h-4 w-4 animate-spin" />
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
                />
                <Button onClick={() => handleSend()} disabled={loading || !input.trim()}>
                    <Send className="h-4 w-4" />
                </Button>
            </div>
        </div>
    );
}
