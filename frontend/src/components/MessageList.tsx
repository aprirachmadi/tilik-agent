import { User } from "lucide-react";
import { useEffect, useState } from "react";
import type { Components } from "react-markdown";
import Markdown from "react-markdown";
import type { ChartSpec, SourceChip, ThreadMessage } from "../api/types";
import { Callout } from "./Callout";
import { ChartCard } from "./ChartCard";
import { Logo } from "./Logo";
import { SourceChips } from "./SourceChips";

const markdown: Components = {
  p: ({ children }) => <p className="mb-4 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="mb-4 list-disc space-y-1.5 pl-5 marker:text-ink-muted last:mb-0">{children}</ul>,
  ol: ({ children }) => <ol className="mb-4 list-decimal space-y-1.5 pl-5 marker:font-mono marker:text-ink-muted last:mb-0">{children}</ol>,
  li: ({ children }) => <li className="pl-1">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-ink">{children}</strong>,
  a: ({ href, children }) => (
    <a href={href} className="font-medium text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink" target="_blank" rel="noreferrer">
      {children}
    </a>
  ),
  code: ({ className, children }) => {
    if (className) {
      return <code className={className}>{children}</code>;
    }
    return <code className="rounded bg-subtle px-1 py-0.5 font-mono text-[0.875em]">{children}</code>;
  },
  pre: ({ children }) => (
    <pre className="mb-4 overflow-x-auto rounded-xl border border-line bg-subtle p-3 font-mono text-[0.8125rem] leading-6 last:mb-0">
      {children}
    </pre>
  ),
  h1: ({ children }) => <h2 className="mt-6 mb-2 text-lg font-semibold tracking-[-0.01em]">{children}</h2>,
  h2: ({ children }) => <h3 className="mt-6 mb-2 text-base font-semibold">{children}</h3>,
  h3: ({ children }) => <h4 className="mt-5 mb-2 text-base font-semibold">{children}</h4>,
};

const ANSWER_TEXT = "max-w-[68ch] text-[1.0625rem] leading-[1.7] text-ink-mid";

function providers(sources: SourceChip[]): string[] {
  return [...new Set(sources.map((source) => (source.kind === "external" ? source.provider : "BPS")))];
}

function PendingStatus({ status }: { status: string }) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const timer = window.setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <div role="status" aria-live="polite" className="max-w-md">
      <div className="flex items-center justify-between gap-4">
        <p className="text-sm font-medium text-ink">{status}</p>
        <span className="font-mono text-[0.6875rem] text-ink-muted tabular-nums" aria-hidden="true">{seconds}s</span>
      </div>
      <div className="mt-2 h-px overflow-hidden bg-line">
        <div className="scan-bar h-px w-1/3 bg-ink" />
      </div>
      <p className="mt-2 text-[0.8125rem] leading-5 text-ink-muted">Jawaban disusun dari sumber resmi dan diperiksa angkanya. Anda dapat menghentikannya kapan saja.</p>
    </div>
  );
}

function answerWithoutDuplicateChart(content: string, chart: ChartSpec | null): string {
  if (!chart) return content;

  return content.replace(
    /(^|\n\n)(?:\*\*Grafik: [^\n]+\*\*\n\n)?```[^\n]*\n([\s\S]*?)\n```/g,
    (block, space: string, data: string) => {
      if (!data.includes("█")) return block;
      const lines = data.split("\n").map((line) => line.trimStart());
      const matchingLabels = new Set(chart.data.map((point) => point.label).filter((label) =>
        lines.some((line) => line.startsWith(`${label} `)),
      ));
      return matchingLabels.size >= 2 ? space : block;
    },
  ).replace(/\n{3,}/g, "\n\n").trim();
}

type MessageListProps = {
  messages: ThreadMessage[];
  status: string | null;
  busy: boolean;
  onRetry: (question: string) => void;
};

export function MessageList({ messages, status, busy, onRetry }: MessageListProps) {
  return (
    <ol>
      {messages.map((message, index) => {
        const last = index === messages.length - 1;
        if (message.role === "user") {
          return (
            <li key={message.key} className={`rise ${index > 0 ? "mt-14 border-t border-line pt-10" : ""}`}>
              <p className="eyebrow flex items-center gap-2">
                <span className="flex size-4 items-center justify-center rounded-full border border-line-strong text-ink-mid" aria-hidden="true">
                  <User size={10} strokeWidth={2.5} />
                </span>
                Anda
              </p>
              <h2 className="mt-2.5 max-w-[40ch] text-[1.375rem] leading-[1.3] font-semibold tracking-[-0.02em] break-words whitespace-pre-wrap text-ink sm:text-[1.625rem]">
                {message.content}
              </h2>
            </li>
          );
        }
        const previousQuestion = [...messages]
          .slice(0, index)
          .reverse()
          .find((item) => item.role === "user")?.content;
        const hasFailure = Boolean(message.planError || message.sqlError || message.retrievalError || message.chartError);
        const hasNotes = !message.pending && hasFailure;
        const orderedBlocks = message.chart && message.blocks?.some((block) => block.type === "chart")
          ? message.blocks
          : null;
        const missingSources = !message.pending && message.sourceRoute !== "direct" &&
          (Boolean(message.content.trim()) || Boolean(message.chart)) && message.sources.length === 0;
        const answerProviders = providers(message.sources);
        return (
          <li key={message.key} className="rise mt-6 space-y-8">
            {message.pending && last && status ? (
              <PendingStatus status={status} />
            ) : (
              (message.content || orderedBlocks) && (
                <section aria-label="Jawaban">
                  <div className="mb-3 flex flex-wrap items-center gap-x-2 gap-y-1">
                    <h3 className="eyebrow flex items-center gap-2 text-ink">
                      <Logo size={16} />
                      Tilik
                    </h3>
                    {answerProviders.map((provider) => (
                      <span key={provider} className="eyebrow text-ink">· {provider}</span>
                    ))}
                    {(hasFailure || missingSources) ? (
                      <span className="ml-1 rounded-full bg-warn-wash px-2 font-mono text-[0.6875rem] leading-5 text-warn">Perlu verifikasi</span>
                    ) : null}
                  </div>
                  <div className="space-y-8">
                    {orderedBlocks ? orderedBlocks.map((block, blockIndex) => (
                      block.type === "text" ? (
                        <div key={blockIndex} className={ANSWER_TEXT}>
                          <Markdown components={markdown}>{block.text}</Markdown>
                        </div>
                      ) : (
                        <section key={blockIndex} aria-label="Visualisasi data">
                          <ChartCard chart={message.chart!} />
                        </section>
                      )
                    )) : (
                      <div className={ANSWER_TEXT}>
                        <Markdown components={markdown}>{answerWithoutDuplicateChart(message.content, message.chart)}</Markdown>
                      </div>
                    )}
                  </div>
                </section>
              )
            )}
            {hasNotes ? (
              <section aria-label="Batasan dan catatan" className="max-w-[68ch] space-y-3">
                {message.planError ? (
                  <Callout tone="warning" title="Jawaban belum selesai" guidance="Ulangi pertanyaan. Jika masih gagal, sebutkan indikator, wilayah, dan periode dengan lebih jelas.">
                    {message.planError}
                  </Callout>
                ) : null}
                {message.sqlError ? (
                  <Callout tone="warning" title="Data tabel belum lengkap" guidance="Coba lagi, atau persempit pertanyaan ke satu indikator, wilayah, dan periode.">
                    {message.sqlError}
                  </Callout>
                ) : null}
                {message.retrievalError ? (
                  <Callout tone="warning" title="Kutipan publikasi belum lengkap" guidance="Coba lagi atau tanyakan topik publikasi dengan lebih spesifik.">
                    {message.retrievalError}
                  </Callout>
                ) : null}
                {message.chartError ? (
                  <Callout tone="warning" title="Grafik belum tersedia" guidance="Gunakan angka dalam jawaban jika tersedia, atau ulangi pertanyaan untuk mencoba membuat grafik lagi.">
                    {message.chartError}
                  </Callout>
                ) : null}
                {hasFailure && previousQuestion ? (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => onRetry(previousQuestion)}
                    className="min-h-10 rounded-full bg-ink px-4 text-sm font-medium text-canvas hover:bg-ink-mid disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    Ulangi pertanyaan
                  </button>
                ) : null}
              </section>
            ) : null}
            {message.chart && !orderedBlocks ? (
              <section aria-label="Visualisasi data">
                <ChartCard chart={message.chart} />
              </section>
            ) : null}
            {message.sources.length > 0 || missingSources ? (
              <section aria-label="Sumber data">
                <h3 className="eyebrow mb-2">Sumber</h3>
                {missingSources ? (
                  <Callout
                    tone="warning"
                    title="Sumber belum tersedia untuk jawaban ini"
                    guidance="Anda dapat bertanya ulang dengan indikator, wilayah, dan periode yang lebih spesifik."
                  >
                    Periksa angka sebelum digunakan.
                  </Callout>
                ) : <SourceChips sources={message.sources} />}
              </section>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
