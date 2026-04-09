import type { BatteryData, GridData, OutputData, PVData } from "../types";

interface Props {
  pv: PVData;
  grid: GridData;
  battery: BatteryData;
  output: OutputData;
}

function fmt(w: number): string {
  const abs = Math.abs(w);
  return abs >= 1000 ? `${(abs / 1000).toFixed(1)} kW` : `${Math.round(abs)} W`;
}

const SOLAR_CLR = "#ff8c42";
const GRID_CLR  = "#4499ff";
const HOME_CLR  = "#22d4e8";
const BAT_CLR   = "#d966f0";
const FONT = "Inter, system-ui, sans-serif";

const CX = 200, CY = 200;
const NR = 39; // node radius (52 × 0.75)

const S = { x: 200, y: 62  }; // Solar   – top
const G = { x: 62,  y: 200 }; // Grid    – left
const H = { x: 338, y: 200 }; // Home    – right
const B = { x: 200, y: 338 }; // Battery – bottom

const GLOW: Record<string, string> = {
  [SOLAR_CLR]: "rgba(255,140,66,0.1)",
  [GRID_CLR]:  "rgba(68,153,255,0.1)",
  [HOME_CLR]:  "rgba(34,212,232,0.1)",
  [BAT_CLR]:   "rgba(217,102,240,0.1)",
};

function lineClass(on: boolean, inward: boolean) {
  if (!on) return "flow-line flow-inactive";
  return `flow-line ${inward ? "flow-active-in" : "flow-active-out"}`;
}

interface NodeProps {
  cx: number; cy: number;
  color: string;
  icon: string;
  label: string;
  active: boolean;
  labelSide?: "top" | "bottom";
}

function Node({ cx, cy, color, icon, label, active, labelSide = "bottom" }: NodeProps) {
  const labelY = labelSide === "top" ? cy - NR - 7 : cy + NR + 14;
  return (
    <>
      {active && <circle cx={cx} cy={cy} r={NR + 5} fill={GLOW[color]} />}
      <circle
        cx={cx} cy={cy} r={NR}
        fill="var(--card)"
        stroke={color}
        strokeWidth={active ? 2.5 : 1.5}
        strokeOpacity={active ? 1 : 0.3}
      />
      <text x={cx} y={cy + 7} textAnchor="middle" fontSize={18}>{icon}</text>
      <text x={cx} y={labelY} textAnchor="middle" fontSize={10.5} fill="var(--text-muted)" fontFamily={FONT}>
        {label}
      </text>
    </>
  );
}

export default function EnergyFlowCard({ pv, grid, battery, output }: Props) {
  const pvW   = pv.total_power ?? 0;
  const gridW = grid.power     ?? 0;
  const batW  = battery.power  ?? 0;
  const loadW = output.power   ?? 0;

  const solarOn = pvW   > 10;
  const gridOn  = Math.abs(gridW) > 10;
  const batOn   = Math.abs(batW)  > 10;
  const homeOn  = loadW > 10;

  const gridLabel = `${fmt(gridW)} ${gridW > 10 ? "↓" : gridW < -10 ? "↑" : "·"} Grid`;
  const batLabel  = `${fmt(batW)} ${batW > 10 ? "↓" : batW < -10 ? "↑" : "·"} ${battery.soc}%`;

  return (
    <div className="card" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div className="card-header">
        <div className="card-icon card-icon-cyan">⚡</div>
        <span className="card-title">Energy Flow</span>
      </div>

      <div style={{ flex: 1, display: "flex", alignItems: "stretch" }}>
      <svg
        viewBox="0 0 400 400"
        style={{ width: "100%", height: "100%", display: "block" }}
        aria-hidden="true"
      >
        {/* Flow lines — path direction: node → center */}
        <path d={`M ${S.x} ${S.y} L ${CX} ${CY}`} stroke={SOLAR_CLR} strokeWidth={2.5} className={lineClass(solarOn, true)} />
        <path d={`M ${G.x} ${G.y} L ${CX} ${CY}`} stroke={GRID_CLR}  strokeWidth={2.5} className={lineClass(gridOn, gridW > 0)} />
        <path d={`M ${H.x} ${H.y} L ${CX} ${CY}`} stroke={HOME_CLR}  strokeWidth={2.5} className={lineClass(homeOn, false)} />
        <path d={`M ${B.x} ${B.y} L ${CX} ${CY}`} stroke={BAT_CLR}   strokeWidth={2.5} className={lineClass(batOn, batW < 0)} />

        {/* Center hub */}
        <circle cx={CX} cy={CY} r={20} fill="var(--surface)" stroke="var(--border)" strokeWidth={1.5} />
        <text x={CX} y={CY + 4} textAnchor="middle" fontSize={9} fill="var(--text-dim)"
          fontFamily={FONT} fontWeight={600} letterSpacing="1">INV</text>

        {/* Nodes */}
        <Node cx={S.x} cy={S.y} color={SOLAR_CLR} icon="☀️" label={`${fmt(pvW)} · Solar`}  active={solarOn} labelSide="top" />
        <Node cx={G.x} cy={G.y} color={GRID_CLR}  icon="🔌" label={gridLabel}               active={gridOn}  />
        <Node cx={H.x} cy={H.y} color={HOME_CLR}  icon="🏠" label={`${fmt(loadW)} · Load`}  active={homeOn}  />
        <Node cx={B.x} cy={B.y} color={BAT_CLR}   icon="🔋" label={batLabel}                active={batOn}   />
      </svg>
      </div>
    </div>
  );
}
