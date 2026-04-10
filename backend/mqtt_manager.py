"""
MQTT manager for Easun Inverter Monitor.
Handles Home Assistant MQTT discovery and sensor state publishing.
"""
import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", str(Path(__file__).parent / "config")))
CONFIG_PATH = _CONFIG_DIR / "mqtt_config.json"
_CONN_CONFIG_PATH = _CONFIG_DIR / "connection_config.json"


def _dashboard_url() -> Optional[str]:
    """Return the dashboard URL for HA device configuration_url.
    DASHBOARD_URL env var overrides everything (e.g. http://localhost:5173 in dev).
    Falls back to http://<local_ip>:8000 using the saved connection config."""
    explicit = os.environ.get("DASHBOARD_URL")
    if explicit:
        return explicit
    try:
        data = json.loads(_CONN_CONFIG_PATH.read_text())
        ip = data.get("local_ip")
        return f"http://{ip}:8000" if ip else None
    except Exception:
        return None

# Generated once per process; persists across sessions via mqtt_config.json
_session_device_id: Optional[str] = None


def _get_default_device_id() -> str:
    global _session_device_id
    if _session_device_id is None:
        _session_device_id = f"easun_{uuid.uuid4().hex[:8]}"
    return _session_device_id

# ── Sensor definitions ─────────────────────────────────────────────────────
# (id, name, unit, device_class, state_class, icon, path, entity_category)
# entity_category: None = primary sensor, "diagnostic" = detail/diagnostic
SENSOR_DEFS = [
    # ── Primary sensors (shown on HA device dashboard) ──────────────────
    # Battery
    ("battery_soc",          "Battery SOC",           "%",   "battery",     "measurement",      "mdi:battery",              "battery.soc",               None),
    ("battery_power",        "Battery Power",         "W",   "power",       "measurement",      "mdi:battery-charging",     "battery.power",             None),
    # Solar
    ("pv_total_power",       "PV Total Power",        "W",   "power",       "measurement",      "mdi:solar-power",          "pv.total_power",            None),
    ("pv_energy_today",      "PV Energy Today",       "kWh", "energy",      "total_increasing", "mdi:solar-power",          "pv.pv_generated_today",     None),
    ("pv_energy_total",      "PV Energy Total",       "kWh", "energy",      "total_increasing", "mdi:counter",              "pv.pv_generated_total",     None),
    # Grid
    ("grid_power",           "Grid Power",            "W",   "power",       "measurement",      "mdi:transmission-tower",   "grid.power",                None),
    # Output
    ("output_power",         "Output Power",          "W",   "power",       "measurement",      "mdi:power-plug",           "output.power",              None),
    ("output_load_pct",      "Output Load",           "%",   None,          "measurement",      "mdi:gauge",                "output.load_percentage",    None),
    # System
    ("operating_mode",       "Operating Mode",        None,  None,          None,               "mdi:solar-panel-large",    "status.mode_name",          None),

    # ── Diagnostic sensors (collapsed under "Diagnostics" in HA) ────────
    # Battery detail
    ("battery_voltage",      "Battery Voltage",       "V",   "voltage",     "measurement",      "mdi:battery",              "battery.voltage",           "diagnostic"),
    ("battery_current",      "Battery Current",       "A",   "current",     "measurement",      "mdi:current-dc",           "battery.current",           "diagnostic"),
    ("battery_temperature",  "Battery Temperature",   "°C",  "temperature", "measurement",      "mdi:thermometer",          "battery.temperature",       "diagnostic"),
    # PV strings detail
    ("pv_charging_power",    "PV Charging Power",     "W",   "power",       "measurement",      "mdi:solar-power",          "pv.charging_power",         "diagnostic"),
    ("pv1_voltage",          "PV1 Voltage",           "V",   "voltage",     "measurement",      "mdi:solar-panel",          "pv.pv1_voltage",            "diagnostic"),
    ("pv1_current",          "PV1 Current",           "A",   "current",     "measurement",      "mdi:solar-panel",          "pv.pv1_current",            "diagnostic"),
    ("pv1_power",            "PV1 Power",             "W",   "power",       "measurement",      "mdi:solar-panel",          "pv.pv1_power",              "diagnostic"),
    ("pv2_voltage",          "PV2 Voltage",           "V",   "voltage",     "measurement",      "mdi:solar-panel",          "pv.pv2_voltage",            "diagnostic"),
    ("pv2_current",          "PV2 Current",           "A",   "current",     "measurement",      "mdi:solar-panel",          "pv.pv2_current",            "diagnostic"),
    ("pv2_power",            "PV2 Power",             "W",   "power",       "measurement",      "mdi:solar-panel",          "pv.pv2_power",              "diagnostic"),
    # Grid detail
    ("grid_voltage",         "Grid Voltage",          "V",   "voltage",     "measurement",      "mdi:transmission-tower",   "grid.voltage",              "diagnostic"),
    ("grid_frequency",       "Grid Frequency",        "Hz",  "frequency",   "measurement",      "mdi:sine-wave",            "grid.frequency",            "diagnostic"),
    # Output detail
    ("output_voltage",       "Output Voltage",        "V",   "voltage",     "measurement",      "mdi:power-plug",           "output.voltage",            "diagnostic"),
    ("output_current",       "Output Current",        "A",   "current",     "measurement",      "mdi:power-plug",           "output.current",            "diagnostic"),
    ("output_apparent_power","Output Apparent Power", "VA",  None,          "measurement",      "mdi:power-plug",           "output.apparent_power",     "diagnostic"),
    ("output_frequency",     "Output Frequency",      "Hz",  "frequency",   "measurement",      "mdi:sine-wave",            "output.frequency",          "diagnostic"),
]


def _extract(data: dict, path: str) -> Any:
    """Extract a nested value using dot-notation path."""
    parts = path.split(".")
    val = data
    for part in parts:
        if not isinstance(val, dict):
            return None
        val = val.get(part)
    return val


def _format_value(sensor_id: str, raw: Any) -> Optional[str]:
    """Format a raw value for MQTT publishing."""
    if raw is None:
        return None
    # grid_frequency and output_frequency are stored in centihz
    if sensor_id in ("grid_frequency", "output_frequency"):
        try:
            return f"{float(raw) / 100:.2f}"
        except (TypeError, ValueError):
            return None
    return str(raw)


@dataclass
class MQTTConfig:
    host: str
    port: int = 1883
    username: Optional[str] = None
    password: Optional[str] = None
    discovery_prefix: str = "homeassistant"
    device_id: str = "easun_inverter"
    inverter_model: str = "ISOLAR_SMG_II"

    def save(self) -> None:
        CONFIG_PATH.write_text(json.dumps({
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "discovery_prefix": self.discovery_prefix,
            "device_id": self.device_id,
            "inverter_model": self.inverter_model,
        }, indent=2))

    @staticmethod
    def load() -> Optional["MQTTConfig"]:
        if not CONFIG_PATH.exists():
            return None
        try:
            data = json.loads(CONFIG_PATH.read_text())
            return MQTTConfig(**data)
        except Exception as e:
            logger.warning(f"Failed to load saved MQTT config: {e}")
            return None


class MQTTManager:
    def __init__(self):
        self._client: Optional[mqtt.Client] = None
        self._config: Optional[MQTTConfig] = None
        self._connected = False
        self._lock = threading.Lock()
        self._error: Optional[str] = None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def error(self) -> Optional[str]:
        return self._error

    def connect(self, config: MQTTConfig) -> None:
        """Connect to broker and start background loop."""
        with self._lock:
            self._disconnect_internal()
            self._config = config
            self._error = None

            client = mqtt.Client(client_id=f"easun_monitor_{config.device_id}")
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect

            if config.username:
                client.username_pw_set(config.username, config.password)

            try:
                client.connect(config.host, config.port, keepalive=60)
                client.loop_start()
                self._client = client
                config.save()
                logger.info(f"MQTT connecting to {config.host}:{config.port}")
            except Exception as e:
                self._error = str(e)
                logger.error(f"MQTT connect failed: {e}")
                raise

    def disconnect(self) -> None:
        with self._lock:
            self._disconnect_internal()

    def _disconnect_internal(self) -> None:
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._connected = False

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._connected = True
            self._error = None
            logger.info("MQTT connected")
        else:
            self._connected = False
            self._error = f"Connection refused (rc={rc})"
            logger.error(f"MQTT connection refused: rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        self._connected = False
        if rc != 0:
            self._error = f"Unexpected disconnect (rc={rc})"
            logger.warning(f"MQTT unexpected disconnect: rc={rc}")

    def publish_discovery(self) -> None:
        """Publish HA MQTT discovery config messages for all sensors."""
        if not self._connected or not self._config:
            raise RuntimeError("Not connected to MQTT broker")

        cfg = self._config
        device_payload: dict = {
            "identifiers": [cfg.device_id],
            "name": f"Easun Inverter ({cfg.device_id.removeprefix('easun_')})",
            "model": cfg.inverter_model,
            "manufacturer": "Easun Power",
        }
        url = _dashboard_url()
        if url:
            device_payload["configuration_url"] = url

        for sensor_id, name, unit, device_class, state_class, icon, path, entity_category in SENSOR_DEFS:
            unique_id = f"{cfg.device_id}_{sensor_id}"
            state_topic = f"{cfg.device_id}/sensor/{sensor_id}"
            discovery_topic = f"{cfg.discovery_prefix}/sensor/{unique_id}/config"

            payload: dict = {
                "name": name,
                "unique_id": unique_id,
                "object_id": unique_id,
                "state_topic": state_topic,
                "device": device_payload,
            }
            if unit:
                payload["unit_of_measurement"] = unit
            if device_class:
                payload["device_class"] = device_class
            if state_class:
                payload["state_class"] = state_class
            if icon:
                payload["icon"] = icon
            if entity_category:
                payload["entity_category"] = entity_category

            self._client.publish(
                discovery_topic,
                json.dumps(payload),
                retain=True,
            )
            logger.debug(f"Published discovery for {sensor_id}")

        logger.info(f"Published HA discovery for {len(SENSOR_DEFS)} sensors")

    def publish_data(self, data: dict) -> None:
        """Publish sensor state values from an inverter data snapshot."""
        if not self._connected or not self._config or not self._client:
            return

        cfg = self._config
        for sensor_id, _name, _unit, _dc, _sc, _icon, path, _ in SENSOR_DEFS:
            raw = _extract(data, path)
            value = _format_value(sensor_id, raw)
            if value is None:
                continue
            topic = f"{cfg.device_id}/sensor/{sensor_id}"
            self._client.publish(topic, value)

        logger.debug("Published inverter data to MQTT")

    def status(self) -> dict:
        saved = MQTTConfig.load()
        default_device_id = saved.device_id if saved else _get_default_device_id()
        return {
            "connected": self._connected,
            "error": self._error,
            "broker": f"{self._config.host}:{self._config.port}" if self._config else None,
            "default_device_id": default_device_id,
            "saved_config": {
                "host": saved.host,
                "port": saved.port,
                "username": saved.username,
                "password": saved.password,
                "discovery_prefix": saved.discovery_prefix,
                "device_id": saved.device_id,
            } if saved else None,
        }


# Global singleton
mqtt_manager = MQTTManager()
