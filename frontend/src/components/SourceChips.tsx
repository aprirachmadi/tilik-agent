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

function describe(source: SourceChip, index: number): Citation | null {
  if (source.kind === "dataset") {
    const tableId = source.bps_table_id?.trim() ?? "";
    const title = source.title?.trim() || "BPS table";
    if (!tableId && !source.title) {
      return null;
    }
    return {
      key: `dataset-${source.dataset_id ?? index}-${tableId}`,
      title,
      detail: tableId ? `Table ${tableId}` : "BPS table",
      href: tableId ? `https://www.bps.go.id/id/statistics-table/2/${encodeURIComponent(tableId)}` : null,
    };
  }
  if (!source.document_id) {
    return null;
  }
  return {
    key: `document-${source.document_id}`,
    title: "SP2020 demography",
    detail: source.document_id,
    href: null,
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
              {citation.href ? " · Opens on BPS" : ""}
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
