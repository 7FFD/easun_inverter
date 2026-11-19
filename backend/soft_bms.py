"""
SoftBms — software battery management / state-of-charge estimator.

The inverter has no CAN link to the battery, so its SOC is voltage-derived and
useless for lithium chemistries. SoftBms instead integrates the battery power
reading over time (coulomb-counting style) and re-anchors to 100 % every time
the charger finishes its bulk/absorb phase and drops into float.

Observations from real inverter history that drive the design:

* In float the inverter reports a constant +75…150 W (mean ~108 W) even though
  the battery is full. That is NOT stored energy and must be ignored.
* Over full float→discharge→charge→float cycles the measured net energy is
  consistently positive (~+180…200 W × cycle duration). The battery returns to
  the same SOC, so this is a roughly constant measurement bias. SoftBms
  subtracts it from the power reading while the battery is not pinned in float,
  and re-estimates it from every completed cycle.
* Readings are quantised in ~37 W steps, so thresholds below ~40 W are noise.

Model
-----
mode "float_full"  battery is known full; nothing is accumulated.
mode "active"      battery is charging/discharging; the bias-corrected power
                   ``P - bias`` is integrated into ``deficit_wh`` (Wh taken out
                   since last full), with charge weighted by ``efficiency``.

Phases (from the raw power reading, confirmed over 2 samples):
    C  charge     P >= 150 W   (entering from float_full needs P >= 300 W)
    F  float band 20 <= P < 150 W
    D  discharge  P < discharge_enter_w (default 20 W; -50 W for inverters that idle at 0/-38 W)

Anchor: C → F (after a charge that peaked >= 300 W) means the charger reached
float, so deficit is reset to 0 and mode becomes float_full. Known limitation:
a charge that is cut short (e.g. a cloud) and decays into the float band looks
the same and will anchor early.
"""
import json
import logging
import os
import statistics
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", str(Path(__file__).parent / "config")))
SOFT_BMS_PATH = _CONFIG_DIR / "soft_bms.json"

CHARGE_PHASE_W = 150.0       # P >= this → charge phase (while active)
CHARGE_ENTER_W = 300.0       # P >= this leaves float_full / counts as a real charge peak
DISCHARGE_ENTER_W = 20.0     # P < this → discharge phase
CONFIRM_SAMPLES = 2          # consecutive samples needed to change phase
MAX_GAP_S = 90.0             # longer gaps between samples are not integrated
MIN_CYCLE_H = 1.0            # shortest cycle usable for bias calibration
MIN_CYCLE_CHARGE_WH = 100.0  # least measured charge usable for bias calibration
BIAS_RANGE_W = (0.0, 500.0)  # calibrated bias outside this is rejected as bogus
BIAS_HISTORY = 10            # cycles kept for the rolling median
PERSIST_EVERY_S = 60.0


@dataclass
class SoftBmsConfig:
    capacity_wh: Optional[float] = None  # usable capacity; needed for SOC %
    bias_w: float = 190.0                # seed power-reading bias (see module docstring)
    efficiency: float = 0.95             # charge efficiency
    auto_calibrate: bool = True          # re-estimate bias from completed cycles
    # P below this is "discharge". Inverter-specific: some units idle at 0/-38 W (one
    # quantisation step) while full, so they need e.g. -50 or the idle flicker reads as discharge.
    discharge_enter_w: float = DISCHARGE_ENTER_W


def _fresh_state() -> dict:
    return {
        "mode": "unknown",          # unknown | float_full | active
        "phase": None,              # C | F | D (confirmed)
        "anchored": False,          # True once we have seen "full" (or manual set)
        "deficit_wh": 0.0,          # Wh taken from battery since last full
        "discharged_wh": 0.0,       # lifetime, bias-corrected
        "charged_wh": 0.0,          # lifetime, bias-corrected
        "raw_discharged_wh": 0.0,   # lifetime, as measured (P < 0 only)
        "charge_peak_w": 0.0,
        "bias_history": [],
        "anchors": 0,
        "last_anchor": None,
        "cycle": {"active": False, "valid": False, "net_raw_wh": 0.0, "chg_raw_wh": 0.0, "hours": 0.0},
    }


class SoftBms:
    def __init__(self, path: Path = SOFT_BMS_PATH) -> None:
        self._path = path
        self._lock = threading.RLock()
        self.cfg = SoftBmsConfig()
        self.s = _fresh_state()
        # transient, never persisted
        self._last_t: Optional[float] = None
        self._last_p: Optional[float] = None
        self._cand: Optional[str] = None
        self._cand_n = 0
        self._last_persist = 0.0
        self._load()

    # ── persistence ────────────────────────────────────────────────────
    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text())
        except FileNotFoundError:
            return
        except Exception as e:
            logger.warning(f"SoftBms: could not read {self._path}: {e}")
            return
        try:
            cfg = data.get("config", {})
            self.cfg = SoftBmsConfig(**{k: v for k, v in cfg.items() if k in SoftBmsConfig.__dataclass_fields__})
            state = _fresh_state()
            saved = data.get("state", {})
            for k in state:
                if k in saved:
                    state[k] = saved[k]
            self.s = state
            # We were mid-cycle across a restart: the gap makes that cycle useless for calibration.
            if self.s["mode"] == "active":
                self.s["cycle"]["valid"] = False
            logger.info(f"SoftBms: restored state (mode={self.s['mode']}, deficit={self.s['deficit_wh']:.0f} Wh)")
        except Exception as e:
            logger.warning(f"SoftBms: ignoring corrupt state file: {e}")
            self.cfg, self.s = SoftBmsConfig(), _fresh_state()

    def save(self) -> None:
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text(json.dumps({"config": asdict(self.cfg), "state": self.s}, indent=2))
                os.replace(tmp, self._path)
                self._last_persist = time.monotonic()
            except Exception as e:
                logger.warning(f"SoftBms: could not persist state: {e}")

    # ── configuration / manual control ─────────────────────────────────
    @property
    def bias_w(self) -> float:
        if not self.cfg.auto_calibrate or not self.s["bias_history"]:
            return self.cfg.bias_w
        return float(statistics.median([self.cfg.bias_w] + list(self.s["bias_history"])))

    def configure(self, capacity_wh=None, bias_w=None, efficiency=None, auto_calibrate=None,
                  discharge_enter_w=None) -> dict:
        with self._lock:
            if capacity_wh is not None:
                self.cfg.capacity_wh = float(capacity_wh) if capacity_wh > 0 else None
            if bias_w is not None:
                self.cfg.bias_w = max(BIAS_RANGE_W[0], min(BIAS_RANGE_W[1], float(bias_w)))
                self.s["bias_history"] = []  # a manual value restarts calibration
            if efficiency is not None:
                self.cfg.efficiency = max(0.5, min(1.0, float(efficiency)))
            if auto_calibrate is not None:
                self.cfg.auto_calibrate = bool(auto_calibrate)
            if discharge_enter_w is not None:
                self.cfg.discharge_enter_w = max(-200.0, min(100.0, float(discharge_enter_w)))
            self.save()
            return self.status()

    def set_full(self) -> dict:
        """Manually declare the battery full (no bias calibration)."""
        with self._lock:
            self._enter_full(calibrate=False)
            self.save()
            return self.status()

    # ── core ───────────────────────────────────────────────────────────
    def update(self, power_w: float, now: Optional[float] = None) -> dict:
        """Feed one battery power sample (W, negative = discharging). Returns a snapshot."""
        with self._lock:
            now = time.monotonic() if now is None else now
            p = float(power_w)
            s = self.s

            if self._last_t is not None and self._last_p is not None:
                dt = now - self._last_t
                if 0 < dt <= MAX_GAP_S:
                    self._integrate(self._last_p, p, dt / 3600.0)
                elif dt > MAX_GAP_S and s["cycle"]["active"]:
                    s["cycle"]["valid"] = False
                    logger.info(f"SoftBms: {dt:.0f}s gap — current cycle excluded from calibration")
            elif s["cycle"]["active"]:
                s["cycle"]["valid"] = False  # first sample after a restart

            self._last_t, self._last_p = now, p
            self._update_phase(p)

            if now - self._last_persist >= PERSIST_EVERY_S:
                self.save()
            return self.snapshot(p)

    def _integrate(self, p0: float, p1: float, hours: float) -> None:
        s = self.s
        pav = (p0 + p1) / 2.0  # trapezoid
        if pav < 0:
            s["raw_discharged_wh"] += -pav * hours

        if s["mode"] != "active":
            return  # float_full (pinned at 100 %) and unknown accumulate nothing

        pc = pav - self.bias_w
        if pav >= 0 and pc < 0:
            pc = 0.0  # the inverter says the battery is not discharging: the bias must never turn that into drain
        if pc < 0:
            wh = -pc * hours
            s["discharged_wh"] += wh
            s["deficit_wh"] += wh
        else:
            wh = pc * hours
            s["charged_wh"] += wh
            s["deficit_wh"] = max(0.0, s["deficit_wh"] - self.cfg.efficiency * wh)

        c = s["cycle"]
        if c["active"]:
            c["net_raw_wh"] += pav * hours
            c["hours"] += hours
            if pav >= CHARGE_PHASE_W:
                c["chg_raw_wh"] += pav * hours

    def _phase_of(self, p: float, mode: str) -> str:
        charge_at = CHARGE_ENTER_W if mode == "float_full" else CHARGE_PHASE_W
        if p >= charge_at:
            return "C"
        return "D" if p < self.cfg.discharge_enter_w else "F"

    def _update_phase(self, p: float) -> None:
        s = self.s
        phase = self._phase_of(p, s["mode"])

        if s["mode"] == "unknown":
            s["phase"] = phase
            if phase == "F":
                self._enter_full(calibrate=False, assume=True)
            else:
                s["mode"] = "active"  # unanchored until the first charge→float
                if phase == "C":
                    s["charge_peak_w"] = p
            return

        if phase == s["phase"]:
            self._cand, self._cand_n = None, 0
        else:
            if phase == self._cand:
                self._cand_n += 1
            else:
                self._cand, self._cand_n = phase, 1
            if self._cand_n >= CONFIRM_SAMPLES:
                self._on_phase_change(s["phase"], phase, p)
                s["phase"] = phase
                self._cand, self._cand_n = None, 0

        if s["phase"] == "C":
            s["charge_peak_w"] = max(s["charge_peak_w"], p)

    def _on_phase_change(self, old: str, new: str, p: float) -> None:
        s = self.s
        if s["mode"] == "float_full":
            s["mode"] = "active"
            s["charge_peak_w"] = p if new == "C" else 0.0
            if new == "D":  # float → discharge: a clean cycle we can calibrate from
                s["cycle"] = {"active": True, "valid": True, "net_raw_wh": 0.0, "chg_raw_wh": 0.0, "hours": 0.0}
                logger.info("SoftBms: discharge started from float")
            else:
                s["cycle"] = {"active": False, "valid": False, "net_raw_wh": 0.0, "chg_raw_wh": 0.0, "hours": 0.0}
            return

        # mode == active
        if new == "C":
            s["charge_peak_w"] = p
        elif new == "D":
            s["charge_peak_w"] = 0.0
        elif new == "F" and old == "C" and s["charge_peak_w"] >= CHARGE_ENTER_W:
            self._enter_full(calibrate=True)

    def _enter_full(self, calibrate: bool, assume: bool = False) -> None:
        s = self.s
        if calibrate:
            self._calibrate_from_cycle()
        s["mode"] = "float_full"
        s["phase"] = "F"
        s["anchored"] = True
        s["deficit_wh"] = 0.0
        s["charge_peak_w"] = 0.0
        s["cycle"] = {"active": False, "valid": False, "net_raw_wh": 0.0, "chg_raw_wh": 0.0, "hours": 0.0}
        if not assume:
            s["anchors"] += 1
            s["last_anchor"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            logger.info(f"SoftBms: anchored to 100 % (bias={self.bias_w:.0f} W)")
            self.save()

    def _calibrate_from_cycle(self) -> None:
        """Battery is full at both ends of the cycle, so net energy ≈ -(1-η)·charged.
        Whatever remains is bias × duration."""
        s, c = self.s, self.s["cycle"]
        if not (c["active"] and c["valid"]) or c["hours"] < MIN_CYCLE_H or c["chg_raw_wh"] < MIN_CYCLE_CHARGE_WH:
            return
        b = (c["net_raw_wh"] + (1.0 - self.cfg.efficiency) * c["chg_raw_wh"]) / c["hours"]
        if not (BIAS_RANGE_W[0] <= b <= BIAS_RANGE_W[1]):
            logger.warning(f"SoftBms: cycle implies bias {b:.0f} W — outside {BIAS_RANGE_W}, ignored")
            return
        s["bias_history"] = (list(s["bias_history"]) + [round(b, 1)])[-BIAS_HISTORY:]
        logger.info(
            f"SoftBms: cycle {c['hours']:.2f} h, net {c['net_raw_wh']:.0f} Wh, charged {c['chg_raw_wh']:.0f} Wh "
            f"→ bias {b:.0f} W (history {s['bias_history']}, now using {self.bias_w:.0f} W)"
        )

    # ── reporting ──────────────────────────────────────────────────────
    def snapshot(self, power_w: Optional[float] = None) -> dict:
        s, cap = self.s, self.cfg.capacity_wh
        soc = None
        if s["anchored"] and cap:
            soc = round(max(0.0, min(100.0, 100.0 * (1.0 - s["deficit_wh"] / cap))), 1)
        return {
            "mode": s["mode"],
            "phase": s["phase"],
            "anchored": s["anchored"],
            "soc_pct": soc,
            "used_since_full_wh": round(s["deficit_wh"], 1),
            "discharged_kwh": round(s["discharged_wh"] / 1000.0, 3),
            "charged_kwh": round(s["charged_wh"] / 1000.0, 3),
            "raw_discharged_kwh": round(s["raw_discharged_wh"] / 1000.0, 3),
            "bias_w": round(self.bias_w, 1),
            "calibrated_cycles": len(s["bias_history"]),
            "capacity_wh": cap,
        }

    def status(self) -> dict:
        with self._lock:
            return {
                **self.snapshot(),
                "config": asdict(self.cfg),
                "bias_history": list(self.s["bias_history"]),
                "anchors": self.s["anchors"],
                "last_anchor": self.s["last_anchor"],
            }


soft_bms = SoftBms()
