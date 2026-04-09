import { useEffect, useRef, useState } from "react";
import { Config, InverterData, MQTTConfig, MQTTStatus } from "../types";
import BatteryCard from "../components/BatteryCard";
import SolarCard from "../components/SolarCard";
import GridCard from "../components/GridCard";
import OutputCard from "../components/OutputCard";
import EnergyFlowCard from "../components/EnergyFlowCard";

const REFRESH_INTERVAL = 20;

interface Props {
  config: Config;
  onDisconnect: () => void;
}

type ConnState = "connecting" | "live" | "error" | "closed";

const DEFAULT_MQTT: MQTTConfig = {
  host: "192.168.1.1",
  port: 1883,
  username: "",
  password: "",
  discovery_prefix: "homeassistant",
  device_id: "",
};

export default function DashboardPage({ config, onDisconnect }: Props) {
  // Inverter state
  const [data, setData] = useState<InverterData | null>(null);
  const [connState, setConnState] = useState<ConnState>("connecting");
  const [countdown, setCountdown] = useState(REFRESH_INTERVAL);
  const [errorMsg, setErrorMsg] = useState("");
  const wsRef = useRef<WebSocket | null>(null);
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // MQTT state
  const [mqttOpen, setMqttOpen] = useState(false);
  const [mqttCfg, setMqttCfg] = useState<MQTTConfig>(DEFAULT_MQTT);
  const [mqttStatus, setMqttStatus] = useState<MQTTStatus>({ connected: false, error: null, broker: null, default_device_id: "", saved_config: null });
  const [mqttBusy, setMqttBusy] = useState(false);
  const [mqttError, setMqttError] = useState("");
  const [discoveryDone, setDiscoveryDone] = useState(false);

  function resetCountdown() {
    setCountdown(REFRESH_INTERVAL);
    if (countdownRef.current) clearInterval(countdownRef.current);
    countdownRef.current = setInterval(() => {
      setCountdown((c) => (c > 0 ? c - 1 : 0));
    }, 1000);
  }

  // WebSocket setup
  useEffect(() => {
    const ws = new WebSocket(`ws://${window.location.hostname}:8000/ws/live`);
    wsRef.current = ws;

    ws.onopen = () => {
      ws.send(JSON.stringify({
        inverter_ip: config.inverterIp,
        local_ip: config.localIp,
        model: config.model,
      }));
    };

    ws.onmessage = (event) => {
      const payload: InverterData = JSON.parse(event.data);
      if (payload.error) {
        // Keep "connecting" state until we get real data at least once
        setErrorMsg(payload.error);
        setConnState((prev) => prev === "live" ? "live" : "connecting");
      } else {
        setErrorMsg("");
        setData(payload);
        setConnState("live");
        resetCountdown();
      }
    };

    ws.onerror = () => {
      setConnState("error");
      setErrorMsg("WebSocket connection failed");
    };

    ws.onclose = () => {
      setConnState((prev) => (prev === "live" ? "closed" : prev));
    };

    return () => {
      ws.close();
      if (countdownRef.current) clearInterval(countdownRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fetch MQTT status on mount so header dot is correct immediately
  useEffect(() => {
    fetch("/api/mqtt/status")
      .then((r) => r.ok ? r.json() : null)
      .then((s) => { if (s) setMqttStatus(s); })
      .catch(() => {});
  }, []);

  // Load status (and saved config) when panel opens; poll while open
  useEffect(() => {
    if (!mqttOpen) return;
    async function fetchStatus() {
      const res = await fetch("/api/mqtt/status");
      if (!res.ok) return;
      const s: MQTTStatus = await res.json();
      setMqttStatus(s);
      if (s.saved_config) {
        setMqttCfg((prev) => ({
          ...prev,
          host: s.saved_config!.host,
          port: s.saved_config!.port,
          username: s.saved_config!.username ?? "",
          password: s.saved_config!.password ?? "",
          discovery_prefix: s.saved_config!.discovery_prefix,
          device_id: s.saved_config!.device_id,
        }));
      } else {
        setMqttCfg((prev) => ({ ...prev, device_id: s.default_device_id }));
      }
    }
    fetchStatus();
    const poll = setInterval(fetchStatus, 3000);
    return () => clearInterval(poll);
  }, [mqttOpen]);

  async function handleMqttConnect() {
    setMqttBusy(true);
    setMqttError("");
    setDiscoveryDone(false);
    try {
      const res = await fetch("/api/mqtt/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          host: mqttCfg.host,
          port: mqttCfg.port,
          username: mqttCfg.username || null,
          password: mqttCfg.password || null,
          discovery_prefix: mqttCfg.discovery_prefix,
          device_id: mqttCfg.device_id,
          inverter_model: config.model,
        }),
      });
      const status: MQTTStatus = await res.json();
      setMqttStatus(status);
      if (!status.connected) {
        setMqttError(status.error || "Connection failed");
      }
    } catch (e) {
      setMqttError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setMqttBusy(false);
    }
  }

  async function handleMqttDisconnect() {
    setMqttBusy(true);
    try {
      await fetch("/api/mqtt/disconnect", { method: "POST" });
      setMqttStatus({ connected: false, error: null, broker: null, default_device_id: "", saved_config: null });
      setDiscoveryDone(false);
    } finally {
      setMqttBusy(false);
    }
  }

  async function handleDiscovery() {
    setMqttBusy(true);
    setMqttError("");
    try {
      const res = await fetch("/api/mqtt/discovery", { method: "POST" });
      const body = await res.json();
      if (body.ok) {
        setDiscoveryDone(true);
      } else {
        setMqttError(body.error || "Discovery failed");
      }
    } catch (e) {
      setMqttError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setMqttBusy(false);
    }
  }

  // Settings panel state
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsCfg, setSettingsCfg] = useState<Config>({ ...config });
  const [settingsSaved, setSettingsSaved] = useState(false);

  async function handleSaveSettings() {
    await fetch("/api/connection-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        inverter_ip: settingsCfg.inverterIp,
        local_ip: settingsCfg.localIp,
        model: settingsCfg.model,
      }),
    });
    setSettingsSaved(true);
    setTimeout(() => setSettingsSaved(false), 2000);
  }



  const dotClass =
    connState === "live" ? "dot dot-green" :
    connState === "connecting" ? "dot dot-yellow" :
    "dot dot-red";

  const connLabel =
    connState === "live" ? "Live" :
    connState === "connecting" ? "Connecting…" :
    connState === "error" ? "Connection error" : "Disconnected";

  return (
    <div className="dashboard">
      {/* Header */}
      <header className="dash-header">
        <div className="dash-header-left">
          <span style={{ fontSize: "1.25rem" }}>⚡</span>
          <span className="dash-title">Easun Inverter</span>
          <div className="dash-meta">
            <span className="dash-badge">
              <span className={dotClass} />
              {connLabel}
            </span>
            <span className="dash-badge">📡 {config.inverterIp}</span>
            <span className="dash-badge">🖥 {config.model.replace(/_/g, " ")}</span>
          </div>
        </div>

        <div className="dash-header-right">
          {connState === "live" && data && (
            <span className="refresh-info">
              Updated {new Date(data.timestamp).toLocaleTimeString()} · next in {countdown}s
            </span>
          )}

          {/* MQTT button */}
          <button
            className="btn btn-ghost"
            onClick={() => setMqttOpen((v) => !v)}
            style={{ gap: "0.375rem" }}
          >
            <span
              className="dot"
              style={{
                background: mqttStatus.connected ? "var(--green)" : "var(--text-dim)",
                boxShadow: mqttStatus.connected ? "0 0 6px var(--green)" : "none",
              }}
            />
            MQTT
          </button>

          <button
            className="btn btn-ghost"
            onClick={() => { setSettingsOpen((v) => !v); setMqttOpen(false); }}
            title="Connection settings"
          >⚙</button>

        </div>
      </header>

      {/* Connection settings panel */}
      {settingsOpen && (
        <div className="mqtt-overlay" onClick={(e) => e.target === e.currentTarget && setSettingsOpen(false)}>
          <div className="mqtt-panel">
            <div className="mqtt-panel-header">
              <span className="mqtt-panel-title">Connection Settings</span>
              <button className="mqtt-close" onClick={() => setSettingsOpen(false)}>✕</button>
            </div>

            <div className="mqtt-field">
              <label>Inverter IP</label>
              <input type="text" value={settingsCfg.inverterIp}
                onChange={(e) => setSettingsCfg({ ...settingsCfg, inverterIp: e.target.value })} />
            </div>
            <div className="mqtt-field">
              <label>Local Machine IP</label>
              <input type="text" value={settingsCfg.localIp}
                onChange={(e) => setSettingsCfg({ ...settingsCfg, localIp: e.target.value })} />
            </div>
            <div className="mqtt-field">
              <label>Model</label>
              <input type="text" value={settingsCfg.model}
                onChange={(e) => setSettingsCfg({ ...settingsCfg, model: e.target.value })} />
            </div>

            <div className="mqtt-actions">
              <button className="btn btn-mqtt-connect" onClick={handleSaveSettings}>
                {settingsSaved ? "✓ Saved" : "Save"}
              </button>
              <button className="btn btn-mqtt-disconnect" onClick={onDisconnect}>
                🔍 Go to Discovery
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MQTT settings panel */}
      {mqttOpen && (
        <div className="mqtt-overlay" onClick={(e) => e.target === e.currentTarget && setMqttOpen(false)}>
          <div className="mqtt-panel">
            <div className="mqtt-panel-header">
              <span className="mqtt-panel-title">MQTT / Home Assistant</span>
              <button className="mqtt-close" onClick={() => setMqttOpen(false)}>✕</button>
            </div>

            {/* Status */}
            <div className="mqtt-status-row">
              <span
                className="dot"
                style={{
                  background: mqttStatus.connected ? "var(--green)" : "var(--text-dim)",
                  boxShadow: mqttStatus.connected ? "0 0 6px var(--green)" : "none",
                }}
              />
              <span className="mqtt-status-label">
                {mqttStatus.connected ? "Connected" : "Disconnected"}
              </span>
              {mqttStatus.broker && (
                <span className="mqtt-status-broker">{mqttStatus.broker}</span>
              )}
            </div>

            {/* Config fields */}
            <div className="mqtt-field-row">
              <div className="mqtt-field">
                <label>Broker host</label>
                <input
                  type="text"
                  placeholder="192.168.1.1"
                  value={mqttCfg.host}
                  onChange={(e) => setMqttCfg({ ...mqttCfg, host: e.target.value })}
                  disabled={mqttStatus.connected}
                />
              </div>
              <div className="mqtt-field" style={{ width: 80 }}>
                <label>Port</label>
                <input
                  type="number"
                  value={mqttCfg.port}
                  onChange={(e) => setMqttCfg({ ...mqttCfg, port: Number(e.target.value) })}
                  disabled={mqttStatus.connected}
                />
              </div>
            </div>

            <div className="mqtt-field">
              <label>Username (optional)</label>
              <input
                type="text"
                placeholder="mqtt user"
                value={mqttCfg.username}
                onChange={(e) => setMqttCfg({ ...mqttCfg, username: e.target.value })}
                disabled={mqttStatus.connected}
              />
            </div>

            <div className="mqtt-field">
              <label>Password (optional)</label>
              <input
                type="password"
                placeholder="••••••••"
                value={mqttCfg.password}
                onChange={(e) => setMqttCfg({ ...mqttCfg, password: e.target.value })}
                disabled={mqttStatus.connected}
              />
            </div>

            <div className="mqtt-field">
              <label>Discovery prefix</label>
              <input
                type="text"
                value={mqttCfg.discovery_prefix}
                onChange={(e) => setMqttCfg({ ...mqttCfg, discovery_prefix: e.target.value })}
                disabled={mqttStatus.connected}
              />
            </div>

            <div className="mqtt-field">
              <label>Device ID</label>
              <input
                type="text"
                value={mqttCfg.device_id}
                onChange={(e) => setMqttCfg({ ...mqttCfg, device_id: e.target.value })}
                disabled={mqttStatus.connected}
              />
            </div>

            {mqttError && <div className="mqtt-error">{mqttError}</div>}
            {discoveryDone && (
              <div style={{ fontSize: "0.75rem", color: "var(--green)", marginTop: "0.5rem" }}>
                ✓ Discovery messages published to Home Assistant
              </div>
            )}

            <div className="mqtt-actions">
              {!mqttStatus.connected ? (
                <button
                  className="btn btn-mqtt-connect"
                  onClick={handleMqttConnect}
                  disabled={mqttBusy || !mqttCfg.host}
                >
                  {mqttBusy ? <><span className="spinner" /> Connecting…</> : "Connect to broker"}
                </button>
              ) : (
                <>
                  <button
                    className="btn btn-mqtt-discovery"
                    onClick={handleDiscovery}
                    disabled={mqttBusy}
                  >
                    {mqttBusy ? <><span className="spinner" /> Publishing…</> : "📡 Send HA Discovery"}
                  </button>
                  <button
                    className="btn btn-mqtt-disconnect"
                    onClick={handleMqttDisconnect}
                    disabled={mqttBusy}
                  >
                    Disconnect
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Dashboard body */}
      {connState === "connecting" && (
        <div className="dash-loading">
          <div className="spinner" style={{ width: "2rem", height: "2rem", borderWidth: 3 }} />
          <p className="dash-loading-text">Connecting to inverter…</p>
          {errorMsg ? (
            <p style={{ fontSize: "0.8125rem", color: "var(--red)", marginTop: "0.5rem" }}>
              {errorMsg} — retrying…
            </p>
          ) : (
            <p style={{ fontSize: "0.8125rem", color: "var(--text-dim)" }}>
              First data may take up to 30 seconds
            </p>
          )}
        </div>
      )}

      {(connState === "error" || connState === "closed") && (
        <div className="dash-error">
          <div style={{ fontSize: "2rem" }}>⚠️</div>
          <p style={{ color: "var(--text-muted)" }}>{errorMsg || "Connection lost"}</p>
          <button className="btn btn-ghost" onClick={onDisconnect} style={{ marginTop: "0.5rem" }}>
            Back to setup
          </button>
        </div>
      )}

      {connState === "live" && data && (
        <div className="dash-body">
          {errorMsg && (
            <div className="alert alert-error" style={{ marginBottom: "1.25rem" }}>
              Last fetch error: {errorMsg}
            </div>
          )}
          <div className="cards-grid">
            {data.battery && data.pv && data.grid && data.output && (
              <div style={{ gridColumn: "span 2", gridRow: "span 2" }}>
                <EnergyFlowCard
                  pv={data.pv}
                  grid={data.grid}
                  battery={data.battery}
                  output={data.output}
                />
              </div>
            )}
            {data.grid && <GridCard data={data.grid} status={data.status} />}
            {data.battery && <BatteryCard data={data.battery} />}
            {data.output && <OutputCard data={data.output} />}
            {data.pv && <SolarCard data={data.pv} />}
          </div>
        </div>
      )}
    </div>
  );
}
