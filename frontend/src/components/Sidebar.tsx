import { Plus, X } from "lucide-react";
import type { ConversationSummary } from "../api/types";
import { Logo } from "./Logo";

const DAY_MS = 24 * 60 * 60 * 1000;

type SidebarProps = {
  conversations: ConversationSummary[];
  activeId: string | null;
  open: boolean;
  disabled: boolean;
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
    { label: "Today", items: [] },
    { label: "Previous 7 days", items: [] },
    { label: "Older", items: [] },
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
  return conversation.title?.trim() || "Untitled question";
}

export function Sidebar({
  conversations,
  activeId,
  open,
  disabled,
  historyError,
  onClose,
  onNew,
  onSelect,
  onRetryHistory,
}: SidebarProps) {
  const groups = groupConversations(conversations);

  return (
    <>
      <button
        type="button"
        aria-label="Close history"
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
              <p className="text-sm text-ink-muted">BPS statistics</p>
            </div>
          </div>
          <button
            type="button"
            className="flex size-11 items-center justify-center rounded-lg text-ink-mid hover:bg-canvas lg:hidden"
            aria-label="Close sidebar"
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </div>

        <div className="px-3">
          <button
            type="button"
            onClick={onNew}
            disabled={disabled}
            className="flex min-h-11 w-full items-center justify-center gap-2 rounded-lg border border-line bg-canvas px-3 text-sm font-medium text-ink hover:bg-user disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Plus size={16} />
            New question
          </button>
        </div>

        <nav className="mt-4 flex-1 overflow-y-auto px-3 pb-6" aria-label="Question history">
          {historyError ? (
            <div className="mb-4 rounded-xl bg-clay-wash px-3 py-3 text-ink" role="alert">
              <p className="text-sm leading-5">{historyError}</p>
              <button
                type="button"
                onClick={onRetryHistory}
                className="mt-2 min-h-11 rounded-full bg-ink px-4 text-sm font-medium text-canvas"
              >
                Try again
              </button>
            </div>
          ) : null}
          {groups.length === 0 && !historyError ? (
            <p className="px-2 pt-6 text-sm text-ink-muted">No questions yet.</p>
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
                          disabled={disabled}
                          title={label}
                          aria-current={selected ? "page" : undefined}
                          onClick={() => onSelect(conversation.id)}
                          className={`line-clamp-2 w-full rounded-lg px-2.5 py-2 text-left text-sm leading-5 break-words disabled:cursor-not-allowed ${
                            selected ? "bg-user font-medium text-ink" : "text-ink-mid hover:bg-canvas hover:text-ink"
                          }`}
                        >
                          {label}
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
