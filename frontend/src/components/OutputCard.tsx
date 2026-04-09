import { OutputData } from "../types";

function fmt(v: number | null | undefined, decimals = 1): string {
  if (v == null) return "—";
  return v.toFixed(decimals);
}

function loadColor(pct: number): string {
  if (pct < 60) return "var(--green)";
  if (pct < 85) return "var(--yellow)";
  return "var(--red)";
}

interface Props {
  data: OutputData;
}

export default function OutputCard({ data }: Props) {
  const color = loadColor(data.load_percentage);
  return (
    <div className="card">
      <div className="card-header">
        <div className="card-icon card-icon-orange">⚡</div>
        <span className="card-title">Output</span>
      </div>

      <div className="metric-primary">
        <div className="metric-primary-value" style={{ color: "var(--orange)" }}>
          {data.power}
          <span className="metric-primary-unit">W</span>
        </div>
        <div className="metric-primary-label">Active power</div>
      </div>

      <div className="progress-wrap">
        <div className="progress-label-row">
          <span>Load</span>
          <span style={{ color }}>{data.load_percentage}%</span>
        </div>
        <div className="progress-bar-bg">
          <div
            className="progress-bar-fill"
            style={{ width: `${data.load_percentage}%`, background: color }}
          />
        </div>
      </div>

      <div className="metrics-row">
        <div className="metric">
          <div className="metric-label">Voltage</div>
          <div className="metric-value">{fmt(data.voltage)} V</div>
        </div>
        <div className="metric">
          <div className="metric-label">Current</div>
          <div className="metric-value">{fmt(data.current)} A</div>
        </div>
        <div className="metric">
          <div className="metric-label">Apparent</div>
          <div className="metric-value">{data.apparent_power} VA</div>
        </div>
        <div className="metric">
          <div className="metric-label">Frequency</div>
          <div className="metric-value">{fmt(data.frequency / 100, 2)} Hz</div>
        </div>
      </div>
    </div>
  );
}
