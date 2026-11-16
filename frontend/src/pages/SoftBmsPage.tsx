import { useEffect, useRef, useState } from "react";
import { SoftBmsStatus } from "../types";
import { modeLabel, socColor } from "../components/softBmsUtil";

interface Props {
  onBack: () => void;
}

export default function SoftBmsPage({ onBack }: Props) {
  const [status, setStatus] = useState<SoftBmsStatus | null>(null);
  const [error, setError] = useState("");
  const [capacity, setCapacity] = useState("");
  const [bias, setBias] = useState("");
  const [eff, setEff] = useState("");
  const [auto, setAuto] = useState(true);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const formLoaded = useRef(false);

  function applyToForm(s: SoftBmsStatus) {
    setCapacity(s.config.capacity_wh ? String(s.config.capacity_wh) : "");
    setBias(String(s.config.bias_w));
    setEff(String(Math.round(s.config.efficiency * 100)));
    setAuto(s.config.auto_calibrate);
  }

  async function load() {
    try {
      const r = await fetch("/api/soft-bms");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const s: SoftBmsStatus = await r.json();
      setStatus(s);
      setError("");
      // Only seed the form once so polling never overwrites what the user is typing.
      if (!formLoaded.current) {
        formLoaded.current = true;
        applyToForm(s);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load SoftBMS status");
    }
  }

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  async function post(url: string, body?: unknown): Promise<SoftBmsStatus | null> {
    setBusy(true);
    setError("");
    try {
      const r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return await r.json();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function handleSave() {
    const cap = capacity.trim() === "" ? 0 : Number(capacity);
    const b = Number(bias);
    const e = Number(eff) / 100;
    if (!Number.isFinite(cap) || cap < 0) return setError("Capacity must be a positive number of Wh");
    if (!Number.isFinite(b) || b < 0 || b > 500) return setError("Bias must be between 0 and 500 W");
    if (!Number.isFinite(e) || e < 0.5 || e > 1) return setError("Efficiency must be between 50 and 100 %");
    const body: Record<string, unknown> = { capacity_wh: cap, efficiency: e, auto_calibrate: auto };
    // Changing the bias restarts calibration on the backend, so only send it when edited.
    if (status && b !== status.config.bias_w) body.bias_w = b;
    const s = await post("/api/soft-bms/config", body);
    if (s) {
      setStatus(s);
      applyToForm(s);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    }
  }

  async function handleFull() {
    if (!window.confirm("Declare the battery 100% full now? Use this only if you know it is full.")) return;
    const s = await post("/api/soft-bms/full");
    if (s) setStatus(s);
  }

  const hasSoc = status?.soc_pct != null;
  const color = hasSoc ? socColor(status!.soc_pct as number) : "var(--text-dim)";

  return (
    <div className="dash-body">
      <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "1.25rem" }}>
        <button className="btn btn-ghost" onClick={onBack}>← Dashboard</button>
        <h2 style={{ margin: 0, fontSize: "1.125rem" }}>SoftBMS</h2>
      </div>

      {error && <div className="alert alert-error" style={{ marginBottom: "1.25rem" }}>{error}</div>}

      <div className="cards-grid">
        {/* Live state */}
        <div className="card">
          <div className="card-header">
            <div className="card-icon card-icon-green">🧠</div>
            <span className="card-title">Current state</span>
          </div>
          {!status ? (
            <div className="spinner" />
          ) : (
            <>
              <div className="metric-primary">
                <div className="metric-primary-value" style={{ color }}>
                  {hasSoc ? status.soc_pct : "—"}
                  {hasSoc && <span className="metric-primary-unit">%</span>}
                </div>
                <div className="metric-primary-label">{modeLabel(status.mode)}</div>
              </div>
              <div className="metrics-row">
                <div className="metric">
                  <div className="metric-label">Used since full</div>
                  <div className="metric-value">{Math.round(status.used_since_full_wh)} Wh</div>
                </div>
                <div className="metric">
                  <div className="metric-label">Energy out</div>
                  <div className="metric-value">{status.discharged_kwh.toFixed(3)} kWh</div>
                </div>
                <div className="metric">
                  <div className="metric-label">Energy in</div>
                  <div className="metric-value">{status.charged_kwh.toFixed(3)} kWh</div>
                </div>
                <div className="metric">
                  <div className="metric-label">Raw out (uncorrected)</div>
                  <div className="metric-value">{status.raw_discharged_kwh.toFixed(3)} kWh</div>
                </div>
              </div>
              <div style={{ fontSize: "0.75rem", color: "var(--text-dim)", marginTop: "0.75rem" }}>
                Anchored to full {status.anchors}× ·{" "}
                {status.last_anchor ? `last ${new Date(status.last_anchor).toLocaleString()}` : "not yet anchored"}
              </div>
            </>
          )}
        </div>

        {/* Calibration */}
        <div className="card">
          <div className="card-header">
            <div className="card-icon card-icon-purple">📐</div>
            <span className="card-title">Bias calibration</span>
          </div>
          {status && (
            <>
              <div className="metric-primary">
                <div className="metric-primary-value">
                  {Math.round(status.bias_w)}
                  <span className="metric-primary-unit">W</span>
                </div>
                <div className="metric-primary-label">Power-reading bias in use</div>
              </div>
              <p style={{ fontSize: "0.8125rem", color: "var(--text-muted)", margin: "0.5rem 0" }}>
                The inverter over-reports battery power by a roughly constant amount. Each full
                float → discharge → charge → float cycle re-estimates it; the median of the seed and
                recent cycles is used.
              </p>
              <div className="metric-label">Calibrated cycles ({status.bias_history.length})</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "0.375rem", marginTop: "0.375rem" }}>
                {status.bias_history.length === 0 && (
                  <span style={{ fontSize: "0.8125rem", color: "var(--text-dim)" }}>
                    none yet — using the seed value
                  </span>
                )}
                {status.bias_history.map((b, i) => (
                  <span key={i} className="dash-badge">{Math.round(b)} W</span>
                ))}
              </div>
            </>
          )}
        </div>

        {/* Settings */}
        <div className="card">
          <div className="card-header">
            <div className="card-icon card-icon-purple">⚙</div>
            <span className="card-title">Settings</span>
          </div>

          <div className="mqtt-field">
            <label>Usable battery capacity (Wh) — required for SOC %</label>
            <input type="number" min={0} placeholder="e.g. 5000" value={capacity}
              onChange={(e) => setCapacity(e.target.value)} />
          </div>
          <div className="mqtt-field">
            <label>Seed power bias (W) — changing it restarts calibration</label>
            <input type="number" min={0} max={500} value={bias}
              onChange={(e) => setBias(e.target.value)} />
          </div>
          <div className="mqtt-field">
            <label>Charge efficiency (%)</label>
            <input type="number" min={50} max={100} value={eff}
              onChange={(e) => setEff(e.target.value)} />
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", fontSize: "0.8125rem", margin: "0.75rem 0" }}>
            <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
            Auto-calibrate bias from completed cycles
          </label>

          <div className="mqtt-actions">
            <button className="btn btn-mqtt-connect" onClick={handleSave} disabled={busy}>
              {saved ? "✓ Saved" : "Save"}
            </button>
            <button className="btn btn-mqtt-disconnect" onClick={handleFull} disabled={busy}>
              🔋 Set battery full now
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
