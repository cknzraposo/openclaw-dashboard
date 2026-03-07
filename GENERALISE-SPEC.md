# OpenClaw Dashboard — Generalisation Spec

## Goal

Transform the Percy-specific dashboard into a configurable, agent-agnostic dashboard that anyone running OpenClaw can customise. Agent name, theme, panels, host tabs, and data sources are all driven by `config.yaml` + `.env`.

## Architecture

```
config.yaml          — what to show (panels, hosts, identity, theme)
.env                 — secrets (API keys, IPs, credentials)
config.example.yaml  — reference config (committed)
.env.example         — reference secrets (committed)
```

The app loads `config.yaml` at startup. Every panel, tab, and poller is conditional on config. If a section is `enabled: false` or missing, that panel doesn't render, that poller doesn't start, that tab doesn't appear.

## Phase 1: Config-Driven Panels

### 1.1 Config Loader

Add a `config.py` module (or section at top of `app.py`):

```python
import yaml
from pathlib import Path

CONFIG_FILE = Path("config.yaml")
DEFAULT_CONFIG = {
    "agent": {"name": "Agent", "emoji": "🤖", "tagline": "", "avatar": None, "theme": "bioluminescent"},
    "spotify": {"enabled": False},
    "hue": {"enabled": False},
    "yamaha": {"enabled": False},
    "ollama": {"enabled": False},
    "nas": {"enabled": False},
    "backups": {"enabled": False},
    "logs": {"enabled": False},
    "hosts": [],
    "network_devices": [],
}

def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            user = yaml.safe_load(f) or {}
        # Merge: user overrides defaults
        config = {**DEFAULT_CONFIG, **user}
    else:
        config = DEFAULT_CONFIG.copy()
    
    # Expand env vars in string values (e.g. ${REMOTE_HOST_SSH})
    config = expand_env_vars(config)
    return config

def expand_env_vars(obj):
    """Recursively expand ${VAR} references in string values from os.environ."""
    if isinstance(obj, str):
        import re
        return re.sub(r'\$\{(\w+)\}', lambda m: os.environ.get(m.group(1), m.group(0)), obj)
    elif isinstance(obj, dict):
        return {k: expand_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [expand_env_vars(i) for i in obj]
    return obj
```

### 1.2 Conditional Pollers

In the startup event, only start pollers for enabled services:

```python
config = load_config()

if config.get("spotify", {}).get("enabled"):
    asyncio.create_task(poll_spotify())
if config.get("hue", {}).get("enabled"):
    asyncio.create_task(poll_hue())
if config.get("yamaha", {}).get("enabled"):
    asyncio.create_task(poll_yamaha())
if config.get("ollama", {}).get("enabled"):
    asyncio.create_task(poll_hypnos())  # rename to poll_ollama internally
if config.get("backups", {}).get("enabled"):
    asyncio.create_task(poll_backups())
if config.get("nas", {}).get("enabled"):
    # NAS doesn't poll continuously — it's cached on-demand
    pass
for host in config.get("hosts", []):
    if host.get("tab"):
        if host["type"] == "local":
            asyncio.create_task(poll_local_host(host))
        elif host["type"] == "ssh":
            asyncio.create_task(poll_ssh_host(host))
```

### 1.3 Config API Endpoint

Add `GET /api/config` that returns the non-secret parts of config (for frontend rendering):

```python
@app.get("/api/config")
async def get_config():
    """Return config for frontend (agent identity, enabled panels, hosts, theme)."""
    safe = {
        "agent": config["agent"],
        "panels": {
            "spotify": config.get("spotify", {}).get("enabled", False),
            "hue": config.get("hue", {}).get("enabled", False),
            "yamaha": config.get("yamaha", {}).get("enabled", False),
            "ollama": config.get("ollama", {}).get("enabled", False),
            "nas": config.get("nas", {}).get("enabled", False),
            "backups": config.get("backups", {}).get("enabled", False),
            "logs": config.get("logs", {}).get("enabled", False),
        },
        "hosts": [{"name": h["name"], "emoji": h.get("emoji", "🖥️"), "type": h["type"], 
                    "tab": h.get("tab", False), "ollama": h.get("ollama", False),
                    "show_cron": h.get("show_cron", False)} for h in config.get("hosts", [])],
        "network_devices": config.get("network_devices", []),
        "log_files": {k: v.get("label", k) for k, v in config.get("logs", {}).get("files", {}).items()} if config.get("logs", {}).get("enabled") else {},
    }
    return safe
```

### 1.4 Agent Identity in Frontend

The frontend fetches `/api/config` on load and uses it to:
- Set page title: `${agent.name} Dashboard`
- Set header: `${agent.emoji} ${agent.name}`
- Set tagline in header area
- Set avatar if provided
- Load the right theme CSS

Replace all hardcoded "Percy" / "🪸" references in HTML/JS with config values.

### 1.5 Hue Groups from Config

Currently `HUE_GROUPS` is hardcoded. Read from `config.yaml`:
```python
HUE_GROUPS = config.get("hue", {}).get("groups", {})
```

### 1.6 NAS Config

Media paths, NAS name, SSH target — all from config:
```python
NAS_NAME = config.get("nas", {}).get("name", "NAS")
NAS_MEDIA_PATHS = config.get("nas", {}).get("media_paths", {})
```

### 1.7 Log Files from Config

```python
LOG_FILES = {}
if config.get("logs", {}).get("enabled"):
    for key, info in config["logs"].get("files", {}).items():
        LOG_FILES[key] = Path(info["path"]).expanduser()
```

## Phase 2: Theme System

### 2.1 Built-in Themes

Create `static/themes/` directory with 4 CSS files:
- `bioluminescent.css` — current Percy theme (extract from style.css)
- `midnight.css` — deep navy backgrounds, clean white text, subtle blue accents
- `terminal.css` — black background, green text (#00ff00), monospace everything, scanline effect
- `minimal.css` — light mode, white/grey backgrounds, dark text, no glow effects

### 2.2 Theme Structure

Each theme CSS defines:
```css
:root {
    --bg-primary: #0a0e14;
    --bg-panel: #111820;
    --bg-panel-hover: #1a2332;
    --text-primary: #e0e0e0;
    --text-secondary: #8899aa;
    --accent-1: #ff9f43;        /* primary accent */
    --accent-2: #00d2ff;        /* secondary accent */
    --accent-glow: rgba(255, 159, 67, 0.3);
    --status-green: #00e676;
    --status-red: #ff5252;
    --status-yellow: #ffd740;
    --border-color: rgba(255,255,255,0.08);
    --font-heading: 'Space Grotesk', sans-serif;
    --font-mono: 'Space Mono', monospace;
}
```

The main `style.css` uses only CSS variables. Theme files override the variables.

### 2.3 Theme Loading

Frontend loads theme based on `/api/config` response:
```javascript
function loadTheme(themeName) {
    const link = document.getElementById('theme-css');
    link.href = `/static/themes/${themeName}.css`;
}
```

### 2.4 Theme Switching (Settings Tab)

Add a theme selector dropdown in the Settings tab. Selecting a theme:
1. Saves to localStorage
2. Swaps the CSS file
3. No server restart needed

## Phase 3: Dynamic Host Tabs

### 3.1 Remove Hardcoded MORPHEUS/HYPNOS

Delete the static `<section id="tab-morpheus">` and `<section id="tab-hypnos">` HTML.
Delete `updateMorpheusTab()` and `updateHypnosTab()` JS functions.

### 3.2 Dynamic Tab Generation

On page load, after fetching `/api/config`:

```javascript
async function init() {
    const config = await fetch('/api/config').then(r => r.json());
    
    // Build tab list dynamically
    const tabs = ['main'];
    if (config.panels.nas) tabs.push('entertainment');
    for (const host of config.hosts) {
        if (host.tab) tabs.push(host.name.toLowerCase());
    }
    tabs.push('settings');
    if (config.panels.logs) tabs.push('logs');
    
    renderTabBar(tabs, config);
    renderTabContent(tabs, config);
    connectSSE();
}
```

### 3.3 Generic Host Tab Renderer

One function that renders any host tab:

```javascript
function renderHostTab(host, systemData, ollamaData) {
    return `
    <div class="panel">
        <div class="panel-header">${host.emoji} ${host.name} — System</div>
        <div class="panel-body">
            ${renderSystemOverview(systemData)}
        </div>
    </div>
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div class="panel">
            <div class="panel-header">Tools & CLIs</div>
            <div class="panel-body">${renderToolsTable(systemData.tools)}</div>
        </div>
        ${host.ollama && ollamaData ? `
        <div class="panel">
            <div class="panel-header">Ollama Models</div>
            <div class="panel-body">${renderOllamaModels(ollamaData)}</div>
        </div>` : `
        <div class="panel">
            <div class="panel-header">Services</div>
            <div class="panel-body">${renderServices(systemData)}</div>
        </div>`}
    </div>
    ${systemData.projects ? `
    <div class="panel">
        <div class="panel-header">Projects</div>
        <div class="panel-body">${renderProjects(systemData.projects, host.project_descriptions)}</div>
    </div>` : ''}
    ${host.show_cron ? `
    <div class="panel">
        <div class="panel-header">Cron Schedule</div>
        <div class="panel-body" id="cron-body"></div>
    </div>` : ''}`;
}
```

### 3.4 Generic Host Pollers

Backend pollers are generic — driven by host config:

```python
async def poll_local_host(host_config: dict):
    """Poll local machine system info."""
    name = host_config["name"].lower()
    while True:
        data = await collect_local_system(host_config)
        state[f"host_{name}"] = data
        await asyncio.sleep(60)

async def poll_ssh_host(host_config: dict):
    """Poll remote machine system info via SSH."""
    name = host_config["name"].lower()
    while True:
        data = await collect_ssh_system(host_config)
        state[f"host_{name}"] = data
        await asyncio.sleep(60)
```

State keys become `host_morpheus`, `host_hypnos`, etc. — dynamically named from config.

### 3.5 Main Tab Conditional Panels

Main tab only shows panels that are enabled:

```javascript
function renderMainTab(config, state) {
    let html = '';
    if (config.panels.spotify || config.panels.yamaha) {
        html += renderNowPlaying(state, config);
    }
    if (config.panels.hue) {
        html += renderHueLights(state);
    }
    if (config.panels.backups) {
        html += renderBackups(state);
    }
    // Agent status always shown
    html += renderAgentStatus(state, config.agent);
    return html;
}
```

## Important Rules

1. **Do NOT hardcode any device names, IPs, or agent names** — everything from config
2. **Graceful degradation** — if a service is unreachable, show "offline" not crash
3. **Config is optional** — if no `config.yaml` exists, use defaults (basic dashboard with no panels)
4. **`.env` is for secrets, `config.yaml` is for structure** — never put API keys in config.yaml
5. **Existing panel logic must be preserved** — the rendering, SSE, polling patterns all stay the same
6. **All 4 themes must look good** — not just the bioluminescent one
7. **Tab bar must handle 2-8 tabs gracefully** — responsive, no overflow
8. **Keep it vanilla** — no npm, no React, no build step. Tailwind CDN + plain JS.
9. **`config.example.yaml` must be comprehensive** — show every option with comments
10. **Test:** `python3 -m compileall app.py` and `node --check static/app.js` must pass
11. **Commit** with descriptive message when done

## Files to Modify
- `app.py` — config loader, conditional pollers, generic host collectors, `/api/config` endpoint
- `static/index.html` — remove hardcoded tabs/panels, add theme link tag, minimal shell
- `static/app.js` — dynamic tab generation, generic host renderer, config-driven panel rendering
- `static/style.css` — convert to CSS variables only (no hardcoded colours)

## Files to Create
- `static/themes/bioluminescent.css`
- `static/themes/midnight.css`
- `static/themes/terminal.css`
- `static/themes/minimal.css`

## Files NOT to Touch
- `config.example.yaml` (already created)
- `.env.example` (already exists)
- `LICENSE` (already exists)
- `.gitignore` (already exists)
