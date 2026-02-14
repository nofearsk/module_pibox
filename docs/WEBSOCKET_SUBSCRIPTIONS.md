# PiBox WebSocket Subscriptions

Real-time event notifications via WebSocket with selective filtering per camera/lane.

## Connection

```javascript
const ws = new WebSocket('ws://<pibox-ip>:8081');
```

## Multiple Subscriptions

One WebSocket client can subscribe to **multiple cameras** with different filters:

```javascript
const ws = new WebSocket('ws://192.168.1.100:8081');

ws.onopen = () => {
    // Subscribe to 3 different cameras with different filters
    ws.send(JSON.stringify({ action: "subscribe", camera: "VISITOR_01", filter: "all" }));
    ws.send(JSON.stringify({ action: "subscribe", camera: "VISITOR_02", filter: "all" }));
    ws.send(JSON.stringify({ action: "subscribe", camera: "RESIDENT_01", filter: "unregistered" }));
    ws.send(JSON.stringify({ action: "subscribe", camera: "RESIDENT_02", filter: "unregistered" }));
    // Exit cameras - not subscribed
};

// Will receive events from all 4 subscribed cameras
ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === 'camera_event') {
        console.log(`Event from ${msg.camera}:`, msg.data.plate);
    }
};
```

**Key Points:**
- No limit on number of subscriptions per client
- Each subscription can have its own filter
- Unsubscribe individually or reconnect to reset all

## Subscription Types

### Basic Subscription (All Events)

Subscribe to a camera and receive all vehicle detections:

```javascript
ws.send(JSON.stringify({
    action: "subscribe",
    camera: "CAM_REG_CODE"
}));
```

### Filtered Subscription (Planned)

Subscribe with a filter to receive only specific events:

```javascript
ws.send(JSON.stringify({
    action: "subscribe",
    camera: "CAM_REG_CODE",
    filter: "unregistered"
}));
```

#### Filter Options

| Filter | Description | Use Case |
|--------|-------------|----------|
| `all` | All vehicle detections (default) | Visitor lanes, monitoring |
| `unregistered` | Only vehicles NOT in whitelist | Resident lanes, alerts |
| `registered` | Only vehicles IN whitelist | VIP tracking |
| `none` | No notifications | Exit lanes, disabled |

## Example: Multi-Lane Setup

```javascript
const ws = new WebSocket('ws://192.168.1.100:8081');

ws.onopen = () => {
    // Visitor Lane - notify for all vehicles
    ws.send(JSON.stringify({
        action: "subscribe",
        camera: "VISITOR_ENTRY_01",
        filter: "all"
    }));

    // Resident Lane - notify only unregistered (alerts)
    ws.send(JSON.stringify({
        action: "subscribe",
        camera: "RESIDENT_ENTRY_01",
        filter: "unregistered"
    }));

    // Exit Lane - no subscription needed
    // (don't subscribe or use filter: "none")
};

ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);

    if (msg.type === 'camera_event') {
        console.log('Camera:', msg.camera);
        console.log('Plate:', msg.data.plate);
        console.log('Registered:', msg.data.access_granted);
    }
};
```

## Message Types

### Incoming (from PiBox)

#### camera_event
Vehicle detected on subscribed camera:
```json
{
    "type": "camera_event",
    "camera": "CAM_REG_CODE",
    "data": {
        "plate": "SBA1234X",
        "confidence": 95.5,
        "access_granted": true,
        "vehicle_type": "car",
        "timestamp": "2026-02-14T10:30:00",
        "image_url": "https://..."
    }
}
```

#### access_event
Access event broadcast to all clients:
```json
{
    "type": "access_event",
    "data": {
        "plate": "SBA1234X",
        "camera": "CAM_REG_CODE",
        "access_granted": true,
        "action": "barrier_opened"
    }
}
```

#### barrier_status
Relay/barrier state change:
```json
{
    "type": "barrier_status",
    "data": {
        "relays": {
            "1": false,
            "2": true,
            "3": false
        }
    }
}
```

#### system_status
System connection status:
```json
{
    "type": "system_status",
    "data": {
        "odoo_connected": true,
        "last_sync": "2026-02-14T10:30:00",
        "cameras": 5,
        "vehicles": 120
    }
}
```

#### stats
Today's access statistics:
```json
{
    "type": "stats",
    "data": {
        "total": 45,
        "granted": 40,
        "denied": 5
    }
}
```

### Outgoing (to PiBox)

#### subscribe
```json
{"action": "subscribe", "camera": "REG_CODE", "filter": "all"}
```

#### unsubscribe
```json
{"action": "unsubscribe", "camera": "REG_CODE"}
```

#### subscribe_all
```json
{"action": "subscribe_all"}
```

#### get_subscriptions
```json
{"action": "get_subscriptions"}
```

#### ping
```json
{"type": "ping"}
```

#### get_stats
```json
{"type": "get_stats"}
```

#### get_status
```json
{"type": "get_status"}
```

## Response Messages

#### subscribed
```json
{
    "type": "subscribed",
    "camera": "REG_CODE",
    "subscriptions": ["CAM1", "CAM2"]
}
```

#### unsubscribed
```json
{
    "type": "unsubscribed",
    "camera": "REG_CODE",
    "subscriptions": ["CAM1"]
}
```

#### pong
```json
{"type": "pong"}
```

## Use Cases

### 1. Security Guard Tablet (Visitor Lane)
- Subscribe to visitor entry camera with `filter: "all"`
- See every vehicle approaching
- Manually verify visitors

### 2. Control Room Alert System (Resident Lane)
- Subscribe to resident entry with `filter: "unregistered"`
- Only alerted when unknown vehicle detected
- Reduces noise from registered residents

### 3. Dashboard Overview
- Use `subscribe_all` for complete visibility
- Display all activity across all cameras

### 4. Exit Monitoring
- Don't subscribe to exit cameras
- Or use `filter: "none"` if subscription tracking needed

## Connection Management

### Heartbeat
Send periodic pings to keep connection alive:
```javascript
setInterval(() => {
    if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({type: "ping"}));
    }
}, 30000);
```

### Reconnection
```javascript
function connect() {
    const ws = new WebSocket('ws://192.168.1.100:8081');

    ws.onclose = () => {
        console.log('Disconnected, reconnecting in 5s...');
        setTimeout(connect, 5000);
    };

    ws.onopen = () => {
        // Re-subscribe after reconnect
        ws.send(JSON.stringify({
            action: "subscribe",
            camera: "VISITOR_01",
            filter: "all"
        }));
    };
}
```

## React Native Integration

### Install Dependencies

```bash
npm install react-native-url-polyfill
```

### WebSocket Hook

```typescript
// hooks/usePiBoxWebSocket.ts
import { useEffect, useRef, useState, useCallback } from 'react';

interface CameraEvent {
  plate: string;
  confidence: number;
  access_granted: boolean;
  vehicle_type: string;
  timestamp: string;
  image_url?: string;
}

interface Subscription {
  camera: string;
  filter: 'all' | 'unregistered' | 'registered' | 'none';
}

interface UsePiBoxWebSocketProps {
  url: string;
  subscriptions: Subscription[];
  onCameraEvent?: (camera: string, event: CameraEvent) => void;
  onAccessEvent?: (event: any) => void;
  onBarrierStatus?: (relays: Record<string, boolean>) => void;
}

export const usePiBoxWebSocket = ({
  url,
  subscriptions,
  onCameraEvent,
  onAccessEvent,
  onBarrierStatus,
}: UsePiBoxWebSocketProps) => {
  const ws = useRef<WebSocket | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [stats, setStats] = useState({ total: 0, granted: 0, denied: 0 });
  const reconnectTimeout = useRef<NodeJS.Timeout>();

  const connect = useCallback(() => {
    try {
      ws.current = new WebSocket(url);

      ws.current.onopen = () => {
        console.log('PiBox WebSocket connected');
        setIsConnected(true);

        // Subscribe to cameras with filters
        subscriptions.forEach(({ camera, filter }) => {
          if (filter !== 'none') {
            ws.current?.send(JSON.stringify({
              action: 'subscribe',
              camera,
              filter,
            }));
          }
        });
      };

      ws.current.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);

          switch (msg.type) {
            case 'camera_event':
              onCameraEvent?.(msg.camera, msg.data);
              break;

            case 'access_event':
              onAccessEvent?.(msg.data);
              break;

            case 'barrier_status':
              onBarrierStatus?.(msg.data.relays);
              break;

            case 'stats':
              setStats(msg.data);
              break;

            case 'pong':
              // Heartbeat response
              break;
          }
        } catch (e) {
          console.error('Failed to parse message:', e);
        }
      };

      ws.current.onclose = () => {
        console.log('PiBox WebSocket disconnected');
        setIsConnected(false);

        // Reconnect after 5 seconds
        reconnectTimeout.current = setTimeout(connect, 5000);
      };

      ws.current.onerror = (error) => {
        console.error('WebSocket error:', error);
      };
    } catch (e) {
      console.error('Failed to connect:', e);
      reconnectTimeout.current = setTimeout(connect, 5000);
    }
  }, [url, subscriptions, onCameraEvent, onAccessEvent, onBarrierStatus]);

  // Heartbeat
  useEffect(() => {
    const interval = setInterval(() => {
      if (ws.current?.readyState === WebSocket.OPEN) {
        ws.current.send(JSON.stringify({ type: 'ping' }));
      }
    }, 30000);

    return () => clearInterval(interval);
  }, []);

  // Connect on mount
  useEffect(() => {
    connect();

    return () => {
      clearTimeout(reconnectTimeout.current);
      ws.current?.close();
    };
  }, [connect]);

  return {
    isConnected,
    stats,
  };
};
```

### Lane Monitor Component

```typescript
// screens/LaneMonitor.tsx
import React, { useState } from 'react';
import { View, Text, FlatList, Image, StyleSheet } from 'react-native';
import { usePiBoxWebSocket } from '../hooks/usePiBoxWebSocket';

interface VehicleAlert {
  id: string;
  plate: string;
  camera: string;
  timestamp: string;
  access_granted: boolean;
  image_url?: string;
}

const PIBOX_URL = 'ws://192.168.1.100:8081';

const LaneMonitor: React.FC = () => {
  const [alerts, setAlerts] = useState<VehicleAlert[]>([]);

  const { isConnected, stats } = usePiBoxWebSocket({
    url: PIBOX_URL,
    subscriptions: [
      // Visitor lane - show all vehicles
      { camera: 'VISITOR_ENTRY_01', filter: 'all' },
      // Resident lane - only unregistered (alerts)
      { camera: 'RESIDENT_ENTRY_01', filter: 'unregistered' },
      // Exit - no notifications
      { camera: 'EXIT_01', filter: 'none' },
    ],
    onCameraEvent: (camera, event) => {
      const alert: VehicleAlert = {
        id: `${Date.now()}-${event.plate}`,
        plate: event.plate,
        camera,
        timestamp: event.timestamp,
        access_granted: event.access_granted,
        image_url: event.image_url,
      };

      setAlerts(prev => [alert, ...prev.slice(0, 49)]); // Keep last 50
    },
  });

  const renderAlert = ({ item }: { item: VehicleAlert }) => (
    <View style={[
      styles.alertCard,
      item.access_granted ? styles.granted : styles.denied
    ]}>
      {item.image_url && (
        <Image source={{ uri: item.image_url }} style={styles.plateImage} />
      )}
      <View style={styles.alertInfo}>
        <Text style={styles.plateText}>{item.plate}</Text>
        <Text style={styles.cameraText}>{item.camera}</Text>
        <Text style={styles.timeText}>
          {new Date(item.timestamp).toLocaleTimeString()}
        </Text>
      </View>
      <View style={[
        styles.statusBadge,
        item.access_granted ? styles.grantedBadge : styles.deniedBadge
      ]}>
        <Text style={styles.statusText}>
          {item.access_granted ? 'REGISTERED' : 'UNKNOWN'}
        </Text>
      </View>
    </View>
  );

  return (
    <View style={styles.container}>
      {/* Header */}
      <View style={styles.header}>
        <View style={styles.connectionStatus}>
          <View style={[
            styles.statusDot,
            isConnected ? styles.connected : styles.disconnected
          ]} />
          <Text style={styles.statusLabel}>
            {isConnected ? 'Connected' : 'Disconnected'}
          </Text>
        </View>

        <View style={styles.statsRow}>
          <Text style={styles.statItem}>Total: {stats.total}</Text>
          <Text style={styles.statItem}>Granted: {stats.granted}</Text>
          <Text style={styles.statItem}>Denied: {stats.denied}</Text>
        </View>
      </View>

      {/* Alert List */}
      <FlatList
        data={alerts}
        keyExtractor={(item) => item.id}
        renderItem={renderAlert}
        contentContainerStyle={styles.list}
      />
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#1a1a2e',
  },
  header: {
    padding: 16,
    backgroundColor: '#16213e',
  },
  connectionStatus: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 12,
  },
  statusDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    marginRight: 8,
  },
  connected: {
    backgroundColor: '#4caf50',
  },
  disconnected: {
    backgroundColor: '#f44336',
  },
  statusLabel: {
    color: '#fff',
    fontSize: 14,
  },
  statsRow: {
    flexDirection: 'row',
    justifyContent: 'space-around',
  },
  statItem: {
    color: '#aaa',
    fontSize: 12,
  },
  list: {
    padding: 16,
  },
  alertCard: {
    flexDirection: 'row',
    backgroundColor: '#252542',
    borderRadius: 12,
    padding: 12,
    marginBottom: 12,
    borderLeftWidth: 4,
  },
  granted: {
    borderLeftColor: '#4caf50',
  },
  denied: {
    borderLeftColor: '#f44336',
  },
  plateImage: {
    width: 80,
    height: 50,
    borderRadius: 4,
    marginRight: 12,
  },
  alertInfo: {
    flex: 1,
    justifyContent: 'center',
  },
  plateText: {
    color: '#fff',
    fontSize: 18,
    fontWeight: 'bold',
    fontFamily: 'monospace',
  },
  cameraText: {
    color: '#888',
    fontSize: 12,
    marginTop: 4,
  },
  timeText: {
    color: '#666',
    fontSize: 11,
    marginTop: 2,
  },
  statusBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 4,
    alignSelf: 'center',
  },
  grantedBadge: {
    backgroundColor: 'rgba(76, 175, 80, 0.2)',
  },
  deniedBadge: {
    backgroundColor: 'rgba(244, 67, 54, 0.2)',
  },
  statusText: {
    fontSize: 10,
    fontWeight: 'bold',
    color: '#fff',
  },
});

export default LaneMonitor;
```

### Alert Sound Integration

```typescript
// hooks/useAlertSound.ts
import { useEffect } from 'react';
import Sound from 'react-native-sound';

Sound.setCategory('Playback');

const alertSound = new Sound('alert.mp3', Sound.MAIN_BUNDLE);

export const useAlertSound = (playCondition: boolean) => {
  useEffect(() => {
    if (playCondition) {
      alertSound.play();
    }
  }, [playCondition]);
};

// Usage in component:
// Play sound only for unregistered vehicles
usePiBoxWebSocket({
  // ...
  onCameraEvent: (camera, event) => {
    if (!event.access_granted) {
      alertSound.play();  // Alert for unknown vehicle
    }
  },
});
```

### Push Notification on Background

```typescript
// services/PiBoxBackgroundService.ts
import BackgroundService from 'react-native-background-actions';
import PushNotification from 'react-native-push-notification';

const piboxTask = async (taskData: { url: string; cameras: string[] }) => {
  const ws = new WebSocket(taskData.url);

  ws.onopen = () => {
    taskData.cameras.forEach(camera => {
      ws.send(JSON.stringify({
        action: 'subscribe',
        camera,
        filter: 'unregistered',
      }));
    });
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);

    if (msg.type === 'camera_event' && !msg.data.access_granted) {
      PushNotification.localNotification({
        title: 'Unknown Vehicle Detected',
        message: `Plate: ${msg.data.plate} at ${msg.camera}`,
        playSound: true,
        soundName: 'alert.mp3',
      });
    }
  };

  // Keep running
  await new Promise(() => {});
};

export const startBackgroundMonitoring = async () => {
  await BackgroundService.start(piboxTask, {
    taskName: 'PiBox Monitor',
    taskTitle: 'Monitoring Vehicle Access',
    taskDesc: 'Listening for unknown vehicles',
    taskIcon: { name: 'ic_launcher', type: 'mipmap' },
    parameters: {
      url: 'ws://192.168.1.100:8081',
      cameras: ['RESIDENT_ENTRY_01', 'RESIDENT_ENTRY_02'],
    },
  });
};
```

## Implementation Status

| Feature | Status |
|---------|--------|
| Basic subscription | Implemented |
| Camera-specific events | Implemented |
| Broadcast to all | Implemented |
| Filter: all | Implemented (default) |
| Filter: unregistered | Implemented |
| Filter: registered | Implemented |
| Filter: none | Implemented |
