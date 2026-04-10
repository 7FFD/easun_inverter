#!/usr/bin/env python3
"""
Easun Inverter CLI — discover and monitor your inverter from the terminal.

Usage:
    python easun.py discover [--timeout SECONDS]
    python easun.py monitor  [--inverter-ip IP] [--local-ip IP] [--model MODEL]
                             [--interval SECONDS] [--once]
"""

import argparse
import asyncio
import signal
import socket
import sys
import time
from pathlib import Path

# Add backend to path so `easunpy` can be imported
SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from easunpy.async_isolar import AsyncISolar
from easunpy.models import MODEL_CONFIGS, BatteryData, PVData, GridData, OutputData, SystemStatus
from easunpy.modbusclient import create_request, decode_modbus_response

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def get_local_ip() -> str:
    """Best-effort detection of the local IP used to reach the network."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "0.0.0.0"


# ANSI colours
GREEN  = "\033[32m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
RED    = "\033[31m"
BOLD   = "\033[1m"
RESET  = "\033[0m"
DIM    = "\033[2m"


def coloured(text: str, colour: str) -> str:
    return f"{colour}{text}{RESET}"


# ──────────────────────────────────────────────────────────────────────────────
# Discover command
# ──────────────────────────────────────────────────────────────────────────────

DISCOVERY_MESSAGES = [
    "set>server=",
    "WIFIKIT-214028-READ",
    "HF-A11ASSISTHREAD",
    "AT+SEARCH=HF-LPB100",
]


def discover_inverter(timeout: int, verbose: bool = True) -> tuple[str, str] | None:
    """Discover inverter IP via UDP broadcast and return (inverter_ip, local_ip)."""
    if verbose:
        print(coloured(f"\n{'═'*50}", CYAN))
        print(coloured("  Easun Inverter — Discovery", BOLD + CYAN))
        print(coloured(f"{'═'*50}\n", CYAN))
        print(f"Broadcasting on 255.255.255.255:58899  (timeout {timeout}s)\n")

    deadline = time.time() + timeout
    attempt = 0

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(2)

        while time.time() < deadline:
            attempt += 1
            if verbose:
                print(coloured(f"[Attempt {attempt}]", DIM))

            for msg in DISCOVERY_MESSAGES:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                if verbose:
                    print(f"  → {msg}")
                try:
                    sock.sendto(msg.encode(), ("255.255.255.255", 58899))
                    listen_until = time.time() + min(2.0, remaining)
                    while time.time() < listen_until:
                        try:
                            data, addr = sock.recvfrom(1024)
                            inverter_ip = addr[0]
                            local_ip = get_local_ip()
                            if verbose:
                                print(coloured(f"\n✓ Found inverter at {inverter_ip}", GREEN + BOLD))
                                print(f"  Response : {data.decode(errors='ignore')}")
                                print(f"  Local IP : {local_ip}")
                            return inverter_ip, local_ip
                        except socket.timeout:
                            continue
                except Exception as e:
                    if verbose:
                        print(coloured(f"  Error: {e}", RED))

            wait = min(3.0, deadline - time.time())
            if wait > 0:
                if verbose:
                    print(DIM + f"  No response yet — retrying in {wait:.0f}s" + RESET)
                time.sleep(wait)

    return None


def cmd_discover(timeout: int):
    found = discover_inverter(timeout=timeout, verbose=True)
    if not found:
        print(coloured("\n✗ No inverter found within the timeout window.\n", RED))
        sys.exit(1)

    inverter_ip, local_ip = found
    print(coloured("\nQuick templates (with discovered IPs):", CYAN))
    print(f"  ./cli/easun.sh --inverter-ip {inverter_ip} --local-ip {local_ip} monitor --model ISOLAR_SMG_II_6K --once")
    print(f"  ./cli/easun.sh --inverter-ip {inverter_ip} --local-ip {local_ip} monitor --model ISOLAR_SMG_II_6K --interval 10")
    print(f"  ./cli/easun.sh --inverter-ip {inverter_ip} --local-ip {local_ip} read-registers 100 20\n")


def resolve_connection(explicit_inverter_ip: str | None, explicit_local_ip: str | None, timeout: int = 30) -> tuple[str, str]:
    """Use explicit IPs when both are provided; otherwise discover every run."""
    if explicit_inverter_ip and explicit_local_ip:
        return explicit_inverter_ip, explicit_local_ip

    print(coloured("\nNo explicit inverter/local IP pair provided. Running discovery...", CYAN))
    found = discover_inverter(timeout=timeout, verbose=True)
    if not found:
        print(coloured("\n✗ Auto-discovery failed. Provide both --inverter-ip and --local-ip.\n", RED))
        sys.exit(1)
    return found


def merged_explicit_ips(args) -> tuple[str | None, str | None]:
    """Accept IPs either globally (before subcommand) or on subcommand itself."""
    inverter_ip = args.global_inverter_ip or getattr(args, "inverter_ip", None)
    local_ip = args.global_local_ip or getattr(args, "local_ip", None)
    return inverter_ip, local_ip


# ──────────────────────────────────────────────────────────────────────────────
# Read-registers command
# ──────────────────────────────────────────────────────────────────────────────

MAX_REGISTERS_PER_REQUEST = 60  # safe Modbus limit


async def cmd_read_registers(
    inverter_ip: str,
    local_ip: str,
    start: int,
    count: int,
    fmt: str,
    raw: bool,
):
    from easunpy.async_modbusclient import AsyncModbusClient

    print(coloured(f"\n{'═'*60}", CYAN))
    print(coloured(f"  Easun — Raw registers  start={start}  count={count}  fmt={fmt}", BOLD + CYAN))
    print(coloured(f"{'═'*60}\n", CYAN))

    client = AsyncModbusClient(inverter_ip=inverter_ip, local_ip=local_ip)

    # Build batched requests (≤ MAX_REGISTERS_PER_REQUEST each)
    requests = []
    offsets  = []  # (batch_start, batch_count)
    remaining = count
    batch_start = start
    txn = 0x0772
    while remaining > 0:
        batch_count = min(remaining, MAX_REGISTERS_PER_REQUEST)
        req = create_request(txn, 0x0001, 0x00, 0x03, batch_start, batch_count)
        requests.append(req)
        offsets.append((batch_start, batch_count))
        txn = (txn + 1) & 0xFFFF
        batch_start += batch_count
        remaining   -= batch_count

    responses = await client.send_bulk(requests)

    # Collect all (address, value) pairs
    rows: list[tuple[int, int | float | None]] = []
    for (resp, (b_start, b_count)) in zip(responses, offsets):
        if resp is None:
            for i in range(b_count):
                rows.append((b_start + i, None))
            continue
        try:
            values = decode_modbus_response(resp, b_count, fmt)
            for i, v in enumerate(values):
                rows.append((b_start + i, v))
        except Exception as e:
            print(coloured(f"  ✗ Decode error for batch starting at {b_start}: {e}", RED))
            for i in range(b_count):
                rows.append((b_start + i, None))

    if raw:
        # Machine-readable: address<TAB>value
        for addr, val in rows:
            print(f"{addr}\t{val if val is not None else 'None'}")
    else:
        # Human-readable table
        col_w = 10
        header = f"  {'Addr':>5}  {'Dec':>{col_w}}  {'Hex':>6}  {'Signed':>{col_w}}"
        print(coloured(header, BOLD))
        print(coloured("  " + "─" * (len(header) - 2), DIM))
        for addr, val in rows:
            if val is None:
                print(f"  {addr:>5}  {coloured('no data', DIM)}")
            else:
                unsigned = val & 0xFFFF
                signed   = val if val < 32768 else val - 65536
                hex_str  = f"0x{unsigned:04X}"
                print(f"  {addr:>5}  {unsigned:>{col_w}}  {hex_str:>6}  {signed:>{col_w}}")

    print(coloured(f"\n  {len(rows)} register(s) read.\n", DIM))


# ──────────────────────────────────────────────────────────────────────────────
# Monitor command — display helpers
# ──────────────────────────────────────────────────────────────────────────────

NA = coloured("—", DIM)  # placeholder for missing values


def _f1(v) -> str:
    """Format a float to 1 decimal place, or '—' if None."""
    return f"{v:.1f}" if v is not None else NA


def _f2(v) -> str:
    """Format a float to 2 decimal places, or '—' if None."""
    return f"{v:.2f}" if v is not None else NA


def _i(v) -> str:
    """Format an int, or '—' if None."""
    return str(v) if v is not None else NA


def _bar(value, max_value: int = 100, width: int = 20) -> str:
    if value is None:
        return "░" * width
    filled = int(width * min(value, max_value) / max_value)
    return "█" * filled + "░" * (width - filled)


def _freq(raw) -> str:
    if raw is None:
        return NA
    return f"{raw / 100:.2f} Hz"


def _power_sign(w) -> str:
    if w is None:
        return NA
    if w > 0:
        return coloured(f"+{w} W", GREEN)
    if w < 0:
        return coloured(f"{w} W", RED)
    return f"{w} W"


def _div10(v) -> str:
    """Divide by 10 and format to 1 decimal (for kWh tenths), or '—'."""
    return f"{v / 10:.1f}" if v is not None else NA


def print_state(
    battery: BatteryData | None,
    pv: PVData | None,
    grid: GridData | None,
    output: OutputData | None,
    status: SystemStatus | None,
    timestamp: str,
):
    print(coloured(f"\n{'═'*54}", CYAN))
    print(coloured(f"  Easun Inverter — {timestamp}", BOLD + CYAN))
    print(coloured(f"{'═'*54}", CYAN))

    # ── System status ─────────────────────────────────────────
    if status:
        mode_str = status.operating_mode.name if status.operating_mode else "UNKNOWN"
        print(f"\n  {coloured('Mode', BOLD)}  {coloured(mode_str, YELLOW)}")

    # ── Solar ─────────────────────────────────────────────────
    print(f"\n  {coloured('☀  Solar', BOLD + YELLOW)}")
    if pv:
        print(f"     PV1   {_f1(pv.pv1_voltage)} V  ·  {_i(pv.pv1_current)} A  ·  {coloured(_i(pv.pv1_power) + ' W', YELLOW)}")
        print(f"     PV2   {_f1(pv.pv2_voltage)} V  ·  {_i(pv.pv2_current)} A  ·  {coloured(_i(pv.pv2_power) + ' W', YELLOW)}")
        print(f"     Total {coloured(_i(pv.total_power) + ' W', BOLD + YELLOW)}   charging {_i(pv.charging_power)} W")
        print(f"     Today {_div10(pv.pv_generated_today)} kWh   Total {_div10(pv.pv_generated_total)} kWh")
    else:
        print(f"     {coloured('no data', DIM)}")

    # ── Battery ───────────────────────────────────────────────
    print(f"\n  {coloured('🔋 Battery', BOLD + GREEN)}")
    if battery:
        soc = battery.soc if battery.soc is not None else 0
        soc_colour = GREEN if soc >= 50 else (YELLOW if soc >= 20 else RED)
        bar = _bar(soc)
        print(f"     SOC   [{coloured(bar, soc_colour)}] {_i(battery.soc)}%")
        print(f"           {_f1(battery.voltage)} V  ·  {_power_sign(battery.power)}")
        print(f"           Temp {_i(battery.temperature)}°C")
    else:
        print(f"     {coloured('no data', DIM)}")

    # ── Grid ──────────────────────────────────────────────────
    print(f"\n  {coloured('⚡ Grid', BOLD + CYAN)}")
    if grid:
        print(f"     {_f1(grid.voltage)} V  ·  {_freq(grid.frequency)}  ·  {_power_sign(grid.power)}")
    else:
        print(f"     {coloured('no data', DIM)}")

    # ── Output ────────────────────────────────────────────────
    print(f"\n  {coloured('🔌 Output', BOLD)}")
    if output:
        bar = _bar(output.load_percentage)
        print(f"     Load  [{bar}] {_i(output.load_percentage)}%")
        print(f"           {_f1(output.voltage)} V  ·  {_f1(output.current)} A  ·  {_i(output.power)} W  ·  {_freq(output.frequency)}")
    else:
        print(f"     {coloured('no data', DIM)}")

    print(coloured(f"\n{'─'*54}", DIM))


# ──────────────────────────────────────────────────────────────────────────────
# Monitor command
# ──────────────────────────────────────────────────────────────────────────────

async def _poll_once(inverter: AsyncISolar) -> tuple:
    return await inverter.get_all_data()


async def cmd_monitor(inverter_ip: str, local_ip: str, model: str, interval: int, once: bool):
    inverter = AsyncISolar(inverter_ip=inverter_ip, local_ip=local_ip, model=model)

    stop_event = asyncio.Event()

    def _handle_signal(*_):
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    try:
        # Hide cursor and clear screen once; then redraw from top each poll.
        sys.stdout.write("\033[?25l\033[2J\033[H")
        sys.stdout.flush()

        while not stop_event.is_set():
            ts = time.strftime("%Y-%m-%d %H:%M:%S")

            # Move to home and clear screen so each frame renders in-place.
            sys.stdout.write("\033[H\033[2J")
            print(coloured(f"{'═'*54}", CYAN))
            print(coloured("  Easun Inverter — Monitor", BOLD + CYAN))
            print(coloured(f"{'═'*54}", CYAN))
            print(f"  Inverter : {inverter_ip}")
            print(f"  Local IP : {local_ip}")
            print(f"  Model    : {model}")
            if not once:
                print(f"  Interval : {interval}s")
            print()

            try:
                battery, pv, grid, output, status = await _poll_once(inverter)
                print_state(battery, pv, grid, output, status, ts)
            except ConnectionError as e:
                print(coloured(f"\n  ✗ Connection error: {e}\n", RED))
                if once:
                    sys.exit(1)
            except Exception as e:
                print(coloured(f"\n  ✗ Unexpected error: {e}\n", RED))
                if once:
                    sys.exit(1)

            if not once:
                print(coloured("  Press Ctrl+C to stop", DIM))

            sys.stdout.flush()

            if once:
                break

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
    finally:
        # Always restore cursor visibility and leave terminal in a clean state.
        sys.stdout.write("\033[?25h\n")
        sys.stdout.flush()


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="easun",
        description="Easun inverter CLI — discover, monitor, and inspect raw registers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  easun.sh discover
  easun.sh discover --timeout 60
    easun.sh --inverter-ip 192.168.1.160 --local-ip 192.168.1.179 monitor --once
    easun.sh --inverter-ip 192.168.1.160 --local-ip 192.168.1.179 read-registers 100 20
  easun.sh monitor
  easun.sh monitor --once
  easun.sh monitor --inverter-ip 192.168.1.160 --local-ip 192.168.1.100 --model ISOLAR_SMG_II_6K
  easun.sh monitor --interval 10
  easun.sh read-registers 270 20
  easun.sh read-registers 270 20 --fmt UnsignedInt
  easun.sh read-registers 270 20 --raw
""",
    )
    parser.add_argument("--inverter-ip", dest="global_inverter_ip", metavar="IP",
                        help="Global inverter IP (set before command)")
    parser.add_argument("--local-ip", dest="global_local_ip", metavar="IP",
                        help="Global local IP (set before command)")
    sub = parser.add_subparsers(dest="command", required=True)

    # discover
    disc = sub.add_parser("discover", help="Broadcast to find the inverter on the LAN")
    disc.add_argument("--timeout", type=int, default=30, metavar="SECONDS",
                      help="How long to keep trying (default: 30)")

    # monitor
    mon = sub.add_parser("monitor", help="Connect and display live inverter data")
    mon.add_argument("--inverter-ip", metavar="IP",
                     help="Inverter IP (used only when --local-ip is also provided)")
    mon.add_argument("--local-ip", metavar="IP",
                     help="Local IP to listen on (used only when --inverter-ip is also provided)")
    mon.add_argument("--model", choices=list(MODEL_CONFIGS.keys()), required=True,
                     help="Inverter model (required)")
    mon.add_argument("--interval", type=int, default=20, metavar="SECONDS",
                     help="Polling interval in seconds (default: 20)")
    mon.add_argument("--once", action="store_true",
                     help="Poll once and exit")

    # read-registers
    rr = sub.add_parser("read-registers", help="Dump raw Modbus register values")
    rr.add_argument("start", type=int, metavar="START",
                    help="First register address to read")
    rr.add_argument("count", type=int, metavar="COUNT",
                    help="Number of registers to read")
    rr.add_argument("--inverter-ip", metavar="IP",
                    help="Inverter IP (used only when --local-ip is also provided)")
    rr.add_argument("--local-ip", metavar="IP",
                    help="Local IP (used only when --inverter-ip is also provided)")
    rr.add_argument("--fmt", choices=["Int", "UnsignedInt", "Float"], default="UnsignedInt",
                    help="Register decode format (default: UnsignedInt)")
    rr.add_argument("--raw", action="store_true",
                    help="Print tab-separated address/value pairs (machine-readable)")

    return parser


def main():
    parser = build_parser()
    args   = parser.parse_args()

    if args.command == "discover":
        cmd_discover(args.timeout)

    elif args.command == "monitor":
        inverter_ip, local_ip = merged_explicit_ips(args)
        inverter_ip, local_ip = resolve_connection(inverter_ip, local_ip)
        model = args.model

        asyncio.run(cmd_monitor(
            inverter_ip=inverter_ip,
            local_ip=local_ip,
            model=model,
            interval=args.interval,
            once=args.once,
        ))

    elif args.command == "read-registers":
        inverter_ip, local_ip = merged_explicit_ips(args)
        inverter_ip, local_ip = resolve_connection(inverter_ip, local_ip)

        if args.count < 1:
            print(coloured("✗ COUNT must be at least 1.\n", RED))
            sys.exit(1)

        asyncio.run(cmd_read_registers(
            inverter_ip=inverter_ip,
            local_ip=local_ip,
            start=args.start,
            count=args.count,
            fmt=args.fmt,
            raw=args.raw,
        ))


if __name__ == "__main__":
    main()
