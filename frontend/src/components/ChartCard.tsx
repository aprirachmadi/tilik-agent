import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipContentProps } from "recharts";
import type { ChartSpec } from "../api/types";

const SERIES_COLORS = ["#0d5c63", "#8a4632", "#3d4f73", "#6e5a2e"];
const numberFormat = new Intl.NumberFormat("id-ID", { maximumFractionDigits: 2 });
const AXIS_TICK = {
  fill: "#5c554c",
  fontSize: 13,
  fontFamily: "Source Sans 3 Variable, sans-serif",
};

type ChartRow = {
  label: string;
  units: Record<string, string | null>;
} & Record<string, string | number | null | Record<string, string | null>>;

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

function formatTick(value: number): string {
  return numberFormat.format(value);
}

function ChartTooltip({ active, payload, label }: TooltipContentProps) {
  if (!active || !payload?.length) {
    return null;
  }
  const row = payload[0]?.payload as ChartRow | undefined;
  return (
    <div className="rounded-lg bg-elevated px-3 py-2 shadow-artifact ring-1 ring-line">
      <p className="text-sm text-ink-muted">{label}</p>
      <ul className="mt-1 space-y-0.5">
        {payload.map((item) => {
          const series = String(item.name ?? "");
          const unit = row?.units[series];
          const value = typeof item.value === "number" ? numberFormat.format(item.value) : item.value;
          return (
            <li key={series} className="text-sm text-ink tabular-nums">
              {series}: {value}
              {unit ? ` ${unit}` : ""}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function ChartCard({ chart }: ChartCardProps) {
  const { series, rows } = pivot(chart);
  const shared = {
    data: rows,
    margin: { top: 8, right: 8, left: 0, bottom: 0 },
  };
  const axes = (
    <>
      <CartesianGrid stroke="#ebe4d8" vertical={false} />
      <XAxis
        dataKey="label"
        tick={AXIS_TICK}
        tickLine={false}
        axisLine={{ stroke: "#e3dcd0" }}
        interval="preserveStartEnd"
      />
      <YAxis tickFormatter={formatTick} width={72} tick={AXIS_TICK} tickLine={false} axisLine={false} />
      <Tooltip content={ChartTooltip} cursor={{ fill: "#f4f0e8" }} />
    </>
  );

  return (
    <figure className="max-w-3xl rounded-xl bg-elevated p-5 shadow-artifact ring-1 ring-line">
      <figcaption>
        <h3 className="reading text-xl font-medium text-ink">{chart.title}</h3>
        <p className="mt-1 text-sm text-ink-mid">
          {chart.x_axis} · {chart.y_axis}
        </p>
      </figcaption>
      <ul className="mt-4 flex flex-wrap gap-x-5 gap-y-2" aria-label="Seri grafik">
        {series.map((name, index) => (
          <li key={name} className="flex items-center gap-2 text-sm text-ink">
            <span
              className="size-2.5 shrink-0 rounded-sm"
              style={{ backgroundColor: SERIES_COLORS[index % SERIES_COLORS.length] }}
              aria-hidden="true"
            />
            {name}
          </li>
        ))}
      </ul>
      <div className="mt-4 h-72">
        <ResponsiveContainer width="100%" height="100%">
          {chart.type === "line" ? (
            <LineChart {...shared}>
              {axes}
              {series.map((name, index) => {
                const color = SERIES_COLORS[index % SERIES_COLORS.length];
                return (
                  <Line
                    key={name}
                    type="monotone"
                    dataKey={name}
                    name={name}
                    stroke={color}
                    strokeWidth={2}
                    dot={{ r: 3, strokeWidth: 0, fill: color }}
                    connectNulls
                  />
                );
              })}
            </LineChart>
          ) : (
            <BarChart {...shared}>
              {axes}
              {series.map((name, index) => (
                <Bar
                  key={name}
                  dataKey={name}
                  name={name}
                  fill={SERIES_COLORS[index % SERIES_COLORS.length]}
                  radius={[3, 3, 0, 0]}
                  maxBarSize={36}
                />
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
    </figure>
  );
}
