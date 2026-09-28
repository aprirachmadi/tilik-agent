import { Search, Plus, X } from "lucide-react";
import { useState } from "react";
import type { ConversationSummary } from "../api/types";
import { Logo } from "./Logo";

const DAY_MS = 24 * 60 * 60 * 1000;
const updatedFormat = new Intl.DateTimeFormat("id-ID", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});

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
    { label: "Hari ini", items: [] },
    { label: "7 hari terakhir", items: [] },
    { label: "Lebih lama", items: [] },
  ];
  for (const conversation of conversations) {
    const updated = startOfDay(new Date(conversation.updated_at));
    if (updated >= today) {
      groups[0].items.push(conversation);
    } else if (updated >= previousWeek) {
      groups[1].items.push(conversation);
    } else {
      groups[2].items.push(conversation);
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
        className={`fixed inset-0 z-30 bg-ink/30 transition lg:hidden ${open ? "opacity-100" : "pointer-events-none opacity-0"}`}
        onClick={onClose}
      />
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-[280px] flex-col border-r border-line bg-elevated transition-transform duration-200 lg:static lg:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between gap-2 px-3 py-3">
          <div className="flex min-w-0 items-center gap-3">
            <Logo size={36} />
            <div className="min-w-0">
              <p className="reading text-base leading-5 font-medium">Tilik</p>
              <p className="text-sm text-ink-muted">Data BPS</p>
            </div>
          </div>
          <button
            type="button"
            className="flex size-11 items-center justify-center rounded-lg text-ink-mid hover:bg-canvas lg:hidden"
            aria-label="Tutup panel riwayat"
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </div>

        <div className="px-3">
          <button
            type="button"
            onClick={onNew}
            className="flex min-h-11 w-full items-center justify-center gap-2 rounded-lg border border-line bg-canvas px-3 text-sm font-medium text-ink hover:bg-user"
          >
            <Plus size={16} />
            Pertanyaan baru
          </button>
        </div>

        {conversations.length > 0 ? (
          <div className="relative mt-3 px-3">
            <Search size={17} aria-hidden="true" className="pointer-events-none absolute top-3.5 left-6 text-ink-muted" />
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              aria-label="Cari riwayat pertanyaan"
              placeholder="Cari riwayat"
              className="min-h-11 w-full rounded-lg border border-line bg-canvas pr-3 pl-10 text-sm text-ink placeholder:text-ink-muted"
            />
          </div>
        ) : null}

        <nav className="mt-3 flex-1 overflow-y-auto px-3 pb-6" aria-label="Riwayat pertanyaan">
          {historyError ? (
            <div className="mb-4 rounded-xl bg-clay-wash px-3 py-3 text-ink" role="alert">
              <p className="text-sm leading-5">{historyError}</p>
              <button
                type="button"
                onClick={onRetryHistory}
                className="mt-2 min-h-11 rounded-full bg-ink px-4 text-sm font-medium text-canvas"
              >
                Coba lagi
              </button>
            </div>
          ) : null}
          {groups.length === 0 && !historyError ? (
            <p className="px-2 pt-6 text-sm text-ink-muted">
              {query ? "Tidak ada pertanyaan yang cocok." : "Belum ada pertanyaan."}
            </p>
          ) : (
            groups.map((group) => (
              <div key={group.label} className="mb-5">
                <p className="px-2 pb-2 text-sm text-ink-muted">{group.label}</p>
                <ul className="space-y-0.5">
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
                          className={`w-full rounded-lg px-2.5 py-2 text-left text-sm leading-5 break-words ${
                            selected ? "bg-user font-medium text-ink" : "text-ink-mid hover:bg-canvas hover:text-ink"
                          }`}
                        >
                          <span className="line-clamp-2">{label}</span>
                          <span className="mt-0.5 block text-xs font-normal text-ink-muted">
                            {updatedFormat.format(new Date(conversation.updated_at))}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))
          )}
        </nav>
      </aside>
    </>
  );
}
