# 🪸 Percy Dashboard

A real-time home network status dashboard with a deep-sea bioluminescent theme. Built for [Percy (Percebe)](https://percy.raposo.ai) — an AI assistant that monitors infrastructure, media, and automation across a home lab.

![Python](https://img.shields.io/badge/Python-3.10+-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green) ![License](https://img.shields.io/badge/License-MIT-yellow)

## What It Does

Percy Dashboard provides a single-pane view of a home network's vital signs, streamed live via Server-Sent Events:

- **Now Playing** — Spotify playback + Yamaha MusicCast receiver status
- **Hue Lights** — Philips Hue room states (on/off, brightness, colour)
- **Backups** — Freshness indicators for automated backups to NAS
- **Ollama Models** — Local LLM inventory and loaded model status
- **Media Library** — Movies, music, and photos stored on NAS
- **Machine Health** — CPU, RAM, disk, uptime, and tool versions for each host
- **Cron Jobs** — Scheduled task overview with human-readable descriptions
- **Log Viewer** — Tail view of pipeline and backup logs

## Screenshots

*Coming soon — it looks like a bioluminescent reef, not a corporate dashboard.*

## Architecture

```
Browser ──SSE──▶ FastAPI (uvicorn)
                    ├── Spotify Web API (polling)
                    ├── Philips Hue Bridge API (polling)
                    ├── Yamaha MusicCast API (polling)
                    ├── Ollama API (polling)
                    ├── SSH to NAS (cached)
                    ├── SSH to remote hosts (cached)
                    └── Local log files (tailing)
```

- **Backend:** Python 3 + FastAPI + uvicorn with background async pollers
- **Frontend:** Single HTML page + vanilla JS + Tailwind CSS (CDN)
- **Live updates:** Server-Sent Events — backend polls data sources at configurable intervals, pushes state to all connected clients
- **No database** — all data is live from APIs, SSH, and log files
- **No build step** — no npm, no bundler, just static files served by FastAPI

## Tabs

| Tab | Description |
|---|---|
| **Main** | At-a-glance: music, lights, backups, assistant status |
| **Entertainment** | NAS media library: movies, music, photos, storage |
| **MORPHEUS** | Primary host: system info, tools, projects, cron |
| **HYPNOS** | Secondary host: system info, Ollama models, projects |
| **Settings** | Network devices, system configuration |
| **Logs** | Pipeline and backup log viewers |

## Setup

### Requirements

- Python 3.10+
- Network access to your devices (Hue bridge, Yamaha receiver, Ollama instance, NAS via SSH)
- Spotify API credentials (client ID + secret from [developer.spotify.com](https://developer.spotify.com))

### Install

```bash
git clone https://github.com/cknzraposo/percy-dashboard.git
cd percy-dashboard
pip install -r requirements.txt
```

### Configure

The dashboard reads configuration from environment variables and credential files. You'll need to set up:

1. **Spotify** — Client ID, client secret, and refresh token
2. **Philips Hue** — Bridge IP and API key
3. **Yamaha MusicCast** — Receiver IP
4. **Ollama** — Host IP and port
5. **NAS** — SSH access (key-based auth recommended)
6. **Remote hosts** — SSH access for system monitoring

See `app.py` for the configuration section at the top of the file. Replace placeholder values with your own device addresses and credentials.

### Run

```bash
# HTTP
uvicorn app:app --host 0.0.0.0 --port 8080

# HTTPS (with your own certs)
uvicorn app:app --host 0.0.0.0 --port 8080 \
  --ssl-keyfile certs/key.pem \
  --ssl-certfile certs/cert.pem
```

### Systemd (optional)

Create a user service for auto-start:

```ini
[Unit]
Description=Percy Dashboard
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/percy-dashboard
ExecStart=/usr/bin/uvicorn app:app --host 0.0.0.0 --port 8080
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable percy-dashboard
systemctl --user start percy-dashboard
```

## Design

The theme is **deep-sea bioluminescence** — dark slate backgrounds with amber, cyan, and soft blue accents. Glassmorphism panels with subtle glow effects. Space Mono + Space Grotesk typography.

This is intentionally *not* a generic monitoring dashboard. It's meant to feel like a living space — warm, personal, distinctive.

## Tech Stack

| Component | Choice | Why |
|---|---|---|
| Backend | FastAPI + uvicorn | Async-native, lightweight, perfect for SSE |
| Frontend | Vanilla JS + Tailwind CDN | Zero build step, fast iteration |
| Live updates | Server-Sent Events | Simpler than WebSocket for one-way data |
| Data | Direct API calls + SSH | No database needed for live status |

## Security Notes

- **LAN only** — designed for home network use behind a router/NAT
- **No authentication** — relies on network-level security
- **No secrets in repo** — API keys, IPs, and credentials must be configured locally
- **SSL recommended** — use [mkcert](https://github.com/FiloSottile/mkcert) for trusted local HTTPS

## Contributing

This is a personal project, but PRs and ideas are welcome. If you adapt it for your own home lab, I'd love to hear about it.

## License

[MIT](LICENSE) — do whatever you want with it.

---

*Built by [Percy](https://percy.raposo.ai) 🪸 — an AI familiar clinging to the edge of what's possible.*
