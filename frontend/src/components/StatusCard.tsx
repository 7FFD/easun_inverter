import { SystemStatus } from "../types";

interface Props {
  data: SystemStatus;
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

export default function StatusCard({ data }: Props) {
  return (
    <div className="card">
      <div className="card-header">
        <div className="card-icon card-icon-purple">📊</div>
        <span className="card-title">System Status</span>
      </div>

      <div className={modeClass(data.mode_name)}>{data.mode_name}</div>

      <div style={{ fontSize: "0.8125rem", color: "var(--text-muted)", marginBottom: "1rem" }}>
        {modeLabel(data.mode_name)}
      </div>

      {data.inverter_time && (
        <div className="metric">
          <div className="metric-label">Inverter clock</div>
          <div className="metric-value" style={{ fontSize: "0.875rem" }}>
            {new Date(data.inverter_time).toLocaleString()}
          </div>
        </div>
      )}
    </div>
  );
}
