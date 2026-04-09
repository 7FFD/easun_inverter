import { useEffect, useRef, useState } from "react";
import { Config } from "../types";

interface Props {
  onConnect: (config: Config) => void;
}

type DiscoveryState = "idle" | "discovering" | "found" | "stopped" | "error";

interface DiscoveryEvent {
  type: "trying" | "timeout" | "probe_error" | "found" | "retry" | "stopped";
  label?: string;
  message?: string;
  ip?: string;
  local_ip?: string;
  cycle?: number;
}

type LogEntry =
  | { kind: "probe"; label: string; status: "pending" | "timeout" | "error" | "found" }
  | { kind: "divider"; cycle: number };

export default function SetupPage({ onConnect }: Props) {
  const [discoveryState, setDiscoveryState] = useState<DiscoveryState>("idle");
  const [log, setLog] = useState<LogEntry[]>([]);
  const [inverterIp, setInverterIp] = useState("");
  const [localIp, setLocalIp] = useState("");
  const [model, setModel] = useState("ISOLAR_SMG_II_6K");
  const [models, setModels] = useState<string[]>(["ISOLAR_SMG_II_11K", "ISOLAR_SMG_II_6K"]);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    fetch("/api/models")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d?.models?.length) setModels(d.models);
      })
      .catch(() => {});
  }, []);

  function updateLastProbe(label: string, status: "timeout" | "error" | "found") {
    setLog((prev) =>
      prev.map((e) =>
        e.kind === "probe" && e.label === label && e.status === "pending"
          ? { ...e, status }
          : e
      )
    );
  }

  function handleDiscover() {
    if (wsRef.current) wsRef.current.close();
    setLog([]);
    setDiscoveryState("discovering");

    const ws = new WebSocket(`ws://${window.location.hostname}:8000/ws/discover`);
    wsRef.current = ws;

    ws.onmessage = (event) => {
      const msg: DiscoveryEvent = JSON.parse(event.data);

      switch (msg.type) {
        case "trying":
          setLog((prev) => [...prev, { kind: "probe", label: msg.label!, status: "pending" }]);
          break;
        case "timeout":
          updateLastProbe(msg.label!, "timeout");
          break;
        case "probe_error":
          updateLastProbe(msg.label!, "error");
          break;
        case "retry":
          setLog((prev) => [...prev, { kind: "divider", cycle: msg.cycle! }]);
          break;
        case "found":
          updateLastProbe(msg.label ?? "", "found");
          setInverterIp(msg.ip!);
          setLocalIp(msg.local_ip ?? "");
          setDiscoveryState("found");
          break;
        case "stopped":
          setDiscoveryState("stopped");
          break;
      }
    };

    ws.onerror = () => setDiscoveryState("error");
    ws.onclose = () => { wsRef.current = null; };
  }

  function handleStop() {
    wsRef.current?.close();
    wsRef.current = null;
  }

  function handleConnect() {
    if (!inverterIp || !localIp) return;
    onConnect({ inverterIp, localIp, model });
  }

  const canConnect = inverterIp.trim() && localIp.trim() && model;
  const showForm = discoveryState !== "discovering";

  return (
    <div className="setup-page">
      <div className="setup-card">
        <div className="setup-logo">⚡</div>
        <h1 className="setup-title">Easun Inverter Monitor</h1>
        <p className="setup-subtitle">Discover your inverter on the local network</p>

        {/* Scan / Stop button */}
        {discoveryState === "discovering" ? (
          <button className="btn btn-secondary" onClick={handleStop}>
            ⏹ Stop scanning
          </button>
        ) : (
          <button className="btn btn-primary" onClick={handleDiscover}>
            {discoveryState === "idle" ? "🔍 Scan Network" : "🔄 Scan again"}
          </button>
        )}

        {/* Live discovery log — hidden once device is found */}
        {log.length > 0 && discoveryState !== "found" && (
          <div className="discovery-log">
            {log.map((entry, i) =>
              entry.kind === "divider" ? (
                <div key={i} className="discovery-log-divider">
                  retry #{entry.cycle}
                </div>
              ) : (
                <div key={i} className="discovery-log-row">
                  <span className="discovery-log-icon">
                    {entry.status === "pending" && <span className="spinner" style={{ width: 12, height: 12, borderWidth: 2 }} />}
                    {entry.status === "timeout" && <span style={{ color: "var(--text-dim)" }}>–</span>}
                    {entry.status === "error"   && <span style={{ color: "var(--red)" }}>✕</span>}
                    {entry.status === "found"   && <span style={{ color: "var(--green)" }}>✓</span>}
                  </span>
                  <span className="discovery-log-label">{entry.label}</span>
                  <span className="discovery-log-status">
                    {entry.status === "pending" && "probing…"}
                    {entry.status === "timeout" && "no response"}
                    {entry.status === "error"   && "error"}
                    {entry.status === "found"   && inverterIp}
                  </span>
                </div>
              )
            )}
          </div>
        )}

        {discoveryState === "stopped" && (
          <div className="alert alert-info" style={{ marginTop: "0.75rem" }}>
            Scan stopped — enter the IP manually below.
          </div>
        )}
        {discoveryState === "error" && (
          <div className="alert alert-error" style={{ marginTop: "0.75rem" }}>
            WebSocket error — is the backend running?
          </div>
        )}

        {/* Config form */}
        {showForm && (
          <div style={{ marginTop: "1.25rem" }}>
            {discoveryState === "idle" && (
              <div style={{ textAlign: "center", marginBottom: "1rem" }}>
                <span style={{ fontSize: "0.8125rem", color: "var(--text-dim)" }}>— or enter manually —</span>
              </div>
            )}

            <div className="setup-section">
              <div className="setup-label">Inverter IP</div>
              <input className="setup-input" type="text" placeholder="e.g. 192.168.1.100"
                value={inverterIp} onChange={(e) => setInverterIp(e.target.value)} />
            </div>

            <div className="setup-section">
              <div className="setup-label">Local Machine IP</div>
              <input className="setup-input" type="text" placeholder="e.g. 192.168.1.10"
                value={localIp} onChange={(e) => setLocalIp(e.target.value)} />
            </div>

            <div className="setup-section">
              <div className="setup-label">Inverter Model</div>
              <select className="setup-select" value={model} onChange={(e) => setModel(e.target.value)}>
                {models.map((m) => (
                  <option key={m} value={m}>{m.replace(/_/g, " ")}</option>
                ))}
              </select>
            </div>

            {canConnect && (
              <button className="btn btn-primary" onClick={handleConnect}>Connect →</button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
