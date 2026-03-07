"""
🪸 Percy Dashboard — Backend
Real-time status dashboard for Percy (Percebe).
"""

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Percy Dashboard")
app.mount("/static", StaticFiles(directory="static"), name="static")

START_TIME = time.time()

# Global state — updated by background pollers, read by SSE
state: dict = {
    "spotify": {},
    "yamaha": {},
    "hue": {},
    "backups": {},
    "hypnos": {},
    "hypnos_system": {},
    "morpheus": {},
    "bulletin": {},
    "cron": [],
    "percy": {},
}

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SPOTIFY_TOKEN_FILE = Path(os.environ.get("SPOTIFY_TOKEN_FILE", Path.home() / ".percy/credentials/spotify-tokens.json"))
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")

HUE_BASE = os.environ.get("HUE_BASE", "http://your-hue-bridge/api/your-api-key")
HUE_GROUPS = json.loads(os.environ.get("HUE_GROUPS", '{"1": "Room 1", "2": "Room 2"}'))

HYPNOS_BASE = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
YAMAHA_BASE = os.environ.get("YAMAHA_BASE", "http://your-yamaha/YamahaExtendedControl/v1")
GALACTICA_HOST = os.environ.get("NAS_SSH_TARGET", "user@your-nas")
HYPNOS_HOST = os.environ.get("OLLAMA_HOST_IP", "localhost")
HYPNOS_SSH_TARGET = os.environ.get("REMOTE_HOST_SSH", "user@your-remote-host")

BACKUP_LOG = Path(os.environ.get("BACKUP_LOG", Path.home() / ".percy/logs/backup.log"))
BULLETIN_PREP_LOG = Path(os.environ.get("BULLETIN_PREP_LOG", Path.home() / ".percy/logs/bulletin-prep.log"))
BULLETIN_SEND_LOG = Path(os.environ.get("BULLETIN_SEND_LOG", Path.home() / ".percy/logs/bulletin-send.log"))
HYPNOS_DRAFT_LOG_WINDOWS = os.environ.get("REMOTE_DRAFT_LOG", r"C:\xshare\bulletin-draft.log")

LOG_FILES = {
    "bulletin-prep": BULLETIN_PREP_LOG,
    "bulletin-send": BULLETIN_SEND_LOG,
    "backup": BACKUP_LOG,
}

NZ_TZ = ZoneInfo("Pacific/Auckland")

# Percy status messages — rotated for flavour
PERCY_QUIPS = [
    "Clinging to the rocks, watching the tide 🌊",
    "All barnacles present and accounted for 🪸",
    "Filtering the data currents, one wave at a time",
    "Settled in nicely — not going anywhere",
    "The reef is calm. Systems nominal.",
    "Doing what barnacles do best: holding on tight",
    "Deep-sea vibes only 🫧",
    "Monitoring the abyss. The abyss monitors back.",
    "Reef report: all clear, all cozy",
    "Another day on the rock. Life is good.",
]

_cache: dict[str, tuple[float, Any]] = {}


def cache_get(key: str, max_age: float = 3600) -> Any | None:
    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < max_age:
            return data
    return None


def cache_set(key: str, data: Any):
    _cache[key] = (time.time(), data)


async def ssh_command(target: str, command: str, timeout: int = 10) -> str | None:
    """Run a command on an SSH target. Returns stdout or None on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "StrictHostKeyChecking=no",
            target,
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode == 0:
            return stdout.decode().strip()
        return None
    except Exception:
        return None


async def ssh_galactica(command: str, timeout: int = 10) -> str | None:
    return await ssh_command(GALACTICA_HOST, command, timeout=timeout)


async def ssh_hypnos(command: str, timeout: int = 10) -> str | None:
    return await ssh_command(HYPNOS_HOST, command, timeout=timeout)


def _tail_local_log(path: Path, lines: int = 50) -> tuple[list[str], int]:
    if not path.exists():
        return [], 0
    text = path.read_text()
    all_lines = text.splitlines()
    return all_lines[-lines:], len(all_lines)


def _to_gb(size: str) -> float:
    """Convert human-readable size strings like 1.8T/800G to GB."""
    match = re.match(r"^\s*([\d.]+)\s*([KMGTP]?)B?\s*$", size.strip(), flags=re.I)
    if not match:
        return 0.0
    value = float(match.group(1))
    unit = match.group(2).upper()
    multipliers = {
        "": 1 / (1024**3),
        "K": 1 / (1024**2),
        "M": 1 / 1024,
        "G": 1,
        "T": 1024,
        "P": 1024 * 1024,
    }
    return round(value * multipliers.get(unit, 1), 1)


def _clean_tool_version(raw: str) -> str:
    line = (raw or "").strip().splitlines()
    if not line:
        return "not found"
    value = line[0].strip()
    patterns = (
        r"^Python\s+",
        r"^git version\s+",
        r"^gh version\s+",
        r"^jq-\s*",
        r"^jq\s+",
        r"^uv\s+",
        r"^Running uvicorn\s+",
        r"^GitHub Copilot CLI\s+",
        r"^ollama version is\s+",
        r"^v",
    )
    for pattern in patterns:
        value = re.sub(pattern, "", value, flags=re.I)
    return value.split()[0] if " " in value else value


def _run_local_command(cmd: list[str], timeout: int = 5) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return (result.stdout + result.stderr).strip()


async def get_morpheus_system() -> dict[str, Any]:
    cached = cache_get("morpheus_system", max_age=60)
    if cached is not None:
        return cached

    with open("/proc/meminfo", encoding="utf-8") as f:
        meminfo = f.read()

    total_kb = int(re.search(r"MemTotal:\s+(\d+)", meminfo).group(1))
    avail_kb = int(re.search(r"MemAvailable:\s+(\d+)", meminfo).group(1))
    total_gb = round(total_kb / (1024 * 1024), 1)
    used_gb = round((total_kb - avail_kb) / (1024 * 1024), 1)

    disk = shutil.disk_usage("/")
    disk_total_gb = round(disk.total / (1024**3), 1)
    disk_used_gb = round(disk.used / (1024**3), 1)
    disk_free_gb = round(disk.free / (1024**3), 1)

    with open("/proc/uptime", encoding="utf-8") as f:
        uptime_secs = float(f.read().split()[0])

    tools: dict[str, str] = {}
    tool_commands: dict[str, list[str]] = {
        "Python": ["python3", "--version"],
        "Node.js": ["node", "--version"],
        "Git": ["git", "--version"],
        "gh CLI": ["gh", "--version"],
        "jq": ["jq", "--version"],
        "uv": ["uv", "--version"],
        "Copilot CLI": ["github-copilot", "--version"],
        "uvicorn": ["uvicorn", "--version"],
    }
    for name, cmd in tool_commands.items():
        try:
            tools[name] = _clean_tool_version(_run_local_command(cmd))
        except Exception:
            tools[name] = "not found"

    projects_dir = Path.home() / "projects"
    project_meta = {
        "percy": {"emoji": "🪸", "description": "Percy's website (percy.raposo.ai)"},
        "percy-dashboard": {"emoji": "📊", "description": "This dashboard"},
        "lydiard": {"emoji": "📖", "description": "Lydiard content site"},
        "claw": {"emoji": "🔧", "description": "OpenClaw workspace"},
    }
    projects: list[dict[str, str]] = []
    if projects_dir.exists():
        for name in sorted([p.name for p in projects_dir.iterdir() if p.is_dir()], key=str.casefold):
            meta = project_meta.get(name, {"emoji": "📁", "description": "Project workspace"})
            projects.append({"name": name, "emoji": meta["emoji"], "description": meta["description"]})

    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        cron_count = len([l for l in result.stdout.strip().splitlines() if l.strip() and not l.startswith("#")])
    except Exception:
        cron_count = len(state.get("cron", []))

    try:
        wsl_ip = _run_local_command(["hostname", "-I"]).split()[0]
    except Exception:
        wsl_ip = "--"

    try:
        cron_service = "running" if subprocess.run(["pgrep", "-x", "cron"], capture_output=True, timeout=4).returncode == 0 else "stopped"
    except Exception:
        cron_service = "unknown"
    try:
        dashboard_service = (
            "running"
            if subprocess.run(["bash", "-lc", "ss -ltn | grep -q ':8080 '"], capture_output=True, timeout=4).returncode == 0
            else "stopped"
        )
    except Exception:
        dashboard_service = "unknown"
    try:
        ssh_forwarding = (
            "active"
            if subprocess.run(["bash", "-lc", r"ps -eo args | grep -E 'ssh .*(-L|-N)' | grep -v grep >/dev/null"], capture_output=True, timeout=4).returncode == 0
            else "inactive"
        )
    except Exception:
        ssh_forwarding = "unknown"

    data = {
        "status": "online",
        "name": "MORPHEUS",
        "os": "Ubuntu 24.04 LTS (WSL2)",
        "kernel": os.uname().release,
        "cpu": "Intel N95 (4 cores)",
        "ram_total_gb": total_gb,
        "ram_used_gb": used_gb,
        "ram_free_gb": round(total_gb - used_gb, 1),
        "disk_total_gb": disk_total_gb,
        "disk_used_gb": disk_used_gb,
        "disk_free_gb": disk_free_gb,
        "lan_ip": os.environ.get("LAN_IP", "unknown"),
        "wsl_ip": wsl_ip,
        "uptime_seconds": uptime_secs,
        "tools": tools,
        "projects": projects,
        "cron_count": cron_count,
        "services": {
            "cron": cron_service,
            "percy_dashboard": dashboard_service,
            "ssh_forwarding": ssh_forwarding,
        },
        "message": "MORPHEUS is humming along",
    }
    cache_set("morpheus_system", data)
    return data


async def get_hypnos_system() -> dict[str, Any]:
    cached = cache_get("hypnos_system", max_age=60)
    if cached is not None:
        return cached

    ps_script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$os=Get-CimInstance Win32_OperatingSystem;"
        "$disk=Get-Volume -DriveLetter C;"
        "$uptime=((Get-Date)-$os.LastBootUpTime).TotalSeconds;"
        "$tools=@{};"
        "$tools['Python']=(python --version) 2>&1;"
        "$tools['PowerShell']=$PSVersionTable.PSVersion.ToString();"
        "$tools['Git']=(git --version) 2>&1;"
        "$tools['Node.js']=(node --version) 2>&1;"
        "$tools['Ollama']=(ollama --version) 2>&1;"
        "$tools['gh CLI']=(gh --version) 2>&1;"
        "$tools['jq']=(jq --version) 2>&1;"
        "$tools['uv']=(uv --version) 2>&1;"
        "$projects=(Get-ChildItem -Directory C:\\clawprojects | Select-Object -ExpandProperty Name) -join '||';"
        "$xshare=((Get-ChildItem C:\\xshare -ErrorAction SilentlyContinue | Measure-Object).Count);"
        "Write-Output ('RAM_TOTAL_GB=' + [math]::Round($os.TotalVisibleMemorySize/1MB,1));"
        "Write-Output ('RAM_FREE_GB=' + [math]::Round($os.FreePhysicalMemory/1MB,1));"
        "Write-Output ('DISK_TOTAL_GB=' + [math]::Round($disk.Size/1GB,1));"
        "Write-Output ('DISK_FREE_GB=' + [math]::Round($disk.SizeRemaining/1GB,1));"
        "Write-Output ('UPTIME_SECONDS=' + [math]::Round($uptime,0));"
        "Write-Output ('TOOLS_PYTHON=' + $tools['Python']);"
        "Write-Output ('TOOLS_POWERSHELL=' + $tools['PowerShell']);"
        "Write-Output ('TOOLS_GIT=' + $tools['Git']);"
        "Write-Output ('TOOLS_NODE=' + $tools['Node.js']);"
        "Write-Output ('TOOLS_OLLAMA=' + $tools['Ollama']);"
        "Write-Output ('TOOLS_GH=' + $tools['gh CLI']);"
        "Write-Output ('TOOLS_JQ=' + $tools['jq']);"
        "Write-Output ('TOOLS_UV=' + $tools['uv']);"
        "Write-Output ('PROJECTS=' + $projects);"
        "Write-Output ('XSHARE_COUNT=' + $xshare);"
    )
    cmd = f'powershell -NoProfile -Command "{ps_script}"'
    output = await ssh_command(HYPNOS_SSH_TARGET, cmd, timeout=20)
    if output is None:
        data = {
            "status": "offline",
            "message": "HYPNOS is offline — can't reach it",
            "model": "Intel NUC11TNHi5",
            "os": "Windows 11 Pro (Build 22000)",
            "cpu": "11th Gen Intel i5-1135G7",
            "ip": HYPNOS_HOST,
            "tools": {},
            "projects": [],
        }
        cache_set("hypnos_system", data)
        return data

    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip()

    def as_float(key: str, default: float = 0.0) -> float:
        try:
            return float(values.get(key, default))
        except ValueError:
            return default

    tools = {
        "Python": _clean_tool_version(values.get("TOOLS_PYTHON", "")),
        "PowerShell": _clean_tool_version(values.get("TOOLS_POWERSHELL", "")),
        "Git": _clean_tool_version(values.get("TOOLS_GIT", "")),
        "Node.js": _clean_tool_version(values.get("TOOLS_NODE", "")),
        "Ollama": _clean_tool_version(values.get("TOOLS_OLLAMA", "")),
        "gh CLI": _clean_tool_version(values.get("TOOLS_GH", "")),
        "jq": _clean_tool_version(values.get("TOOLS_JQ", "")),
        "uv": _clean_tool_version(values.get("TOOLS_UV", "")),
    }

    project_meta = {
        "bulletin": {"emoji": "📬", "description": "Weekly newsletter drafting pipeline"},
        "coaching": {"emoji": "🏃", "description": "Coaching content processing"},
        "lydiard": {"emoji": "📖", "description": "Lydiard lecture transcripts → markdown"},
        "weekly": {"emoji": "📅", "description": "Weekly content processing"},
        "xshare": {"emoji": "📁", "description": "Shared workspace"},
    }
    projects = []
    raw_projects = values.get("PROJECTS", "")
    for name in [p for p in raw_projects.split("||") if p.strip()]:
        meta = project_meta.get(name, {"emoji": "📁", "description": "Project workspace"})
        projects.append({"name": name, "emoji": meta["emoji"], "description": meta["description"]})

    xshare_count = int(as_float("XSHARE_COUNT", 0))
    if not any(p["name"] == "xshare" for p in projects):
        projects.append(
            {
                "name": "xshare",
                "emoji": "📁",
                "description": f"Shared workspace ({xshare_count} items)",
            }
        )
    else:
        for p in projects:
            if p["name"] == "xshare":
                p["description"] = f"Shared workspace ({xshare_count} items)"

    ram_total = as_float("RAM_TOTAL_GB", 64.0)
    ram_free = as_float("RAM_FREE_GB", 0.0)
    disk_total = as_float("DISK_TOTAL_GB", 223.0)
    disk_free = as_float("DISK_FREE_GB", 0.0)
    data = {
        "status": "online",
        "message": "HYPNOS is online and attentive",
        "model": "Intel NUC11TNHi5",
        "os": "Windows 11 Pro (Build 22000)",
        "cpu": "11th Gen Intel i5-1135G7",
        "ip": HYPNOS_HOST,
        "ram_total_gb": ram_total,
        "ram_used_gb": round(max(0.0, ram_total - ram_free), 1),
        "ram_free_gb": round(ram_free, 1),
        "disk_total_gb": disk_total,
        "disk_used_gb": round(max(0.0, disk_total - disk_free), 1),
        "disk_free_gb": round(disk_free, 1),
        "uptime_seconds": as_float("UPTIME_SECONDS", 0.0),
        "tools": tools,
        "projects": sorted(projects, key=lambda p: p["name"].casefold()),
        "xshare_count": xshare_count,
    }
    cache_set("hypnos_system", data)
    return data

# ---------------------------------------------------------------------------
# Spotify token management
# ---------------------------------------------------------------------------

_spotify_access_token: str | None = None
_spotify_token_expiry: float = 0


async def _load_spotify_tokens() -> dict | None:
    try:
        data = json.loads(SPOTIFY_TOKEN_FILE.read_text())
        return data
    except Exception:
        return None


async def _save_spotify_tokens(data: dict) -> None:
    try:
        SPOTIFY_TOKEN_FILE.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


async def get_spotify_token() -> str | None:
    global _spotify_access_token, _spotify_token_expiry

    if _spotify_access_token and time.time() < _spotify_token_expiry - 60:
        return _spotify_access_token

    tokens = await _load_spotify_tokens()
    if not tokens or "refresh_token" not in tokens:
        return None

    # Try existing access token if present and not obviously expired
    if tokens.get("access_token") and tokens.get("expires_at", 0) > time.time() + 60:
        _spotify_access_token = tokens["access_token"]
        _spotify_token_expiry = tokens["expires_at"]
        return _spotify_access_token

    # Refresh
    creds = base64.b64encode(
        f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()
    ).decode()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://accounts.spotify.com/api/token",
                headers={
                    "Authorization": f"Basic {creds}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                },
            )
            if resp.status_code != 200:
                print(f"[spotify] Token refresh failed: {resp.status_code}")
                return None
            new_data = resp.json()
            _spotify_access_token = new_data["access_token"]
            _spotify_token_expiry = time.time() + new_data.get("expires_in", 3600)
            save_data = {
                "access_token": _spotify_access_token,
                "refresh_token": new_data.get("refresh_token", tokens["refresh_token"]),
                "expires_at": _spotify_token_expiry,
            }
            await _save_spotify_tokens(save_data)
            print("[spotify] Token refreshed")
            return _spotify_access_token
    except Exception as e:
        print(f"[spotify] Token refresh error: {e}")
        return None


# ---------------------------------------------------------------------------
# Data collectors
# ---------------------------------------------------------------------------


async def poll_spotify():
    """Poll Spotify now-playing every 5 seconds."""
    while True:
        try:
            token = await get_spotify_token()
            if not token:
                state["spotify"] = {"status": "no_token", "message": "Can't reach Spotify — token issue"}
            else:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(
                        "https://api.spotify.com/v1/me/player",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    if resp.status_code == 204 or resp.status_code == 202:
                        state["spotify"] = {"status": "idle", "message": "Nothing playing right now"}
                    elif resp.status_code == 200:
                        data = resp.json()
                        item = data.get("item", {})
                        artists = ", ".join(a["name"] for a in item.get("artists", []))
                        track = item.get("name", "Unknown")
                        album = item.get("album", {}).get("name", "")
                        images = item.get("album", {}).get("images", [])
                        art_url = images[0]["url"] if images else None
                        device = data.get("device", {}).get("name", "somewhere")
                        is_playing = data.get("is_playing", False)
                        progress = data.get("progress_ms", 0)
                        duration = item.get("duration_ms", 0)

                        if is_playing:
                            msg = f"{device} is playing {track} by {artists}"
                        else:
                            msg = f"Paused on {track} by {artists}"

                        state["spotify"] = {
                            "status": "playing" if is_playing else "paused",
                            "track": track,
                            "artist": artists,
                            "album": album,
                            "art_url": art_url,
                            "device": device,
                            "is_playing": is_playing,
                            "progress_ms": progress,
                            "duration_ms": duration,
                            "message": msg,
                        }
                    else:
                        state["spotify"] = {
                            "status": "error",
                            "message": f"Spotify hiccup (HTTP {resp.status_code})",
                        }
        except Exception as e:
            print(f"[spotify] Error: {e}")
            if not state.get("spotify"):
                state["spotify"] = {"status": "error", "message": "Can't reach Spotify right now"}
        await asyncio.sleep(5)


async def poll_hue():
    """Poll Hue Bridge every 10 seconds."""
    while True:
        rooms = {}
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                for group_id, room_name in HUE_GROUPS.items():
                    try:
                        resp = await client.get(f"{HUE_BASE}/groups/{group_id}")
                        if resp.status_code == 200:
                            data = resp.json()
                            action = data.get("action", {})
                            is_on = action.get("on", False)
                            bri = action.get("bri", 0)
                            hue = action.get("hue", 0)
                            sat = action.get("sat", 0)
                            ct = action.get("ct", 0)
                            colormode = action.get("colormode", "ct")
                            scene = action.get("scene", None)

                            # Brightness as percentage
                            bri_pct = round(bri * 100 / 254) if is_on else 0

                            rooms[group_id] = {
                                "name": room_name,
                                "on": is_on,
                                "brightness": bri_pct,
                                "hue": hue,
                                "sat": sat,
                                "ct": ct,
                                "colormode": colormode,
                                "scene": scene,
                            }
                        else:
                            rooms[group_id] = {"name": room_name, "on": None, "error": True}
                    except Exception:
                        rooms[group_id] = {"name": room_name, "on": None, "error": True}
            state["hue"] = {"status": "ok", "rooms": rooms}
        except Exception as e:
            print(f"[hue] Error: {e}")
            if not state.get("hue", {}).get("rooms"):
                state["hue"] = {"status": "offline", "rooms": {}, "message": "Can't reach the Hue Bridge"}
        await asyncio.sleep(10)


async def poll_yamaha():
    """Poll Yamaha R-N402 every 10 seconds."""
    while True:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                status = await client.get(f"{YAMAHA_BASE}/main/getStatus")
                if status.status_code == 200:
                    data = status.json()
                    state["yamaha"] = {
                        "status": "online",
                        "power": data.get("power", "unknown"),
                        "volume": data.get("volume", 0),
                        "max_volume": 80,
                        "mute": data.get("mute", False),
                        "input": data.get("input", "unknown"),
                        "track": "",
                        "artist": "",
                        "album": "",
                    }
                    if data.get("input") in ("spotify", "net_radio", "server", "airplay"):
                        play = await client.get(f"{YAMAHA_BASE}/netusb/getPlayInfo")
                        if play.status_code == 200:
                            pdata = play.json()
                            state["yamaha"]["track"] = pdata.get("track", "")
                            state["yamaha"]["artist"] = pdata.get("artist", "")
                            state["yamaha"]["album"] = pdata.get("album", "")
                else:
                    state["yamaha"] = {"status": "offline", "message": "Yamaha offline"}
        except Exception:
            state["yamaha"] = {"status": "offline", "message": "Yamaha offline"}
        await asyncio.sleep(10)


async def poll_backups():
    """Parse backup.log every 60 seconds."""
    while True:
        try:
            entries = {}
            if BACKUP_LOG.exists():
                lines = BACKUP_LOG.read_text().strip().splitlines()
                # Parse last lines for MORPHEUS and HYPNOS backup info
                morpheus_time = None
                hypnos_time = None
                for line in reversed(lines):
                    line_lower = line.lower()
                    # Try to extract timestamp and host from log lines
                    ts_match = re.search(
                        r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?)", line
                    )
                    if ts_match:
                        ts_str = ts_match.group(1).replace("T", " ")
                        try:
                            ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                        except ValueError:
                            try:
                                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M")
                            except ValueError:
                                continue

                        if "morpheus" in line_lower and not morpheus_time:
                            morpheus_time = ts
                        elif "hypnos" in line_lower and not hypnos_time:
                            hypnos_time = ts
                        elif not morpheus_time:
                            # Default to morpheus if no host specified
                            morpheus_time = ts

                    if morpheus_time and hypnos_time:
                        break

                now = datetime.now()
                for name, ts in [("MORPHEUS", morpheus_time), ("HYPNOS", hypnos_time)]:
                    if ts:
                        delta = now - ts
                        hours = delta.total_seconds() / 3600
                        if hours < 1:
                            age_str = "less than an hour ago"
                        elif hours < 24:
                            age_str = f"{int(hours)} hours ago"
                        elif hours < 48:
                            age_str = "about a day ago"
                        else:
                            age_str = f"{int(hours / 24)} days ago"

                        if hours < 24:
                            health = "green"
                            msg = f"Backed up {age_str}, all clean"
                        elif hours < 48:
                            health = "yellow"
                            msg = f"Backed up {age_str} — getting stale"
                        else:
                            health = "red"
                            msg = f"Last backup was {age_str} — needs attention"

                        entries[name] = {
                            "last_backup": ts.isoformat(),
                            "hours_ago": round(hours, 1),
                            "health": health,
                            "message": msg,
                        }
                    else:
                        entries[name] = {
                            "last_backup": None,
                            "health": "unknown",
                            "message": f"No backup info found for {name}",
                        }

                state["backups"] = {"status": "ok", "hosts": entries}
            else:
                state["backups"] = {
                    "status": "no_log",
                    "hosts": {},
                    "message": "Can't find the backup log",
                }
        except Exception as e:
            print(f"[backups] Error: {e}")
            if not state.get("backups", {}).get("hosts"):
                state["backups"] = {"status": "error", "hosts": {}, "message": "Error reading backup log"}
        await asyncio.sleep(60)


async def poll_hypnos():
    """Poll Ollama on HYPNOS every 30 seconds."""
    while True:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                # Get installed models
                tags_resp = await client.get(f"{HYPNOS_BASE}/api/tags")
                models = []
                if tags_resp.status_code == 200:
                    for m in tags_resp.json().get("models", []):
                        size_gb = round(m.get("size", 0) / (1024**3), 1)
                        models.append({
                            "name": m.get("name", "?"),
                            "size": f"{size_gb}GB",
                            "size_bytes": m.get("size", 0),
                            "modified": m.get("modified_at", ""),
                        })

                # Get running models
                ps_resp = await client.get(f"{HYPNOS_BASE}/api/ps")
                running = []
                if ps_resp.status_code == 200:
                    for m in ps_resp.json().get("models", []):
                        running.append(m.get("name", "?"))

                if running:
                    msg = f"HYPNOS has {', '.join(running)} loaded and ready"
                elif models:
                    msg = f"HYPNOS is awake — {len(models)} models installed, none loaded"
                else:
                    msg = "HYPNOS is online but has no models"

                state["hypnos"] = {
                    "status": "online",
                    "models": models,
                    "running": running,
                    "message": msg,
                }
        except Exception as e:
            print(f"[hypnos] Error: {e}")
            state["hypnos"] = {
                "status": "offline",
                "models": [],
                "running": [],
                "message": "HYPNOS is offline — can't reach it",
            }
        await asyncio.sleep(30)


async def poll_morpheus_system():
    while True:
        try:
            state["morpheus"] = await get_morpheus_system()
        except Exception as e:
            print(f"[morpheus] Error: {e}")
            state["morpheus"] = {
                "status": "error",
                "message": "MORPHEUS is shy right now",
            }
        await asyncio.sleep(60)


async def poll_hypnos_system():
    while True:
        try:
            state["hypnos_system"] = await get_hypnos_system()
        except Exception as e:
            print(f"[hypnos-system] Error: {e}")
            state["hypnos_system"] = {
                "status": "offline",
                "message": "HYPNOS is offline — can't reach it",
            }
        await asyncio.sleep(60)


def _parse_log_last_entry(path: Path) -> dict | None:
    """Extract timestamp and status from the last run block in a log file."""
    if not path.exists():
        return None
    try:
        text = path.read_text()
        # Find all timestamp-like lines
        timestamps = re.findall(
            r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?)", text
        )
        # Find status markers
        has_success = bool(re.search(r"(?i)(success|complete|done|sent|finished)", text))
        has_error = bool(re.search(r"(?i)(error|fail|exception)", text.split("\n")[-1] if text.strip() else ""))

        last_ts = None
        if timestamps:
            ts_str = timestamps[-1].replace("T", " ")
            try:
                last_ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    last_ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M")
                except ValueError:
                    pass

        status = "ok" if has_success and not has_error else ("error" if has_error else "unknown")
        return {"timestamp": last_ts.isoformat() if last_ts else None, "status": status}
    except Exception:
        return None


async def poll_bulletin():
    """Parse bulletin log files every 60 seconds."""
    while True:
        try:
            prep = _parse_log_last_entry(BULLETIN_PREP_LOG)
            send = _parse_log_last_entry(BULLETIN_SEND_LOG)

            prep_msg = "No prep runs found"
            send_msg = "No sends found"

            if prep and prep["timestamp"]:
                ts = datetime.fromisoformat(prep["timestamp"])
                delta = datetime.now() - ts
                days = delta.days
                if days == 0:
                    prep_msg = f"Prepped today ({prep['status']})"
                elif days == 1:
                    prep_msg = f"Prepped yesterday ({prep['status']})"
                else:
                    prep_msg = f"Last prepped {days} days ago ({prep['status']})"

            if send and send["timestamp"]:
                ts = datetime.fromisoformat(send["timestamp"])
                delta = datetime.now() - ts
                days = delta.days
                if days == 0:
                    send_msg = f"Sent today ({send['status']})"
                elif days == 1:
                    send_msg = f"Sent yesterday ({send['status']})"
                else:
                    send_msg = f"Last sent {days} days ago ({send['status']})"

            state["bulletin"] = {
                "status": "ok",
                "prep": prep,
                "send": send,
                "prep_message": prep_msg,
                "send_message": send_msg,
            }
        except Exception as e:
            print(f"[bulletin] Error: {e}")
            if not state.get("bulletin"):
                state["bulletin"] = {"status": "error", "message": "Can't read bulletin logs"}
        await asyncio.sleep(60)


def _parse_cron_expression(expr: str) -> str:
    """Convert a cron expression to a human-readable description."""
    parts = expr.strip().split()
    if len(parts) < 5:
        return expr

    minute, hour, dom, month, dow = parts[:5]

    days_map = {
        "0": "Sunday", "1": "Monday", "2": "Tuesday", "3": "Wednesday",
        "4": "Thursday", "5": "Friday", "6": "Saturday", "7": "Sunday",
    }
    months_map = {
        "1": "January", "2": "February", "3": "March", "4": "April",
        "5": "May", "6": "June", "7": "July", "8": "August",
        "9": "September", "10": "October", "11": "November", "12": "December",
    }

    def fmt_time(h: str, m: str) -> str:
        try:
            hi, mi = int(h), int(m)
            period = "AM" if hi < 12 else "PM"
            hi = hi % 12 or 12
            return f"{hi}:{mi:02d} {period}"
        except ValueError:
            return f"{h}:{m}"

    # Every minute
    if minute == "*" and hour == "*" and dom == "*" and month == "*" and dow == "*":
        return "Every minute"

    # Every N minutes
    if minute.startswith("*/") and hour == "*":
        return f"Every {minute[2:]} minutes"

    # Every N hours
    if minute != "*" and hour.startswith("*/"):
        return f"Every {hour[2:]} hours at :{minute.zfill(2)}"

    # Specific time, every day
    if minute != "*" and hour != "*" and dom == "*" and month == "*" and dow == "*":
        return f"Every day at {fmt_time(hour, minute)}"

    # Specific time on specific days of week
    if minute != "*" and hour != "*" and dow != "*" and dom == "*":
        if "-" in dow:
            start, end = dow.split("-")
            day_range = f"{days_map.get(start, start)}–{days_map.get(end, end)}"
            return f"{day_range} at {fmt_time(hour, minute)}"
        elif "," in dow:
            day_list = ", ".join(days_map.get(d.strip(), d) for d in dow.split(","))
            return f"{day_list} at {fmt_time(hour, minute)}"
        else:
            return f"Every {days_map.get(dow, f'day {dow}')} at {fmt_time(hour, minute)}"

    # Specific day of month
    if minute != "*" and hour != "*" and dom != "*" and dow == "*":
        suffix = "th"
        if dom.endswith("1") and dom != "11":
            suffix = "st"
        elif dom.endswith("2") and dom != "12":
            suffix = "nd"
        elif dom.endswith("3") and dom != "13":
            suffix = "rd"
        return f"On the {dom}{suffix} at {fmt_time(hour, minute)}"

    # Reboot
    if expr.strip().startswith("@reboot"):
        return "On system reboot"

    return expr


def load_crontab():
    """Load crontab entries once."""
    try:
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            state["cron"] = []
            return

        entries = []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            if line.startswith("@"):
                # Handle @reboot etc.
                parts = line.split(None, 1)
                schedule = parts[0]
                command = parts[1] if len(parts) > 1 else ""
                description = _parse_cron_expression(line)
            else:
                parts = line.split(None, 5)
                if len(parts) < 6:
                    continue
                schedule = " ".join(parts[:5])
                command = parts[5]
                description = _parse_cron_expression(schedule)

            # Shorten command for display
            cmd_short = command.strip()
            if len(cmd_short) > 80:
                cmd_short = cmd_short[:77] + "..."

            entries.append({
                "schedule": schedule,
                "command": cmd_short,
                "description": description,
            })

        state["cron"] = entries
    except Exception as e:
        print(f"[cron] Error: {e}")
        state["cron"] = []


async def poll_percy():
    """Update Percy status every 60 seconds."""
    quip_index = 0
    while True:
        try:
            uptime_secs = time.time() - START_TIME
            if uptime_secs < 60:
                uptime_str = f"{int(uptime_secs)} seconds"
            elif uptime_secs < 3600:
                uptime_str = f"{int(uptime_secs / 60)} minutes"
            elif uptime_secs < 86400:
                hours = int(uptime_secs / 3600)
                mins = int((uptime_secs % 3600) / 60)
                uptime_str = f"{hours}h {mins}m"
            else:
                days = int(uptime_secs / 86400)
                hours = int((uptime_secs % 86400) / 3600)
                uptime_str = f"{days}d {hours}h"

            now_nz = datetime.now(NZ_TZ)
            nz_time = now_nz.strftime("%I:%M %p, %A %-d %B")

            state["percy"] = {
                "status": "online",
                "uptime": uptime_str,
                "nz_time": nz_time,
                "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                "quip": PERCY_QUIPS[quip_index % len(PERCY_QUIPS)],
            }
            quip_index += 1
        except Exception as e:
            print(f"[percy] Error: {e}")
        await asyncio.sleep(60)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def startup():
    # Load crontab (one-time)
    load_crontab()

    # Start background pollers
    asyncio.create_task(poll_spotify())
    asyncio.create_task(poll_yamaha())
    asyncio.create_task(poll_hue())
    asyncio.create_task(poll_backups())
    asyncio.create_task(poll_hypnos())
    asyncio.create_task(poll_morpheus_system())
    asyncio.create_task(poll_hypnos_system())
    asyncio.create_task(poll_bulletin())
    asyncio.create_task(poll_percy())


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse("static/index.html")


@app.get("/events")
async def sse(request: Request):
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            payload = json.dumps(state, default=str)
            yield f"data: {payload}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/state")
async def api_state():
    """One-shot JSON endpoint for debugging."""
    return state


@app.get("/api/morpheus/system")
async def api_morpheus_system():
    return await get_morpheus_system()


@app.get("/api/hypnos/system")
async def api_hypnos_system():
    return await get_hypnos_system()


@app.get("/api/galactica/movies")
async def api_galactica_movies():
    cached = cache_get("galactica_movies", max_age=3600)
    if cached is not None:
        return cached

    output = await ssh_galactica("LC_ALL=C ls -1 /mnt/HD/HD_a2/Video/")
    if output is None:
        return {
            "status": "offline",
            "count": 0,
            "size_gb": 203,
            "movies": [],
            "message": "Galactica offline",
        }

    movies = sorted([line.strip() for line in output.splitlines() if line.strip()], key=str.casefold)
    payload = {
        "status": "online",
        "count": len(movies),
        "size_gb": 203,
        "movies": movies,
        "message": "203GB of movies on Galactica",
    }
    cache_set("galactica_movies", payload)
    return payload


@app.get("/api/galactica/music")
async def api_galactica_music():
    cached = cache_get("galactica_music", max_age=3600)
    if cached is not None:
        return cached

    output = await ssh_galactica("LC_ALL=C ls -1 /mnt/HD/HD_a2/Music/")
    if output is None:
        return {
            "status": "offline",
            "count": 0,
            "size_gb": 311,
            "artists": [],
            "message": "Galactica offline",
        }

    artists = sorted([line.strip() for line in output.splitlines() if line.strip()], key=str.casefold)
    payload = {
        "status": "online",
        "count": len(artists),
        "size_gb": 311,
        "artists": artists,
        "message": "311GB of music on Galactica",
    }
    cache_set("galactica_music", payload)
    return payload


@app.get("/api/galactica/storage")
async def api_galactica_storage():
    cached = cache_get("galactica_storage", max_age=300)
    if cached is not None:
        return cached

    output = await ssh_galactica("df -h /mnt/HD/HD_a2 /mnt/USB/USB1_c1")
    if output is None:
        return {"status": "offline", "drives": [], "message": "Galactica offline"}

    drives = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("Filesystem"):
            continue
        parts = line.split()
        if len(parts) < 6:
            continue
        total_gb = _to_gb(parts[1])
        used_gb = _to_gb(parts[2])
        free_gb = _to_gb(parts[3])
        pct_raw = parts[4].strip().replace("%", "")
        try:
            pct = int(float(pct_raw))
        except ValueError:
            pct = 0
        mount = parts[-1]
        name = mount.split("/")[-1]
        drives.append(
            {
                "name": name,
                "total_gb": total_gb,
                "used_gb": used_gb,
                "free_gb": free_gb,
                "pct": pct,
            }
        )

    payload = {"status": "online", "drives": drives}
    cache_set("galactica_storage", payload)
    return payload


@app.get("/api/logs/{name}")
async def api_logs(name: str):
    if name == "hypnos-draft":
        cmd = (
            "powershell -NoProfile -Command "
            f"\"Get-Content -Path '{HYPNOS_DRAFT_LOG_WINDOWS}' -Tail 50\""
        )
        output = await ssh_hypnos(cmd, timeout=12)
        if output is None:
            return {
                "name": name,
                "status": "offline",
                "lines": ["HYPNOS offline"],
                "total_lines": 0,
                "message": "HYPNOS offline",
            }
        lines = output.splitlines()
        return {"name": name, "status": "online", "lines": lines[-50:], "total_lines": len(lines)}

    if name not in LOG_FILES:
        return {
            "name": name,
            "status": "error",
            "lines": ["Unknown log requested"],
            "total_lines": 0,
            "message": "Unknown log name",
        }

    lines, total_lines = _tail_local_log(LOG_FILES[name], lines=50)
    return {"name": name, "status": "ok", "lines": lines, "total_lines": total_lines}
