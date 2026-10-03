import { Menu } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError, getConversation, listConversations, streamQuestion } from "../api/client";
import {
  messageFromHistory,
  messageFromResult,
  type ConversationSummary,
  type ThreadMessage,
} from "../api/types";
import { Composer } from "../components/Composer";
import { EmptyState } from "../components/EmptyState";
import { MessageList } from "../components/MessageList";
import { Sidebar } from "../components/Sidebar";

function readableError(error: unknown, fallback: string): string {
  if (error instanceof ApiError && error.message && !/^Request failed \(\d+\)$/.test(error.message)) {
    return error.message;
  }
  return fallback;
}

function localizedStatus(value: string): string {
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
    if (!node) {
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
    const conversationAtSend = activeId;
    const controller = new AbortController();
    abortRef.current = controller;
    setDraft("");
    setStreaming(true);
    setStatus("Memeriksa tabel yang tersedia");
    setThreadError(null);
    setMessages((current) => [
      ...current,
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

  function pickSuggestion(question: string) {
    setDraft(question);
    composerField.current?.focus();
  }

  const title =
    conversations.find((conversation) => conversation.id === activeId)?.title?.trim() ||
    (activeId ? "Pertanyaan tanpa judul" : null);
  const showEmpty = !activeId && messages.length === 0 && !loadingThread && !threadError;

  const composer = (
    <Composer
      value={draft}
      disabled={streaming}
      streaming={streaming}
      fieldRef={composerField}
      onChange={setDraft}
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
      <main className="relative flex min-w-0 flex-1 flex-col">
        <header
          className={`flex min-h-14 shrink-0 items-center gap-2 border-b border-line bg-canvas px-2 sm:px-4 ${
            title ? "" : "lg:hidden"
          }`}
        >
          <button
            type="button"
            className="flex size-11 items-center justify-center rounded-lg text-ink-mid hover:bg-user lg:hidden"
            aria-label="Buka riwayat"
            aria-expanded={sidebarOpen}
            onClick={() => setSidebarOpen(true)}
          >
            <Menu size={20} />
          </button>
          {title ? (
            <h1 className="line-clamp-2 py-2 text-base leading-snug font-medium" title={title}>
              {title}
            </h1>
          ) : (
            <p className="reading text-base leading-5 font-medium">Tilik</p>
          )}
        </header>

        <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto">
          {showEmpty ? (
            <div className="flex min-h-full flex-col justify-center px-4 py-10 sm:px-6">
              <EmptyState onPick={pickSuggestion} />
              <div className="mx-auto mt-8 w-full max-w-xl">{composer}</div>
            </div>
          ) : (
            <div className="mx-auto w-full max-w-3xl px-4 pt-8 pb-40 sm:px-6">
              {loadingThread ? (
                <p className="text-sm text-ink-mid" role="status">
                  Membuka pertanyaan
                </p>
              ) : null}
              {threadError ? (
                <div className="rounded-xl bg-clay-wash px-4 py-3 text-ink" role="alert">
                  <p className="text-base leading-6">{threadError}</p>
                  <button
                    type="button"
                    onClick={() => setThreadReload((current) => current + 1)}
                    className="mt-3 min-h-11 rounded-full bg-ink px-4 text-sm font-medium text-canvas"
                  >
                    Coba lagi
                  </button>
                </div>
              ) : null}
              {messages.length > 0 ? (
                <MessageList messages={messages} status={status} busy={streaming} onRetry={(question) => void ask(question)} />
              ) : null}
            </div>
          )}
        </div>

        {showEmpty ? null : (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-canvas via-canvas to-transparent pt-8">
            <div className="pointer-events-auto mx-auto w-full max-w-3xl px-4 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-6">
              {composer}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
