type CalloutProps = {
  tone: "insight" | "warning";
  children: string;
  title?: string;
  guidance?: string;
};

export function Callout({ tone, children, title, guidance }: CalloutProps) {
  const warning = tone === "warning";
  return (
    <div
      role={warning ? "alert" : "note"}
      className={`rounded-xl px-4 py-3 ${warning ? "bg-clay-wash text-ink" : "bg-elevated text-ink ring-1 ring-line"}`}
    >
      <p className={`text-sm font-medium ${warning ? "text-clay" : "text-ink-mid"}`}>
        {title ?? (warning ? "Perlu diperhatikan" : "Catatan")}
      </p>
      <p className="mt-1 text-base leading-6">{children}</p>
      {guidance ? <p className="mt-2 text-sm leading-5 text-ink-mid">{guidance}</p> : null}
    </div>
  );
}
