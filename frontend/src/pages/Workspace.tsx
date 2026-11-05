import { Menu, Pin } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError, getConversation, listConversations, streamQuestion } from "../api/client";
import {
  messageFromHistory,
  messageFromResult,
  type ConversationSummary,
  type ThreadMessage,
} from "../api/types";
import { Composer, type SourceChoice } from "../components/Composer";
import { EmptyState } from "../components/EmptyState";
import { MessageList } from "../components/MessageList";
import { Sidebar } from "../components/Sidebar";
import type { SourceId } from "../suggestions";

function readableError(error: unknown, fallback: string): string {
  if (error instanceof ApiError && error.message && !/^Request failed \(\d+\)$/.test(error.message)) {
    return error.message;
  }
  return fallback;
}

function localizedStatus(value: string): string {
  if (value.startsWith("Checking ")) return `Memeriksa ${value.slice(9)}`;
  const labels: Record<string, string> = {
    "Looking up datasets": "Mencari tabel BPS",
    "Searching SP2020": "Mencari publikasi SP2020",
    "Writing the answer": "Menyusun jawaban",
  };
  return labels[value] ?? "Memeriksa sumber data";
}

function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

export function Workspace() {
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const activeId = conversationId ?? null;
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [messages, setMessages] = useState<ThreadMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [source, setSource] = useState<SourceChoice>("auto");
  const [status, setStatus] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [threadError, setThreadError] = useState<string | null>(null);
  const [loadingThread, setLoadingThread] = useState(Boolean(activeId));
  const [historyReload, setHistoryReload] = useState(0);
  const [threadReload, setThreadReload] = useState(0);
  const scroller = useRef<HTMLDivElement>(null);
  const composerField = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const skipLoad = useRef<string | null>(null);
  const [boundRoute, setBoundRoute] = useState(activeId ?? "new");
  const routeKey = activeId ?? "new";
  if (routeKey !== boundRoute) {
    setBoundRoute(routeKey);
    const keepStream = activeId !== null && skipLoad.current === activeId;
    if (!keepStream) {
      setMessages([]);
      setDraft("");
      setThreadError(null);
      setLoadingThread(Boolean(activeId));
    }
  }

  const refreshConversations = useCallback(async () => {
    const rows = await listConversations();
    setConversations(rows);
    setHistoryError(null);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    listConversations(controller.signal)
      .then((rows) => {
        if (!controller.signal.aborted) {
          setConversations(rows);
          setHistoryError(null);
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted && !isAbort(error)) {
          setHistoryError(readableError(error, "Riwayat tidak dapat dimuat. Coba lagi."));
        }
      });
    return () => controller.abort();
  }, [historyReload]);

  useEffect(() => {
    if (!activeId) {
      return;
    }
    if (skipLoad.current === activeId) {
      skipLoad.current = null;
      return;
    }
    const controller = new AbortController();
    getConversation(activeId, controller.signal)
      .then((detail) => {
        if (controller.signal.aborted) {
          return;
        }
        setMessages(detail.messages.map(messageFromHistory).filter((message) => message !== null));
        setThreadError(null);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted && !isAbort(error)) {
          setMessages([]);
          setThreadError(readableError(error, "Pertanyaan ini tidak dapat dibuka. Coba lagi."));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoadingThread(false);
        }
      });
    return () => controller.abort();
  }, [activeId, threadReload]);

  useEffect(() => {
    const node = scroller.current;
    if (!node || messages.length === 0) {
      return;
    }
    node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
  }, [messages, status, loadingThread]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setSidebarOpen(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function stop() {
    abortRef.current?.abort();
  }

  function leaveFor(path: string) {
    if (path === (activeId ? `/c/${activeId}` : "/")) {
      stop();
      setSidebarOpen(false);
      return;
    }
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
      setStreaming(false);
      setStatus(null);
    }
    setSidebarOpen(false);
    navigate(path);
  }

  async function ask(question: string) {
    const text = question.trim();
    if (!text || streaming) {
      return;
    }
    // A pinned example is read-only, so asking from it starts the visitor's own conversation.
    const conversationAtSend = activePinned ? null : activeId;
    const controller = new AbortController();
    abortRef.current = controller;
    setDraft("");
    setStreaming(true);
    setStatus("Memahami pertanyaan");
    setThreadError(null);
    setMessages((current) => [
      ...(conversationAtSend ? current : []),
      {
        key: `user-${Date.now()}`,
        role: "user",
        content: text,
        chart: null,
        blocks: null,
        sources: [],
        sourceRoute: null,
        chartError: null,
        planError: null,
        sqlError: null,
        sqlWarnings: null,
        retrievalError: null,
        pending: false,
      },
      {
        key: "pending-assistant",
        role: "assistant",
        content: "",
        chart: null,
        blocks: null,
        sources: [],
        sourceRoute: null,
        chartError: null,
        planError: null,
        sqlError: null,
        sqlWarnings: null,
        retrievalError: null,
        pending: true,
      },
    ]);

    try {
      const result = await streamQuestion(
        text,
        conversationAtSend,
        (value) => {
          if (abortRef.current === controller) setStatus(localizedStatus(value));
        },
        controller.signal,
        source === "auto" ? null : [source],
      );
      if (abortRef.current !== controller) return;
      setMessages((current) => current.map((message) => (message.pending ? messageFromResult(result) : message)));
      setStatus(null);
      await refreshConversations();
      if (abortRef.current !== controller) return;
      if (!conversationAtSend) {
        skipLoad.current = result.conversation_id;
        navigate(`/c/${result.conversation_id}`);
      }
    } catch (error: unknown) {
      if (abortRef.current !== controller) return;
      const stopped = isAbort(error);
      const detail = stopped
        ? "Pencarian dihentikan sebelum jawaban selesai. Anda dapat mencoba lagi."
        : readableError(error, "Jawaban belum dapat disiapkan. Coba lagi.");
      setMessages((current) =>
        current.map((message) =>
          message.pending
            ? {
                ...message,
                pending: false,
                planError: detail,
              }
            : message,
        ),
      );
      setStatus(null);
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
        setStreaming(false);
        composerField.current?.focus();
      }
    }
  }

  function pickSuggestion(question: string, suggested: SourceId) {
    setDraft(question);
    setSource(suggested);
    composerField.current?.focus();
  }

  const active = conversations.find((conversation) => conversation.id === activeId);
  const activePinned = Boolean(active?.pinned);
  const example = conversations.find((conversation) => conversation.pinned);
  const title = active?.title?.trim() || (activeId ? "Pertanyaan tanpa judul" : null);
  const showEmpty = !activeId && messages.length === 0 && !loadingThread && !threadError;

  const composer = (
    <Composer
      value={draft}
      source={source}
      disabled={streaming}
      streaming={streaming}
      fieldRef={composerField}
      onChange={setDraft}
      onSourceChange={setSource}
      onSubmit={() => void ask(draft)}
      onStop={stop}
    />
  );

  return (
    <div className="flex h-dvh overflow-hidden bg-canvas text-ink">
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        open={sidebarOpen}
        historyError={historyError}
        onClose={() => setSidebarOpen(false)}
        onNew={() => leaveFor("/")}
        onSelect={(id) => leaveFor(`/c/${id}`)}
        onRetryHistory={() => setHistoryReload((current) => current + 1)}
      />
      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-2 border-b border-line px-2 sm:px-6 lg:border-transparent">
          <button
            type="button"
            className="flex size-10 items-center justify-center rounded-lg text-ink-mid hover:bg-subtle lg:hidden"
            aria-label="Buka riwayat"
            aria-expanded={sidebarOpen}
            onClick={() => setSidebarOpen(true)}
          >
            <Menu size={20} />
          </button>
          {title ? (
            <p className="min-w-0 truncate text-sm text-ink-mid" title={title}>
              {title}
            </p>
          ) : (
            <p className="text-sm font-semibold tracking-[-0.01em] lg:hidden">tilik</p>
          )}
        </header>

        <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto">
          {showEmpty ? (
            <div className="flex min-h-full flex-col justify-center px-5 py-12 sm:px-10">
              <EmptyState
                composer={composer}
                onPick={pickSuggestion}
                onOpenExample={example ? () => leaveFor(`/c/${example.id}`) : null}
              />
            </div>
          ) : (
            <div className="mx-auto w-full max-w-3xl px-5 pt-8 pb-16 sm:px-8">
              {loadingThread ? (
                <div role="status" className="max-w-md">
                  <p className="text-sm text-ink-mid">Membuka pertanyaan</p>
                  <div className="mt-2 h-px overflow-hidden bg-line">
                    <div className="scan-bar h-px w-1/3 bg-ink" />
                  </div>
                </div>
              ) : null}
              {threadError ? (
                <div className="max-w-[68ch] rounded-xl border-l-2 border-warn bg-warn-wash px-4 py-3" role="alert">
                  <p className="text-sm leading-6 text-ink">{threadError}</p>
                  <button
                    type="button"
                    onClick={() => setThreadReload((current) => current + 1)}
                    className="mt-3 min-h-10 rounded-full bg-ink px-4 text-sm font-medium text-canvas hover:bg-ink-mid"
                  >
                    Coba lagi
                  </button>
                </div>
              ) : null}
              {activePinned && !streaming ? (
                <p className="mb-8 flex items-start gap-2 rounded-xl border border-line bg-subtle px-4 py-3 text-sm leading-5 text-ink-mid">
                  <Pin size={15} aria-hidden="true" className="mt-0.5 shrink-0 text-accent" />
                  <span>
                    <span className="font-medium text-ink">Contoh percakapan.</span> Hanya untuk dilihat. Pertanyaan
                    yang Anda kirim di bawah akan dimulai sebagai percakapan baru milik Anda.
                  </span>
                </p>
              ) : null}
              {messages.length > 0 ? (
                <MessageList messages={messages} status={status} busy={streaming} onRetry={(question) => void ask(question)} />
              ) : null}
            </div>
          )}
        </div>

        {showEmpty ? null : (
          <div className="shrink-0 border-t border-line bg-canvas/90 backdrop-blur">
            <div className="mx-auto w-full max-w-3xl px-5 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-8">
              {composer}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
