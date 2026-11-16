import { SoftBmsSnapshot } from "../types";

export function modeLabel(m: SoftBmsSnapshot["mode"]): string {
  if (m === "float_full") return "Full (float)";
  if (m === "active") return "Charging / discharging";
  return "Waiting for data";
}

export function socColor(soc: number): string {
  if (soc >= 60) return "var(--green)";
  if (soc >= 25) return "var(--yellow)";
  return "var(--red)";
}
