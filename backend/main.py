import asyncio
import datetime
import logging
import os
import queue as thread_queue
import socket
import threading
import time
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Optional

from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from easunpy.utils import get_local_ip
from easunpy.models import MODEL_CONFIGS
from easunpy.async_isolar import AsyncISolar

from mqtt_manager import MQTTConfig, mqtt_manager

logging.basicConfig(level=logging.INFO)
logging.getLogger("easunpy").setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)

# ── Connection config persistence ──────────────────────────────────────────
import json
from pathlib import Path

CONN_CONFIG_PATH = Path(os.environ.get("CONFIG_DIR", str(Path(__file__).parent / "config"))) / "connection_config.json"


def load_connection_config() -> Optional[dict]:
    if not CONN_CONFIG_PATH.exists():
        return None
    try:
        return json.loads(CONN_CONFIG_PATH.read_text())
    except Exception:
        return None


def save_connection_config(inverter_ip: str, local_ip: str, model: str) -> None:
    CONN_CONFIG_PATH.write_text(json.dumps(
        {"inverter_ip": inverter_ip, "local_ip": local_ip, "model": model}, indent=2
    ))


# ── Background inverter poller ─────────────────────────────────────────────

POLL_INTERVAL = 20  # seconds

# Shared state: latest payload + set of WebSocket queues to fan out to
_latest_payload: Optional[dict] = None
_subscribers: set[asyncio.Queue] = set()


async def _poll_inverter(inverter_ip: str, local_ip: str, model: str) -> None:
    """Continuously poll the inverter and fan data out to all subscribers + MQTT."""
    global _latest_payload
    isolar = AsyncISolar(inverter_ip, local_ip, model)
    logger.info(f"Background poller started: inverter={inverter_ip} model={model}")

    while True:
        try:
            battery, pv, grid, output, status = await asyncio.wait_for(
                isolar.get_all_data(), timeout=90.0
            )
            if all(v is None for v in (battery, pv, grid, output, status)):
                raise ConnectionError("Inverter returned empty data")

            payload = {
                "battery": to_serializable(battery),
                "pv": to_serializable(pv),
                "grid": to_serializable(grid),
                "output": to_serializable(output),
                "status": to_serializable(status),
                "timestamp": datetime.datetime.now().isoformat(),
            }
            _latest_payload = payload

            for q in list(_subscribers):
                await q.put(payload)

            if mqtt_manager.connected:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, mqtt_manager.publish_data, payload)

        except asyncio.TimeoutError:
            logger.error("Inverter poll timed out after 90 s")
            err = {"error": "Inverter data timeout (90s)", "timestamp": datetime.datetime.now().isoformat()}
            for q in list(_subscribers):
                await q.put(err)
        except Exception as e:
            logger.exception("Inverter poll error")
            err = {"error": str(e), "timestamp": datetime.datetime.now().isoformat()}
            for q in list(_subscribers):
                await q.put(err)

        await asyncio.sleep(POLL_INTERVAL)


_poller_task: Optional[asyncio.Task] = None


def _start_poller(inverter_ip: str, local_ip: str, model: str) -> None:
    """Start (or restart) the background poller task."""
    global _poller_task, _latest_payload
    if _poller_task and not _poller_task.done():
        _poller_task.cancel()
    _latest_payload = None
    _poller_task = asyncio.ensure_future(_poll_inverter(inverter_ip, local_ip, model))


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Auto-connect MQTT
    saved_mqtt = MQTTConfig.load()
    if saved_mqtt:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, mqtt_manager.connect, saved_mqtt)
            await asyncio.sleep(1.5)
            logger.info(f"Auto-connected to MQTT broker {saved_mqtt.host}:{saved_mqtt.port}")
        except Exception:
            logger.warning("Auto-connect to MQTT failed — will retry on next manual connect")

    # Auto-start inverter poller
    saved_conn = load_connection_config()
    if saved_conn:
        _start_poller(saved_conn["inverter_ip"], saved_conn["local_ip"], saved_conn["model"])
        logger.info("Auto-started background inverter poller from saved config")

    yield

    if _poller_task and not _poller_task.done():
        _poller_task.cancel()


app = FastAPI(title="Easun Inverter API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def to_serializable(obj):
    """Recursively convert dataclasses, enums, and datetimes to JSON-safe types."""
    if obj is None:
        return None
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_serializable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    if isinstance(obj, (list, tuple)):
        return [to_serializable(i) for i in obj]
    if isinstance(obj, dict):
        return {k: to_serializable(v) for k, v in obj.items()}
    return obj


# ── Discovery WebSocket ────────────────────────────────────────────────────

DISCOVERY_PROBES = [
    ("set>server=",        "Easun WiFi protocol"),
    ("WIFIKIT-214028-READ","WiFi kit discovery"),
    ("HF-A11ASSISTHREAD",  "HF-A11 module"),
    ("AT+SEARCH=HF-LPB100","HF-LPB100 module"),
]


RETRY_PAUSE = 3  # seconds between full probe cycles


def _discover_with_updates(q: thread_queue.Queue, stop: threading.Event) -> None:
    """
    Repeatedly probe all UDP discovery messages until a device is found or
    stop is set. Posts status dicts to q.
    """
    local_ip = get_local_ip()
    cycle = 0

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(10)  # Overall timeout for each probe attempt

        while not stop.is_set():
            cycle += 1
            if cycle > 1:
                q.put({"type": "retry", "cycle": cycle})

            for message, label in DISCOVERY_PROBES:
                if stop.is_set():
                    break
                q.put({"type": "trying", "label": label})
                try:
                    sock.sendto(message.encode(), ("255.255.255.255", 58899))
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        if stop.is_set():
                            break
                        try:
                            _, addr = sock.recvfrom(1024)
                            q.put({"type": "found", "ip": addr[0], "local_ip": local_ip})
                            return
                        except socket.timeout:
                            break
                    if not stop.is_set():
                        q.put({"type": "timeout", "label": label})
                except Exception as e:
                    q.put({"type": "probe_error", "label": label, "message": str(e)})

            # Interruptible pause before next cycle
            deadline = time.monotonic() + RETRY_PAUSE
            while time.monotonic() < deadline and not stop.is_set():
                time.sleep(0.1)

    q.put({"type": "stopped"})


@app.websocket("/ws/discover")
async def ws_discover(websocket: WebSocket):
    """
    Stream UDP discovery progress to the client. Retries indefinitely.

    Sends JSON messages:
      {"type": "trying",      "label": "..."}
      {"type": "timeout",     "label": "..."}
      {"type": "probe_error", "label": "...", "message": "..."}
      {"type": "retry",       "cycle": N}
      {"type": "found",       "ip": "...", "local_ip": "..."}
      {"type": "stopped"}
    """
    await websocket.accept()
    q: thread_queue.Queue = thread_queue.Queue()
    stop = threading.Event()
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(None, _discover_with_updates, q, stop)

    try:
        while True:
            try:
                msg = q.get_nowait()
            except thread_queue.Empty:
                if future.done():
                    break
                await asyncio.sleep(0.05)
                continue

            await websocket.send_json(msg)
            if msg["type"] in ("found", "stopped"):
                break

        await future
    except WebSocketDisconnect:
        stop.set()
        await future
    except Exception:
        stop.set()
        logger.exception("Discovery WebSocket error")


@app.get("/api/models")
async def api_models():
    """Return list of supported inverter models."""
    return {"models": list(MODEL_CONFIGS.keys())}


# ── Connection config endpoints ────────────────────────────────────────────

@app.get("/api/connection-config")
async def api_get_connection_config():
    cfg = load_connection_config()
    return cfg if cfg else {}


class ConnectionConfigRequest(BaseModel):
    inverter_ip: str
    local_ip: str
    model: str


@app.post("/api/connection-config")
async def api_save_connection_config(req: ConnectionConfigRequest):
    save_connection_config(req.inverter_ip, req.local_ip, req.model)
    return {"ok": True}


@app.delete("/api/connection-config")
async def api_delete_connection_config():
    CONN_CONFIG_PATH.unlink(missing_ok=True)
    return {"ok": True}


# ── MQTT endpoints ─────────────────────────────────────────────────────────

class MQTTConnectRequest(BaseModel):
    host: str
    port: int = 1883
    username: Optional[str] = None
    password: Optional[str] = None
    discovery_prefix: str = "homeassistant"
    device_id: str = "easun_inverter"
    inverter_model: str = "ISOLAR_SMG_II"


@app.get("/api/mqtt/status")
async def api_mqtt_status():
    return mqtt_manager.status()


@app.post("/api/mqtt/connect")
async def api_mqtt_connect(req: MQTTConnectRequest):
    cfg = MQTTConfig(
        host=req.host,
        port=req.port,
        username=req.username or None,
        password=req.password or None,
        discovery_prefix=req.discovery_prefix,
        device_id=req.device_id,
        inverter_model=req.inverter_model,
    )
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, mqtt_manager.connect, cfg)
        # Give the broker a moment to confirm connection
        await asyncio.sleep(1.5)
        return mqtt_manager.status()
    except Exception as e:
        return {"connected": False, "error": str(e), "broker": f"{req.host}:{req.port}"}


@app.post("/api/mqtt/discovery")
async def api_mqtt_discovery():
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, mqtt_manager.publish_discovery)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/mqtt/disconnect")
async def api_mqtt_disconnect():
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, mqtt_manager.disconnect)
    return {"connected": False}


# ── WebSocket ──────────────────────────────────────────────────────────────

@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    """
    WebSocket endpoint for live inverter data.

    Client must send a JSON config message first:
      {"inverter_ip": "...", "local_ip": "...", "model": "..."}

    Then receives inverter data every 20 seconds.
    """
    await websocket.accept()
    q: asyncio.Queue = asyncio.Queue()
    try:
        config = await websocket.receive_json()
        inverter_ip: str = config["inverter_ip"]
        local_ip: str = config.get("local_ip") or get_local_ip()
        model: str = config["model"]

        logger.info(f"WS connect: inverter={inverter_ip} local={local_ip} model={model}")
        save_connection_config(inverter_ip, local_ip, model)

        # Start/restart poller if config changed or not running
        if _poller_task is None or _poller_task.done():
            _start_poller(inverter_ip, local_ip, model)

        # Subscribe to poller output
        _subscribers.add(q)

        # Send cached payload immediately so client doesn't wait a full cycle
        if _latest_payload:
            await websocket.send_json(_latest_payload)

        while True:
            payload = await q.get()
            await websocket.send_json(payload)

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception:
        logger.exception("WebSocket error")
    finally:
        _subscribers.discard(q)


# ── SPA static file serving (production / Docker) ─────────────────────────

DIST_DIR = Path(__file__).parent / "dist"

if DIST_DIR.exists():
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        candidate = DIST_DIR / full_path
        if candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(DIST_DIR / "index.html"))
