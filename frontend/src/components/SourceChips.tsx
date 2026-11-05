import { ArrowUpRight } from "lucide-react";
import type { SourceChip } from "../api/types";

type SourceChipsProps = {
  sources: SourceChip[];
};

type Citation = {
  key: string;
  provider: string;
  title: string;
  detail: string;
  href: string | null;
};

const SP2020_PUBLICATION_URL =
  "https://www.bps.go.id/id/publication/2025/01/31/29a40174e02f20a7a31b5bc3/statistik-demografi-indonesia--hasil-sensus-penduduk-2020-.html";

const timeFormat = new Intl.DateTimeFormat("id-ID", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Asia/Jakarta",
});

/** Format ISO timestamps for readers in WIB; leave plain periods such as "2024" as they are. */
function readableTime(value: string): string {
  if (!value.includes("T")) return value;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : `${timeFormat.format(date)} WIB`;
}

function describe(source: SourceChip, index: number): Citation | null {
  if (source.kind === "external") {
    return {
      key: `external-${source.provider}-${source.source_id ?? index}`,
      provider: source.provider,
      title: source.title,
      detail: [
        source.data_time ? `Data ${readableTime(source.data_time)}` : null,
        source.retrieved_at ? `Diambil ${readableTime(source.retrieved_at)}` : null,
        source.expires_at ? `Berlaku hingga ${readableTime(source.expires_at)}` : null,
      ]
        .filter(Boolean)
        .join(" · ") || "Sumber data resmi",
      href: source.url ?? null,
    };
  }
  if (source.kind === "dataset") {
    const tableId = source.bps_table_id?.trim() ?? "";
    const tablesource = source.tablesource?.trim() ?? "";
    if (!tableId && !source.title) {
      return null;
    }
    const href =
      tableId && tablesource
        ? "https://www.bps.go.id/id/statistics-table/" +
          `${encodeURIComponent(tablesource)}/${encodeURIComponent(tableId)}/tabel.html`
        : null;
    return {
      key: `dataset-${source.dataset_id ?? index}-${tableId}`,
      provider: "BPS",
      title: source.title?.trim() || "Tabel BPS",
      detail: tableId ? `Tabel ${tableId}` : "Tabel statistik",
      href,
    };
  }
  if (!source.document_id) {
    return null;
  }
  return {
    key: `document-${source.document_id}`,
    provider: "BPS",
    title: "Statistik Demografi Indonesia (SP2020)",
    detail: "Publikasi",
    href: SP2020_PUBLICATION_URL,
  };
}

export function SourceChips({ sources }: SourceChipsProps) {
  const citations = sources
    .map((source, index) => describe(source, index))
    .filter((citation): citation is Citation => citation !== null);
  if (citations.length === 0) {
    return null;
  }
  return (
    <ol className="border-t border-line">
      {citations.map((citation, index) => {
        const body = (
          <>
            <span className="w-5 shrink-0 pt-0.5 font-mono text-[0.6875rem] text-ink-muted">
              {String(index + 1).padStart(2, "0")}
            </span>
            <span className="mt-px shrink-0 rounded border border-line-strong px-1.5 font-mono text-[0.6875rem] leading-4 font-medium text-ink">
              {citation.provider}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm leading-5 font-medium text-ink">{citation.title}</span>
              <span className="mt-0.5 block font-mono text-[0.6875rem] leading-4 text-ink-muted">{citation.detail}</span>
            </span>
            {citation.href ? (
              <ArrowUpRight size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-ink-muted transition group-hover:text-ink" />
            ) : null}
          </>
        );
        const rowClass = "flex items-start gap-3 py-3";
        return (
          <li key={citation.key} className="border-b border-line">
            {citation.href ? (
              <a
                href={citation.href}
                target="_blank"
                rel="noreferrer"
                className={`group -mx-2 rounded-lg px-2 hover:bg-subtle ${rowClass}`}
                aria-label={`${citation.provider}: ${citation.title} (buka sumber di tab baru)`}
              >
                {body}
              </a>
            ) : (
              <div className={rowClass}>{body}</div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
