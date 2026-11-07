import { ArrowUp, Square } from "lucide-react";
import { useLayoutEffect, type RefObject } from "react";
import type { SourceId } from "../suggestions";

export type SourceChoice = "auto" | SourceId;

const SOURCE_OPTIONS: { value: SourceChoice; label: string }[] = [
  { value: "auto", label: "Otomatis" },
  { value: "bps", label: "BPS" },
  { value: "bnpb", label: "BNPB" },
  { value: "bmkg", label: "BMKG" },
  { value: "bapanas", label: "Bapanas" },
  { value: "sipongi", label: "SiPongi" },
];

type ComposerProps = {
  value: string;
  source: SourceChoice;
  disabled: boolean;
  streaming: boolean;
  fieldRef: RefObject<HTMLTextAreaElement | null>;
  onChange: (value: string) => void;
  onSourceChange: (source: SourceChoice) => void;
  onSubmit: () => void;
  onStop: () => void;
};

export function Composer({
  value,
  source,
  disabled,
  streaming,
  fieldRef,
  onChange,
  onSourceChange,
  onSubmit,
  onStop,
}: ComposerProps) {
  useLayoutEffect(() => {
    const element = fieldRef.current;
    if (!element) {
      return;
    }
    element.style.height = "0px";
    element.style.height = `${Math.min(element.scrollHeight, 200)}px`;
  }, [fieldRef, value]);

  const canSend = !disabled && Boolean(value.trim());

  return (
    <form
      className="rounded-2xl border border-line-strong bg-canvas shadow-composer transition focus-within:border-ink"
      onSubmit={(event) => {
        event.preventDefault();
        if (canSend) {
          onSubmit();
        }
      }}
    >
      <label className="sr-only" htmlFor="question">
        Pertanyaan
      </label>
      <textarea
        id="question"
        ref={fieldRef}
        rows={1}
        value={value}
        disabled={disabled}
        placeholder="Tanyakan statistik, bencana, cuaca, atau gempa…"
        className="block max-h-50 min-h-14 w-full resize-none bg-transparent px-4 pt-4 pb-2 text-base leading-6 text-ink caret-accent outline-none placeholder:text-ink-muted disabled:opacity-60"
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            if (canSend) {
              onSubmit();
            }
          }
        }}
      />
      <div className="flex items-center gap-3 px-2 pb-2">
        <fieldset className="flex min-w-0 items-center gap-0.5 overflow-x-auto" disabled={streaming}>
          <legend className="sr-only">Sumber data</legend>
          {SOURCE_OPTIONS.map((option) => {
            const selected = option.value === source;
            return (
              <label
                key={option.value}
                className={`flex min-h-8 cursor-pointer items-center rounded-full px-3 text-[0.8125rem] whitespace-nowrap transition has-focus-visible:outline-2 has-focus-visible:outline-ink ${
                  selected ? "bg-ink font-medium text-canvas" : "text-ink-mid hover:bg-subtle hover:text-ink"
                }`}
              >
                <input
                  type="radio"
                  name="source"
                  value={option.value}
                  checked={selected}
                  onChange={() => onSourceChange(option.value)}
                  className="sr-only"
                />
                {option.label}
              </label>
            );
          })}
        </fieldset>
        <span className="ml-auto hidden font-mono text-[0.6875rem] text-ink-muted sm:inline">↵ kirim · ⇧↵ baris baru</span>
        {streaming ? (
          <button
            type="button"
            onClick={onStop}
            aria-label="Hentikan jawaban"
            className="ml-auto flex size-9 shrink-0 items-center justify-center rounded-full bg-ink text-canvas hover:bg-ink-mid sm:ml-0"
          >
            <Square size={12} fill="currentColor" />
          </button>
        ) : (
          <button
            type="submit"
            aria-label="Kirim pertanyaan"
            disabled={!canSend}
            className="ml-auto flex size-9 shrink-0 items-center justify-center rounded-full bg-ink text-canvas transition hover:bg-ink-mid disabled:cursor-not-allowed disabled:bg-hover disabled:text-ink-muted sm:ml-0"
          >
            <ArrowUp size={17} strokeWidth={2.25} />
          </button>
        )}
      </div>
    </form>
  );
}
