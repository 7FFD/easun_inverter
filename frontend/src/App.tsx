import { useEffect, useState } from "react";
import SetupPage from "./pages/SetupPage";
import DashboardPage from "./pages/DashboardPage";
import { Config } from "./types";

export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/connection-config")
      .then((r) => r.ok ? r.json() : null)
      .then((d) => {
        if (d?.inverter_ip && d?.local_ip && d?.model) {
          setConfig({ inverterIp: d.inverter_ip, localIp: d.local_ip, model: d.model });
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) return null;

  return config ? (
    <DashboardPage config={config} onDisconnect={() => setConfig(null)} />
  ) : (
    <SetupPage onConnect={setConfig} />
  );
}
