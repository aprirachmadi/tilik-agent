import { Pin, Plus, Search, X } from "lucide-react";
import { useState } from "react";
import type { ConversationSummary } from "../api/types";
import { Logo } from "./Logo";

const DAY_MS = 24 * 60 * 60 * 1000;

type SidebarProps = {
  conversations: ConversationSummary[];
  activeId: string | null;
  open: boolean;
  historyError: string | null;
  onClose: () => void;
  onNew: () => void;
  onSelect: (id: string) => void;
  onRetryHistory: () => void;
};

function startOfDay(value: Date): number {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
}

function groupConversations(conversations: ConversationSummary[]) {
  const today = startOfDay(new Date());
  const previousWeek = today - 7 * DAY_MS;
  const groups: { label: string; items: ConversationSummary[] }[] = [
    { label: "Contoh", items: [] },
    { label: "Hari ini", items: [] },
    { label: "7 hari terakhir", items: [] },
    { label: "Lebih lama", items: [] },
  ];
  for (const conversation of conversations) {
    const updated = startOfDay(new Date(conversation.updated_at));
    if (conversation.pinned) {
      groups[0].items.push(conversation);
    } else if (updated >= today) {
      groups[1].items.push(conversation);
    } else if (updated >= previousWeek) {
      groups[2].items.push(conversation);
    } else {
      groups[3].items.push(conversation);
    }
  }
  return groups.filter((group) => group.items.length > 0);
}

function conversationLabel(conversation: ConversationSummary): string {
  return conversation.title?.trim() || "Pertanyaan tanpa judul";
}

export function Sidebar({
  conversations,
  activeId,
  open,
  historyError,
  onClose,
  onNew,
  onSelect,
  onRetryHistory,
}: SidebarProps) {
  const [search, setSearch] = useState("");
  const query = search.trim().toLocaleLowerCase("id-ID");
  const filtered = query
    ? conversations.filter((conversation) => conversationLabel(conversation).toLocaleLowerCase("id-ID").includes(query))
    : conversations;
  const groups = groupConversations(filtered);

  return (
    <>
      <button
        type="button"
        aria-label="Tutup riwayat"
        className={`fixed inset-0 z-30 bg-ink/20 backdrop-blur-[2px] transition lg:hidden ${open ? "opacity-100" : "pointer-events-none opacity-0"}`}
        onClick={onClose}
      />
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-[272px] flex-col border-r border-line bg-subtle transition-transform duration-200 ease-out lg:static lg:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex h-14 items-center justify-between gap-2 px-4">
          <button type="button" onClick={onNew} className="flex items-center gap-2.5 rounded-lg" aria-label="Tilik, beranda">
            <Logo size={26} />
            <span className="text-[1.0625rem] font-semibold tracking-[-0.02em]">tilik</span>
          </button>
          <button
            type="button"
            className="-mr-2 flex size-10 items-center justify-center rounded-lg text-ink-mid hover:bg-hover lg:hidden"
            aria-label="Tutup panel riwayat"
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </div>

        <div className="space-y-2 px-3 pt-1">
          <button
            type="button"
            onClick={onNew}
            className="flex min-h-10 w-full items-center gap-2 rounded-full bg-ink px-4 text-sm font-medium text-canvas transition hover:bg-ink-mid"
          >
            <Plus size={16} strokeWidth={2.25} />
            Pertanyaan baru
          </button>
          {conversations.length > 0 ? (
            <div className="relative">
              <Search size={15} aria-hidden="true" className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-ink-muted" />
              <input
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                aria-label="Cari riwayat pertanyaan"
                placeholder="Cari riwayat"
                className="min-h-10 w-full rounded-full border border-transparent bg-transparent pr-3 pl-9 text-sm text-ink placeholder:text-ink-muted hover:bg-hover focus:border-line-strong focus:bg-canvas"
              />
            </div>
          ) : null}
        </div>

        <nav className="mt-4 flex-1 overflow-y-auto px-3 pb-6" aria-label="Riwayat pertanyaan">
          {historyError ? (
            <div className="mb-4 rounded-xl border border-line bg-canvas px-3 py-3" role="alert">
              <p className="text-sm leading-5 text-ink">{historyError}</p>
              <button
                type="button"
                onClick={onRetryHistory}
                className="mt-2 min-h-9 rounded-full border border-line-strong px-4 text-sm font-medium text-ink hover:bg-subtle"
              >
                Coba lagi
              </button>
            </div>
          ) : null}
          {groups.length === 0 && !historyError ? (
            <p className="px-3 pt-2 text-sm leading-5 text-ink-muted">
              {query ? "Tidak ada pertanyaan yang cocok." : "Pertanyaan Anda akan tersimpan di sini."}
            </p>
          ) : (
            groups.map((group) => (
              <div key={group.label} className="mb-5">
                <p className="eyebrow px-3 pb-1.5">{group.label}</p>
                <ul>
                  {group.items.map((conversation) => {
                    const selected = conversation.id === activeId;
                    const label = conversationLabel(conversation);
                    return (
                      <li key={conversation.id}>
                        <button
                          type="button"
                          title={label}
                          aria-current={selected ? "page" : undefined}
                          onClick={() => onSelect(conversation.id)}
                          className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm leading-5 transition ${
                            selected ? "bg-canvas font-medium text-ink shadow-[0_0_0_1px_var(--color-line)]" : "text-ink-mid hover:bg-hover hover:text-ink"
                          }`}
                        >
                          {conversation.pinned ? <Pin size={13} aria-label="Contoh" className="shrink-0 text-accent" /> : null}
                          <span className="truncate">{label}</span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))
          )}
        </nav>

        <p className="border-t border-line px-4 py-3 font-mono text-[0.6875rem] leading-4 text-ink-muted">
          Sumber: BPS · BNPB · BMKG · Bapanas · SiPongi
        </p>
      </aside>
    </>
  );
}
