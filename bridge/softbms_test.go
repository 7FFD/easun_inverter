package main

import "testing"

// Expected values were produced by running the same scenario through backend/soft_bms.py.
func TestSoftBmsMatchesPython(t *testing.T) {
	b := NewSoftBms("", 0)
	cap := 5000.0
	b.cfg.CapacityWh = &cap
	type step struct {
		p, dur float64
		want   map[string]string
		hist   int
	}
	steps := []step{
		{100, 120, map[string]string{"mode": "float_full", "soc_pct": "100", "used_since_full_wh": "0", "bias_w": "190"}, 0},
		{-500, 1.5 * 3600, map[string]string{"mode": "active", "soc_pct": "79.8", "used_since_full_wh": "1009", "discharged_kwh": "1.009", "bias_w": "190"}, 0},
		{900, 1.3 * 3600, map[string]string{"mode": "active", "soc_pct": "97.6", "used_since_full_wh": "120.7", "charged_kwh": "0.935"}, 0},
		{110, 300, map[string]string{"mode": "float_full", "soc_pct": "100", "charged_kwh": "0.937", "bias_w": "187.7"}, 1},
		{-300, 600, map[string]string{"mode": "active", "soc_pct": "98.5", "used_since_full_wh": "74", "discharged_kwh": "1.083", "bias_w": "187.7"}, 1},
	}
	now := 0.0
	for i, st := range steps {
		var snap map[string]string
		for k := 0; k < int(st.dur/20); k++ {
			p := st.p
			if k%3 == 0 {
				p += 37
			}
			snap = b.Update(p, now)
			now += 20
		}
		for k, want := range st.want {
			if snap[k] != want {
				t.Errorf("step %d: %s = %q, want %q", i, k, snap[k], want)
			}
		}
		if len(b.s.BiasHistory) != st.hist {
			t.Errorf("step %d: bias history len %d, want %d", i, len(b.s.BiasHistory), st.hist)
		}
	}
	if b.s.BiasHistory[0] != 185.3 {
		t.Errorf("calibrated bias %v, want 185.3", b.s.BiasHistory[0])
	}
}

func TestSoftBmsPersistRoundTrip(t *testing.T) {
	path := t.TempDir() + "/bms.state"
	a := NewSoftBms(path, 0)
	cap := 4800.0
	a.cfg.CapacityWh = &cap
	a.s.Mode, a.s.Phase, a.s.Anchored = "active", "D", true
	a.s.DeficitWh, a.s.Anchors, a.s.BiasHistory = 321.5, 3, []float64{185.3, 190.1}
	a.Save()
	b := NewSoftBms(path, 0)
	if b.s.Mode != "active" || b.s.Phase != "D" || !b.s.Anchored || b.s.DeficitWh != 321.5 || b.s.Anchors != 3 ||
		len(b.s.BiasHistory) != 2 || b.s.BiasHistory[1] != 190.1 || b.cfg.CapacityWh == nil || *b.cfg.CapacityWh != 4800 {
		t.Errorf("round trip mismatch: %+v cfg %+v", b.s, b.cfg)
	}
}

func TestDiscoveryJSONIsValid(t *testing.T) {
	msgs := discoveryMessages("easun_x", "homeassistant", "ISOLAR_SMG_II_6K", models["ISOLAR_SMG_II_6K"], true)
	if len(msgs) != 26 {
		t.Fatalf("got %d messages", len(msgs))
	}
}
