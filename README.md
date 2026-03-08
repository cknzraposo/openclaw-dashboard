# 🪸 OpenClaw Dashboard

A real-time home network dashboard for [OpenClaw](https://github.com/openclaw/openclaw) agents. One file to edit, one command to run, zero build step.

![Python](https://img.shields.io/badge/Python-3.10+-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green) ![License](https://img.shields.io/badge/License-MIT-yellow)

## What is OpenClaw?

[OpenClaw](https://docs.openclaw.ai) is an open-source AI agent framework that gives your AI assistant persistent memory, tool access, and the ability to interact with your world - files, APIs, devices, messaging platforms, and more. Think of it as the operating system for your personal AI.

This dashboard gives your OpenClaw agent a visual status page - a single-pane view of the infrastructure it monitors and controls.

## What It Does

Your agent manages services across your home network. This dashboard shows their vital signs, streamed live via Server-Sent Events:

- **Now Playing** - Spotify playback + Yamaha MusicCast receiver status
- **Hue Lights** - Philips Hue room states (on/off, brightness, colour)
- **Backups** - Freshness indicators for automated backups to NAS
- **Ollama Models** - Local LLM inventory and loaded model status
- **Entertainment** - Movies, music, and photos stored on NAS
- **System Health** - CPU, RAM, disk, uptime, and tool versions for each host
- **Cron Jobs** - Scheduled task overview with human-readable descriptions
- **Log Viewer** - Tail view of pipeline and backup logs

Every panel is included. Comment out what you don't need.

![OpenClaw Dashboard — Main tab showing Now Playing, Lights, Backups, and Agent Status](docs/screenshot.png)
*Dark bioluminescent theme - slate with amber/cyan glow.*

## Quick Start

```bash
git clone https://github.com/cknzraposo/openclaw-dashboard.git
cd openclaw-dashboard
pip install -r requirements.txt
cp .env.example .env    # add your API keys
python app.py
```

Open `http://localhost:8080`.

## Make It Yours

1. **Secrets** go in `.env` - Spotify client ID/secret, Hue API key, SSH credentials.
2. **Everything else** is at the top of `app.py` - agent name, host IPs, NAS paths, log files. Clear comments show what to change.

No config files to learn. No theme switching. No framework. Just Python you can read and edit.

## Architecture

```
.env ──────────────▶ Secrets (API keys, credentials)
app.py ────────────▶ Everything else (edit the top variables)

Browser ──SSE──▶ FastAPI (uvicorn)
                    ├── Spotify Web API (polling)
                    ├── Philips Hue Bridge API (polling)
                    ├── Yamaha MusicCast API (polling)
                    ├── Ollama API (polling)
                    ├── SSH to NAS (cached)
                    ├── SSH to remote hosts (cached)
                    └── Local system info + log files
```

- **Backend:** Python 3 + FastAPI + uvicorn with async pollers
- **Frontend:** Single HTML page + vanilla JS + Tailwind CSS (CDN)
- **Live updates:** SSE - backend polls data sources, pushes state to all connected clients
- **No database** - all data is live from APIs, SSH, and the local system
- **No build step** - no npm, no bundler, just static files served by FastAPI

## Notes

- Missing or unconfigured services fail gracefully and show as offline/unavailable.
- Default port is `8080`.

## License

[MIT](LICENSE)
