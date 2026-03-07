/* ==========================================================================
   🪸 Percy Dashboard — SSE Client & DOM Updates
   ========================================================================== */

(function () {
    'use strict';

    let evtSource = null;
    let reconnectAttempts = 0;
    const MAX_RECONNECT_DELAY = 30000;

    const TAB_IDS = ['main', 'entertainment', 'morpheus', 'hypnos', 'settings', 'logs'];
    const networkDevices = [
        // Configure your own network devices here
        // ['Device Name', 'IP Address', 'Role'],
        ['Example Host', '10.0.0.1', 'Primary'],
        ['Example NAS', '10.0.0.2', 'Storage'],
    ];

    const galleryState = {
        movies: null,
        music: null,
        storage: null,
    };

    function connect() {
        evtSource = new EventSource('/events');

        evtSource.onopen = () => {
            reconnectAttempts = 0;
            setConnectionStatus('connected');
        };

        evtSource.onmessage = (event) => {
            try {
                const state = JSON.parse(event.data);
                updateAll(state);
            } catch (e) {
                console.error('[percy] Failed to parse SSE data:', e);
            }
        };

        evtSource.onerror = () => {
            setConnectionStatus('error');
            evtSource.close();
            reconnectAttempts++;
            const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), MAX_RECONNECT_DELAY);
            console.log(`[percy] Reconnecting in ${delay}ms...`);
            setTimeout(connect, delay);
        };
    }

    function setConnectionStatus(status) {
        const dot = document.getElementById('connection-dot');
        if (!dot) return;

        dot.className = 'w-2.5 h-2.5 rounded-full ring-2 transition-colors duration-300';
        switch (status) {
            case 'connected':
                dot.className += ' bg-green-400 ring-green-400/30';
                dot.title = 'Connected';
                break;
            case 'error':
                dot.className += ' bg-red-400 ring-red-400/30';
                dot.title = 'Disconnected — reconnecting...';
                break;
            default:
                dot.className += ' bg-gray-600 ring-gray-600/30';
                dot.title = 'Connecting...';
        }
    }

    function updateAll(state) {
        if (state.spotify) updateSpotify(state.spotify);
        if (state.yamaha) updateYamaha(state.yamaha);
        if (state.hue) updateHue(state.hue);
        if (state.backups) updateBackups(state.backups);
        updateMorpheusTab(state.morpheus || null);
        updateHypnosTab(state.hypnos || null, state.hypnos_system || null);
        if (state.bulletin) updateBulletin(state.bulletin);
        if (state.cron) updateCron(state.cron);
        if (state.percy) updatePercy(state.percy);

        const ft = document.getElementById('footer-time');
        if (ft) ft.textContent = new Date().toLocaleTimeString();
    }

    function updateSpotify(data) {
        const panel = document.getElementById('panel-spotify');
        const status = document.getElementById('spotify-status');
        const track = document.getElementById('spotify-track');
        const artist = document.getElementById('spotify-artist');
        const message = document.getElementById('spotify-message');
        const artEl = document.getElementById('spotify-art');
        const progress = document.getElementById('spotify-progress');

        status.textContent = data.status || '';

        if (data.status === 'playing' || data.status === 'paused') {
            track.textContent = data.track || '--';
            artist.textContent = data.artist || '--';
            message.textContent = data.message || '';

            if (data.art_url) {
                artEl.innerHTML = `<img src="${escapeHtml(data.art_url)}" alt="Album art" loading="lazy">`;
            } else {
                artEl.innerHTML = '🎵';
            }

            if (data.duration_ms > 0) {
                const pct = Math.min(100, (data.progress_ms / data.duration_ms) * 100);
                progress.style.width = pct + '%';
            }

            panel.classList.toggle('panel-glow-green', data.status === 'playing');
            panel.classList.toggle('panel-glow-amber', data.status === 'paused');
        } else {
            track.textContent = '--';
            artist.textContent = '--';
            message.textContent = data.message || 'Nothing playing right now';
            artEl.innerHTML = '🎵';
            progress.style.width = '0%';
            panel.classList.remove('panel-glow-green', 'panel-glow-amber');
        }
    }

    function updateYamaha(data) {
        const power = document.getElementById('yamaha-power');
        const volume = document.getElementById('yamaha-volume');
        const input = document.getElementById('yamaha-input');
        const track = document.getElementById('yamaha-track');
        if (!power || !volume || !input || !track) return;

        if (!data || data.status !== 'online') {
            power.textContent = 'offline';
            volume.textContent = '--';
            input.textContent = '--';
            track.textContent = (data && data.message) || 'Yamaha offline';
            return;
        }

        power.textContent = data.power || 'unknown';
        const maxVol = Number(data.max_volume || 80);
        const volVal = Number(data.volume || 0);
        volume.textContent = `${volVal}/${maxVol}${data.mute ? ' (muted)' : ''}`;
        input.textContent = data.input || 'unknown';

        if (data.track || data.artist) {
            track.textContent = [data.track, data.artist].filter(Boolean).join(' — ');
        } else {
            track.textContent = `Input: ${data.input || 'unknown'}`;
        }
    }

    function hueToCSS(hue, sat, bri) {
        const h = Math.round((hue / 65535) * 360);
        const s = Math.round((sat / 254) * 100);
        const l = Math.round(25 + (bri / 254) * 40);
        return `hsl(${h}, ${s}%, ${l}%)`;
    }

    function ctToCSS(ct) {
        const t = (ct - 153) / (500 - 153);
        const r = Math.round(200 + t * 55);
        const g = Math.round(180 + (1 - t) * 40 - t * 30);
        const b = Math.round(255 - t * 120);
        return `rgb(${r}, ${g}, ${b})`;
    }

    function updateHue(data) {
        const body = document.getElementById('hue-body');
        if (!data.rooms || Object.keys(data.rooms).length === 0) {
            body.innerHTML = `<p class="text-sm text-gray-500">${data.message || 'No lights found'}</p>`;
            return;
        }

        let html = '';
        for (const [id, room] of Object.entries(data.rooms)) {
            if (room.error) {
                html += `
                    <div class="hue-room">
                        <div class="hue-color-dot" style="background: #4a5568;"></div>
                        <span class="hue-room-name">${escapeHtml(room.name)}</span>
                        <span class="hue-room-detail">unreachable</span>
                    </div>`;
                continue;
            }

            const isOn = room.on;
            let dotColor = '#2d3748';
            if (isOn) {
                if (room.colormode === 'ct') {
                    dotColor = ctToCSS(room.ct);
                } else {
                    dotColor = hueToCSS(room.hue, room.sat, 200);
                }
            }

            const statusText = isOn ? `${room.brightness}%` : 'off';
            const glowStyle = isOn ? `box-shadow: 0 0 10px ${dotColor}40;` : '';

            html += `
                <div class="hue-room ${isOn ? 'room-on' : ''}">
                    <div class="hue-color-dot" style="background: ${dotColor}; ${glowStyle}"></div>
                    <span class="hue-room-name">${escapeHtml(room.name)}</span>
                    <span class="hue-room-detail">${statusText}</span>
                </div>`;
        }
        body.innerHTML = html;
    }

    function updateBackups(data) {
        const body = document.getElementById('backups-body');
        if (!data.hosts || Object.keys(data.hosts).length === 0) {
            body.innerHTML = `<p class="text-sm text-gray-500">${data.message || 'No backup data'}</p>`;
            return;
        }

        let html = '';
        for (const [name, info] of Object.entries(data.hosts)) {
            const indicatorClass = `backup-${info.health || 'unknown'}`;
            html += `
                <div class="backup-host">
                    <div class="backup-indicator ${indicatorClass}"></div>
                    <div class="flex-1 min-w-0">
                        <div class="text-sm font-semibold text-white/80">${escapeHtml(name)}</div>
                        <div class="text-xs text-gray-500 font-mono mt-0.5">${escapeHtml(info.message)}</div>
                    </div>
                </div>`;
        }
        body.innerHTML = html;
    }

    function fmtUptime(seconds) {
        const value = Number(seconds || 0);
        if (!Number.isFinite(value) || value <= 0) return '--';
        const days = Math.floor(value / 86400);
        const hours = Math.floor((value % 86400) / 3600);
        const mins = Math.floor((value % 3600) / 60);
        if (days > 0) return `${days}d ${hours}h`;
        if (hours > 0) return `${hours}h ${mins}m`;
        return `${mins}m`;
    }

    function pct(used, total) {
        const u = Number(used || 0);
        const t = Number(total || 0);
        if (!Number.isFinite(u) || !Number.isFinite(t) || t <= 0) return 0;
        return Math.max(0, Math.min(100, (u / t) * 100));
    }

    function renderUsageBar(label, used, free, total, unit) {
        const percent = pct(used, total).toFixed(1);
        return `
            <div class="storage-row">
                <div class="flex items-center justify-between text-xs font-mono mb-1">
                    <span class="text-white/75">${label}</span>
                    <span class="text-gray-400">${used}${unit} used · ${free}${unit} free</span>
                </div>
                <div class="storage-bar">
                    <div class="storage-fill" style="width:${percent}%"></div>
                </div>
                <div class="text-[0.68rem] text-gray-500 font-mono mt-1">${total}${unit} total · ${percent}% used</div>
            </div>`;
    }

    function renderToolTable(tools, order) {
        if (!tools || Object.keys(tools).length === 0) {
            return '<p class="text-sm text-gray-500">No tool data yet.</p>';
        }
        const names = order && order.length ? order : Object.keys(tools).sort();
        const rows = names
            .filter((name) => Object.prototype.hasOwnProperty.call(tools, name))
            .map((name) => `<tr><td>${escapeHtml(name)}</td><td class="font-mono">${escapeHtml(String(tools[name] || '--'))}</td></tr>`)
            .join('');
        return `<div class="overflow-x-auto"><table class="device-table"><thead><tr><th>Tool</th><th>Version</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }

    function renderProjectRows(projects) {
        if (!Array.isArray(projects) || projects.length === 0) {
            return '<p class="text-sm text-gray-500">No projects found.</p>';
        }
        const rows = projects.map((project) => `
            <tr>
                <td class="font-mono">${escapeHtml(project.emoji || '📁')}</td>
                <td>${escapeHtml(project.name || '?')}</td>
                <td>${escapeHtml(project.description || '')}</td>
            </tr>`).join('');
        return `<div class="overflow-x-auto"><table class="device-table"><thead><tr><th>Emoji</th><th>Name</th><th>Description</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }

    function updateMorpheusTab(data) {
        const status = document.getElementById('morpheus-overview-status');
        const overviewBody = document.getElementById('morpheus-overview-body');
        const toolsBody = document.getElementById('morpheus-tools-body');
        const servicesBody = document.getElementById('morpheus-services-body');
        const projectsBody = document.getElementById('morpheus-projects-body');
        if (!status || !overviewBody || !toolsBody || !servicesBody || !projectsBody) return;

        if (!data || data.status !== 'online') {
            status.textContent = 'Offline';
            overviewBody.innerHTML = '<p class="text-sm text-gray-500">Waiting for MORPHEUS telemetry...</p>';
            toolsBody.innerHTML = '<p class="text-sm text-gray-500">Waiting for MORPHEUS telemetry...</p>';
            servicesBody.innerHTML = '<p class="text-sm text-gray-500">Waiting for MORPHEUS telemetry...</p>';
            projectsBody.innerHTML = '<p class="text-sm text-gray-500">Waiting for MORPHEUS telemetry...</p>';
            return;
        }

        status.textContent = data.message || 'MORPHEUS is humming along';
        overviewBody.innerHTML = `
            <div class="grid grid-cols-1 lg:grid-cols-3 gap-3 mb-3 text-xs font-mono">
                <div class="stat-block"><span class="stat-label">OS</span><span class="stat-value">${escapeHtml(data.os || '--')}</span></div>
                <div class="stat-block"><span class="stat-label">Kernel</span><span class="stat-value">${escapeHtml(data.kernel || '--')}</span></div>
                <div class="stat-block"><span class="stat-label">CPU</span><span class="stat-value">${escapeHtml(data.cpu || '--')}</span></div>
                <div class="stat-block"><span class="stat-label">LAN IP</span><span class="stat-value">${escapeHtml(data.lan_ip || '--')}</span></div>
                <div class="stat-block"><span class="stat-label">WSL IP</span><span class="stat-value">${escapeHtml(data.wsl_ip || '--')}</span></div>
                <div class="stat-block"><span class="stat-label">Uptime</span><span class="stat-value">${escapeHtml(fmtUptime(data.uptime_seconds))}</span></div>
            </div>
            <div class="space-y-3">
                ${renderUsageBar('RAM', Number(data.ram_used_gb || 0).toFixed(1), Number(data.ram_free_gb || 0).toFixed(1), Number(data.ram_total_gb || 0).toFixed(1), 'GB')}
                ${renderUsageBar('Disk', Number(data.disk_used_gb || 0).toFixed(1), Number(data.disk_free_gb || 0).toFixed(1), Number(data.disk_total_gb || 0).toFixed(1), 'GB')}
            </div>`;

        toolsBody.innerHTML = renderToolTable(data.tools || {}, ['Python', 'Node.js', 'Git', 'gh CLI', 'jq', 'uv', 'Copilot CLI', 'uvicorn']);

        const services = data.services || {};
        const rows = [
            ['cron', services.cron || 'unknown'],
            ['percy-dashboard', services.percy_dashboard || 'unknown'],
            ['SSH forwarding', services.ssh_forwarding || 'unknown'],
        ];
        servicesBody.innerHTML = `
            <div class="space-y-2">
                ${rows.map(([name, value]) => `
                    <div class="bulletin-row">
                        <span class="bulletin-label">${escapeHtml(name)}</span>
                        <span class="bulletin-msg font-mono">${escapeHtml(value)}</span>
                    </div>`).join('')}
                <p class="text-xs text-cyan/80 font-mono mt-2">${escapeHtml(String(data.cron_count || 0))} cron jobs scheduled</p>
            </div>`;

        projectsBody.innerHTML = renderProjectRows(data.projects || []);
    }

    function toGB(sizeBytes) {
        const bytes = Number(sizeBytes || 0);
        if (!Number.isFinite(bytes) || bytes <= 0) return 0;
        return bytes / (1024 ** 3);
    }

    function updateHypnosTab(hypnos, hypnosSystem) {
        const dot = document.getElementById('hypnos-tab-dot');
        const overviewPanel = document.getElementById('panel-hypnos-overview');
        const modelsPanel = document.getElementById('panel-hypnos-models');
        const toolsPanel = document.getElementById('panel-hypnos-tools');
        const projectsPanel = document.getElementById('panel-hypnos-projects');
        const overviewBody = document.getElementById('hypnos-overview-body');
        const modelsBody = document.getElementById('hypnos-models-body');
        const toolsBody = document.getElementById('hypnos-tools-body');
        const projectsBody = document.getElementById('hypnos-projects-body');
        if (!dot || !overviewPanel || !modelsPanel || !toolsPanel || !projectsPanel || !overviewBody || !modelsBody || !toolsBody || !projectsBody) return;

        const online = hypnosSystem && hypnosSystem.status === 'online';
        dot.className = `status-dot ${online ? 'status-online' : (hypnosSystem ? 'status-offline' : 'status-idle')}`;
        overviewPanel.classList.toggle('panel-muted', !online);
        modelsPanel.classList.toggle('panel-muted', !online);
        toolsPanel.classList.toggle('panel-muted', !online);
        projectsPanel.classList.toggle('panel-muted', !online);

        if (!online) {
            overviewBody.innerHTML = `<p class="text-sm text-gray-400 font-mono">HYPNOS is offline — can't reach it</p>`;
            toolsBody.innerHTML = '<p class="text-sm text-gray-500">No tool data while HYPNOS is offline.</p>';
            projectsBody.innerHTML = '<p class="text-sm text-gray-500">No project data while HYPNOS is offline.</p>';
        } else {
            overviewBody.innerHTML = `
                <div class="grid grid-cols-1 lg:grid-cols-3 gap-3 mb-3 text-xs font-mono">
                    <div class="stat-block"><span class="stat-label">Model</span><span class="stat-value">${escapeHtml(hypnosSystem.model || '--')}</span></div>
                    <div class="stat-block"><span class="stat-label">OS</span><span class="stat-value">${escapeHtml(hypnosSystem.os || '--')}</span></div>
                    <div class="stat-block"><span class="stat-label">CPU</span><span class="stat-value">${escapeHtml(hypnosSystem.cpu || '--')}</span></div>
                    <div class="stat-block"><span class="stat-label">IP</span><span class="stat-value">${escapeHtml(hypnosSystem.ip || '--')}</span></div>
                    <div class="stat-block"><span class="stat-label">Status</span><span class="stat-value text-seagreen">Online</span></div>
                    <div class="stat-block"><span class="stat-label">Uptime</span><span class="stat-value">${escapeHtml(fmtUptime(hypnosSystem.uptime_seconds))}</span></div>
                </div>
                <div class="space-y-3">
                    ${renderUsageBar('RAM', Number(hypnosSystem.ram_used_gb || 0).toFixed(1), Number(hypnosSystem.ram_free_gb || 0).toFixed(1), Number(hypnosSystem.ram_total_gb || 0).toFixed(1), 'GB')}
                    ${renderUsageBar('Disk C:', Number(hypnosSystem.disk_used_gb || 0).toFixed(1), Number(hypnosSystem.disk_free_gb || 0).toFixed(1), Number(hypnosSystem.disk_total_gb || 0).toFixed(1), 'GB')}
                </div>`;
            toolsBody.innerHTML = renderToolTable(hypnosSystem.tools || {}, ['Python', 'PowerShell', 'Git', 'Node.js', 'Ollama', 'gh CLI', 'jq', 'uv']);
            projectsBody.innerHTML = renderProjectRows(hypnosSystem.projects || []);
        }

        if (!hypnos || hypnos.status !== 'online') {
            modelsBody.innerHTML = '<p class="text-sm text-gray-400 font-mono">HYPNOS is offline — can\'t fetch Ollama models.</p>';
            return;
        }
        if (!hypnos.models || hypnos.models.length === 0) {
            modelsBody.innerHTML = '<p class="text-sm text-gray-500">No models found.</p>';
            return;
        }

        const running = Array.isArray(hypnos.running) ? hypnos.running : [];
        const rows = hypnos.models.map((model) => {
            const active = running.includes(model.name);
            return `<tr class="${active ? 'model-row-running' : ''}">
                <td>${escapeHtml(model.name || '?')}</td>
                <td class="font-mono">${escapeHtml(model.size || '--')}</td>
                <td class="font-mono">${escapeHtml((model.modified || '').replace('T', ' ').slice(0, 16) || '--')}</td>
            </tr>`;
        }).join('');
        const totalGB = hypnos.models.reduce((sum, model) => sum + toGB(model.size_bytes), 0);
        const loaded = running.length ? running.join(', ') : 'none';
        const loadedSize = hypnos.models
            .filter((model) => running.includes(model.name))
            .reduce((sum, model) => sum + toGB(model.size_bytes), 0);
        modelsBody.innerHTML = `
            <div class="overflow-x-auto mb-3">
                <table class="device-table">
                    <thead><tr><th>Model</th><th>Size</th><th>Modified</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
            <div class="text-xs font-mono text-gray-400">
                <div>Total: ${totalGB.toFixed(1)}GB on disk</div>
                <div>Loaded: ${escapeHtml(loaded)}${running.length ? ` (${loadedSize.toFixed(1)}GB in RAM)` : ''}</div>
            </div>`;
    }

    function updateBulletin(data) {
        const body = document.getElementById('bulletin-body');

        let html = '';

        if (data.prep_message) {
            const statusIcon = data.prep && data.prep.status === 'ok' ? '✓' : '○';
            html += `
                <div class="bulletin-row">
                    <span class="bulletin-label">Prep</span>
                    <span class="bulletin-msg">${statusIcon} ${escapeHtml(data.prep_message)}</span>
                </div>`;
        }

        if (data.send_message) {
            const statusIcon = data.send && data.send.status === 'ok' ? '✓' : '○';
            html += `
                <div class="bulletin-row">
                    <span class="bulletin-label">Send</span>
                    <span class="bulletin-msg">${statusIcon} ${escapeHtml(data.send_message)}</span>
                </div>`;
        }

        if (!html) {
            html = `<p class="text-sm text-gray-500">${escapeHtml(data.message || 'No bulletin data')}</p>`;
        }

        body.innerHTML = html;
    }

    function updateCron(entries) {
        const body = document.getElementById('cron-body');

        if (!entries || entries.length === 0) {
            body.innerHTML = '<p class="text-sm text-gray-500">No cron jobs found</p>';
            return;
        }

        let html = `
            <div class="overflow-x-auto">
                <table class="cron-table">
                    <thead>
                        <tr>
                            <th>When</th>
                            <th>Command</th>
                        </tr>
                    </thead>
                    <tbody>`;

        for (const entry of entries) {
            html += `
                        <tr>
                            <td class="cron-desc">${escapeHtml(entry.description)}</td>
                            <td class="cron-cmd">${escapeHtml(entry.command)}</td>
                        </tr>`;
        }

        html += `
                    </tbody>
                </table>
            </div>`;

        body.innerHTML = html;
    }

    function updatePercy(data) {
        const quip = document.getElementById('percy-quip');
        const uptime = document.getElementById('percy-uptime');
        const python = document.getElementById('percy-python');
        const nzTime = document.getElementById('nz-time');
        const headerUptime = document.getElementById('uptime');
        const tagline = document.getElementById('percy-tagline');
        const sysPython = document.getElementById('sys-python');

        if (quip) quip.textContent = `"${data.quip || '...'}"`;
        if (uptime) uptime.textContent = data.uptime || '--';
        if (python) python.textContent = data.python_version || '--';
        if (nzTime) nzTime.textContent = data.nz_time || '--:-- --';
        if (headerUptime) headerUptime.textContent = `Up ${data.uptime || '--'}`;
        if (tagline) tagline.textContent = data.quip || '...';
        if (sysPython) sysPython.textContent = data.python_version || '--';
    }

    function initTabs() {
        const buttons = Array.from(document.querySelectorAll('.tab-btn'));
        for (const button of buttons) {
            button.addEventListener('click', () => {
                const tab = button.dataset.tab;
                setActiveTab(tab, true);
            });
        }

        window.addEventListener('hashchange', () => {
            const tab = hashToTab(window.location.hash);
            setActiveTab(tab, false);
        });

        setActiveTab(hashToTab(window.location.hash), false);
    }

    function hashToTab(hash) {
        const clean = (hash || '').replace('#', '').toLowerCase();
        return TAB_IDS.includes(clean) ? clean : 'main';
    }

    function setActiveTab(tab, pushHash) {
        const nextTab = TAB_IDS.includes(tab) ? tab : 'main';
        const buttons = Array.from(document.querySelectorAll('.tab-btn'));

        for (const button of buttons) {
            const active = button.dataset.tab === nextTab;
            button.classList.toggle('active', active);
            button.setAttribute('aria-selected', active ? 'true' : 'false');
        }

        for (const id of TAB_IDS) {
            const panel = document.getElementById(`tab-${id}`);
            if (!panel) continue;
            const active = id === nextTab;
            panel.classList.toggle('hidden', !active);
            panel.setAttribute('aria-hidden', active ? 'false' : 'true');
        }

        const nextHash = `#${nextTab}`;
        if (pushHash && window.location.hash !== nextHash) {
            history.replaceState(null, '', nextHash);
        }
    }

    function initStaticPanels() {
        renderNetworkDevices();
        renderSystemInfo();
    }

    function renderNetworkDevices() {
        const body = document.getElementById('network-devices-body');
        if (!body) return;

        let html = '<div class="overflow-x-auto"><table class="device-table"><thead><tr><th>Device</th><th>IP</th><th>Role</th></tr></thead><tbody>';
        for (const [name, ip, role] of networkDevices) {
            html += `<tr><td>${escapeHtml(name)}</td><td class="font-mono">${escapeHtml(ip)}</td><td>${escapeHtml(role)}</td></tr>`;
        }
        html += '</tbody></table></div>';
        body.innerHTML = html;
    }

    function renderSystemInfo() {
        const body = document.getElementById('system-info-body');
        if (!body) return;
        const wslIp = window.location.hostname || '--';

        body.innerHTML = `
            <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-2 text-xs font-mono">
                <div class="stat-block"><span class="stat-label">Host IP</span><span class="stat-value">${escapeHtml(wslIp)}</span></div>
                <div class="stat-block"><span class="stat-label">Dashboard</span><span class="stat-value">${window.location.host}</span></div>
                <div class="stat-block"><span class="stat-label">WSL IP</span><span class="stat-value">${escapeHtml(wslIp)}</span></div>
                <div class="stat-block"><span class="stat-label">Python</span><span class="stat-value" id="sys-python">--</span></div>
                <div class="stat-block"><span class="stat-label">OpenClaw</span><span class="stat-value">v2</span></div>
            </div>`;
    }

    async function loadEntertainmentData() {
        await Promise.all([loadMovies(), loadMusic(), loadStorage()]);
    }

    async function loadMovies() {
        const summary = document.getElementById('movies-summary');
        const list = document.getElementById('movies-list');
        if (!summary || !list) return;
        try {
            const resp = await fetch('/api/galactica/movies');
            const data = await resp.json();
            galleryState.movies = data;
            if (data.status === 'offline') {
                summary.textContent = 'Galactica offline';
                list.innerHTML = '<p class="text-sm text-red-300/80">Galactica offline</p>';
                return;
            }
            summary.textContent = `${data.count} films · ${data.size_gb}GB of movies on Galactica`;
            list.innerHTML = (data.movies || []).map((name) => `<div class="media-item">${escapeHtml(name)}</div>`).join('');
        } catch (_err) {
            summary.textContent = 'Galactica offline';
            list.innerHTML = '<p class="text-sm text-red-300/80">Galactica offline</p>';
        }
    }

    async function loadMusic() {
        const summary = document.getElementById('music-summary');
        const list = document.getElementById('music-list');
        if (!summary || !list) return;
        try {
            const resp = await fetch('/api/galactica/music');
            const data = await resp.json();
            galleryState.music = data;
            if (data.status === 'offline') {
                summary.textContent = 'Galactica offline';
                list.innerHTML = '<p class="text-sm text-red-300/80">Galactica offline</p>';
                return;
            }
            summary.textContent = `${data.count} artists · ${data.size_gb}GB of music on Galactica`;
            renderMusicList('');
        } catch (_err) {
            summary.textContent = 'Galactica offline';
            list.innerHTML = '<p class="text-sm text-red-300/80">Galactica offline</p>';
        }
    }

    async function loadStorage() {
        const body = document.getElementById('storage-body');
        if (!body) return;
        try {
            const resp = await fetch('/api/galactica/storage');
            const data = await resp.json();
            galleryState.storage = data;
            if (data.status === 'offline') {
                body.innerHTML = '<p class="text-sm text-red-300/80">Galactica offline</p>';
                return;
            }
            let html = '';
            for (const drive of (data.drives || [])) {
                html += `
                    <div class="storage-row">
                        <div class="flex items-center justify-between text-xs font-mono mb-1">
                            <span class="text-white/70">${escapeHtml(drive.name)}</span>
                            <span class="text-gray-400">${drive.used_gb}GB used · ${drive.free_gb}GB free</span>
                        </div>
                        <div class="storage-bar">
                            <div class="storage-fill" style="width:${Math.min(100, Math.max(0, drive.pct || 0))}%"></div>
                        </div>
                        <div class="text-[0.68rem] text-gray-500 font-mono mt-1">${drive.total_gb}GB total · ${drive.pct}% full</div>
                    </div>`;
            }
            body.innerHTML = html || '<p class="text-sm text-gray-500">No drive data</p>';
        } catch (_err) {
            body.innerHTML = '<p class="text-sm text-red-300/80">Galactica offline</p>';
        }
    }

    function initMusicSearch() {
        const input = document.getElementById('music-search');
        if (!input) return;
        input.addEventListener('input', () => {
            renderMusicList(input.value || '');
        });
    }

    function renderMusicList(query) {
        const list = document.getElementById('music-list');
        if (!list || !galleryState.music || !Array.isArray(galleryState.music.artists)) return;
        const q = query.trim().toLowerCase();
        const artists = q
            ? galleryState.music.artists.filter((name) => String(name).toLowerCase().includes(q))
            : galleryState.music.artists;
        if (artists.length === 0) {
            list.innerHTML = '<p class="text-sm text-gray-500">No artists match this filter.</p>';
            return;
        }
        list.innerHTML = artists.map((name) => `<div class="media-item">${escapeHtml(name)}</div>`).join('');
    }

    async function refreshLogs() {
        await Promise.all([
            fetchLog('bulletin-prep', 'log-bulletin-prep'),
            fetchLog('bulletin-send', 'log-bulletin-send'),
            fetchLog('backup', 'log-backup'),
            fetchLog('hypnos-draft', 'log-hypnos-draft'),
        ]);
    }

    async function fetchLog(name, elementId) {
        const el = document.getElementById(elementId);
        if (!el) return;

        try {
            const resp = await fetch(`/api/logs/${encodeURIComponent(name)}`);
            const data = await resp.json();
            const lines = Array.isArray(data.lines) ? data.lines : [];
            el.textContent = lines.length ? lines.join('\n') : (data.message || 'No log data');
            el.scrollTop = el.scrollHeight;
        } catch (_err) {
            el.textContent = name === 'hypnos-draft' ? 'HYPNOS offline' : 'Log unavailable';
        }
    }

    function escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    document.addEventListener('DOMContentLoaded', () => {
        initTabs();
        initStaticPanels();
        initMusicSearch();
        loadEntertainmentData();
        refreshLogs();
        setInterval(refreshLogs, 20000);
        connect();
    });
})();
