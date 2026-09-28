import type { Components } from "react-markdown";
import Markdown from "react-markdown";
import type { ThreadMessage } from "../api/types";
import { Callout } from "./Callout";
import { ChartCard } from "./ChartCard";
import { SourceChips } from "./SourceChips";

const markdown: Components = {
  p: ({ children }) => <p className="mb-4 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="mb-4 list-disc space-y-1 pl-5 last:mb-0">{children}</ul>,
  ol: ({ children }) => <ol className="mb-4 list-decimal space-y-1 pl-5 last:mb-0">{children}</ol>,
  li: ({ children }) => <li>{children}</li>,
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  a: ({ href, children }) => (
    <a href={href} className="text-teal underline decoration-teal/40 underline-offset-4" target="_blank" rel="noreferrer">
      {children}
    </a>
  ),
  code: ({ className, children }) => {
    if (className) {
      return <code className={className}>{children}</code>;
    }
    return <code className="rounded bg-user px-1 py-0.5 text-[0.95em]">{children}</code>;
  },
  pre: ({ children }) => (
    <pre className="mb-4 overflow-x-auto rounded-xl bg-user p-3 font-sans text-sm leading-6 ring-1 ring-line last:mb-0">
      {children}
    </pre>
  ),
  h1: ({ children }) => <h2 className="mt-5 mb-2 text-xl font-medium">{children}</h2>,
  h2: ({ children }) => <h3 className="mt-5 mb-2 text-lg font-medium">{children}</h3>,
  h3: ({ children }) => <h4 className="mt-4 mb-2 text-base font-medium">{children}</h4>,
};

type MessageListProps = {
  messages: ThreadMessage[];
  status: string | null;
  onRetry: (question: string) => void;
};

function otherNotices(message: ThreadMessage): string[] {
  return [message.sqlError, message.retrievalError, message.chartError].filter((value): value is string => Boolean(value));
}

export function MessageList({ messages, status, onRetry }: MessageListProps) {
  return (
    <ol className="space-y-10">
      {messages.map((message, index) => {
        const last = index === messages.length - 1;
        if (message.role === "user") {
          return (
            <li key={message.key} className="flex justify-end">
              <p className="max-w-[65ch] rounded-2xl bg-user px-4 py-3 text-base leading-6 break-words whitespace-pre-wrap text-ink">
                {message.content}
              </p>
            </li>
          );
        }
        const previousQuestion = [...messages]
          .slice(0, index)
          .reverse()
          .find((item) => item.role === "user")?.content;
        const notices = otherNotices(message);
        const hasNotes = !message.pending && (Boolean(message.planError) || notices.length > 0 || Boolean(message.sqlWarnings));
        return (
          <li key={message.key} className="space-y-7">
            {message.pending && last && status ? (
              <div role="status" aria-live="polite">
                <p className="flex items-center gap-2 text-sm text-ink-mid">
                  <span className="status-dot size-1.5 rounded-full bg-teal" />
                  {status}
                </p>
                <p className="mt-1 text-sm text-ink-muted">Pencarian sumber dapat memerlukan beberapa saat. Anda dapat menghentikannya kapan saja.</p>
              </div>
            ) : (
              message.content && (
                <section aria-label="Jawaban">
                  <h2 className="mb-3 text-sm font-semibold text-ink-mid">Jawaban</h2>
                  <div className="reading max-w-[65ch] text-[1.0625rem] text-ink">
                    <Markdown components={markdown}>{message.content}</Markdown>
                  </div>
                </section>
              )
            )}
            {hasNotes ? (
              <section aria-label="Batasan dan catatan" className="space-y-3">
                <h3 className="text-sm font-semibold text-ink-mid">Batasan dan catatan</h3>
                {message.planError ? (
                  <Callout tone="warning" onRetry={previousQuestion ? () => onRetry(previousQuestion) : undefined}>
                    {message.planError}
                  </Callout>
                ) : null}
                {notices.map((text) => <Callout key={text} tone="warning">{text}</Callout>)}
                {message.sqlWarnings ? <Callout tone="insight">{message.sqlWarnings}</Callout> : null}
              </section>
            ) : null}
            {message.chart ? (
              <section aria-label="Visualisasi data">
                <ChartCard chart={message.chart} />
              </section>
            ) : null}
            {message.sources.length > 0 ? (
              <section aria-label="Sumber data">
                <h3 className="mb-3 text-sm font-semibold text-ink-mid">Sumber data</h3>
                <SourceChips sources={message.sources} />
              </section>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
