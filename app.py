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
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

CONFIG_FILE = Path("config.yaml")
NZ_TZ = ZoneInfo("Pacific/Auckland")
START_TIME = time.time()

DEFAULT_CONFIG: dict[str, Any] = {
    "agent": {"name": "Agent", "emoji": "🤖", "tagline": "Monitoring system status", "avatar": None, "theme": "bioluminescent"},
    "spotify": {"enabled": False, "token_file": "~/.openclaw/credentials/spotify-tokens.json"},
    "hue": {"enabled": False, "base_url": "http://your-hue-bridge/api/your-api-key", "groups": {}},
    "yamaha": {"enabled": False, "base_url": "http://your-yamaha/YamahaExtendedControl/v1"},
    "ollama": {"enabled": False, "base_url": "http://localhost:11434"},
    "nas": {"enabled": False, "name": "NAS", "ssh_target": "", "media_paths": {}, "storage_paths": [], "media_sizes_gb": {}},
    "backups": {"enabled": False, "log_file": "~/.openclaw/logs/backup.log", "warning_hours": 24, "critical_hours": 48, "hosts": []},
    "logs": {"enabled": False, "files": {}},
    "hosts": [],
    "network_devices": [],
}
PANELS = ("spotify", "hue", "yamaha", "ollama", "nas", "backups", "logs")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def expand_env_vars(obj: Any) -> Any:
    if isinstance(obj, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), m.group(0)), obj)
    if isinstance(obj, dict):
        return {k: expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [expand_env_vars(v) for v in obj]
    return obj


def slugify(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", (value or "").strip().lower())
    return re.sub(r"_+", "_", text).strip("_") or "host"


def load_config() -> dict[str, Any]:
    user = {}
    if CONFIG_FILE.exists():
        user = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
    cfg = expand_env_vars(deep_merge(DEFAULT_CONFIG, user))

    cfg["spotify"]["token_file"] = str(Path(cfg["spotify"]["token_file"]).expanduser())
    cfg["backups"]["log_file"] = str(Path(cfg["backups"]["log_file"]).expanduser())
    if cfg.get("logs", {}).get("enabled"):
        for _, info in cfg["logs"].get("files", {}).items():
            if info.get("path"):
                info["path"] = str(Path(info["path"]).expanduser())

    hosts = []
    for host in cfg.get("hosts", []):
        if not isinstance(host, dict) or not host.get("name"):
            continue
        h = dict(host)
        h.setdefault("emoji", "🖥️")
        h.setdefault("type", "local")
        h.setdefault("tab", False)
        h.setdefault("ollama", False)
        h.setdefault("show_cron", False)
        h.setdefault("enabled", True)
        h.setdefault("projects_dir", "~/projects")
        h.setdefault("project_descriptions", {})
        h["slug"] = slugify(h["name"])
        hosts.append(h)
    cfg["hosts"] = hosts
    return cfg


CONFIG = load_config()
SPOTIFY_TOKEN_FILE = Path(CONFIG["spotify"]["token_file"])
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
HUE_BASE = CONFIG["hue"].get("base_url", "")
HUE_GROUPS = CONFIG["hue"].get("groups", {})
YAMAHA_BASE = CONFIG["yamaha"].get("base_url", "")
OLLAMA_BASE = CONFIG["ollama"].get("base_url", "")
NAS_NAME = CONFIG["nas"].get("name", "NAS")
NAS_TARGET = CONFIG["nas"].get("ssh_target", "")
NAS_MEDIA = CONFIG["nas"].get("media_paths", {})
NAS_STORAGE_PATHS = CONFIG["nas"].get("storage_paths", []) or list(NAS_MEDIA.values())
NAS_MEDIA_SIZES = CONFIG["nas"].get("media_sizes_gb", {})
BACKUP_LOG = Path(CONFIG["backups"]["log_file"])
LOG_FILES = CONFIG["logs"].get("files", {}) if CONFIG.get("logs", {}).get("enabled") else {}

app = FastAPI(title=f"{CONFIG['agent'].get('name', 'Agent')} Dashboard")
app.mount("/static", StaticFiles(directory="static"), name="static")

state: dict[str, Any] = {"agent": {}, "spotify": {}, "hue": {}, "yamaha": {}, "ollama": {}, "backups": {}}
for host in CONFIG.get("hosts", []):
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
            names = CONFIG["backups"].get("hosts") or [h["name"] for h in CONFIG.get("hosts", [])]
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
            warning = float(CONFIG["backups"].get("warning_hours", 24))
            critical = float(CONFIG["backups"].get("critical_hours", 48))
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
        "agent": {k: CONFIG["agent"].get(k) for k in ("name", "emoji", "tagline", "avatar", "theme")},
        "panels": {name: bool(CONFIG.get(name, {}).get("enabled", False)) for name in PANELS},
        "nas": {"name": NAS_NAME, "media_paths": list(NAS_MEDIA.keys())},
        "hosts": [{"name": h["name"], "slug": h["slug"], "emoji": h["emoji"], "type": h["type"], "tab": bool(h["tab"]), "ollama": bool(h["ollama"]), "show_cron": bool(h["show_cron"]), "project_descriptions": h["project_descriptions"]} for h in CONFIG.get("hosts", []) if h.get("enabled", True)],
        "network_devices": CONFIG.get("network_devices", []),
        "log_files": {k: v.get("label", k) for k, v in LOG_FILES.items()} if CONFIG.get("logs", {}).get("enabled") else {},
        "themes": ["bioluminescent", "midnight", "terminal", "minimal"],
    }


@app.on_event("startup")
async def startup():
    asyncio.create_task(poll_agent())
    if CONFIG.get("spotify", {}).get("enabled"):
        asyncio.create_task(poll_spotify())
    if CONFIG.get("hue", {}).get("enabled"):
        asyncio.create_task(poll_hue())
    if CONFIG.get("yamaha", {}).get("enabled"):
        asyncio.create_task(poll_yamaha())
    if CONFIG.get("ollama", {}).get("enabled"):
        asyncio.create_task(poll_ollama())
    if CONFIG.get("backups", {}).get("enabled"):
        asyncio.create_task(poll_backups())
    for host in CONFIG.get("hosts", []):
        if host.get("enabled", True) and host.get("tab"):
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
    if not CONFIG.get("nas", {}).get("enabled"):
        return {"status": "disabled", "name": library, "items": [], "count": 0, "message": "NAS disabled"}
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
    if not CONFIG.get("nas", {}).get("enabled"):
        return {"status": "disabled", "drives": [], "message": "NAS disabled"}
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
