import type { SourceChip } from "../api/types";

type SourceChipsProps = {
  sources: SourceChip[];
};

type Citation = {
  key: string;
  title: string;
  detail: string;
  href: string | null;
};

const SP2020_PUBLICATION_URL =
  "https://www.bps.go.id/id/publication/2025/01/31/29a40174e02f20a7a31b5bc3/statistik-demografi-indonesia--hasil-sensus-penduduk-2020-.html";

function describe(source: SourceChip, index: number): Citation | null {
  if (source.kind === "dataset") {
    const tableId = source.bps_table_id?.trim() ?? "";
    const title = source.title?.trim() || "Tabel BPS";
    if (!tableId && !source.title) {
      return null;
    }
    return {
      key: `dataset-${source.dataset_id ?? index}-${tableId}`,
      title,
      detail: tableId ? `Tabel ${tableId}` : "Tabel BPS",
      href: tableId ? `https://www.bps.go.id/id/statistics-table/2/${encodeURIComponent(tableId)}` : null,
    };
  }
  if (!source.document_id) {
    return null;
  }
  return {
    key: `document-${source.document_id}`,
    title: "Statistik Demografi Indonesia (SP2020)",
    detail: "Publikasi BPS",
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
    <ul className="flex max-w-[65ch] flex-col gap-2">
      {citations.map((citation) => {
        const body = (
          <>
            <span className="block text-sm font-medium text-teal">{citation.title}</span>
            <span className="mt-0.5 block text-sm text-ink-mid">
              {citation.detail}
              {citation.href ? " · Buka di BPS" : ""}
            </span>
          </>
        );
        return (
          <li key={citation.key}>
            {citation.href ? (
              <a
                href={citation.href}
                target="_blank"
                rel="noreferrer"
                className="block rounded-lg bg-teal-wash px-3 py-2 hover:bg-teal/10"
              >
                {body}
              </a>
            ) : (
              <div className="rounded-lg bg-teal-wash px-3 py-2">{body}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
