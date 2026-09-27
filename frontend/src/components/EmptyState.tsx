import { useState } from "react";
import { suggestions } from "../suggestions";
import { Logo } from "./Logo";

type EmptyStateProps = {
  onPick: (question: string) => void;
};

export function EmptyState({ onPick }: EmptyStateProps) {
  const [showMore, setShowMore] = useState(false);
  const visible = showMore ? suggestions : suggestions.slice(0, 2);
  const hiddenCount = suggestions.length - 2;

  return (
    <div className="mx-auto flex w-full max-w-xl flex-col items-center text-center">
      <Logo size={40} />
      <h1 className="reading mt-8 max-w-[22rem] text-[1.75rem] leading-tight font-medium text-balance">
        Ask about a BPS table or the SP2020 publication.
      </h1>
      <ul className="mt-5 w-full border-b border-line text-left">
        {visible.map((suggestion) => (
          <li key={suggestion.id} className="border-t border-line">
            <button
              type="button"
              onClick={() => onPick(suggestion.question)}
              className="w-full rounded-md px-1 py-3 text-left text-base leading-6 text-ink hover:bg-user/70"
            >
              {suggestion.question}
            </button>
          </li>
        ))}
      </ul>
      {hiddenCount > 0 ? (
        <button
          type="button"
          className="mt-3 min-h-11 rounded-md px-3 text-sm text-ink-mid hover:text-ink"
          aria-expanded={showMore}
          onClick={() => setShowMore((current) => !current)}
        >
          {showMore ? "Show fewer questions" : `${hiddenCount} more questions`}
        </button>
      ) : null}
    </div>
  );
}
