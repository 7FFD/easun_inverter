// easun-bridge: reads Modbus registers from an Easun inverter and publishes them to
// MQTT: raw registers, decoded Home Assistant sensors (with discovery) and SoftBms.
// Static, dependency-free binary for tiny routers.
package main

import (
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"
)

type span struct{ start, count int }

// parseRanges turns "200-300,696-701,5" into inclusive [lo,hi] intervals.
func parseRanges(s string) ([][2]int, error) {
	var out [][2]int
	for _, part := range strings.Split(s, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		lo, hi := part, part
		if k := strings.Index(part, "-"); k >= 0 {
			lo, hi = part[:k], part[k+1:]
		}
		a, err := strconv.Atoi(lo)
		if err != nil {
			return nil, err
		}
		b, err := strconv.Atoi(hi)
		if err != nil || b < a || b > 65535 || a < 0 {
			return nil, strconv.ErrSyntax
		}
		out = append(out, [2]int{a, b})
	}
	return out, nil
}

// planReads merges intervals (gaps <= 10 are bridged, like easunpy) and splits them
// into requests of at most maxChunk registers.
func planReads(iv [][2]int, maxChunk int) []span {
	sort.Slice(iv, func(i, j int) bool { return iv[i][0] < iv[j][0] })
	var merged [][2]int
	for _, r := range iv {
		if n := len(merged); n > 0 && r[0] <= merged[n-1][1]+10 {
			if r[1] > merged[n-1][1] {
				merged[n-1][1] = r[1]
			}
			continue
		}
		merged = append(merged, r)
	}
	var out []span
	for _, r := range merged {
		for a := r[0]; a <= r[1]; {
			n := r[1] - a + 1
			if n > maxChunk {
				n = maxChunk
			}
			out = append(out, span{a, n})
			a += n
		}
	}
	return out
}

func main() {
	invIP := flag.String("inverter-ip", "", "inverter IP (required)")
	localIP := flag.String("local-ip", "", "this device's LAN IP, reachable by the inverter (required)")
	broker := flag.String("mqtt", "127.0.0.1:1883", "MQTT broker host:port")
	user := flag.String("mqtt-user", "", "MQTT username")
	pass := flag.String("mqtt-pass", "", "MQTT password")
	modelName := flag.String("model", "", "inverter model (ISOLAR_SMG_II_4K|6K|11K): enables decoded sensors, HA discovery and SoftBms")
	deviceID := flag.String("device-id", "easun_inverter", "HA device id; sensors publish to <device-id>/sensor/<id>")
	discPrefix := flag.String("discovery-prefix", "homeassistant", "Home Assistant MQTT discovery prefix")
	prefix := flag.String("topic", "easun/inverter", "prefix for raw register topics: <topic>/reg/<address>")
	regs := flag.String("regs", "", "also publish these raw registers, e.g. 200-300,696-701")
	chunk := flag.Int("chunk", 100, "max registers per Modbus request")
	interval := flag.Duration("interval", 20*time.Second, "poll interval")
	retain := flag.Bool("retain", false, "publish raw registers with the retain flag")
	once := flag.Bool("once", false, "read once, print values to stdout and exit (no MQTT)")
	useBms := flag.Bool("softbms", true, "run SoftBms battery estimator (needs -model)")
	bmsState := flag.String("bms-state", "/etc/easun-bridge-bms.json", "SoftBms state file ('' = don't persist)")
	bmsSave := flag.Duration("bms-save-interval", 30*time.Minute, "how often SoftBms state is written (also on anchor and exit); keep long to spare flash")
	bmsCap := flag.Float64("bms-capacity-wh", 0, "usable battery capacity in Wh (needed for SoftBMS SOC %)")
	bmsBias := flag.Float64("bms-bias-w", 190, "seed power-reading bias in W")
	bmsEff := flag.Float64("bms-efficiency", 0.95, "charge efficiency 0.5-1.0")
	bmsDisch := flag.Float64("bms-discharge-w", 20, "power below this (W) counts as discharge; use -50 for inverters that idle at 0/-38 W")
	bmsAuto := flag.Bool("bms-auto-calibrate", true, "re-estimate bias from completed cycles")
	flag.Parse()

	if *invIP == "" || *localIP == "" {
		flag.Usage()
		os.Exit(2)
	}
	var model map[string]reg
	if *modelName != "" {
		if model = models[*modelName]; model == nil {
			log.Fatalf("unknown -model %q", *modelName)
		}
	}
	raw, err := parseRanges(*regs)
	if err != nil {
		log.Fatalf("invalid -regs %q", *regs)
	}
	var want [][2]int
	for _, rc := range model {
		want = append(want, [2]int{rc.addr, rc.addr})
	}
	want = append(want, raw...)
	spans := planReads(want, *chunk)
	if len(spans) == 0 {
		log.Fatal("nothing to read: set -model and/or -regs")
	}
	isRaw := func(a int) bool {
		for _, r := range raw {
			if a >= r[0] && a <= r[1] {
				return true
			}
		}
		return false
	}

	var bms *SoftBms
	if model != nil && *useBms {
		path := *bmsState
		if *once {
			path = "" // a one-shot read must never touch the persisted state
		}
		bms = NewSoftBms(path, *bmsSave)
		flag.Visit(func(fl *flag.Flag) { // explicit flags override the state file's config
			switch fl.Name {
			case "bms-capacity-wh":
				if c := *bmsCap; c > 0 { // 0 = unspecified: keep what the state file has
					bms.cfg.CapacityWh = &c
				}
			case "bms-bias-w":
				bms.cfg.BiasW = *bmsBias
			case "bms-efficiency":
				bms.cfg.Efficiency = *bmsEff
			case "bms-discharge-w":
				bms.cfg.DischargeW = *bmsDisch
			case "bms-auto-calibrate":
				bms.cfg.AutoCalibrate = *bmsAuto
			}
		})
		if bms.cfg.CapacityWh == nil {
			log.Printf("softbms: no -bms-capacity-wh set, SoftBMS SOC will not be published")
		}
	}

	inv := NewInverter(*invIP, *localIP, 8899)
	start := time.Now()

	readAll := func() (map[int]int16, error) {
		vals := map[int]int16{}
		for _, s := range spans {
			v, err := inv.ReadRegisters(uint16(s.start), uint16(s.count))
			if err != nil {
				return nil, fmt.Errorf("read %d+%d: %w", s.start, s.count, err)
			}
			for k, x := range v {
				vals[s.start+k] = x
			}
			time.Sleep(100 * time.Millisecond)
		}
		return vals, nil
	}
	// process decodes a snapshot, feeds SoftBms, and returns the sensor + raw messages.
	process := func(vals map[int]int16) (msgs [][3]string) {
		dec := decode(model, vals)
		var bmsVals map[string]string
		if bms != nil {
			if bp, ok := dec["battery_power"]; ok {
				bmsVals = bms.Update(bp.v, time.Since(start).Seconds())
			}
		}
		if model != nil {
			for _, s := range sensors {
				if v, ok := sensorValue(s, dec, bmsVals); ok {
					msgs = append(msgs, [3]string{*deviceID + "/sensor/" + s.id, v, "0"})
				}
			}
		}
		var addrs []int
		for a := range vals {
			if isRaw(a) {
				addrs = append(addrs, a)
			}
		}
		sort.Ints(addrs)
		for _, a := range addrs {
			r := "0"
			if *retain {
				r = "1"
			}
			msgs = append(msgs, [3]string{*prefix + "/reg/" + strconv.Itoa(a), strconv.Itoa(int(vals[a])), r})
		}
		return msgs
	}

	if *once {
		vals, err := readAll()
		if err != nil {
			log.Fatal(err)
		}
		for _, m := range process(vals) {
			t := strings.TrimPrefix(strings.TrimPrefix(m[0], *deviceID+"/sensor/"), *prefix+"/reg/")
			fmt.Println(t, m[1])
		}
		inv.Close()
		return
	}

	statusTopic := *deviceID + "/status"
	mq := &MQTT{addr: *broker, user: *user, pass: *pass,
		clientID: "easun-bridge-" + strconv.Itoa(os.Getpid()), willTopic: statusTopic}
	discGen := 0

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)

	for {
		if vals, err := readAll(); err != nil {
			log.Print(err)
		} else {
			msgs := process(vals)
			err := mq.Publish(statusTopic, "online", true)
			if err == nil && model != nil && mq.Generation() != discGen {
				disc := discoveryMessages(*deviceID, *discPrefix, *modelName, model, bms != nil)
				for _, d := range disc {
					if err = mq.Publish(d[0], d[1], true); err != nil {
						break
					}
				}
				if err == nil {
					discGen = mq.Generation()
					log.Printf("published HA discovery for %d sensors", len(disc))
				}
			}
			for _, m := range msgs {
				if err != nil {
					break
				}
				err = mq.Publish(m[0], m[1], m[2] == "1")
			}
			if err != nil {
				log.Printf("mqtt: %v", err)
			} else {
				log.Printf("published %d values", len(msgs))
			}
		}
		select {
		case <-stop:
			if bms != nil {
				bms.Save()
			}
			mq.Publish(statusTopic, "offline", true)
			inv.Close()
			return
		case <-time.After(*interval):
		}
	}
}
