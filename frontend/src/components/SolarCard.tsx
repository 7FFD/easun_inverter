import { PVData } from "../types";

function fmt(v: number | null | undefined, decimals = 1): string {
  if (v == null) return "—";
  return v.toFixed(decimals);
}

interface Props {
  data: PVData;
}

export default function SolarCard({ data }: Props) {
  return (
    <div className="card">
      <div className="card-header">
        <div className="card-icon card-icon-yellow">☀️</div>
        <span className="card-title">Solar</span>
      </div>

      <div className="metric-primary">
        <div className="metric-primary-value" style={{ color: "var(--yellow)" }}>
          {data.total_power}
          <span className="metric-primary-unit">W</span>
        </div>
        <div className="metric-primary-label">Total PV power</div>
      </div>

      <div className="metrics-row">
        <div className="metric">
          <div className="metric-label">Charging</div>
          <div className="metric-value">{data.charging_power} W</div>
        </div>
        <div className="metric">
          <div className="metric-label">Chr. Current</div>
          <div className="metric-value">{fmt(data.charging_current)} A</div>
        </div>
        <div className="metric">
          <div className="metric-label">Temp</div>
          <div className="metric-value">{data.temperature} °C</div>
        </div>
      </div>

      <hr className="card-divider" />

      <div className="subsection-title">PV String 1</div>
      <div className="metrics-row">
        <div className="metric">
          <div className="metric-label">Voltage</div>
          <div className="metric-value">{fmt(data.pv1_voltage)} V</div>
        </div>
        <div className="metric">
          <div className="metric-label">Current</div>
          <div className="metric-value">{fmt(data.pv1_current)} A</div>
        </div>
        <div className="metric">
          <div className="metric-label">Power</div>
          <div className="metric-value">{data.pv1_power} W</div>
        </div>
      </div>

      {data.pv2_voltage != null && data.pv2_voltage > 0 && (
        <>
          <div className="subsection-title" style={{ marginTop: "0.875rem" }}>
            PV String 2
          </div>
          <div className="metrics-row">
            <div className="metric">
              <div className="metric-label">Voltage</div>
              <div className="metric-value">{fmt(data.pv2_voltage)} V</div>
            </div>
            <div className="metric">
              <div className="metric-label">Current</div>
              <div className="metric-value">{fmt(data.pv2_current)} A</div>
            </div>
            <div className="metric">
              <div className="metric-label">Power</div>
              <div className="metric-value">{data.pv2_power} W</div>
            </div>
          </div>
        </>
      )}

      {(data.pv_generated_today != null || data.pv_generated_total != null) && (
        <>
          <hr className="card-divider" />
          <div className="metrics-row">
            {data.pv_generated_today != null && (
              <div className="metric">
                <div className="metric-label">Today</div>
                <div className="metric-value">{fmt(data.pv_generated_today, 2)} kWh</div>
              </div>
            )}
            {data.pv_generated_total != null && (
              <div className="metric">
                <div className="metric-label">Total</div>
                <div className="metric-value">{fmt(data.pv_generated_total, 2)} kWh</div>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
