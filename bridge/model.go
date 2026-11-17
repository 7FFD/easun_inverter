package main

import (
	"fmt"
	"strconv"
)

// Register maps ported from backend/easunpy/models.py (address 0 = unsupported, omitted).
type reg struct {
	addr  int
	scale float64
}

var common6K = map[string]reg{
	"operation_mode": {201, 1}, "battery_voltage": {215, 0.1}, "battery_current": {216, 0.1},
	"battery_power": {217, 1}, "battery_soc": {229, 1}, "battery_temperature": {226, 1},
	"pv_total_power": {223, 1}, "pv_charging_power": {224, 1}, "pv1_voltage": {219, 0.1},
	"pv1_current": {220, 0.1}, "pv1_power": {223, 1}, "grid_voltage": {202, 0.1},
	"grid_power": {204, 1}, "grid_frequency": {203, 1}, "output_voltage": {210, 0.1},
	"output_current": {211, 0.1}, "output_power": {213, 1}, "output_apparent_power": {214, 1},
	"output_load_percentage": {225, 0.01}, "output_frequency": {212, 1},
}

var models = map[string]map[string]reg{
	"ISOLAR_SMG_II_6K": common6K,
	"ISOLAR_SMG_II_4K": common6K,
	"ISOLAR_SMG_II_11K": {
		"operation_mode": {201, 1}, "battery_voltage": {277, 0.1}, "battery_current": {278, 0.1},
		"battery_power": {279, 1}, "battery_soc": {280, 1}, "battery_temperature": {281, 1},
		"pv_total_power": {302, 1}, "pv_charging_power": {303, 1}, "pv1_voltage": {351, 0.1},
		"pv1_current": {352, 0.1}, "pv1_power": {353, 1}, "pv2_voltage": {389, 0.1},
		"pv2_current": {390, 0.1}, "pv2_power": {391, 1}, "grid_voltage": {338, 0.1},
		"grid_power": {340, 1}, "grid_frequency": {607, 1}, "output_voltage": {346, 0.1},
		"output_current": {347, 0.1}, "output_power": {348, 1}, "output_apparent_power": {349, 1},
		"output_load_percentage": {350, 1}, "output_frequency": {607, 1},
		"pv_energy_today": {702, 0.01}, "pv_energy_total": {703, 0.01},
	},
}

type value struct {
	v     float64
	scale float64
}

// decode scales raw registers by name. Like the Python backend, battery values are
// only produced when all five battery registers were read.
func decode(model map[string]reg, raw map[int]int16) map[string]value {
	out := map[string]value{}
	for name, rc := range model {
		if v, ok := raw[rc.addr]; ok {
			out[name] = value{float64(v) * rc.scale, rc.scale}
		}
	}
	for _, k := range []string{"battery_voltage", "battery_current", "battery_power", "battery_soc", "battery_temperature"} {
		if _, ok := out[k]; !ok {
			for _, d := range []string{"battery_voltage", "battery_current", "battery_power", "battery_soc", "battery_temperature"} {
				delete(out, d)
			}
			break
		}
	}
	return out
}

func fmtValue(v value) string {
	switch {
	case v.scale >= 1:
		return strconv.FormatFloat(v.v, 'f', 0, 64)
	case v.scale >= 0.1:
		return strconv.FormatFloat(v.v, 'f', 1, 64)
	default:
		return strconv.FormatFloat(v.v, 'f', 2, 64)
	}
}

func modeName(v int) string {
	switch v {
	case 2:
		return "SUB"
	case 3:
		return "SBU"
	}
	return fmt.Sprintf("UNKNOWN (%d)", v)
}

// Sensor table: same ids/names/units as backend/mqtt_manager.py SENSOR_DEFS, so
// Home Assistant entities stay compatible with the Python backend.
// src: "reg:<name>" | "freq:<name>" (centi-Hz) | "mode" | "bms:<field>"
type sensor struct{ id, name, unit, devClass, stateClass, icon, src, category string }

var sensors = []sensor{
	{"battery_soc", "Battery SOC", "%", "battery", "measurement", "mdi:battery", "reg:battery_soc", ""},
	{"battery_power", "Battery Power", "W", "power", "measurement", "mdi:battery-charging", "reg:battery_power", ""},
	{"soft_bms_soc", "SoftBMS SOC", "%", "battery", "measurement", "mdi:battery-heart-variant", "bms:soc_pct", ""},
	{"pv_total_power", "PV Total Power", "W", "power", "measurement", "mdi:solar-power", "reg:pv_total_power", ""},
	{"pv_energy_today", "PV Energy Today", "kWh", "energy", "total_increasing", "mdi:solar-power", "reg:pv_energy_today", ""},
	{"pv_energy_total", "PV Energy Total", "kWh", "energy", "total_increasing", "mdi:counter", "reg:pv_energy_total", ""},
	{"grid_power", "Grid Power", "W", "power", "measurement", "mdi:transmission-tower", "reg:grid_power", ""},
	{"output_power", "Output Power", "W", "power", "measurement", "mdi:power-plug", "reg:output_power", ""},
	{"output_load_pct", "Output Load", "%", "", "measurement", "mdi:gauge", "reg:output_load_percentage", ""},
	{"operating_mode", "Operating Mode", "", "", "", "mdi:solar-panel-large", "mode", ""},

	{"battery_voltage", "Battery Voltage", "V", "voltage", "measurement", "mdi:battery", "reg:battery_voltage", "diagnostic"},
	{"battery_current", "Battery Current", "A", "current", "measurement", "mdi:current-dc", "reg:battery_current", "diagnostic"},
	{"battery_temperature", "Battery Temperature", "°C", "temperature", "measurement", "mdi:thermometer", "reg:battery_temperature", "diagnostic"},
	{"soft_bms_used", "SoftBMS Used Since Full", "Wh", "", "measurement", "mdi:battery-minus", "bms:used_since_full_wh", "diagnostic"},
	{"soft_bms_discharged", "SoftBMS Energy Out", "kWh", "energy", "total_increasing", "mdi:battery-arrow-down", "bms:discharged_kwh", "diagnostic"},
	{"soft_bms_charged", "SoftBMS Energy In", "kWh", "energy", "total_increasing", "mdi:battery-arrow-up", "bms:charged_kwh", "diagnostic"},
	{"soft_bms_bias", "SoftBMS Power Bias", "W", "power", "measurement", "mdi:delta", "bms:bias_w", "diagnostic"},
	{"soft_bms_mode", "SoftBMS Mode", "", "", "", "mdi:state-machine", "bms:mode", "diagnostic"},
	{"pv_charging_power", "PV Charging Power", "W", "power", "measurement", "mdi:solar-power", "reg:pv_charging_power", "diagnostic"},
	{"pv1_voltage", "PV1 Voltage", "V", "voltage", "measurement", "mdi:solar-panel", "reg:pv1_voltage", "diagnostic"},
	{"pv1_current", "PV1 Current", "A", "current", "measurement", "mdi:solar-panel", "reg:pv1_current", "diagnostic"},
	{"pv1_power", "PV1 Power", "W", "power", "measurement", "mdi:solar-panel", "reg:pv1_power", "diagnostic"},
	{"pv2_voltage", "PV2 Voltage", "V", "voltage", "measurement", "mdi:solar-panel", "reg:pv2_voltage", "diagnostic"},
	{"pv2_current", "PV2 Current", "A", "current", "measurement", "mdi:solar-panel", "reg:pv2_current", "diagnostic"},
	{"pv2_power", "PV2 Power", "W", "power", "measurement", "mdi:solar-panel", "reg:pv2_power", "diagnostic"},
	{"grid_voltage", "Grid Voltage", "V", "voltage", "measurement", "mdi:transmission-tower", "reg:grid_voltage", "diagnostic"},
	{"grid_frequency", "Grid Frequency", "Hz", "frequency", "measurement", "mdi:sine-wave", "freq:grid_frequency", "diagnostic"},
	{"output_voltage", "Output Voltage", "V", "voltage", "measurement", "mdi:power-plug", "reg:output_voltage", "diagnostic"},
	{"output_current", "Output Current", "A", "current", "measurement", "mdi:power-plug", "reg:output_current", "diagnostic"},
	{"output_apparent_power", "Output Apparent Power", "VA", "", "measurement", "mdi:power-plug", "reg:output_apparent_power", "diagnostic"},
	{"output_frequency", "Output Frequency", "Hz", "frequency", "measurement", "mdi:sine-wave", "freq:output_frequency", "diagnostic"},
}

// supported reports whether this model can ever produce the sensor.
func supported(s sensor, model map[string]reg) bool {
	for i := 0; i < len(s.src); i++ {
		if s.src[i] == ':' {
			switch s.src[:i] {
			case "reg", "freq":
				_, ok := model[s.src[i+1:]]
				return ok
			}
			return true
		}
	}
	_, ok := model["operation_mode"]
	return ok
}

// sensorValue returns the MQTT state payload for a sensor, if available.
func sensorValue(s sensor, dec map[string]value, bms map[string]string) (string, bool) {
	kind, name := s.src, ""
	for i := 0; i < len(s.src); i++ {
		if s.src[i] == ':' {
			kind, name = s.src[:i], s.src[i+1:]
			break
		}
	}
	switch kind {
	case "reg":
		if v, ok := dec[name]; ok {
			return fmtValue(v), true
		}
	case "freq":
		if v, ok := dec[name]; ok {
			return strconv.FormatFloat(v.v/100, 'f', 2, 64), true
		}
	case "mode":
		if v, ok := dec["operation_mode"]; ok {
			return modeName(int(v.v)), true
		}
	case "bms":
		if v, ok := bms[name]; ok {
			return v, true
		}
	}
	return "", false
}
