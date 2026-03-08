(function () {
    'use strict';

    let config = null;
    let tabs = [];
    let state = {};
    let eventSource = null;
    let reconnectAttempts = 0;
    const MAX_RECONNECT_DELAY = 30000;
    const nasState = { media: {}, storage: null };

    function slugify(value) {
        return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'host';
    }

    function libraryId(name) {
        return `nas_${slugify(name)}`;
    }

    function escapeHtml(str) {
        if (str === null || str === undefined) return '';
        const div = document.createElement('div');
        div.textContent = String(str);
        return div.innerHTML;
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

    function applyIdentity() {
        const agent = config.agent || {};
        document.title = `${agent.name || 'Agent'} Dashboard`;
        const icon = agent.emoji || '🤖';
        const iconHref = `data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>${encodeURIComponent(icon)}</text></svg>`;
        let favicon = document.querySelector('link[rel="icon"]');
        if (!favicon) {
            favicon = document.createElement('link');
            favicon.rel = 'icon';
            document.head.appendChild(favicon);
        }
        favicon.href = iconHref;
        document.getElementById('agent-emoji').textContent = icon;
        document.getElementById('agent-title').textContent = `${agent.name || 'Agent'} Dashboard`;
        document.getElementById('agent-tagline').textContent = agent.tagline || 'Monitoring system status';
        document.getElementById('footer-agent').textContent = `${icon} ${agent.name || 'Agent'}`;
        const avatar = document.getElementById('agent-avatar');
        if (agent.avatar) {
            avatar.src = agent.avatar.startsWith('/') ? agent.avatar : `/${agent.avatar.replace(/^\/+/, '')}`;
            avatar.classList.remove('hidden');
        } else {
            avatar.classList.add('hidden');
        }
    }

    function buildTabs() {
        tabs = [{ id: 'main', label: 'Main', type: 'main' }];
        tabs.push({ id: 'entertainment', label: config.nas?.name || 'Media', type: 'nas' });
        for (const host of config.hosts || []) {
            if (host.tab) tabs.push({ id: host.slug || slugify(host.name), label: `${host.emoji || '🖥️'} ${host.name}`, type: 'host', host });
        }
        tabs.push({ id: 'settings', label: 'Settings', type: 'settings' });
        tabs.push({ id: 'logs', label: 'Logs', type: 'logs' });
    }

    function renderTabs() {
        const nav = document.getElementById('tab-nav');
        nav.innerHTML = tabs.map((tab, idx) => `<button class="tab-btn ${idx === 0 ? 'active' : ''}" data-tab="${tab.id}" role="tab" aria-selected="${idx === 0}">${escapeHtml(tab.label)}</button>`).join('');
        for (const button of nav.querySelectorAll('.tab-btn')) {
            button.addEventListener('click', () => setActiveTab(button.dataset.tab, true));
        }
        window.addEventListener('hashchange', () => setActiveTab(hashToTab(window.location.hash), false));
    }

    function renderLayout() {
        const root = document.getElementById('tab-content');
        root.innerHTML = tabs.map((tab, idx) => `<section id="tab-${tab.id}" class="tab-content ${idx === 0 ? '' : 'hidden'}" aria-hidden="${idx === 0 ? 'false' : 'true'}">${renderTabInner(tab)}</section>`).join('');
        initMusicSearch();
    }

    function renderTabInner(tab) {
        if (tab.type === 'main') return renderMainTab();
        if (tab.type === 'nas') return renderNasTab();
        if (tab.type === 'host') return renderHostShell(tab.host);
        if (tab.type === 'settings') return renderSettingsTab();
        if (tab.type === 'logs') return renderLogsTab();
        return '';
    }

    function renderMainTab() {
        return `
            <div class="grid grid-cols-1 lg:grid-cols-3 gap-5">
                <div class="lg:col-span-2">
                    <div class="panel" id="panel-spotify">
                        <div class="panel-header"><span class="panel-icon">🎵</span><span class="panel-title">Now Playing</span><span class="panel-status" id="spotify-status">...</span></div>
                        <div class="panel-body">
                            <div class="flex items-center gap-5">
                                <div id="spotify-art" class="w-24 h-24 rounded-lg bg-[color:var(--bg-panel-hover)] flex-shrink-0 flex items-center justify-center text-3xl overflow-hidden">🎵</div>
                                <div class="flex-1 min-w-0">
                                    <p class="text-lg font-semibold truncate" id="spotify-track">--</p>
                                    <p class="text-sm text-[color:var(--text-secondary)] truncate" id="spotify-artist">--</p>
                                    <p class="text-xs mt-1 font-mono text-[color:var(--text-muted)]" id="spotify-message">Waiting for data...</p>
                                    <div class="mt-2 h-1 rounded-full bg-[color:var(--bg-panel-hover)] overflow-hidden"><div class="h-full rounded-full storage-fill" id="spotify-progress" style="width: 0%"></div></div>
                                </div>
                            </div>
                            <div class="mt-4 pt-3 border-t border-[color:var(--border-color)] space-y-1.5">
                                <div class="grid grid-cols-1 sm:grid-cols-3 gap-2 text-xs font-mono">
                                    <div class="stat-block"><span class="stat-label">Power</span><span class="stat-value" id="yamaha-power">--</span></div>
                                    <div class="stat-block"><span class="stat-label">Volume</span><span class="stat-value" id="yamaha-volume">--</span></div>
                                    <div class="stat-block"><span class="stat-label">Input</span><span class="stat-value" id="yamaha-input">--</span></div>
                                </div>
                                <p class="text-xs font-mono text-[color:var(--text-muted)]" id="yamaha-track">Receiver unavailable</p>
                            </div>
                        </div>
                    </div>
                </div>

                <div>
                    <div class="panel panel-glow-cyan">
                        <div class="panel-header"><span class="panel-icon">${escapeHtml(config.agent?.emoji || '🤖')}</span><span class="panel-title">Agent Status</span><span class="status-dot status-online"></span></div>
                        <div class="panel-body space-y-3">
                            <p class="text-sm italic text-[color:var(--accent-2)]" id="agent-quip">"..."</p>
                            <div class="grid grid-cols-2 gap-2 text-xs font-mono">
                                <div class="stat-block"><span class="stat-label">Uptime</span><span class="stat-value" id="agent-uptime">--</span></div>
                                <div class="stat-block"><span class="stat-label">Python</span><span class="stat-value" id="agent-python">--</span></div>
                            </div>
                        </div>
                    </div>
                </div>

                <div><div class="panel"><div class="panel-header"><span class="panel-icon">💡</span><span class="panel-title">Lights</span></div><div class="panel-body space-y-3" id="hue-body"><p class="text-sm text-[color:var(--text-muted)]">Waiting for data...</p></div></div></div>
                <div><div class="panel"><div class="panel-header"><span class="panel-icon">💾</span><span class="panel-title">Backups</span></div><div class="panel-body space-y-3" id="backups-body"><p class="text-sm text-[color:var(--text-muted)]">Waiting for data...</p></div></div></div>
            </div>`;
    }

    function renderNasTab() {
        const names = config.nas?.media_paths || [];
        return `
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
                ${names.map((name) => {
                    const id = libraryId(name);
                    return `
                <div class="panel">
                    <div class="panel-header"><span class="panel-icon">📁</span><span class="panel-title">${escapeHtml(name)}</span></div>
                    <div class="panel-body">
                        <p class="text-xs font-mono text-[color:var(--text-secondary)] mb-3" id="nas-summary-${id}">Loading...</p>
                        <div id="nas-list-${id}" class="media-list media-list-tall"><p class="text-sm text-[color:var(--text-muted)]">Waiting for data...</p></div>
                    </div>
                </div>`;
                }).join('')}
                <div class="panel">
                    <div class="panel-header"><span class="panel-icon">💿</span><span class="panel-title">Storage</span></div>
                    <div class="panel-body space-y-3" id="storage-body"><p class="text-sm text-[color:var(--text-muted)]">Loading storage data...</p></div>
                </div>
            </div>`;
    }

    function renderHostShell(host) {
        const slug = host.slug || slugify(host.name);
        return `
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
                <div class="lg:col-span-2">
                    <div class="panel panel-glow-cyan" id="panel-host-overview-${slug}">
                        <div class="panel-header"><span class="panel-icon">${escapeHtml(host.emoji || '🖥️')}</span><span class="panel-title">${escapeHtml(host.name)} System Overview</span><span class="status-dot" id="host-dot-${slug}"></span></div>
                        <div class="panel-body" id="host-overview-${slug}"><p class="text-sm text-[color:var(--text-muted)]">Waiting for telemetry...</p></div>
                    </div>
                </div>
                <div><div class="panel"><div class="panel-header"><span class="panel-icon">🛠️</span><span class="panel-title">Tools & CLIs</span></div><div class="panel-body" id="host-tools-${slug}"><p class="text-sm text-[color:var(--text-muted)]">Waiting for telemetry...</p></div></div></div>
                <div><div class="panel"><div class="panel-header"><span class="panel-icon">${host.ollama ? '🧠' : '⚙️'}</span><span class="panel-title">${host.ollama ? 'Ollama Models' : 'Services'}</span></div><div class="panel-body" id="host-side-${slug}"><p class="text-sm text-[color:var(--text-muted)]">Waiting for telemetry...</p></div></div></div>
                <div class="lg:col-span-2"><div class="panel"><div class="panel-header"><span class="panel-icon">🗂️</span><span class="panel-title">Projects</span></div><div class="panel-body" id="host-projects-${slug}"><p class="text-sm text-[color:var(--text-muted)]">Waiting for telemetry...</p></div></div></div>
                ${host.show_cron ? `<div class="lg:col-span-2"><div class="panel"><div class="panel-header"><span class="panel-icon">⏰</span><span class="panel-title">Cron Schedule</span></div><div class="panel-body" id="host-cron-${slug}"><p class="text-sm text-[color:var(--text-muted)]">Waiting for telemetry...</p></div></div></div>` : ''}
            </div>`;
    }

    function renderSettingsTab() {
        return `
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
                <div><div class="panel"><div class="panel-header"><span class="panel-icon">🌐</span><span class="panel-title">Network Devices</span></div><div class="panel-body" id="network-devices-body"></div></div></div>
                <div><div class="panel"><div class="panel-header"><span class="panel-icon">🔧</span><span class="panel-title">System Info</span></div><div class="panel-body" id="system-info-body"></div></div></div>
            </div>`;
    }

    function renderLogsTab() {
        const entries = Object.entries(config.log_files || {});
        return `<div class="grid grid-cols-1 lg:grid-cols-2 gap-5">${entries.map(([name, label]) => `
            <div class="panel">
                <div class="panel-header"><span class="panel-icon">📄</span><span class="panel-title">${escapeHtml(label)}</span></div>
                <div class="panel-body"><pre id="log-${escapeHtml(name)}" class="log-block log-block-xl">Loading...</pre></div>
            </div>`).join('')}</div>`;
    }

    function hashToTab(hash) {
        const clean = (hash || '').replace('#', '').toLowerCase();
        return tabs.some((t) => t.id === clean) ? clean : 'main';
    }

    function setActiveTab(tab, pushHash) {
        const next = tabs.some((t) => t.id === tab) ? tab : 'main';
        for (const btn of document.querySelectorAll('.tab-btn')) {
            const active = btn.dataset.tab === next;
            btn.classList.toggle('active', active);
            btn.setAttribute('aria-selected', active ? 'true' : 'false');
        }
        for (const t of tabs) {
            const panel = document.getElementById(`tab-${t.id}`);
            if (!panel) continue;
            const active = t.id === next;
            panel.classList.toggle('hidden', !active);
            panel.setAttribute('aria-hidden', active ? 'false' : 'true');
        }
        const nextHash = `#${next}`;
        if (pushHash && window.location.hash !== nextHash) history.replaceState(null, '', nextHash);
    }

    function renderNetworkDevices() {
        const body = document.getElementById('network-devices-body');
        if (!body) return;
        const rows = (config.network_devices || []).map((d) => `<tr><td>${escapeHtml(d.name || '--')}</td><td class="font-mono">${escapeHtml(d.ip || '--')}</td><td>${escapeHtml(d.role || '--')}</td></tr>`).join('');
        body.innerHTML = `<div class="overflow-x-auto"><table class="device-table"><thead><tr><th>Device</th><th>IP</th><th>Role</th></tr></thead><tbody>${rows || '<tr><td colspan="3">No configured devices</td></tr>'}</tbody></table></div>`;
    }

    function renderSystemInfo() {
        const body = document.getElementById('system-info-body');
        if (!body) return;
        const host = window.location.hostname || '--';
        body.innerHTML = `
            <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 text-xs font-mono">
                <div class="stat-block"><span class="stat-label">Host IP</span><span class="stat-value">${escapeHtml(host)}</span></div>
                <div class="stat-block"><span class="stat-label">Dashboard</span><span class="stat-value">${escapeHtml(window.location.host)}</span></div>
                <div class="stat-block"><span class="stat-label">Python</span><span class="stat-value" id="sys-python">--</span></div>
            </div>`;
    }

    function setConnectionStatus(status) {
        const dot = document.getElementById('connection-dot');
        if (!dot) return;
        dot.className = 'w-2.5 h-2.5 rounded-full ring-2';
        if (status === 'connected') {
            dot.classList.add('bg-green-400', 'ring-green-400/30');
            dot.title = 'Connected';
        } else if (status === 'error') {
            dot.classList.add('bg-red-400', 'ring-red-400/30');
            dot.title = 'Disconnected — reconnecting';
        } else {
            dot.classList.add('bg-gray-600', 'ring-gray-600/30');
            dot.title = 'Connecting...';
        }
    }

    function connect() {
        eventSource = new EventSource('/events');
        eventSource.onopen = () => {
            reconnectAttempts = 0;
            setConnectionStatus('connected');
        };
        eventSource.onmessage = (event) => {
            try {
                state = JSON.parse(event.data);
                updateAll();
            } catch (err) {
                console.error('SSE parse failed:', err);
            }
        };
        eventSource.onerror = () => {
            setConnectionStatus('error');
            eventSource.close();
            reconnectAttempts++;
            const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), MAX_RECONNECT_DELAY);
            setTimeout(connect, delay);
        };
    }

    function updateAll() {
        updateSpotify(state.spotify || {});
        updateYamaha(state.yamaha || {});
        updateHue(state.hue || {});
        updateBackups(state.backups || {});
        updateAgent(state.agent || {});
        for (const host of config.hosts || []) {
            if (host.tab) updateHostTab(host, state[`host_${host.slug || slugify(host.name)}`] || null, state.ollama || null);
        }
        const ft = document.getElementById('footer-time');
        if (ft) ft.textContent = new Date().toLocaleTimeString();
    }

    function updateSpotify(data) {
        const status = document.getElementById('spotify-status');
        const track = document.getElementById('spotify-track');
        const artist = document.getElementById('spotify-artist');
        const message = document.getElementById('spotify-message');
        const art = document.getElementById('spotify-art');
        const progress = document.getElementById('spotify-progress');
        if (!status || !track || !artist || !message || !art || !progress) return;
        status.textContent = data.status || '';
        if (data.status === 'playing' || data.status === 'paused') {
            track.textContent = data.track || '--';
            artist.textContent = data.artist || '--';
            message.textContent = data.message || '';
            art.innerHTML = data.art_url ? `<img src="${escapeHtml(data.art_url)}" alt="Album art" loading="lazy">` : '🎵';
            progress.style.width = data.duration_ms > 0 ? `${Math.min(100, (Number(data.progress_ms || 0) / Number(data.duration_ms || 1)) * 100)}%` : '0%';
        } else {
            track.textContent = '--';
            artist.textContent = '--';
            message.textContent = data.message || 'Nothing playing';
            art.innerHTML = '🎵';
            progress.style.width = '0%';
        }
    }

    function updateYamaha(data) {
        const power = document.getElementById('yamaha-power');
        const volume = document.getElementById('yamaha-volume');
        const input = document.getElementById('yamaha-input');
        const track = document.getElementById('yamaha-track');
        if (!power || !volume || !input || !track) return;
        if (!data || data.status !== 'online') {
            power.textContent = 'offline'; volume.textContent = '--'; input.textContent = '--'; track.textContent = data?.message || 'Receiver unavailable';
            return;
        }
        power.textContent = data.power || 'unknown';
        volume.textContent = `${data.volume || 0}/${data.max_volume || 80}${data.mute ? ' (muted)' : ''}`;
        input.textContent = data.input || 'unknown';
        track.textContent = data.track || data.artist ? `${data.track || ''}${data.track && data.artist ? ' — ' : ''}${data.artist || ''}` : `Input: ${data.input || 'unknown'}`;
    }

    function hueToCSS(hue, sat, bri) {
        const h = Math.round((Number(hue || 0) / 65535) * 360);
        const s = Math.round((Number(sat || 0) / 254) * 100);
        const l = Math.round(25 + (Number(bri || 0) / 254) * 40);
        return `hsl(${h}, ${s}%, ${l}%)`;
    }

    function ctToCSS(ct) {
        const t = (Number(ct || 153) - 153) / (500 - 153);
        const r = Math.round(200 + t * 55);
        const g = Math.round(180 + (1 - t) * 40 - t * 30);
        const b = Math.round(255 - t * 120);
        return `rgb(${r}, ${g}, ${b})`;
    }

    function updateHue(data) {
        const body = document.getElementById('hue-body');
        if (!body) return;
        if (!data.rooms || Object.keys(data.rooms).length === 0) {
            body.innerHTML = `<p class="text-sm text-[color:var(--text-muted)]">${escapeHtml(data.message || 'No lights found')}</p>`;
            return;
        }
        body.innerHTML = Object.values(data.rooms).map((room) => {
            if (room.error) return `<div class="hue-room"><div class="hue-color-dot" style="background:var(--status-gray);"></div><span class="hue-room-name">${escapeHtml(room.name)}</span><span class="hue-room-detail">unreachable</span></div>`;
            const isOn = !!room.on;
            const color = isOn ? (room.colormode === 'ct' ? ctToCSS(room.ct) : hueToCSS(room.hue, room.sat, 200)) : 'var(--status-gray)';
            const detail = isOn ? `${room.brightness}%` : 'off';
            return `<div class="hue-room ${isOn ? 'room-on' : ''}"><div class="hue-color-dot" style="background:${color};${isOn ? `box-shadow:0 0 10px ${color}40;` : ''}"></div><span class="hue-room-name">${escapeHtml(room.name)}</span><span class="hue-room-detail">${detail}</span></div>`;
        }).join('');
    }

    function updateBackups(data) {
        const body = document.getElementById('backups-body');
        if (!body) return;
        const hosts = data.hosts || {};
        if (Object.keys(hosts).length === 0) {
            body.innerHTML = `<p class="text-sm text-[color:var(--text-muted)]">${escapeHtml(data.message || 'No backup data')}</p>`;
            return;
        }
        body.innerHTML = Object.entries(hosts).map(([name, info]) => `
            <div class="backup-host">
                <div class="backup-indicator backup-${escapeHtml(info.health || 'unknown')}"></div>
                <div class="flex-1 min-w-0">
                    <div class="text-sm font-semibold">${escapeHtml(name)}</div>
                    <div class="text-xs font-mono mt-0.5 text-[color:var(--text-muted)]">${escapeHtml(info.message || '')}</div>
                </div>
            </div>`).join('');
    }

    function renderUsageBar(label, used, free, total, unit) {
        const percent = pct(used, total).toFixed(1);
        return `<div class="storage-row"><div class="flex items-center justify-between text-xs font-mono mb-1"><span>${escapeHtml(label)}</span><span class="text-[color:var(--text-muted)]">${used}${unit} used · ${free}${unit} free</span></div><div class="storage-bar"><div class="storage-fill" style="width:${percent}%"></div></div><div class="text-[0.68rem] text-[color:var(--text-muted)] font-mono mt-1">${total}${unit} total · ${percent}% used</div></div>`;
    }

    function renderToolTable(tools) {
        if (!tools || Object.keys(tools).length === 0) return '<p class="text-sm text-[color:var(--text-muted)]">No tool data yet.</p>';
        const rows = Object.entries(tools).sort((a, b) => a[0].localeCompare(b[0])).map(([name, value]) => `<tr><td>${escapeHtml(name)}</td><td class="font-mono">${escapeHtml(value || '--')}</td></tr>`).join('');
        return `<div class="overflow-x-auto"><table class="device-table"><thead><tr><th>Tool</th><th>Version</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }

    function renderProjects(projects) {
        if (!Array.isArray(projects) || projects.length === 0) return '<p class="text-sm text-[color:var(--text-muted)]">No projects found.</p>';
        const rows = projects.map((p) => `<tr><td class="font-mono">${escapeHtml(p.emoji || '📁')}</td><td>${escapeHtml(p.name || '?')}</td><td>${escapeHtml(p.description || '')}</td></tr>`).join('');
        return `<div class="overflow-x-auto"><table class="device-table"><thead><tr><th>Emoji</th><th>Name</th><th>Description</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }

    function renderModels(ollama) {
        if (!ollama || ollama.status !== 'online') return '<p class="text-sm text-[color:var(--text-muted)]">Ollama unavailable.</p>';
        if (!Array.isArray(ollama.models) || ollama.models.length === 0) return '<p class="text-sm text-[color:var(--text-muted)]">No models found.</p>';
        const running = Array.isArray(ollama.running) ? ollama.running : [];
        const rows = ollama.models.map((m) => `<tr class="${running.includes(m.name) ? 'model-row-running' : ''}"><td>${escapeHtml(m.name)}</td><td class="font-mono">${escapeHtml(m.size || '--')}</td><td class="font-mono">${escapeHtml((m.modified || '').replace('T', ' ').slice(0, 16) || '--')}</td></tr>`).join('');
        return `<div class="overflow-x-auto"><table class="device-table"><thead><tr><th>Model</th><th>Size</th><th>Modified</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }

    function renderServices(services) {
        if (!services || Object.keys(services).length === 0) return '<p class="text-sm text-[color:var(--text-muted)]">No configured service checks.</p>';
        return `<div class="space-y-2">${Object.entries(services).map(([name, value]) => `<div class="bulletin-row"><span class="bulletin-label">${escapeHtml(name)}</span><span class="bulletin-msg font-mono">${escapeHtml(value)}</span></div>`).join('')}</div>`;
    }

    function renderCron(entries) {
        if (!Array.isArray(entries) || entries.length === 0) return '<p class="text-sm text-[color:var(--text-muted)]">No cron jobs found</p>';
        const rows = entries.map((e) => `<tr><td class="cron-desc">${escapeHtml(e.description || e.schedule || '')}</td><td class="cron-cmd">${escapeHtml(e.command || '')}</td></tr>`).join('');
        return `<div class="overflow-x-auto"><table class="cron-table"><thead><tr><th>When</th><th>Command</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }

    function updateHostTab(host, hostData, ollamaData) {
        const slug = host.slug || slugify(host.name);
        const dot = document.getElementById(`host-dot-${slug}`);
        const overview = document.getElementById(`host-overview-${slug}`);
        const tools = document.getElementById(`host-tools-${slug}`);
        const side = document.getElementById(`host-side-${slug}`);
        const projects = document.getElementById(`host-projects-${slug}`);
        const cron = document.getElementById(`host-cron-${slug}`);
        if (!dot || !overview || !tools || !side || !projects) return;
        const online = hostData && hostData.status === 'online';
        dot.className = `status-dot ${online ? 'status-online' : (hostData ? 'status-offline' : 'status-idle')}`;
        if (!online) {
            overview.innerHTML = `<p class="text-sm text-[color:var(--text-muted)]">${escapeHtml(hostData?.message || 'Host offline')}</p>`;
            tools.innerHTML = '<p class="text-sm text-[color:var(--text-muted)]">No host data.</p>';
            side.innerHTML = '<p class="text-sm text-[color:var(--text-muted)]">No host data.</p>';
            projects.innerHTML = '<p class="text-sm text-[color:var(--text-muted)]">No host data.</p>';
            if (cron) cron.innerHTML = '<p class="text-sm text-[color:var(--text-muted)]">No cron data.</p>';
            return;
        }
        overview.innerHTML = `
            <div class="grid grid-cols-1 lg:grid-cols-3 gap-3 mb-3 text-xs font-mono">
                <div class="stat-block"><span class="stat-label">OS</span><span class="stat-value">${escapeHtml(hostData.os || '--')}</span></div>
                <div class="stat-block"><span class="stat-label">Kernel</span><span class="stat-value">${escapeHtml(hostData.kernel || '--')}</span></div>
                <div class="stat-block"><span class="stat-label">CPU</span><span class="stat-value">${escapeHtml(hostData.cpu || '--')}</span></div>
                <div class="stat-block"><span class="stat-label">IP</span><span class="stat-value">${escapeHtml(hostData.ip || '--')}</span></div>
                <div class="stat-block"><span class="stat-label">Status</span><span class="stat-value">${escapeHtml(hostData.message || 'Online')}</span></div>
                <div class="stat-block"><span class="stat-label">Uptime</span><span class="stat-value">${escapeHtml(fmtUptime(hostData.uptime_seconds))}</span></div>
            </div>
            <div class="space-y-3">
                ${renderUsageBar('RAM', Number(hostData.ram_used_gb || 0).toFixed(1), Number(hostData.ram_free_gb || 0).toFixed(1), Number(hostData.ram_total_gb || 0).toFixed(1), 'GB')}
                ${renderUsageBar('Disk', Number(hostData.disk_used_gb || 0).toFixed(1), Number(hostData.disk_free_gb || 0).toFixed(1), Number(hostData.disk_total_gb || 0).toFixed(1), 'GB')}
            </div>`;
        tools.innerHTML = renderToolTable(hostData.tools || {});
        side.innerHTML = host.ollama ? renderModels(ollamaData) : renderServices(hostData.services || {});
        projects.innerHTML = renderProjects(hostData.projects || []);
        if (cron) cron.innerHTML = renderCron(hostData.cron || []);
    }

    function updateAgent(data) {
        const quip = document.getElementById('agent-quip');
        const uptime = document.getElementById('agent-uptime');
        const python = document.getElementById('agent-python');
        const nzTime = document.getElementById('nz-time');
        const headerUptime = document.getElementById('uptime');
        const tagline = document.getElementById('agent-tagline');
        const sysPython = document.getElementById('sys-python');
        if (quip) quip.textContent = `"${data.quip || '...'}"`;
        if (uptime) uptime.textContent = data.uptime || '--';
        if (python) python.textContent = data.python_version || '--';
        if (nzTime) nzTime.textContent = data.nz_time || '--:-- --';
        if (headerUptime) headerUptime.textContent = `Up ${data.uptime || '--'}`;
        if (tagline && !config.agent?.tagline) tagline.textContent = data.quip || 'Monitoring system status';
        if (sysPython) sysPython.textContent = data.python_version || '--';
    }

    async function loadNasData() {
        for (const name of config.nas?.media_paths || []) {
            await loadNasLibrary(name);
        }
        await loadNasStorage();
    }

    async function loadNasLibrary(name) {
        const id = libraryId(name);
        const summary = document.getElementById(`nas-summary-${id}`);
        const list = document.getElementById(`nas-list-${id}`);
        if (!summary || !list) return;
        try {
            const resp = await fetch(`/api/nas/media/${encodeURIComponent(name)}`);
            const data = await resp.json();
            nasState.media[name] = data;
            if (data.status !== 'online') {
                summary.textContent = data.message || 'Offline';
                list.innerHTML = `<p class="text-sm text-[color:var(--status-red)]">${escapeHtml(data.message || 'Offline')}</p>`;
                return;
            }
            summary.textContent = `${data.count} items${data.size_gb ? ` · ${data.size_gb}GB` : ''}`;
            list.innerHTML = (data.items || []).map((item) => `<div class="media-item">${escapeHtml(item)}</div>`).join('') || '<p class="text-sm text-[color:var(--text-muted)]">No items found.</p>';
        } catch {
            summary.textContent = 'Offline';
            list.innerHTML = '<p class="text-sm text-[color:var(--status-red)]">Unavailable</p>';
        }
    }

    async function loadNasStorage() {
        const body = document.getElementById('storage-body');
        if (!body) return;
        try {
            const resp = await fetch('/api/nas/storage');
            const data = await resp.json();
            nasState.storage = data;
            if (data.status !== 'online') {
                body.innerHTML = `<p class="text-sm text-[color:var(--status-red)]">${escapeHtml(data.message || 'Unavailable')}</p>`;
                return;
            }
            body.innerHTML = (data.drives || []).map((d) => `<div class="storage-row"><div class="flex items-center justify-between text-xs font-mono mb-1"><span>${escapeHtml(d.name)}</span><span class="text-[color:var(--text-muted)]">${d.used_gb}GB used · ${d.free_gb}GB free</span></div><div class="storage-bar"><div class="storage-fill" style="width:${Math.min(100, Math.max(0, d.pct || 0))}%"></div></div><div class="text-[0.68rem] text-[color:var(--text-muted)] font-mono mt-1">${d.total_gb}GB total · ${d.pct}% full</div></div>`).join('') || '<p class="text-sm text-[color:var(--text-muted)]">No drive data.</p>';
        } catch {
            body.innerHTML = '<p class="text-sm text-[color:var(--status-red)]">Unavailable</p>';
        }
    }

    function initMusicSearch() {
        for (const name of config.nas?.media_paths || []) {
            const input = document.getElementById(`nas-search-${name}`);
            if (!input) continue;
            input.addEventListener('input', () => {});
        }
    }

    async function refreshLogs() {
        for (const [name] of Object.entries(config.log_files || {})) {
            const el = document.getElementById(`log-${name}`);
            if (!el) continue;
            try {
                const resp = await fetch(`/api/logs/${encodeURIComponent(name)}`);
                const data = await resp.json();
                const lines = Array.isArray(data.lines) ? data.lines : [];
                el.textContent = lines.length ? lines.join('\n') : (data.message || 'No log data');
                el.scrollTop = el.scrollHeight;
            } catch {
                el.textContent = 'Log unavailable';
            }
        }
    }

    async function init() {
        const response = await fetch('/api/config');
        config = await response.json();
        applyIdentity();
        buildTabs();
        renderTabs();
        renderLayout();
        renderNetworkDevices();
        renderSystemInfo();
        setActiveTab(hashToTab(window.location.hash), false);
        loadNasData();
        if (Object.keys(config.log_files || {}).length > 0) {
            refreshLogs();
            setInterval(refreshLogs, 20000);
        }
        connect();
    }

    document.addEventListener('DOMContentLoaded', () => {
        init().catch((err) => {
            console.error('Failed to initialize dashboard:', err);
        });
    });
})();
