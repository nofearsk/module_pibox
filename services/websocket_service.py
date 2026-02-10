"""
WebSocket Service
Real-time broadcast to web UI and tablets
"""
import asyncio
import json
import logging
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)

# Try to import websockets
try:
    import websockets
    from websockets.server import serve
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False
    logger.warning("websockets not available - WebSocket server disabled")


class WebSocketService:
    """Service for WebSocket real-time communication"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.clients = set()
        self._server = None
        self._loop = None
        self._thread = None
        self._running = False
        self._message_queue = []
        self._stats_interval = 30  # seconds

    async def _handler(self, websocket, path=None):
        """Handle WebSocket connections"""
        self.clients.add(websocket)
        client_ip = websocket.remote_address[0] if websocket.remote_address else 'unknown'
        logger.info(f"WebSocket client connected: {client_ip} (total: {len(self.clients)})")

        try:
            # Send initial status
            await self._send_system_status(websocket)

            # Keep connection alive and handle messages
            async for message in websocket:
                try:
                    data = json.loads(message)
                    await self._handle_message(websocket, data)
                except json.JSONDecodeError:
                    pass

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.discard(websocket)
            logger.info(f"WebSocket client disconnected: {client_ip} (total: {len(self.clients)})")

    async def _handle_message(self, websocket, data):
        """Handle incoming WebSocket messages"""
        msg_type = data.get('type')

        if msg_type == 'ping':
            await websocket.send(json.dumps({'type': 'pong'}))

        elif msg_type == 'get_stats':
            await self._send_stats(websocket)

        elif msg_type == 'get_status':
            await self._send_system_status(websocket)

    async def _send_stats(self, websocket):
        """Send current statistics"""
        from database.models import AccessLogModel
        stats = AccessLogModel.get_today_stats()
        await websocket.send(json.dumps({
            'type': 'stats',
            'data': stats
        }))

    async def _send_system_status(self, websocket):
        """Send system status"""
        from services.sync_service import sync_service
        status = sync_service.get_status()
        await websocket.send(json.dumps({
            'type': 'system_status',
            'data': status
        }))

    async def _broadcast(self, message):
        """Broadcast message to all connected clients"""
        if not self.clients:
            return

        if isinstance(message, dict):
            message = json.dumps(message)

        # Send to all clients, removing dead connections
        dead_clients = set()
        for client in self.clients.copy():
            try:
                await client.send(message)
            except Exception:
                dead_clients.add(client)

        self.clients -= dead_clients

    async def _stats_loop(self):
        """Periodically broadcast stats"""
        while self._running:
            try:
                if self.clients:
                    from database.models import AccessLogModel
                    stats = AccessLogModel.get_today_stats()
                    await self._broadcast({
                        'type': 'stats',
                        'data': stats
                    })
            except Exception as e:
                logger.error(f"Stats broadcast error: {e}")

            await asyncio.sleep(self._stats_interval)

    async def _run_server(self, host, port):
        """Run the WebSocket server"""
        self._running = True

        async with serve(self._handler, host, port):
            logger.info(f"WebSocket server started on ws://{host}:{port}")

            # Start stats loop
            stats_task = asyncio.create_task(self._stats_loop())

            # Process queued messages
            while self._running:
                while self._message_queue:
                    message = self._message_queue.pop(0)
                    await self._broadcast(message)

                await asyncio.sleep(0.1)

            stats_task.cancel()

    def start(self, host='0.0.0.0', port=8081):
        """Start WebSocket server in background thread"""
        if not WS_AVAILABLE:
            logger.warning("WebSocket server not started - websockets library not available")
            return

        if self._running:
            return

        def run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._run_server(host, port))
            except Exception as e:
                logger.error(f"WebSocket server error: {e}")
            finally:
                self._loop.close()

        self._thread = threading.Thread(target=run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop WebSocket server"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("WebSocket server stopped")

    def broadcast_access_event(self, event_data):
        """
        Broadcast access event to all clients

        Args:
            event_data: dict with access event details
        """
        message = {
            'type': 'access_event',
            'data': event_data
        }
        self._message_queue.append(message)
        logger.debug(f"Queued access event broadcast: {event_data.get('plate')}")

    def broadcast_barrier_status(self, relay_states):
        """Broadcast barrier status update"""
        message = {
            'type': 'barrier_status',
            'data': {'relays': relay_states}
        }
        self._message_queue.append(message)

    def broadcast_system_status(self):
        """Broadcast system status update"""
        from services.sync_service import sync_service
        status = sync_service.get_status()
        message = {
            'type': 'system_status',
            'data': status
        }
        self._message_queue.append(message)

    def get_client_count(self):
        """Get number of connected clients"""
        return len(self.clients)


# Singleton instance
websocket_service = WebSocketService()
