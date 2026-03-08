import asyncio
import base64
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

NZ_TZ = ZoneInfo("Pacific/Auckland")
START_TIME = time.time()


def slugify(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", (value or "").strip().lower())
    return re.sub(r"_+", "_", text).strip("_") or "host"
#
# Clone-and-modify setup:
# Edit these variables directly to match your environment.
# Keep secrets (API keys, OAuth secrets, SSH credentials) in .env only.
#
AGENT_NAME = "Percy"
AGENT_EMOJI = "🪸"
AGENT_TAGLINE = "An AI familiar clinging to the edge of what's possible"
AGENT_AVATAR = None  # Example: "static/avatar.png"

SPOTIFY_TOKEN_FILE = Path("~/.percy/credentials/spotify-tokens.json").expanduser()
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")

HUE_BRIDGE_IP = "10.0.0.20"
HUE_API_KEY = os.environ.get("HUE_API_KEY", "")
HUE_BASE = f"http://{HUE_BRIDGE_IP}/api/{HUE_API_KEY}" if HUE_API_KEY else ""
HUE_GROUPS = {"1": "Living Room", "2": "Bedroom"}

YAMAHA_BASE = "http://your-yamaha-ip/YamahaExtendedControl/v1"
OLLAMA_BASE = "http://127.0.0.1:11434"

NAS_NAME = "NAS"
NAS_TARGET = os.environ.get("NAS_SSH_TARGET", "")
NAS_MEDIA = {"Music": "/volume1/Music", "Movies": "/volume1/Movies", "Photos": "/volume1/Photos"}
NAS_STORAGE_PATHS = ["/volume1"]
NAS_MEDIA_SIZES = {"Music": 0, "Movies": 0, "Photos": 0}

BACKUP_LOG = Path("~/.percy/logs/backup.log").expanduser()
BACKUP_WARNING_HOURS = 24.0
BACKUP_CRITICAL_HOURS = 48.0

REMOTE_HOST_SSH = os.environ.get("REMOTE_HOST_SSH", "")
HOSTS: list[dict[str, Any]] = [
    {
        "name": "MORPHEUS",
        "emoji": "🖥️",
        "type": "local",
        "tab": True,
        "ollama": False,
        "show_cron": True,
        "projects_dir": "~/projects",
        "project_descriptions": {"openclaw-dashboard": "Dashboard source"},
    },
    {
        "name": "HYPNOS",
        "emoji": "🤖",
        "type": "ssh",
        "ssh_target": REMOTE_HOST_SSH,
        "ip": "10.0.0.40",
        "tab": True,
        "ollama": True,
        "show_cron": False,
        "projects_dir": "~/projects",
        "project_descriptions": {},
    },
]
for _host in HOSTS:
    _host.setdefault("slug", slugify(_host.get("name", "host")))

NETWORK_DEVICES = [
    {"name": "Primary Host", "ip": "10.0.0.10", "role": "Dashboard host"},
    {"name": "Remote Host", "ip": "10.0.0.40", "role": "LLM server"},
    {"name": NAS_NAME, "ip": "10.0.0.30", "role": "Storage"},
]

LOG_FILES = {
    "backup": {"path": str(Path("~/.percy/logs/backup.log").expanduser()), "label": "Backup", "tail_lines": 80},
    "bulletin-prep": {"path": str(Path("~/.percy/logs/bulletin-prep.log").expanduser()), "label": "Bulletin Prep", "tail_lines": 80},
    "bulletin-send": {"path": str(Path("~/.percy/logs/bulletin-send.log").expanduser()), "label": "Bulletin Send", "tail_lines": 80},
}

app = FastAPI(title=f"{AGENT_NAME} Dashboard")
app.mount("/static", StaticFiles(directory="static"), name="static")

state: dict[str, Any] = {"agent": {}, "spotify": {}, "hue": {}, "yamaha": {}, "ollama": {}, "backups": {}}
for host in HOSTS:
    state[f"host_{host['slug']}"] = {"status": "unknown", "name": host["name"], "message": "Waiting for telemetry"}

_cache: dict[str, tuple[float, Any]] = {}
_spotify_access_token: str | None = None
_spotify_token_expiry = 0.0
STATUS_MESSAGES = ["Monitoring telemetry streams", "Collecting host diagnostics", "Watching service health", "Keeping signals in sync"]


def cache_get(key: str, max_age: float) -> Any | None:
    item = _cache.get(key)
    if not item:
        return None
    ts, data = item
    return data if time.time() - ts < max_age else None


def cache_set(key: str, data: Any):
    _cache[key] = (time.time(), data)


async def ssh_command(target: str, command: str, timeout: int = 10) -> str | None:
    if not target:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no", target, command,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode(errors="replace").strip() if proc.returncode == 0 else None
    except Exception:
        return None


def run_local(cmd: list[str], timeout: int = 5) -> tuple[int, str]:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.returncode, (out.stdout + out.stderr).strip()
    except Exception:
        return 1, ""


def clean_version(raw: str) -> str:
    line = (raw or "").strip().splitlines()
    if not line:
        return "not found"
    value = line[0].strip()
    for pat in (r"^Python\s+", r"^git version\s+", r"^gh version\s+", r"^jq-\s*", r"^jq\s+", r"^uv\s+", r"^v"):
        value = re.sub(pat, "", value, flags=re.I)
    return value.split()[0] if " " in value else value


async def spotify_token() -> str | None:
    global _spotify_access_token, _spotify_token_expiry
    if _spotify_access_token and time.time() < _spotify_token_expiry - 60:
        return _spotify_access_token
    try:
        tokens = json.loads(SPOTIFY_TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    if tokens.get("access_token") and tokens.get("expires_at", 0) > time.time() + 60:
        _spotify_access_token = tokens["access_token"]
        _spotify_token_expiry = tokens["expires_at"]
        return _spotify_access_token
    if not tokens.get("refresh_token"):
        return None
    creds = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://accounts.spotify.com/api/token",
                headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"},
                data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            _spotify_access_token = data["access_token"]
            _spotify_token_expiry = time.time() + data.get("expires_in", 3600)
            SPOTIFY_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            SPOTIFY_TOKEN_FILE.write_text(json.dumps({
                "access_token": _spotify_access_token,
                "refresh_token": data.get("refresh_token", tokens["refresh_token"]),
                "expires_at": _spotify_token_expiry,
            }, indent=2), encoding="utf-8")
            return _spotify_access_token
    except Exception:
        return None


def parse_cron(lines: list[str]) -> list[dict[str, str]]:
    rows = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("@"):
            sch, cmd = (line.split(None, 1) + [""])[:2]
            desc = "On system reboot" if sch == "@reboot" else sch
        else:
            parts = line.split(None, 5)
            if len(parts) < 6:
                continue
            sch, cmd = " ".join(parts[:5]), parts[5]
            desc = sch
        rows.append({"schedule": sch, "command": cmd[:80], "description": desc})
    return rows


def collect_projects_local(path: str, descriptions: dict[str, str]) -> list[dict[str, str]]:
    base = Path(path).expanduser()
    if not base.exists():
        return []
    return [{"name": p.name, "emoji": "📁", "description": descriptions.get(p.name, "Project workspace")} for p in sorted(base.iterdir(), key=lambda p: p.name.casefold()) if p.is_dir()]


async def collect_projects_ssh(target: str, path: str, descriptions: dict[str, str]) -> list[dict[str, str]]:
    out = await ssh_command(target, f"if [ -d {shlex.quote(path)} ]; then ls -1 {shlex.quote(path)}; fi", timeout=10)
    if out is None:
        return []
    names = sorted([x.strip() for x in out.splitlines() if x.strip()], key=str.casefold)
    return [{"name": name, "emoji": "📁", "description": descriptions.get(name, "Project workspace")} for name in names]


async def collect_local_host(host: dict[str, Any]) -> dict[str, Any]:
    key = f"host_{host['slug']}"
    cached = cache_get(key, 60)
    if cached is not None:
        return cached
    mem_total, mem_avail = 0, 0
    try:
        mem = Path("/proc/meminfo").read_text(encoding="utf-8")
        mem_total = int(re.search(r"MemTotal:\s+(\d+)", mem).group(1))
        mem_avail = int(re.search(r"MemAvailable:\s+(\d+)", mem).group(1))
    except Exception:
        pass
    disk = shutil.disk_usage("/")
    rc, ip_out = run_local(["hostname", "-I"])
    ip = ip_out.split()[0] if rc == 0 and ip_out else "--"
    uptime = 0.0
    try:
        uptime = float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    except Exception:
        pass
    tools = {}
    for name, cmd in {"Python": ["python3", "--version"], "Node.js": ["node", "--version"], "Git": ["git", "--version"], "gh CLI": ["gh", "--version"], "jq": ["jq", "--version"], "uv": ["uv", "--version"]}.items():
        rc, out = run_local(cmd)
        tools[name] = clean_version(out) if rc == 0 else "not found"
    cron = []
    if host.get("show_cron"):
        rc, out = run_local(["crontab", "-l"])
        cron = parse_cron(out.splitlines()) if rc == 0 else []
    data = {
        "status": "online", "name": host["name"], "os": platform.platform(), "kernel": platform.release(),
        "cpu": platform.processor() or "unknown", "ip": ip, "uptime_seconds": uptime,
        "ram_total_gb": round(mem_total / (1024 * 1024), 1) if mem_total else 0.0,
        "ram_used_gb": round((mem_total - mem_avail) / (1024 * 1024), 1) if mem_total else 0.0,
        "ram_free_gb": round(mem_avail / (1024 * 1024), 1) if mem_avail else 0.0,
        "disk_total_gb": round(disk.total / (1024**3), 1), "disk_used_gb": round(disk.used / (1024**3), 1), "disk_free_gb": round(disk.free / (1024**3), 1),
        "tools": tools, "projects": collect_projects_local(host["projects_dir"], host["project_descriptions"]), "services": {}, "cron": cron,
        "message": f"{host['name']} is online",
    }
    cache_set(key, data)
    return data


async def collect_ssh_host(host: dict[str, Any]) -> dict[str, Any]:
    key = f"host_{host['slug']}"
    cached = cache_get(key, 60)
    if cached is not None:
        return cached
    target = host.get("ssh_target", "")
    if not target:
        return {"status": "offline", "name": host["name"], "message": "SSH target not configured"}
    cmd = (
        "echo OS=$(uname -s);echo KERNEL=$(uname -r);"
        "awk '/MemTotal/{print \"MEM_TOTAL=\"$2}/MemAvailable/{print \"MEM_AVAIL=\"$2}' /proc/meminfo;"
        "df -k / | tail -1 | awk '{print \"DISK_T=\"$2\"\\nDISK_U=\"$3\"\\nDISK_F=\"$4}';"
        "awk '{print \"UPTIME=\"$1}' /proc/uptime;"
        "echo TOOLS_PY=$(python3 --version 2>&1);echo TOOLS_NODE=$(node --version 2>&1);echo TOOLS_GIT=$(git --version 2>&1);"
    )
    out = await ssh_command(target, cmd, timeout=15)
    if out is None:
        return {"status": "offline", "name": host["name"], "message": f"{host['name']} is unreachable"}
    vals = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip()
    def num(k: str) -> float:
        try:
            return float(vals.get(k, 0))
        except Exception:
            return 0.0
    mt, ma = num("MEM_TOTAL"), num("MEM_AVAIL")
    dt, du, dfree = num("DISK_T"), num("DISK_U"), num("DISK_F")
    cron = []
    if host.get("show_cron"):
        cron_out = await ssh_command(target, "crontab -l", timeout=8)
        cron = parse_cron(cron_out.splitlines()) if cron_out else []
    data = {
        "status": "online", "name": host["name"], "os": vals.get("OS", "Linux"), "kernel": vals.get("KERNEL", ""),
        "cpu": "unknown", "ip": host.get("ip", "--"), "uptime_seconds": num("UPTIME"),
        "ram_total_gb": round(mt / (1024 * 1024), 1) if mt else 0.0, "ram_used_gb": round((mt - ma) / (1024 * 1024), 1) if mt else 0.0, "ram_free_gb": round(ma / (1024 * 1024), 1) if ma else 0.0,
        "disk_total_gb": round(dt / (1024**2), 1) if dt else 0.0, "disk_used_gb": round(du / (1024**2), 1) if du else 0.0, "disk_free_gb": round(dfree / (1024**2), 1) if dfree else 0.0,
        "tools": {"Python": clean_version(vals.get("TOOLS_PY", "")), "Node.js": clean_version(vals.get("TOOLS_NODE", "")), "Git": clean_version(vals.get("TOOLS_GIT", ""))},
        "projects": await collect_projects_ssh(target, host["projects_dir"], host["project_descriptions"]), "services": {}, "cron": cron,
        "message": f"{host['name']} is online",
    }
    cache_set(key, data)
    return data


async def poll_spotify():
    while True:
        try:
            token = await spotify_token()
            if not token:
                state["spotify"] = {"status": "no_token", "message": "Spotify credentials unavailable"}
            else:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get("https://api.spotify.com/v1/me/player", headers={"Authorization": f"Bearer {token}"})
                    if resp.status_code in (202, 204):
                        state["spotify"] = {"status": "idle", "message": "Nothing playing"}
                    elif resp.status_code == 200:
                        data = resp.json()
                        item = data.get("item", {})
                        artists = ", ".join(a["name"] for a in item.get("artists", []))
                        imgs = item.get("album", {}).get("images", [])
                        state["spotify"] = {
                            "status": "playing" if data.get("is_playing", False) else "paused",
                            "track": item.get("name", "Unknown"), "artist": artists, "album": item.get("album", {}).get("name", ""),
                            "art_url": imgs[0]["url"] if imgs else None, "progress_ms": data.get("progress_ms", 0), "duration_ms": item.get("duration_ms", 0),
                            "message": f"{item.get('name', 'Unknown')} — {artists}" if artists else item.get("name", "Unknown"),
                        }
                    else:
                        state["spotify"] = {"status": "error", "message": f"Spotify request failed ({resp.status_code})"}
        except Exception:
            state["spotify"] = {"status": "error", "message": "Spotify unavailable"}
        await asyncio.sleep(5)


async def poll_hue():
    while True:
        if not HUE_BASE:
            state["hue"] = {"status": "offline", "rooms": {}, "message": "Set HUE_BASE in app.py"}
            await asyncio.sleep(10)
            continue
        rooms = {}
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                for gid, name in HUE_GROUPS.items():
                    try:
                        resp = await client.get(f"{HUE_BASE}/groups/{gid}")
                        if resp.status_code == 200:
                            action = resp.json().get("action", {})
                            on = action.get("on", False)
                            rooms[str(gid)] = {"name": name, "on": on, "brightness": round(action.get("bri", 0) * 100 / 254) if on else 0, "hue": action.get("hue", 0), "sat": action.get("sat", 0), "ct": action.get("ct", 0), "colormode": action.get("colormode", "ct")}
                        else:
                            rooms[str(gid)] = {"name": name, "on": None, "error": True}
                    except Exception:
                        rooms[str(gid)] = {"name": name, "on": None, "error": True}
            state["hue"] = {"status": "ok", "rooms": rooms}
        except Exception:
            state["hue"] = {"status": "offline", "rooms": {}, "message": "Hue bridge unavailable"}
        await asyncio.sleep(10)


async def poll_yamaha():
    while True:
        if not YAMAHA_BASE:
            state["yamaha"] = {"status": "offline", "message": "Set YAMAHA_BASE in app.py"}
            await asyncio.sleep(10)
            continue
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{YAMAHA_BASE}/main/getStatus")
                if resp.status_code == 200:
                    data = resp.json()
                    state["yamaha"] = {"status": "online", "power": data.get("power", "unknown"), "volume": data.get("volume", 0), "max_volume": 80, "mute": data.get("mute", False), "input": data.get("input", "unknown"), "track": "", "artist": ""}
                else:
                    state["yamaha"] = {"status": "offline", "message": "Receiver unavailable"}
        except Exception:
            state["yamaha"] = {"status": "offline", "message": "Receiver unavailable"}
        await asyncio.sleep(10)


async def poll_ollama():
    while True:
        if not OLLAMA_BASE:
            state["ollama"] = {"status": "offline", "models": [], "running": [], "message": "Set OLLAMA_BASE in app.py"}
            await asyncio.sleep(30)
            continue
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                tags = await client.get(f"{OLLAMA_BASE}/api/tags")
                ps = await client.get(f"{OLLAMA_BASE}/api/ps")
            models = [{"name": m.get("name", "?"), "size": f"{round(m.get('size', 0)/(1024**3), 1)}GB", "size_bytes": m.get("size", 0), "modified": m.get("modified_at", "")} for m in tags.json().get("models", [])] if tags.status_code == 200 else []
            running = [m.get("name", "?") for m in ps.json().get("models", [])] if ps.status_code == 200 else []
            state["ollama"] = {"status": "online", "models": models, "running": running, "message": f"{len(models)} models installed"}
        except Exception:
            state["ollama"] = {"status": "offline", "models": [], "running": [], "message": "Ollama unavailable"}
        await asyncio.sleep(30)


async def poll_backups():
    while True:
        try:
            names = [h["name"] for h in HOSTS]
            if not BACKUP_LOG.exists():
                state["backups"] = {"status": "no_log", "hosts": {}, "message": "Backup log not found"}
                await asyncio.sleep(60)
                continue
            lines = BACKUP_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
            found = {name: None for name in names}
            for line in reversed(lines):
                ts_match = re.search(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?)", line)
                if not ts_match:
                    continue
                ts = None
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                    try:
                        ts = datetime.strptime(ts_match.group(1).replace("T", " "), fmt)
                        break
                    except ValueError:
                        pass
                if not ts:
                    continue
                ll = line.lower()
                for name in names:
                    if found[name] is None and name.lower() in ll:
                        found[name] = ts
                if names and all(found.values()):
                    break
            warning = BACKUP_WARNING_HOURS
            critical = BACKUP_CRITICAL_HOURS
            now = datetime.now()
            hosts = {}
            for name in names:
                ts = found[name]
                if not ts:
                    hosts[name] = {"last_backup": None, "health": "unknown", "message": "No backup record found"}
                else:
                    hours = (now - ts).total_seconds() / 3600
                    health = "green" if hours < warning else ("yellow" if hours < critical else "red")
                    hosts[name] = {"last_backup": ts.isoformat(), "hours_ago": round(hours, 1), "health": health, "message": f"Last backup {round(hours, 1)} hours ago"}
            state["backups"] = {"status": "ok", "hosts": hosts}
        except Exception:
            state["backups"] = {"status": "error", "hosts": {}, "message": "Backup polling failed"}
        await asyncio.sleep(60)


async def poll_host(host: dict[str, Any]):
    key = f"host_{host['slug']}"
    while True:
        try:
            state[key] = await (collect_ssh_host(host) if host.get("type") == "ssh" else collect_local_host(host))
        except Exception:
            state[key] = {"status": "offline", "name": host["name"], "message": f"{host['name']} telemetry unavailable"}
        await asyncio.sleep(60)


async def poll_agent():
    idx = 0
    while True:
        uptime_secs = time.time() - START_TIME
        uptime = f"{int(uptime_secs)} seconds" if uptime_secs < 60 else (f"{int(uptime_secs/60)} minutes" if uptime_secs < 3600 else (f"{int(uptime_secs/3600)}h {int((uptime_secs % 3600)/60)}m" if uptime_secs < 86400 else f"{int(uptime_secs/86400)}d {int((uptime_secs % 86400)/3600)}h"))
        now_nz = datetime.now(NZ_TZ)
        state["agent"] = {"status": "online", "uptime": uptime, "nz_time": now_nz.strftime("%I:%M %p, %A %-d %B"), "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}", "quip": STATUS_MESSAGES[idx % len(STATUS_MESSAGES)]}
        idx += 1
        await asyncio.sleep(60)


def safe_config() -> dict[str, Any]:
    return {
        "agent": {"name": AGENT_NAME, "emoji": AGENT_EMOJI, "tagline": AGENT_TAGLINE, "avatar": AGENT_AVATAR},
        "nas": {"name": NAS_NAME, "media_paths": list(NAS_MEDIA.keys())},
        "hosts": [{"name": h["name"], "slug": h["slug"], "emoji": h["emoji"], "type": h["type"], "tab": bool(h["tab"]), "ollama": bool(h["ollama"]), "show_cron": bool(h["show_cron"]), "project_descriptions": h["project_descriptions"]} for h in HOSTS if h.get("tab")],
        "network_devices": NETWORK_DEVICES,
        "log_files": {k: v.get("label", k) for k, v in LOG_FILES.items()},
    }


@app.on_event("startup")
async def startup():
    asyncio.create_task(poll_agent())
    asyncio.create_task(poll_spotify())
    asyncio.create_task(poll_hue())
    asyncio.create_task(poll_yamaha())
    asyncio.create_task(poll_ollama())
    asyncio.create_task(poll_backups())
    for host in HOSTS:
        if host.get("tab"):
            asyncio.create_task(poll_host(host))


@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse("static/index.html")


@app.get("/events")
async def events(request: Request):
    async def gen():
        while True:
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps(state, default=str)}\n\n"
            await asyncio.sleep(2)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


@app.get("/api/state")
async def api_state():
    return state


@app.get("/api/config")
async def api_config():
    return safe_config()


@app.get("/api/nas/media/{library}")
async def api_nas_media(library: str):
    path = NAS_MEDIA.get(library)
    if not path:
        return {"status": "error", "name": library, "items": [], "count": 0, "message": "Unknown media path"}
    key = f"nas_media_{library}"
    cached = cache_get(key, 3600)
    if cached is not None:
        return cached
    out = await ssh_command(NAS_TARGET, f"LC_ALL=C ls -1 {shlex.quote(path)}", timeout=12)
    if out is None:
        return {"status": "offline", "name": library, "items": [], "count": 0, "size_gb": NAS_MEDIA_SIZES.get(library, 0), "message": f"{NAS_NAME} offline"}
    items = sorted([x.strip() for x in out.splitlines() if x.strip()], key=str.casefold)
    payload = {"status": "online", "name": library, "items": items, "count": len(items), "size_gb": NAS_MEDIA_SIZES.get(library, 0), "message": f"{NAS_NAME} online"}
    cache_set(key, payload)
    return payload


@app.get("/api/nas/storage")
async def api_nas_storage():
    if not NAS_STORAGE_PATHS:
        return {"status": "error", "drives": [], "message": "No NAS storage paths configured"}
    cached = cache_get("nas_storage", 300)
    if cached is not None:
        return cached
    out = await ssh_command(NAS_TARGET, f"df -h {' '.join(shlex.quote(p) for p in NAS_STORAGE_PATHS)}", timeout=10)
    if out is None:
        return {"status": "offline", "drives": [], "message": f"{NAS_NAME} offline"}
    drives = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("Filesystem"):
            continue
        parts = line.split()
        if len(parts) < 6:
            continue
        try:
            pct = int(float(parts[4].replace("%", "")))
        except ValueError:
            pct = 0
        drives.append({"name": parts[-1].split("/")[-1] or parts[-1], "total_gb": size_to_gb(parts[1]), "used_gb": size_to_gb(parts[2]), "free_gb": size_to_gb(parts[3]), "pct": pct})
    payload = {"status": "online", "drives": drives, "message": f"{NAS_NAME} online"}
    cache_set("nas_storage", payload)
    return payload


def size_to_gb(size: str) -> float:
    m = re.match(r"^\s*([\d.]+)\s*([KMGTP]?)B?\s*$", size.strip(), flags=re.I)
    if not m:
        return 0.0
    n = float(m.group(1))
    u = m.group(2).upper()
    mult = {"": 1 / (1024**3), "K": 1 / (1024**2), "M": 1 / 1024, "G": 1, "T": 1024, "P": 1024 * 1024}
    return round(n * mult.get(u, 1), 1)


@app.get("/api/logs/{name}")
async def api_logs(name: str):
    info = LOG_FILES.get(name)
    if not info:
        return {"name": name, "status": "error", "lines": ["Unknown log requested"], "total_lines": 0, "message": "Unknown log name"}
    tail = int(info.get("tail_lines", 50))
    if info.get("path"):
        path = Path(info["path"])
        if not path.exists():
            return {"name": name, "status": "error", "lines": ["Log file not found"], "total_lines": 0, "message": "Log file not found"}
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return {"name": name, "status": "ok", "lines": lines[-tail:], "total_lines": len(lines), "label": info.get("label", name)}
    if info.get("ssh_target") and info.get("command"):
        out = await ssh_command(info["ssh_target"], info["command"], timeout=12)
        if out is None:
            return {"name": name, "status": "offline", "lines": ["Remote log unavailable"], "total_lines": 0, "message": "Remote log unavailable"}
        lines = out.splitlines()
        return {"name": name, "status": "ok", "lines": lines[-tail:], "total_lines": len(lines), "label": info.get("label", name)}
    return {"name": name, "status": "error", "lines": ["Log source not configured"], "total_lines": 0, "message": "Log source not configured"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8080, reload=False)
