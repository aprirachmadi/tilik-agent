import { ArrowUpRight } from "lucide-react";
import type { ReactNode } from "react";
import { sourceCatalog, type SourceId } from "../suggestions";

type EmptyStateProps = {
  composer: ReactNode;
  onPick: (question: string, source: SourceId) => void;
  onOpenExample: (() => void) | null;
};

export function EmptyState({ composer, onPick, onOpenExample }: EmptyStateProps) {
  return (
    <div className="rise mx-auto w-full max-w-4xl">
      <p className="eyebrow">Analis data Indonesia</p>
      <h1 className="mt-4 text-[2.5rem] leading-[1.05] font-semibold tracking-[-0.035em] text-balance sm:text-[3.25rem]">
        Tanya data Indonesia.
        <span className="block text-ink-muted">Jawaban lengkap dengan sumbernya.</span>
      </h1>
      <div className="mt-10">{composer}</div>
      {onOpenExample ? (
        <button
          type="button"
          onClick={onOpenExample}
          className="group mt-4 inline-flex items-center gap-1.5 text-sm font-medium text-ink-mid hover:text-ink"
        >
          Belum mau mencoba? Lihat contoh percakapan
          <ArrowUpRight size={15} aria-hidden="true" className="transition group-hover:-translate-y-px group-hover:translate-x-px" />
        </button>
      ) : null}
      <section aria-label="Sumber data dan contoh pertanyaan" className="mt-14 grid border-t border-line sm:grid-cols-3">
        {sourceCatalog.map((source, index) => (
          <div
            key={source.id}
            className={`flex flex-col py-6 sm:px-6 ${index > 0 ? "border-t border-line" : ""} ${index < 3 ? "sm:border-t-0" : ""} ${index % 3 ? "sm:border-l sm:border-line" : "sm:pl-0"}`}
          >
            <p className="eyebrow text-ink">{source.id}</p>
            <p className="mt-2 text-sm font-medium text-ink">{source.name}</p>
            <p className="mt-1 text-sm leading-5 text-ink-mid">{source.coverage}</p>
            <ul className="mt-4 space-y-1">
              {source.questions.map((question) => (
                <li key={question}>
                  <button
                    type="button"
                    onClick={() => onPick(question, source.id)}
                    className="group -mx-2 flex w-[calc(100%+1rem)] items-start gap-2 rounded-lg px-2 py-2 text-left text-sm leading-5 text-ink hover:bg-subtle"
                  >
                    <span className="flex-1">{question}</span>
                    <ArrowUpRight
                      size={16}
                      aria-hidden="true"
                      className="mt-0.5 shrink-0 text-ink-muted transition group-hover:text-ink"
                    />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </section>
    </div>
  );
}
