import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipContentProps } from "recharts";
import type { ChartSpec } from "../api/types";

// One series reads as ink; several use the validated categorical order (blue, orange, aqua, yellow).
const SINGLE_COLOR = "#18181b";
const PEAK_COLOR = "#ff4d00";
const SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"];
const numberFormat = new Intl.NumberFormat("id-ID", { maximumFractionDigits: 2 });
const percentFormat = new Intl.NumberFormat("id-ID", { maximumFractionDigits: 1, signDisplay: "always" });
const AXIS_TICK = {
  fill: "#71717a",
  fontSize: 11,
  fontFamily: "Geist Mono Variable, ui-monospace, monospace",
};

type ChartRow = {
  label: string;
  units: Record<string, string | null>;
} & Record<string, string | number | null | Record<string, string | null>>;

type Stat = { label: string; value: string; unit: string | null; accent?: boolean };

type ChartCardProps = {
  chart: ChartSpec;
};

function pivot(chart: ChartSpec): { series: string[]; rows: ChartRow[] } {
  const series: string[] = [];
  const rows = new Map<string, ChartRow>();
  for (const point of chart.data) {
    if (!series.includes(point.series)) {
      series.push(point.series);
    }
    const row: ChartRow = rows.get(point.label) ?? { label: point.label, units: {} };
    row[point.series] = point.value;
    row.units[point.series] = point.unit;
    rows.set(point.label, row);
  }
  return { series, rows: [...rows.values()] };
}

/** Headline figures for a single series, read straight from the plotted values. */
function summarize(chart: ChartSpec): Stat[] {
  const points = chart.data;
  if (points.length < 2 || new Set(points.map((point) => point.series)).size !== 1) {
    return [];
  }
  const first = points[0];
  const last = points[points.length - 1];
  const peak = points.reduce((best, point) => (point.value > best.value ? point : best));
  const stats: Stat[] = [
    { label: first.label, value: numberFormat.format(first.value), unit: first.unit },
    { label: last.label, value: numberFormat.format(last.value), unit: last.unit },
  ];
  if (first.value > 0) {
    stats.push({
      label: "Perubahan",
      value: percentFormat.format(((last.value - first.value) / first.value) * 100),
      unit: "%",
    });
  }
  if (peak !== first && peak !== last) {
    stats.push({ label: `Tertinggi · ${peak.label}`, value: numberFormat.format(peak.value), unit: peak.unit, accent: true });
  }
  return stats;
}

function ChartTooltip({ active, payload, label }: TooltipContentProps) {
  if (!active || !payload?.length) {
    return null;
  }
  const row = payload[0]?.payload as ChartRow | undefined;
  return (
    <div className="rounded-lg border border-line bg-canvas px-3 py-2 shadow-pop">
      <p className="font-mono text-[0.6875rem] text-ink-muted">{label}</p>
      <ul className="mt-1 space-y-0.5">
        {payload.map((item) => {
          const series = String(item.name ?? "");
          const unit = row?.units[series];
          const value = typeof item.value === "number" ? numberFormat.format(item.value) : item.value;
          return (
            <li key={series} className="text-sm font-medium text-ink tabular-nums">
              {payload.length > 1 ? <span className="font-normal text-ink-mid">{series} </span> : null}
              {value}
              {unit ? <span className="font-normal text-ink-muted"> {unit}</span> : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function ChartCard({ chart }: ChartCardProps) {
  const { series, rows } = pivot(chart);
  const single = series.length === 1;
  const stats = summarize(chart);
  const peakLabel = stats.some((stat) => stat.accent)
    ? chart.data.reduce((best, point) => (point.value > best.value ? point : best)).label
    : null;
  const colorFor = (index: number) => (single ? SINGLE_COLOR : SERIES_COLORS[index % SERIES_COLORS.length]);
  const shared = {
    data: rows,
    margin: { top: 8, right: 4, left: 0, bottom: 0 },
  };
  const axes = (
    <>
      <CartesianGrid stroke="#f0f0f2" vertical={false} />
      <XAxis dataKey="label" tick={AXIS_TICK} tickLine={false} axisLine={{ stroke: "#d4d4d8" }} interval="preserveStartEnd" tickMargin={8} />
      <YAxis tickFormatter={(value: number) => numberFormat.format(value)} width={56} tick={AXIS_TICK} tickLine={false} axisLine={false} />
      <Tooltip content={ChartTooltip} cursor={chart.type === "line" ? { stroke: "#d4d4d8" } : { fill: "#f6f6f7" }} />
    </>
  );

  return (
    <figure className="rounded-2xl border border-line bg-canvas">
      <figcaption className="px-5 pt-5">
        <p className="eyebrow">{chart.x_axis} · {chart.y_axis}</p>
        <h3 className="mt-1.5 text-base leading-6 font-semibold tracking-[-0.01em] text-ink">{chart.title}</h3>
      </figcaption>

      {stats.length > 0 ? (
        <dl className="mx-5 mt-4 grid grid-cols-2 border-y border-line sm:grid-cols-4">
          {stats.map((stat, index) => (
            <div
              key={stat.label}
              className={`py-3 pr-3 ${index % 2 === 1 ? "border-l border-line pl-3" : ""} ${
                index > 1 ? "border-t border-line sm:border-t-0" : ""
              } ${index === 2 ? "sm:border-l sm:pl-3" : ""}`}
            >
              <dt className="eyebrow flex items-center gap-1.5">
                {stat.accent ? <span className="size-1.5 rounded-full bg-accent" aria-hidden="true" /> : null}
                {stat.label}
              </dt>
              <dd className="mt-1 text-[1.625rem] leading-8 font-semibold tracking-[-0.03em] text-ink tabular-nums">
                {stat.value}
                {stat.unit ? <span className="ml-1 text-sm font-normal tracking-normal text-ink-muted">{stat.unit}</span> : null}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}

      {single ? null : (
        <ul className="mx-5 mt-4 flex flex-wrap gap-x-5 gap-y-2" aria-label="Seri grafik">
          {series.map((name, index) => (
            <li key={name} className="flex items-center gap-2 text-sm text-ink">
              <span className="size-2.5 shrink-0 rounded-sm" style={{ backgroundColor: colorFor(index) }} aria-hidden="true" />
              {name}
            </li>
          ))}
        </ul>
      )}

      <div className="mt-5 h-64 px-3" aria-hidden="true">
        <ResponsiveContainer width="100%" height="100%">
          {chart.type === "line" ? (
            <LineChart {...shared}>
              {axes}
              {series.map((name, index) => (
                <Line
                  key={name}
                  type="monotone"
                  dataKey={name}
                  name={name}
                  stroke={colorFor(index)}
                  strokeWidth={2}
                  dot={{ r: 4, strokeWidth: 2, stroke: "#ffffff", fill: colorFor(index) }}
                  activeDot={{ r: 5, strokeWidth: 2, stroke: "#ffffff" }}
                  connectNulls
                />
              ))}
            </LineChart>
          ) : (
            <BarChart {...shared} barCategoryGap="22%">
              {axes}
              {series.map((name, index) => (
                <Bar key={name} dataKey={name} name={name} fill={colorFor(index)} radius={[4, 4, 0, 0]} maxBarSize={40}>
                  {single
                    ? rows.map((row) => (
                        <Cell key={row.label} fill={row.label === peakLabel ? PEAK_COLOR : SINGLE_COLOR} />
                      ))
                    : null}
                </Bar>
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>

      <details className="group mt-3 border-t border-line">
        <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 px-5 text-[0.8125rem] font-medium text-ink-mid hover:text-ink [&::-webkit-details-marker]:hidden">
          <span className="font-mono transition group-open:rotate-90" aria-hidden="true">›</span>
          Lihat data grafik
        </summary>
        <div className="overflow-x-auto px-5 pb-4">
          <table className="w-full min-w-max border-collapse text-left text-sm tabular-nums">
            <caption className="sr-only">Data grafik: {chart.title}</caption>
            <thead>
              <tr className="border-b border-line">
                <th scope="col" className="eyebrow py-2 pr-4 font-normal">{chart.x_axis}</th>
                {series.map((name) => (
                  <th key={name} scope="col" className="eyebrow py-2 pr-4 text-right font-normal">{name}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.label} className="border-b border-line last:border-0">
                  <th scope="row" className="py-2 pr-4 font-mono text-[0.8125rem] font-normal text-ink-mid">{row.label}</th>
                  {series.map((name) => (
                    <td key={name} className="py-2 pr-4 text-right text-ink">
                      {typeof row[name] === "number" ? numberFormat.format(row[name]) : "—"}
                      {typeof row[name] === "number" && row.units[name] ? (
                        <span className="text-ink-muted"> {row.units[name]}</span>
                      ) : null}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
