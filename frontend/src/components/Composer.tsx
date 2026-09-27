import { ArrowUp } from "lucide-react";
import { useLayoutEffect, type RefObject } from "react";

type ComposerProps = {
  value: string;
  disabled: boolean;
  streaming: boolean;
  fieldRef: RefObject<HTMLTextAreaElement | null>;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onStop: () => void;
};

export function Composer({ value, disabled, streaming, fieldRef, onChange, onSubmit, onStop }: ComposerProps) {
  useLayoutEffect(() => {
    const element = fieldRef.current;
    if (!element) {
      return;
    }
    element.style.height = "0px";
    element.style.height = `${Math.min(element.scrollHeight, 160)}px`;
  }, [fieldRef, value]);

  return (
    <form
      className="rounded-2xl bg-elevated shadow-float ring-1 ring-line transition focus-within:ring-2 focus-within:ring-teal"
      onSubmit={(event) => {
        event.preventDefault();
        if (!disabled && value.trim()) {
          onSubmit();
        }
      }}
    >
      <label className="sr-only" htmlFor="question">
        Question
      </label>
      <div className="flex items-end gap-2 px-3 py-2">
        <textarea
          id="question"
          ref={fieldRef}
          rows={1}
          value={value}
          disabled={disabled}
          placeholder="Ask a question about a BPS figure"
          aria-describedby="question-hint"
          className="max-h-40 min-h-11 flex-1 resize-none bg-transparent px-1 py-2.5 text-base leading-6 text-ink caret-teal outline-none placeholder:text-ink-muted disabled:opacity-60"
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              if (!disabled && value.trim()) {
                onSubmit();
              }
            }
          }}
        />
        {streaming ? (
          <button
            type="button"
            onClick={onStop}
            className="mb-1 min-h-11 shrink-0 rounded-full bg-ink px-4 text-sm font-medium text-canvas hover:bg-ink-mid"
          >
            Stop
          </button>
        ) : (
          <button
            type="submit"
            aria-label="Send question"
            disabled={disabled || !value.trim()}
            className="mb-1 flex size-11 shrink-0 items-center justify-center rounded-full bg-teal text-white transition hover:bg-teal-press disabled:cursor-not-allowed disabled:bg-line disabled:text-ink-muted"
          >
            <ArrowUp size={18} />
          </button>
        )}
      </div>
      <p id="question-hint" className="px-4 pb-3 text-sm leading-5 text-ink-muted">
        Enter sends. Shift+Enter starts a new line.
      </p>
    </form>
  );
}
