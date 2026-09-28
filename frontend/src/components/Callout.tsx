type CalloutProps = {
  tone: "insight" | "warning";
  children: string;
  onRetry?: () => void;
};

export function Callout({ tone, children, onRetry }: CalloutProps) {
  const warning = tone === "warning";
  return (
    <div
      role={warning ? "alert" : "note"}
      className={`rounded-xl px-4 py-3 ${warning ? "bg-clay-wash text-ink" : "bg-elevated text-ink ring-1 ring-line"}`}
    >
      <p className={`text-sm font-medium ${warning ? "text-clay" : "text-ink-mid"}`}>
        {warning ? "Perlu diperhatikan" : "Catatan"}
      </p>
      <p className="mt-1 text-base leading-6">{children}</p>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 min-h-11 rounded-full bg-ink px-4 text-sm font-medium text-canvas hover:bg-ink-mid"
        >
          Coba lagi
        </button>
      ) : null}
    </div>
  );
}
