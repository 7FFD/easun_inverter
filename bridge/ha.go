package main

import (
	"sort"
	"strings"
)

func jstr(s string) string {
	return `"` + strings.NewReplacer(`\`, `\\`, `"`, `\"`).Replace(s) + `"`
}

// discoveryMessages builds retained Home Assistant MQTT discovery configs. Entity ids,
// names and state topics match backend/mqtt_manager.py so existing HA entities carry over.
// JSON is written by hand: encoding/json would add ~1 MB to the binary.
func discoveryMessages(deviceID, prefix, modelName string, model map[string]reg, withBms bool) [][2]string {
	device := `{"identifiers":[` + jstr(deviceID) + `],"name":` + jstr("Easun Inverter ("+strings.TrimPrefix(deviceID, "easun_")+")") +
		`,"model":` + jstr(modelName) + `,"manufacturer":"Easun Power"}`
	var out [][2]string
	for _, s := range sensors {
		if !supported(s, model) || (!withBms && strings.HasPrefix(s.src, "bms:")) {
			continue
		}
		uid := deviceID + "_" + s.id
		kv := map[string]string{
			"name": s.name, "unique_id": uid, "object_id": uid,
			"state_topic": deviceID + "/sensor/" + s.id, "availability_topic": deviceID + "/status",
			"unit_of_measurement": s.unit, "device_class": s.devClass, "state_class": s.stateClass,
			"icon": s.icon, "entity_category": s.category,
		}
		keys := make([]string, 0, len(kv))
		for k, v := range kv {
			if v != "" {
				keys = append(keys, k)
			}
		}
		sort.Strings(keys)
		var b strings.Builder
		b.WriteString("{")
		for _, k := range keys {
			b.WriteString(jstr(k) + ":" + jstr(kv[k]) + ",")
		}
		b.WriteString(`"device":` + device + "}")
		out = append(out, [2]string{prefix + "/sensor/" + uid + "/config", b.String()})
	}
	return out
}
