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
      className={`rounded-xl border-l-2 px-4 py-3 ${warning ? "border-warn bg-warn-wash" : "border-line-strong bg-subtle"}`}
    >
      <p className={`text-sm font-semibold ${warning ? "text-warn" : "text-ink"}`}>
        {title ?? (warning ? "Perlu diperhatikan" : "Catatan")}
      </p>
      <p className="mt-1 text-sm leading-6 text-ink">{children}</p>
      {guidance ? <p className="mt-1.5 text-sm leading-5 text-ink-mid">{guidance}</p> : null}
    </div>
  );
}
