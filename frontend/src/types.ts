export interface BatteryData {
  voltage: number;
  current: number;
  power: number;
  soc: number;
  temperature: number;
}

export interface PVData {
  total_power: number;
  charging_power: number;
  charging_current: number;
  temperature: number;
  pv1_voltage: number;
  pv1_current: number;
  pv1_power: number;
  pv2_voltage: number | null;
  pv2_current: number | null;
  pv2_power: number | null;
  pv_generated_today: number | null;
  pv_generated_total: number | null;
}

export interface GridData {
  voltage: number;
  power: number;
  frequency: number;
}

export interface OutputData {
  voltage: number;
  current: number;
  power: number;
  apparent_power: number;
  load_percentage: number;
  frequency: number;
}

export interface SystemStatus {
  operating_mode: number;
  mode_name: string;
  inverter_time: string | null;
}

export interface InverterData {
  battery: BatteryData | null;
  pv: PVData | null;
  grid: GridData | null;
  output: OutputData | null;
  status: SystemStatus | null;
  timestamp: string;
  error?: string;
}

export interface Config {
  inverterIp: string;
  localIp: string;
  model: string;
}

export interface MQTTConfig {
  host: string;
  port: number;
  username: string;
  password: string;
  discovery_prefix: string;
  device_id: string;
}

export interface MQTTStatus {
  connected: boolean;
  error: string | null;
  broker: string | null;
  default_device_id: string;
  saved_config: Omit<MQTTConfig, "discovery_prefix"> & { discovery_prefix: string } | null;
}
