import { GridData, SystemStatus } from "../types";

function fmt(v: number | null | undefined, decimals = 1): string {
  if (v == null) return "—";
  return v.toFixed(decimals);
}

function modeClass(name: string): string {
  if (name === "SUB") return "status-mode-badge mode-sub";
  if (name === "SBU") return "status-mode-badge mode-sbu";
  return "status-mode-badge mode-unknown";
}

function modeLabel(name: string): string {
  if (name === "SUB") return "Solar → Utility → Battery";
  if (name === "SBU") return "Solar → Battery → Utility";
  return name;
}

interface Props {
  data: GridData;
  status?: SystemStatus | null;
}

export default function GridCard({ data, status }: Props) {
  return (
    <div className="card">
      <div className="card-header">
        <div className="card-icon card-icon-blue">🔌</div>
        <span className="card-title">Grid</span>
      </div>

      {status && (
        <>
          <div className={modeClass(status.mode_name)}>{status.mode_name}</div>
          <div style={{ fontSize: "0.8125rem", color: "var(--text-muted)", marginBottom: "1rem" }}>
            {modeLabel(status.mode_name)}
          </div>
        </>
      )}

      <div className="metric-primary">
        <div className="metric-primary-value" style={{ color: "var(--blue)" }}>
          {fmt(data.voltage)}
          <span className="metric-primary-unit">V</span>
        </div>
        <div className="metric-primary-label">Grid voltage</div>
      </div>

      <div className="metrics-row">
        <div className="metric">
          <div className="metric-label">Power</div>
          <div className="metric-value">{data.power} W</div>
        </div>
        <div className="metric">
          <div className="metric-label">Frequency</div>
          <div className="metric-value">{fmt(data.frequency / 100, 2)} Hz</div>
        </div>
      </div>
    </div>
  );
}
