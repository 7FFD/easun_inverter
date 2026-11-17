package main

import (
	"bufio"
	"fmt"
	"log"
	"math"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

// SoftBms: port of backend/soft_bms.py (see its docstring for the model).
// State file format is compatible with the Python one.
const (
	chargePhaseW     = 150.0
	chargeEnterW     = 300.0
	dischargeEnterW  = 20.0
	confirmSamples   = 2
	maxGapS          = 90.0
	minCycleH        = 1.0
	minCycleChargeWh = 100.0
	biasMin, biasMax = 0.0, 500.0
	biasHistory      = 10
)

type bmsConfig struct {
	CapacityWh    *float64
	BiasW         float64
	Efficiency    float64
	AutoCalibrate bool
}

type bmsCycle struct {
	Active   bool
	Valid    bool
	NetRawWh float64
	ChgRawWh float64
	Hours    float64
}

type bmsState struct {
	Mode          string
	Phase         string
	Anchored      bool
	DeficitWh     float64
	DischargedWh  float64
	ChargedWh     float64
	RawDischarged float64
	ChargePeakW   float64
	BiasHistory   []float64
	Anchors       int
	LastAnchor    string
	Cycle         bmsCycle
}

func freshState() bmsState { return bmsState{Mode: "unknown", BiasHistory: []float64{}} }

type SoftBms struct {
	mu           sync.Mutex
	path         string
	cfg          bmsConfig
	s            bmsState
	lastT, lastP float64
	haveLast     bool
	cand         string
	candN        int
	saveEvery    time.Duration
	lastPersist  time.Time
}

func NewSoftBms(path string, saveEvery time.Duration) *SoftBms {
	b := &SoftBms{path: path, saveEvery: saveEvery, lastPersist: time.Now(),
		cfg: bmsConfig{BiasW: 190, Efficiency: 0.95, AutoCalibrate: true}, s: freshState()}
	b.load()
	return b
}

// State file: one "key value" per line (plain text keeps the binary small).
func (b *SoftBms) load() {
	if b.path == "" {
		return
	}
	fh, err := os.Open(b.path)
	if err != nil {
		return
	}
	defer fh.Close()
	kv := map[string]string{}
	sc := bufio.NewScanner(fh)
	for sc.Scan() {
		if k, v, ok := strings.Cut(sc.Text(), " "); ok {
			kv[k] = v
		}
	}
	num := func(k string, dst *float64) {
		if x, err := strconv.ParseFloat(kv[k], 64); err == nil {
			*dst = x
		}
	}
	flag := func(k string, dst *bool) {
		if v, ok := kv[k]; ok {
			*dst = v == "1"
		}
	}
	if v, ok := kv["mode"]; ok {
		b.s.Mode = v
	}
	b.s.Phase = kv["phase"]
	b.s.LastAnchor = kv["last_anchor"]
	flag("anchored", &b.s.Anchored)
	num("deficit_wh", &b.s.DeficitWh)
	num("discharged_wh", &b.s.DischargedWh)
	num("charged_wh", &b.s.ChargedWh)
	num("raw_discharged_wh", &b.s.RawDischarged)
	num("charge_peak_w", &b.s.ChargePeakW)
	if n, err := strconv.Atoi(kv["anchors"]); err == nil {
		b.s.Anchors = n
	}
	for _, x := range strings.Split(kv["bias_history"], ",") {
		if v, err := strconv.ParseFloat(x, 64); err == nil {
			b.s.BiasHistory = append(b.s.BiasHistory, v)
		}
	}
	if x, err := strconv.ParseFloat(kv["cfg_capacity_wh"], 64); err == nil && x > 0 {
		b.cfg.CapacityWh = &x
	}
	num("cfg_bias_w", &b.cfg.BiasW)
	num("cfg_efficiency", &b.cfg.Efficiency)
	flag("cfg_auto_calibrate", &b.cfg.AutoCalibrate)
	// A restart mid-cycle makes that cycle useless for calibration, so the cycle is not restored.
	log.Printf("softbms: restored state (mode=%s, deficit=%.0f Wh)", b.s.Mode, b.s.DeficitWh)
}

func (b *SoftBms) Save() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.saveLocked()
}

func (b *SoftBms) saveLocked() {
	if b.path == "" {
		return
	}
	s, c := &b.s, &b.cfg
	one := func(v bool) string {
		if v {
			return "1"
		}
		return "0"
	}
	var sb strings.Builder
	w := func(k string, v any) { fmt.Fprintf(&sb, "%s %v\n", k, v) }
	w("mode", s.Mode)
	w("phase", s.Phase)
	w("anchored", one(s.Anchored))
	w("deficit_wh", f(s.DeficitWh))
	w("discharged_wh", f(s.DischargedWh))
	w("charged_wh", f(s.ChargedWh))
	w("raw_discharged_wh", f(s.RawDischarged))
	w("charge_peak_w", f(s.ChargePeakW))
	w("anchors", s.Anchors)
	w("last_anchor", s.LastAnchor)
	h := make([]string, len(s.BiasHistory))
	for i, v := range s.BiasHistory {
		h[i] = f(v)
	}
	w("bias_history", strings.Join(h, ","))
	if c.CapacityWh != nil {
		w("cfg_capacity_wh", f(*c.CapacityWh))
	}
	w("cfg_bias_w", f(c.BiasW))
	w("cfg_efficiency", f(c.Efficiency))
	w("cfg_auto_calibrate", one(c.AutoCalibrate))
	tmp := b.path + ".tmp"
	if err := os.WriteFile(tmp, []byte(sb.String()), 0o644); err != nil {
		log.Printf("softbms: persist failed: %v", err)
		return
	}
	if err := os.Rename(tmp, b.path); err != nil {
		log.Printf("softbms: persist failed: %v", err)
		return
	}
	b.lastPersist = time.Now()
}

func (b *SoftBms) biasW() float64 {
	if !b.cfg.AutoCalibrate || len(b.s.BiasHistory) == 0 {
		return b.cfg.BiasW
	}
	xs := append([]float64{b.cfg.BiasW}, b.s.BiasHistory...)
	sort.Float64s(xs)
	n := len(xs)
	if n%2 == 1 {
		return xs[n/2]
	}
	return (xs[n/2-1] + xs[n/2]) / 2
}

// Update feeds one battery power sample (W, negative = discharging); now is seconds (monotonic).
func (b *SoftBms) Update(p, now float64) map[string]string {
	b.mu.Lock()
	defer b.mu.Unlock()
	s := &b.s
	if b.haveLast {
		dt := now - b.lastT
		if dt > 0 && dt <= maxGapS {
			b.integrate(b.lastP, p, dt/3600)
		} else if dt > maxGapS && s.Cycle.Active {
			s.Cycle.Valid = false
			log.Printf("softbms: %.0fs gap — current cycle excluded from calibration", dt)
		}
	} else if s.Cycle.Active {
		s.Cycle.Valid = false
	}
	b.lastT, b.lastP, b.haveLast = now, p, true
	b.updatePhase(p)
	if b.saveEvery > 0 && time.Since(b.lastPersist) >= b.saveEvery {
		b.saveLocked()
	}
	return b.snapshotLocked()
}

func (b *SoftBms) integrate(p0, p1, hours float64) {
	s := &b.s
	pav := (p0 + p1) / 2
	if pav < 0 {
		s.RawDischarged += -pav * hours
	}
	if s.Mode != "active" {
		return
	}
	pc := pav - b.biasW()
	if pc < 0 {
		wh := -pc * hours
		s.DischargedWh += wh
		s.DeficitWh += wh
	} else {
		wh := pc * hours
		s.ChargedWh += wh
		s.DeficitWh = math.Max(0, s.DeficitWh-b.cfg.Efficiency*wh)
	}
	c := &s.Cycle
	if c.Active {
		c.NetRawWh += pav * hours
		c.Hours += hours
		if pav >= chargePhaseW {
			c.ChgRawWh += pav * hours
		}
	}
}

func phaseOf(p float64, mode string) string {
	at := chargePhaseW
	if mode == "float_full" {
		at = chargeEnterW
	}
	if p >= at {
		return "C"
	}
	if p < dischargeEnterW {
		return "D"
	}
	return "F"
}

func (b *SoftBms) updatePhase(p float64) {
	s := &b.s
	phase := phaseOf(p, s.Mode)
	if s.Mode == "unknown" {
		s.Phase = phase
		if phase == "F" {
			b.enterFull(false, true)
		} else {
			s.Mode = "active"
			if phase == "C" {
				s.ChargePeakW = p
			}
		}
		return
	}
	if phase == s.Phase {
		b.cand, b.candN = "", 0
	} else {
		if phase == b.cand {
			b.candN++
		} else {
			b.cand, b.candN = phase, 1
		}
		if b.candN >= confirmSamples {
			b.onPhaseChange(s.Phase, phase, p)
			s.Phase = phase
			b.cand, b.candN = "", 0
		}
	}
	if s.Phase == "C" {
		s.ChargePeakW = math.Max(s.ChargePeakW, p)
	}
}

func (b *SoftBms) onPhaseChange(old, new string, p float64) {
	s := &b.s
	if s.Mode == "float_full" {
		s.Mode = "active"
		s.ChargePeakW = 0
		if new == "C" {
			s.ChargePeakW = p
		}
		if new == "D" {
			s.Cycle = bmsCycle{Active: true, Valid: true}
			log.Printf("softbms: discharge started from float")
		} else {
			s.Cycle = bmsCycle{}
		}
		return
	}
	switch {
	case new == "C":
		s.ChargePeakW = p
	case new == "D":
		s.ChargePeakW = 0
	case new == "F" && old == "C" && s.ChargePeakW >= chargeEnterW:
		b.enterFull(true, false)
	}
}

func (b *SoftBms) enterFull(calibrate, assume bool) {
	s := &b.s
	if calibrate {
		b.calibrateFromCycle()
	}
	s.Mode, s.Phase, s.Anchored = "float_full", "F", true
	s.DeficitWh, s.ChargePeakW = 0, 0
	s.Cycle = bmsCycle{}
	if !assume {
		s.Anchors++
		s.LastAnchor = time.Now().UTC().Format("2006-01-02T15:04:05+00:00")
		log.Printf("softbms: anchored to 100%% (bias=%.0f W)", b.biasW())
		b.saveLocked()
	}
}

func (b *SoftBms) calibrateFromCycle() {
	s, c := &b.s, &b.s.Cycle
	if !(c.Active && c.Valid) || c.Hours < minCycleH || c.ChgRawWh < minCycleChargeWh {
		return
	}
	bias := (c.NetRawWh + (1-b.cfg.Efficiency)*c.ChgRawWh) / c.Hours
	if bias < biasMin || bias > biasMax {
		log.Printf("softbms: cycle implies bias %.0f W — outside range, ignored", bias)
		return
	}
	h := append(s.BiasHistory, math.Round(bias*10)/10)
	if len(h) > biasHistory {
		h = h[len(h)-biasHistory:]
	}
	s.BiasHistory = h
	log.Printf("softbms: cycle %.2f h net %.0f Wh charged %.0f Wh -> bias %.0f W (now using %.0f W)",
		c.Hours, c.NetRawWh, c.ChgRawWh, bias, b.biasW())
}

func round(v float64, d int) float64 { p := math.Pow(10, float64(d)); return math.Round(v*p) / p }

func f(v float64) string { return strconv.FormatFloat(v, 'f', -1, 64) }

// snapshot -> MQTT-ready strings. soc_pct is absent until anchored and capacity is known.
func (b *SoftBms) snapshotLocked() map[string]string {
	s := &b.s
	out := map[string]string{
		"mode":               s.Mode,
		"used_since_full_wh": f(round(s.DeficitWh, 1)),
		"discharged_kwh":     f(round(s.DischargedWh/1000, 3)),
		"charged_kwh":        f(round(s.ChargedWh/1000, 3)),
		"bias_w":             f(round(b.biasW(), 1)),
	}
	if s.Anchored && b.cfg.CapacityWh != nil && *b.cfg.CapacityWh > 0 {
		out["soc_pct"] = f(round(math.Max(0, math.Min(100, 100*(1 - s.DeficitWh / *b.cfg.CapacityWh))), 1))
	}
	return out
}
