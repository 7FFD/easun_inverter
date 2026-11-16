import { SoftBmsSnapshot } from "../types";
import { modeLabel, socColor } from "./softBmsUtil";

interface Props {
  data: SoftBmsSnapshot;
  onManage: () => void;
}

export default function SoftBmsCard({ data, onManage }: Props) {
  const hasSoc = data.soc_pct != null;
  const color = hasSoc ? socColor(data.soc_pct as number) : "var(--text-dim)";
  return (
    <div className="card">
      <div className="card-header">
        <div className="card-icon card-icon-green">🧠</div>
        <span className="card-title">SoftBMS</span>
        <button className="btn btn-ghost" style={{ marginLeft: "auto", padding: "0.125rem 0.5rem" }} onClick={onManage}>
          Manage
        </button>
      </div>

      <div className="metric-primary">
        <div className="metric-primary-value" style={{ color }}>
          {hasSoc ? data.soc_pct : "—"}
          {hasSoc && <span className="metric-primary-unit">%</span>}
        </div>
        <div className="metric-primary-label">
          {hasSoc
            ? "Estimated state of charge"
            : data.capacity_wh
              ? "Waiting for first full charge"
              : "Set battery capacity to get SOC %"}
        </div>
      </div>

      {hasSoc && (
        <div className="progress-wrap">
          <div className="progress-bar-bg">
            <div className="progress-bar-fill" style={{ width: `${data.soc_pct}%`, background: color }} />
          </div>
        </div>
      )}

      <div className="metrics-row">
        <div className="metric">
          <div className="metric-label">Used since full</div>
          <div className="metric-value">{Math.round(data.used_since_full_wh)} Wh</div>
        </div>
        <div className="metric">
          <div className="metric-label">Energy out</div>
          <div className="metric-value">{data.discharged_kwh.toFixed(2)} kWh</div>
        </div>
        <div className="metric">
          <div className="metric-label">Energy in</div>
          <div className="metric-value">{data.charged_kwh.toFixed(2)} kWh</div>
        </div>
        <div className="metric">
          <div className="metric-label">Power bias</div>
          <div className="metric-value">{Math.round(data.bias_w)} W</div>
        </div>
      </div>
      <div style={{ fontSize: "0.75rem", color: "var(--text-dim)", marginTop: "0.5rem" }}>
        {modeLabel(data.mode)} · {data.calibrated_cycles} calibrated cycle{data.calibrated_cycles === 1 ? "" : "s"}
      </div>
    </div>
  );
}
