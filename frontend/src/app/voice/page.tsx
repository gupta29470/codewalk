"use client";

import { useState, useRef, useCallback } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Loader2, Mic, MicOff, Volume2, Bot, User } from "lucide-react";
import ReactMarkdown from "react-markdown";

interface VoiceMessage {
    role: "user" | "assistant";
    content: string;
    speech?: string;
    tool?: string | null;
    audioBase64?: string;
}

export default function VoicePage() {
    const [messages, setMessages] = useState<VoiceMessage[]>([
        {
            role: "assistant",
            content:
                "Voice mode ready. Click the mic button and ask anything about your codebase. I'll speak the answer back.",
        },
    ]);
    const [recording, setRecording] = useState(false);
    const [processing, setProcessing] = useState(false);
    const [threadId] = useState(() => `voice-${Date.now()}`);
    const mediaRecorderRef = useRef<MediaRecorder | null>(null);
    const chunksRef = useRef<Blob[]>([]);
    const scrollRef = useRef<HTMLDivElement>(null);

    const scrollToBottom = useCallback(() => {
        setTimeout(() => {
            if (scrollRef.current) {
                scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
            }
        }, 100);
    }, []);

    function playAudio(base64: string) {
        const bytes = atob(base64);
        const arr = new Uint8Array(bytes.length);
        for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
        const blob = new Blob([arr], { type: "audio/mp3" });
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        audio.play();
        audio.onended = () => URL.revokeObjectURL(url);
    }

    async function startRecording() {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            const mediaRecorder = new MediaRecorder(stream, {
                mimeType: MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
                    ? "audio/webm;codecs=opus"
                    : "audio/webm",
            });
            mediaRecorderRef.current = mediaRecorder;
            chunksRef.current = [];

            mediaRecorder.ondataavailable = (e) => {
                if (e.data.size > 0) chunksRef.current.push(e.data);
            };

            mediaRecorder.onstop = async () => {
                stream.getTracks().forEach((t) => t.stop());
                const blob = new Blob(chunksRef.current, { type: "audio/webm" });
                if (blob.size < 1000) return; // too short, ignore
                await sendAudio(blob);
            };

            mediaRecorder.start(250);
            setRecording(true);
        } catch (err) {
            console.error("Mic access denied:", err);
        }
    }

    function stopRecording() {
        if (mediaRecorderRef.current && recording) {
            mediaRecorderRef.current.stop();
            setRecording(false);
        }
    }

    async function sendAudio(blob: Blob) {
        setProcessing(true);
        scrollToBottom();

        try {
            const res = await api.voiceAsk(blob, threadId);

            if (res.question) {
                setMessages((prev) => [
                    ...prev,
                    { role: "user", content: res.question },
                ]);
            }

            setMessages((prev) => [
                ...prev,
                {
                    role: "assistant",
                    content: res.answer,
                    speech: res.speech,
                    tool: res.tool,
                    audioBase64: res.audio_base64,
                },
            ]);

            scrollToBottom();

            // Auto-play the spoken response
            if (res.audio_base64) {
                playAudio(res.audio_base64);
            }
        } catch (err) {
            setMessages((prev) => [
                ...prev,
                {
                    role: "assistant",
                    content: `Error: ${err instanceof Error ? err.message : "Something went wrong"}`,
                },
            ]);
        } finally {
            setProcessing(false);
        }
    }

    return (
        <div className="flex flex-col h-[calc(100vh-3rem)]">
            <h1 className="text-2xl font-bold mb-4">Voice Assistant</h1>

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
                                        <div>
                                            {msg.tool && (
                                                <span className="text-xs text-muted-foreground block mb-1">
                                                    🔧 {msg.tool}
                                                </span>
                                            )}
                                            {msg.speech && msg.speech !== msg.content && (
                                                <div className="mb-3 p-3 bg-primary/5 rounded-md border border-primary/10">
                                                    <span className="text-xs font-medium text-primary block mb-1">
                                                        🔊 Voice
                                                    </span>
                                                    <p className="text-sm">{msg.speech}</p>
                                                </div>
                                            )}
                                            <div className="prose prose-sm dark:prose-invert max-w-none">
                                                <ReactMarkdown>{msg.content}</ReactMarkdown>
                                            </div>
                                            {msg.audioBase64 && (
                                                <button
                                                    onClick={() => playAudio(msg.audioBase64!)}
                                                    className="mt-2 text-xs text-muted-foreground hover:text-foreground flex items-center gap-1"
                                                >
                                                    <Volume2 className="h-3 w-3" />
                                                    Replay
                                                </button>
                                            )}
                                        </div>
                                    ) : (
                                        <p>{msg.content}</p>
                                    )}
                                </div>
                                {msg.role === "user" && (
                                    <div className="h-8 w-8 rounded-full bg-muted flex items-center justify-center flex-shrink-0">
                                        <User className="h-4 w-4" />
                                    </div>
                                )}
                            </div>
                        ))}

                        {processing && (
                            <div className="flex gap-3 justify-start">
                                <div className="h-8 w-8 rounded-full bg-primary flex items-center justify-center flex-shrink-0">
                                    <Bot className="h-4 w-4 text-primary-foreground" />
                                </div>
                                <div className="bg-muted rounded-lg px-4 py-2 text-sm flex items-center gap-2">
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                    Transcribing & thinking...
                                </div>
                            </div>
                        )}
                    </div>
                </ScrollArea>
            </Card>

            {/* Mic controls */}
            <div className="mt-4 flex justify-center">
                <Button
                    size="lg"
                    variant={recording ? "destructive" : "default"}
                    className="rounded-full h-16 w-16"
                    onClick={recording ? stopRecording : startRecording}
                    disabled={processing}
                >
                    {processing ? (
                        <Loader2 className="h-6 w-6 animate-spin" />
                    ) : recording ? (
                        <MicOff className="h-6 w-6" />
                    ) : (
                        <Mic className="h-6 w-6" />
                    )}
                </Button>
            </div>
            <p className="text-center text-xs text-muted-foreground mt-2 mb-4">
                {recording
                    ? "Recording... click to stop"
                    : processing
                        ? "Processing your question..."
                        : "Click the mic to ask a question"}
            </p>
        </div>
    );
}
