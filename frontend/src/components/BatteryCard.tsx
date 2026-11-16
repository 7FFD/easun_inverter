import { BatteryData, SoftBmsSnapshot } from "../types";

function socColor(soc: number): string {
  if (soc >= 60) return "var(--green)";
  if (soc >= 25) return "var(--yellow)";
  return "var(--red)";
}

function fmt(v: number | null | undefined, decimals = 1): string {
  if (v == null) return "—";
  return v.toFixed(decimals);
}

interface Props {
  data: BatteryData;
  softBms?: SoftBmsSnapshot;
}

export default function BatteryCard({ data, softBms }: Props) {
  // Prefer SoftBMS (energy-integrated) over the inverter's voltage-derived SOC when available.
  const useSoft = softBms?.soc_pct != null;
  const soc = useSoft ? (softBms!.soc_pct as number) : data.soc;
  const color = socColor(soc);
  return (
    <div className="card">
      <div className="card-header">
        <div className="card-icon card-icon-green">🔋</div>
        <span className="card-title">Battery</span>
      </div>

      <div className="metric-primary">
        <div className="metric-primary-value" style={{ color }}>
          {soc}
          <span className="metric-primary-unit">%</span>
        </div>
        <div className="metric-primary-label">
          {useSoft ? `State of charge · SoftBMS (inverter: ${data.soc}%)` : "State of charge"}
        </div>
      </div>

      <div className="progress-wrap">
        <div className="progress-bar-bg">
          <div
            className="progress-bar-fill"
            style={{ width: `${soc}%`, background: color }}
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
          <div className="metric-label">Power</div>
          <div className="metric-value">{data.power} W</div>
        </div>
        <div className="metric">
          <div className="metric-label">Temp</div>
          <div className="metric-value">{data.temperature} °C</div>
        </div>
      </div>
    </div>
  );
}
