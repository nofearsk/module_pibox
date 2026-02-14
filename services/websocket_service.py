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
        # Camera subscriptions: {websocket: {reg_code: filter}}
        # filter: 'all' | 'unregistered' | 'registered' | 'none'
        self._camera_subscriptions = {}
        # Camera-specific message queue: [(reg_code, message, event_data), ...]
        self._camera_queue = []

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
            # Clean up camera subscriptions
            if websocket in self._camera_subscriptions:
                del self._camera_subscriptions[websocket]
            logger.info(f"WebSocket client disconnected: {client_ip} (total: {len(self.clients)})")

    async def _handle_message(self, websocket, data):
        """Handle incoming WebSocket messages"""
        msg_type = data.get('type')
        action = data.get('action')

        if msg_type == 'ping':
            await websocket.send(json.dumps({'type': 'pong'}))

        elif msg_type == 'get_stats':
            await self._send_stats(websocket)

        elif msg_type == 'get_status':
            await self._send_system_status(websocket)

        # Camera subscription actions
        elif action == 'subscribe':
            camera = data.get('camera')  # reg_code
            filter_type = data.get('filter', 'all')  # all, unregistered, registered, none
            if camera:
                if websocket not in self._camera_subscriptions:
                    self._camera_subscriptions[websocket] = {}
                self._camera_subscriptions[websocket][camera] = filter_type
                client_ip = websocket.remote_address[0] if websocket.remote_address else 'unknown'
                await websocket.send(json.dumps({
                    'type': 'subscribed',
                    'camera': camera,
                    'filter': filter_type,
                    'subscriptions': list(self._camera_subscriptions[websocket].keys())
                }))
                logger.info(f"Client {client_ip} subscribed to camera: {camera} with filter: {filter_type}")

        elif action == 'unsubscribe':
            camera = data.get('camera')
            if camera and websocket in self._camera_subscriptions:
                self._camera_subscriptions[websocket].pop(camera, None)
                await websocket.send(json.dumps({
                    'type': 'unsubscribed',
                    'camera': camera,
                    'subscriptions': list(self._camera_subscriptions[websocket].keys())
                }))
                logger.debug(f"Client unsubscribed from camera: {camera}")

        elif action == 'subscribe_all':
            # Subscribe to all cameras with default filter
            from database.models import AnprCameraModel
            cameras = AnprCameraModel.get_all()
            filter_type = data.get('filter', 'all')
            if websocket not in self._camera_subscriptions:
                self._camera_subscriptions[websocket] = {}
            for cam in cameras:
                self._camera_subscriptions[websocket][cam['reg_code']] = filter_type
            await websocket.send(json.dumps({
                'type': 'subscribed_all',
                'filter': filter_type,
                'subscriptions': list(self._camera_subscriptions[websocket].keys())
            }))

        elif action == 'get_subscriptions':
            subs = self._camera_subscriptions.get(websocket, {})
            await websocket.send(json.dumps({
                'type': 'subscriptions',
                'subscriptions': subs  # {camera: filter}
            }))

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

    async def _broadcast_to_camera(self, reg_code, message, event_data=None):
        """Broadcast message to clients subscribed to a specific camera with filtering"""
        msg_str = json.dumps(message) if isinstance(message, dict) else message

        # Get access_granted from event data for filtering
        access_granted = None
        if event_data:
            access_granted = event_data.get('access_granted')

        dead_clients = set()
        logger.info(f"Broadcasting to camera {reg_code}, {len(self._camera_subscriptions)} clients with subscriptions")
        for client, subscriptions in list(self._camera_subscriptions.items()):
            client_ip = client.remote_address[0] if client.remote_address else 'unknown'
            logger.info(f"  Client {client_ip} subscriptions: {list(subscriptions.keys())}")
            if reg_code in subscriptions:
                filter_type = subscriptions[reg_code]

                # Apply filter logic
                should_send = False
                if filter_type == 'all':
                    should_send = True
                elif filter_type == 'none':
                    should_send = False
                elif filter_type == 'unregistered' and access_granted is not None:
                    should_send = not access_granted  # Only send if NOT registered
                elif filter_type == 'registered' and access_granted is not None:
                    should_send = access_granted  # Only send if registered
                else:
                    # Default: send if no filter or unknown filter
                    should_send = True

                if should_send:
                    try:
                        await client.send(msg_str)
                        client_ip = client.remote_address[0] if client.remote_address else 'unknown'
                        logger.info(f"Sent event to {client_ip} (camera: {reg_code}, filter: {filter_type})")
                    except Exception:
                        dead_clients.add(client)
                else:
                    client_ip = client.remote_address[0] if client.remote_address else 'unknown'
                    logger.info(f"Filtered event for {client_ip} (camera: {reg_code}, filter: {filter_type}, access: {access_granted})")

        # Clean up dead clients
        for client in dead_clients:
            self.clients.discard(client)
            if client in self._camera_subscriptions:
                del self._camera_subscriptions[client]

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
                # Broadcast to all clients
                while self._message_queue:
                    message = self._message_queue.pop(0)
                    await self._broadcast(message)

                # Broadcast to camera subscribers
                while self._camera_queue:
                    item = self._camera_queue.pop(0)
                    if len(item) == 3:
                        reg_code, message, event_data = item
                    else:
                        reg_code, message = item
                        event_data = None
                    await self._broadcast_to_camera(reg_code, message, event_data)

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

    def broadcast_camera_event(self, reg_code, event_data):
        """
        Broadcast event to clients subscribed to a specific camera
        Respects subscription filters (all, unregistered, registered, none)

        Args:
            reg_code: Camera registration code
            event_data: dict with plate detection details (must include 'access_granted' for filtering)
        """
        message = {
            'type': 'camera_event',
            'camera': reg_code,
            'data': event_data
        }
        # Include event_data for filter checking
        self._camera_queue.append((reg_code, message, event_data))
        logger.info(f"Queued camera event for {reg_code}: {event_data.get('plate')} (access_granted: {event_data.get('access_granted')})")

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
