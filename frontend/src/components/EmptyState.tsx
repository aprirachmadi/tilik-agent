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
        Tanyakan data dan temukan sumbernya.
      </h1>
      <p className="mt-3 max-w-[34rem] text-base leading-6 text-ink-mid">
        Tilik membaca tabel BPS, riwayat bencana BNPB, serta prakiraan, gempa, dan
        peringatan dini BMKG. Tahun, wilayah, dan waktu pembaruan mengikuti tiap sumber.
      </p>
      <p className="mt-6 w-full text-left text-sm font-medium text-ink-mid">Contoh pertanyaan</p>
      <ul className="mt-2 w-full border-b border-line text-left">
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
          {showMore ? "Tampilkan lebih sedikit" : `Lihat ${hiddenCount} contoh lainnya`}
        </button>
      ) : null}
    </div>
  );
}
