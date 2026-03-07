# 🪸 OpenClaw Dashboard

A real-time, config-driven home network dashboard for [OpenClaw](https://github.com/openclaw/openclaw) agents. Name your agent, pick a theme, enable the panels you need — zero build step, zero npm dependencies.

![Python](https://img.shields.io/badge/Python-3.10+-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green) ![License](https://img.shields.io/badge/License-MIT-yellow)

## What is OpenClaw?

[OpenClaw](https://docs.openclaw.ai) is an open-source AI agent framework that gives your AI assistant persistent memory, tool access, and the ability to interact with your world — files, APIs, devices, messaging platforms, and more. Think of it as the operating system for your personal AI.

This dashboard gives your OpenClaw agent a visual status page — a single-pane view of the infrastructure it monitors and controls.

## What It Does

Your agent manages services across your home network. This dashboard shows their vital signs, streamed live via Server-Sent Events:

- **Now Playing** — Spotify playback + Yamaha MusicCast receiver status
- **Hue Lights** — Philips Hue room states (on/off, brightness, colour)
- **Backups** — Freshness indicators for automated backups to NAS
- **Ollama Models** — Local LLM inventory and loaded model status
- **Media Library** — Movies, music, and photos stored on NAS
- **Machine Health** — CPU, RAM, disk, uptime, and tool versions for each host
- **Cron Jobs** — Scheduled task overview with human-readable descriptions
- **Log Viewer** — Tail view of pipeline and backup logs

**Every panel is optional.** Enable what you have, skip what you don't.

## Screenshots

*Coming soon — it looks like a bioluminescent reef, not a corporate dashboard.*

## Architecture

```
config.yaml ──────▶ What to show (panels, hosts, identity, theme)
.env ──────────────▶ Secrets (API keys, IPs, credentials)

Browser ──SSE──▶ FastAPI (uvicorn)
                    ├── Spotify Web API (polling)
                    ├── Philips Hue Bridge API (polling)
                    ├── Yamaha MusicCast API (polling)
                    ├── Ollama API (polling)
                    ├── SSH to NAS (cached)
                    ├── SSH to remote hosts (cached)
                    └── Local system info + log files
```

- **Backend:** Python 3 + FastAPI + uvicorn with background async pollers
- **Frontend:** Single HTML page + vanilla JS + Tailwind CSS (CDN)
- **Live updates:** Server-Sent Events — backend polls data sources, pushes state to all connected clients
- **Config-driven:** `config.yaml` controls everything — agent identity, panels, host tabs, theme
- **No database** — all data is live from APIs, SSH, and the local system
- **No build step** — no npm, no bundler, just static files served by FastAPI

## Quick Start

### Already running OpenClaw?

Just tell your agent:

> *"Set up a dashboard for me using https://github.com/cknzraposo/openclaw-dashboard"*

Your agent knows your network, your devices, and your preferences. It'll clone the repo, write the config, and have it running before you finish your coffee. ☕

### Manual Setup

```bash
git clone https://github.com/cknzraposo/openclaw-dashboard.git
cd openclaw-dashboard
pip install -r requirements.txt

# Configure
cp config.example.yaml config.yaml   # edit: agent name, panels, hosts
cp .env.example .env                 # edit: API keys, IPs, credentials

# Run
uvicorn app:app --host 0.0.0.0 --port 8080
```

Open `http://localhost:8080` — your dashboard is live.

## Configuration

### Agent Identity

```yaml
agent:
  name: "Atlas"              # your agent's name
  emoji: "🌍"                # shown in header and title
  tagline: "Watching over everything"
  theme: "midnight"          # bioluminescent | midnight | terminal | minimal
```

### Enable Panels

Only enable what you have. Everything else stays hidden:

```yaml
spotify:
  enabled: true              # needs SPOTIFY_CLIENT_ID in .env

hue:
  enabled: false             # no Hue bridge? skip it

yamaha:
  enabled: true              # MusicCast receiver

ollama:
  enabled: true              # local LLM server

nas:
  enabled: true              # NAS with media library
  name: "Vault"
  media_paths:
    music: "/volume1/Music"
    movies: "/volume1/Movies"
```

### Host Tabs

Monitor any number of machines — each gets its own tab:

```yaml
hosts:
  - name: "Titan"
    type: "local"            # this machine
    emoji: "🖥️"
    tab: true
    show_cron: true

  - name: "Forge"
    type: "ssh"              # remote via SSH
    emoji: "🔥"
    ssh_target: "${REMOTE_HOST_SSH}"  # from .env
    tab: true
    ollama: true             # show Ollama models panel
```

### Secrets

API keys and IPs go in `.env` (never committed):

```bash
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
HUE_BASE=http://your-hue-bridge/api/your-api-key
OLLAMA_HOST=http://10.0.0.20:11434
NAS_SSH_TARGET=user@your-nas
REMOTE_HOST_SSH=user@your-llm-box
```

See `.env.example` for all options.

## Themes

Four built-in themes — switch in `config.yaml` or live in the Settings tab:

| Theme | Vibe |
|---|---|
| `bioluminescent` | Dark slate, amber/cyan glow, deep-sea atmosphere |
| `midnight` | Deep navy, clean whites, subtle blue accents |
| `terminal` | Green-on-black, monospace, retro hacker aesthetic |
| `minimal` | Light mode, clean and simple |

Themes use CSS variables — create your own in `static/themes/custom.css`.

## Tabs

Tabs are generated dynamically from your config:

| Tab | When it appears |
|---|---|
| **Main** | Always — shows enabled panels (now playing, lights, backups, agent status) |
| **Entertainment** | When NAS media is configured |
| **[Host tabs]** | One per machine in `hosts[]` with `tab: true` |
| **Settings** | Always — network devices, theme switcher, system info |
| **Logs** | When log files are configured |

## Systemd Service (optional)

For auto-start on boot:

```ini
[Unit]
Description=OpenClaw Dashboard
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/openclaw-dashboard
ExecStart=/usr/bin/uvicorn app:app --host 0.0.0.0 --port 8080
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable openclaw-dashboard
systemctl --user start openclaw-dashboard
```

## HTTPS (recommended)

Use [mkcert](https://github.com/FiloSottile/mkcert) for trusted local HTTPS:

```bash
mkcert -install
mkcert your-hostname localhost 127.0.0.1
uvicorn app:app --host 0.0.0.0 --port 8080 \
  --ssl-keyfile your-hostname+2-key.pem \
  --ssl-certfile your-hostname+2.pem
```

## Tech Stack

| Component | Choice | Why |
|---|---|---|
| Backend | FastAPI + uvicorn | Async-native, lightweight, perfect for SSE |
| Frontend | Vanilla JS + Tailwind CDN | Zero build step, fast iteration |
| Config | YAML + dotenv | Human-readable, secrets separated |
| Live updates | Server-Sent Events | Simpler than WebSocket for one-way data |
| Data | Direct API calls + SSH | No database needed for live status |

## Security Notes

- **LAN only** — designed for home network use behind a router/NAT
- **No authentication** — relies on network-level security
- **No secrets in repo** — API keys, IPs, and credentials stay in `.env`
- **Config is safe to commit** — `config.yaml` contains structure, not secrets

## Origin Story

This started as a personal dashboard for [Percy](https://percy.raposo.ai) 🪸 — an AI familiar built on OpenClaw. Percy needed a way to see the state of the home network it manages: music playback, smart lights, backup freshness, local LLM servers, and the machines running it all.

It turned out to be useful enough to generalise. Now any OpenClaw agent can have its own dashboard — just change the config.

## Contributing

PRs and ideas welcome. If you adapt it for your own setup, I'd love to hear about it.

Some areas that could use help:
- More themes
- Additional panel types (Home Assistant, Grafana, Pi-hole, etc.)
- Authentication options for non-LAN deployments
- Docker support

## Contributors

- [CK](https://github.com/cknzraposo) — creator
- [GitHub Copilot](https://github.com/features/copilot) — implementation partner (config system, themes, dynamic tabs)
- [Percy](https://percy.raposo.ai) — architecture, specs, and the original dashboard

## License

[MIT](LICENSE) — do whatever you want with it.

---

*Built for [OpenClaw](https://github.com/openclaw/openclaw) agents everywhere.*
