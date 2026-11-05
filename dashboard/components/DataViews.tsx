import type { Amount, EquitySample } from "../lib/types";
export const money = (v: Amount | undefined, digits = 2) =>
  v == null
    ? "—"
    : Number(v).toLocaleString("en-US", {
        style: "currency",
        currency: "USD",
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
export const time = (v: string | null | undefined) =>
  v
    ? new Date(v).toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "America/New_York",
      }) + " ET"
    : "Not available";
export const label = (v: string) =>
  v.replaceAll("_", " ").replaceAll(".", " / ");
export function Value({ value }: { value: unknown }) {
  if (value === null || value === undefined)
    return <span className="muted">Not recorded</span>;
  if (Array.isArray(value))
    return value.length ? (
      <ul>
        {value.map((v, i) => (
          <li key={i}>
            <Value value={v} />
          </li>
        ))}
      </ul>
    ) : (
      <span className="muted">None recorded</span>
    );
  if (typeof value === "object")
    return (
      <dl className="details-grid">
        {Object.entries(value).map(([k, v]) => (
          <div key={k}>
            <dt>{label(k)}</dt>
            <dd>
              <Value value={v} />
            </dd>
          </div>
        ))}
      </dl>
    );
  return <span>{String(value)}</span>;
}
export function Curve({ samples }: { samples: EquitySample[] }) {
  if (samples.length < 2)
    return (
      <div className="chart-empty">
        <span className="empty-icon">↗</span>
        <strong>Performance history is building</strong>
        <p>
          At least two recorded daily closes are needed.
          <br />
          No synthetic returns or backfilled performance.
        </p>
      </div>
    );
  const values = samples.map((x) => Number(x.equity));
  const min = Math.min(...values),
    max = Math.max(...values);
  const pad = Math.max((max - min) * 0.2, 1);
  const points = values
    .map(
      (v, i) =>
        `${35 + (i / (values.length - 1)) * 730},${190 - ((v - min + pad) / (max - min + 2 * pad)) * 155}`,
    )
    .join(" ");
  return (
    <div className="chart">
      <div className="chart-labels">
        <span>{money(max, 0)}</span>
        <span>{money(min, 0)}</span>
      </div>
      <svg
        viewBox="0 0 800 225"
        role="img"
        aria-label="Recorded daily portfolio equity"
      >
        <defs>
          <linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">
            <stop stopColor="#259888" stopOpacity=".18" />
            <stop offset="1" stopColor="#259888" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[45, 100, 155, 210].map((y) => (
          <line
            key={y}
            x1="35"
            y1={y}
            x2="765"
            y2={y}
            stroke="#e3e7e5"
            strokeDasharray="3 5"
          />
        ))}
        <polygon points={`35,210 ${points} 765,210`} fill="url(#fill)" />
        <polyline
          points={points}
          fill="none"
          stroke="#188475"
          strokeWidth="3"
        />
      </svg>
      <div className="chart-dates">
        <span>{samples[0].date}</span>
        <span>{samples[samples.length - 1].date}</span>
      </div>
    </div>
  );
}
