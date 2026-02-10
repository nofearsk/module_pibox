/**
 * PiBox Main JavaScript
 */

// Toast notification
function showToast(message, duration = 3000) {
    const existing = document.querySelector('.toast');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(() => toast.remove(), duration);
}

// Format timestamp
function formatTime(timestamp) {
    if (!timestamp) return '-';
    const date = new Date(timestamp);
    return date.toLocaleTimeString();
}

// Format date
function formatDate(timestamp) {
    if (!timestamp) return '-';
    const date = new Date(timestamp);
    return date.toLocaleDateString();
}

// API helper
async function api(endpoint, options = {}) {
    const response = await fetch(endpoint, {
        headers: {
            'Content-Type': 'application/json',
            ...options.headers
        },
        ...options
    });
    return response.json();
}

// Update stats display
function updateStats(stats) {
    const elements = {
        'stat-total': stats.total,
        'stat-granted': stats.granted,
        'stat-denied': stats.denied,
        'stat-unknown': stats.unknown
    };

    for (const [id, value] of Object.entries(elements)) {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    }
}

// Update system status indicators
function updateSystemStatus(status) {
    const odooStatus = document.getElementById('odoo-status');
    const syncTime = document.getElementById('sync-time');

    if (odooStatus) {
        odooStatus.className = `status-dot ${status.odoo_connected ? 'connected' : 'disconnected'}`;
    }

    if (syncTime && status.last_sync) {
        syncTime.textContent = `Last sync: ${formatTime(status.last_sync)}`;
    }
}

// Add access event to feed
function addAccessEvent(event) {
    const feed = document.getElementById('access-feed');
    if (!feed) return;

    const item = document.createElement('div');
    item.className = `feed-item ${event.access_granted ? 'granted' : 'denied'}`;

    item.innerHTML = `
        <div class="feed-image">
            ${event.image_url
                ? `<img src="${event.image_url}" alt="${event.plate}">`
                : '<div class="no-image">No Image</div>'}
        </div>
        <div class="feed-info">
            <div class="feed-plate">${event.plate}</div>
            <div class="feed-details">
                ${event.owner_name
                    ? `${event.owner_name} | ${event.unit_name}`
                    : 'Unknown Vehicle'}
            </div>
        </div>
        <div class="feed-status">
            <span class="status-badge ${event.access_granted ? 'success' : 'danger'}">
                ${event.access_granted ? 'GRANTED' : 'DENIED'}
            </span>
            <span class="feed-time">${formatTime(event.timestamp)}</span>
        </div>
    `;

    // Insert at top
    feed.insertBefore(item, feed.firstChild);

    // Limit to 20 items
    while (feed.children.length > 20) {
        feed.removeChild(feed.lastChild);
    }

    // Flash animation
    item.style.animation = 'none';
    item.offsetHeight; // Trigger reflow
    item.style.animation = 'slideIn 0.3s ease';
}

// Update barrier status
function updateBarrierStatus(relays) {
    for (const [channel, relay] of Object.entries(relays)) {
        const card = document.getElementById(`barrier-${channel}`) ||
                     document.getElementById(`relay-${channel}`);
        if (card) {
            const stateEl = card.querySelector('.barrier-state, .relay-state');
            if (stateEl) {
                stateEl.textContent = relay.state ? 'ON' : 'OFF';
                stateEl.className = `barrier-state ${relay.state ? 'on' : 'off'}`;
            }
        }
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    // Periodic status check (fallback if WebSocket not available)
    setInterval(() => {
        fetch('/api/sync/status')
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    updateSystemStatus(data);
                }
            })
            .catch(() => {});
    }, 60000); // Every minute
});
