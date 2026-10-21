import json
import os
import re
import socket
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Locally this is home-dashboard/config/config.json.
# In Docker, CONFIG_PATH is set to /config/config.json (see Dockerfile).
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", BASE_DIR.parent / "config" / "config.json"))

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")

app = FastAPI(title="Home Dashboard")


def load_config() -> dict:
    """Read the config on every request, so edits take effect without a restart."""
    if not CONFIG_PATH.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Config file not found at {CONFIG_PATH}. "
                   "Copy config.example.json to config/config.json and edit it.",
        )
    try:
        # utf-8-sig tolerates the invisible BOM some Windows editors add
        with CONFIG_PATH.open(encoding="utf-8-sig") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"config.json is not valid JSON: {e}")


def get_device(cfg: dict, device_id: str) -> dict:
    for device in cfg.get("devices", []):
        if device.get("id") == device_id:
            return device
    raise HTTPException(status_code=404, detail=f"No device with id '{device_id}' in config")


def send_magic_packet(mac: str, broadcast: str, port: int) -> None:
    """A magic packet is 6 bytes of 0xFF followed by the MAC address repeated 16 times."""
    mac_bytes = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    packet = b"\xff" * 6 + mac_bytes * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (broadcast, port))


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/devices")
def list_devices():
    cfg = load_config()
    return [
        {
            "id": d.get("id"),
            "name": d.get("name", d.get("id")),
            "ip": d.get("ip"),
            "mac": d.get("mac"),
        }
        for d in cfg.get("devices", [])
    ]


@app.post("/api/wake/{device_id}")
def wake(device_id: str):
    cfg = load_config()
    device = get_device(cfg, device_id)

    mac = device.get("mac", "")
    if not MAC_RE.match(mac):
        raise HTTPException(status_code=500, detail=f"Invalid MAC address for '{device_id}': {mac!r}")

    # A device can override the global broadcast address / port if needed
    broadcast = device.get("broadcast_ip", cfg.get("broadcast_ip", "255.255.255.255"))
    port = int(device.get("wol_port", cfg.get("wol_port", 9)))

    try:
        send_magic_packet(mac, broadcast, port)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to send packet: {e}")

    return {"status": "sent", "device": device.get("name", device_id), "mac": mac}


@app.get("/api/health")
def health():
    return {"status": "ok"}