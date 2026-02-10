/**
 * PiBox WebSocket Client
 */

let ws = null;
let wsReconnectTimer = null;
const WS_RECONNECT_DELAY = 5000;

function initWebSocket() {
    const wsPort = window.WS_PORT || 8081;
    const wsHost = window.location.hostname;
    const wsUrl = `ws://${wsHost}:${wsPort}`;

    console.log('Connecting to WebSocket:', wsUrl);

    try {
        ws = new WebSocket(wsUrl);

        ws.onopen = function() {
            console.log('WebSocket connected');
            updateWsStatus(true);
            clearTimeout(wsReconnectTimer);

            // Request initial data
            ws.send(JSON.stringify({ type: 'get_stats' }));
            ws.send(JSON.stringify({ type: 'get_status' }));
        };

        ws.onmessage = function(event) {
            try {
                const message = JSON.parse(event.data);
                handleWsMessage(message);
            } catch (e) {
                console.error('WebSocket message parse error:', e);
            }
        };

        ws.onclose = function() {
            console.log('WebSocket disconnected');
            updateWsStatus(false);
            scheduleReconnect();
        };

        ws.onerror = function(error) {
            console.error('WebSocket error:', error);
            updateWsStatus(false);
        };

    } catch (e) {
        console.error('WebSocket init error:', e);
        updateWsStatus(false);
        scheduleReconnect();
    }
}

function handleWsMessage(message) {
    switch (message.type) {
        case 'access_event':
            addAccessEvent(message.data);
            // Play notification sound (optional)
            // playNotificationSound(message.data.access_granted);
            break;

        case 'stats':
            updateStats(message.data);
            break;

        case 'system_status':
            updateSystemStatus(message.data);
            break;

        case 'barrier_status':
            updateBarrierStatus(message.data.relays);
            break;

        case 'pong':
            // Heartbeat response
            break;

        default:
            console.log('Unknown WebSocket message type:', message.type);
    }
}

function updateWsStatus(connected) {
    const wsStatus = document.getElementById('ws-status');
    const wsBadge = document.getElementById('ws-badge');

    if (wsStatus) {
        wsStatus.className = `status-dot ${connected ? 'connected' : 'disconnected'}`;
    }

    if (wsBadge) {
        wsBadge.textContent = connected ? 'Live' : 'Disconnected';
        wsBadge.className = `badge ${connected ? '' : 'disconnected'}`;
    }
}

function scheduleReconnect() {
    if (wsReconnectTimer) return;

    wsReconnectTimer = setTimeout(() => {
        wsReconnectTimer = null;
        initWebSocket();
    }, WS_RECONNECT_DELAY);
}

function sendWsMessage(data) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(data));
    }
}

// Heartbeat to keep connection alive
setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }));
    }
}, 30000);

// Optional: Notification sound
function playNotificationSound(granted) {
    // Uncomment to enable sound notifications
    // const audio = new Audio(granted ? '/static/sounds/granted.mp3' : '/static/sounds/denied.mp3');
    // audio.play().catch(() => {});
}
