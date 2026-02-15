"""
System Health Monitoring
"""
import os
import subprocess
import logging

logger = logging.getLogger(__name__)


def get_cpu_usage():
    """Get CPU usage percentage"""
    try:
        with open('/proc/stat', 'r') as f:
            line = f.readline()
            fields = line.split()
            idle = int(fields[4])
            total = sum(int(x) for x in fields[1:])
            # This is instantaneous, for better accuracy would need to compare over time
            usage = 100.0 * (1.0 - idle / total) if total > 0 else 0
            return round(usage, 1)
    except Exception as e:
        logger.debug(f"CPU usage error: {e}")
        return None


def get_cpu_usage_avg():
    """Get CPU load average (1, 5, 15 min)"""
    try:
        with open('/proc/loadavg', 'r') as f:
            parts = f.read().split()
            return {
                'load_1': float(parts[0]),
                'load_5': float(parts[1]),
                'load_15': float(parts[2])
            }
    except Exception as e:
        logger.debug(f"Load average error: {e}")
        return {'load_1': 0, 'load_5': 0, 'load_15': 0}


def get_memory_usage():
    """Get memory usage"""
    try:
        with open('/proc/meminfo', 'r') as f:
            meminfo = {}
            for line in f:
                parts = line.split(':')
                if len(parts) == 2:
                    key = parts[0].strip()
                    value = int(parts[1].strip().split()[0])  # value in kB
                    meminfo[key] = value

            total = meminfo.get('MemTotal', 0)
            free = meminfo.get('MemFree', 0)
            buffers = meminfo.get('Buffers', 0)
            cached = meminfo.get('Cached', 0)
            available = meminfo.get('MemAvailable', free + buffers + cached)

            used = total - available
            percent = (used / total * 100) if total > 0 else 0

            return {
                'total_mb': round(total / 1024, 1),
                'used_mb': round(used / 1024, 1),
                'free_mb': round(available / 1024, 1),
                'percent': round(percent, 1)
            }
    except Exception as e:
        logger.debug(f"Memory usage error: {e}")
        return {'total_mb': 0, 'used_mb': 0, 'free_mb': 0, 'percent': 0}


def get_disk_usage(path='/'):
    """Get disk usage for a path"""
    try:
        stat = os.statvfs(path)
        total = stat.f_blocks * stat.f_frsize
        free = stat.f_bavail * stat.f_frsize
        used = total - free
        percent = (used / total * 100) if total > 0 else 0

        return {
            'total_gb': round(total / (1024**3), 1),
            'used_gb': round(used / (1024**3), 1),
            'free_gb': round(free / (1024**3), 1),
            'percent': round(percent, 1)
        }
    except Exception as e:
        logger.debug(f"Disk usage error: {e}")
        return {'total_gb': 0, 'used_gb': 0, 'free_gb': 0, 'percent': 0}


def get_cpu_temperature():
    """Get CPU temperature (Raspberry Pi)"""
    try:
        # Try Raspberry Pi thermal zone
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            temp = int(f.read().strip()) / 1000.0
            return round(temp, 1)
    except Exception:
        try:
            # Try vcgencmd (Raspberry Pi)
            result = subprocess.run(['vcgencmd', 'measure_temp'],
                                    capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                # Output: temp=42.0'C
                temp_str = result.stdout.strip()
                temp = float(temp_str.replace('temp=', '').replace("'C", ''))
                return round(temp, 1)
        except Exception:
            pass
    return None


def get_uptime():
    """Get system uptime"""
    try:
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.read().split()[0])
            days = int(uptime_seconds // 86400)
            hours = int((uptime_seconds % 86400) // 3600)
            minutes = int((uptime_seconds % 3600) // 60)
            return {
                'seconds': int(uptime_seconds),
                'days': days,
                'hours': hours,
                'minutes': minutes,
                'formatted': f"{days}d {hours}h {minutes}m"
            }
    except Exception as e:
        logger.debug(f"Uptime error: {e}")
        return {'seconds': 0, 'days': 0, 'hours': 0, 'minutes': 0, 'formatted': 'N/A'}


def get_network_info():
    """Get network interface info"""
    interfaces = {}
    try:
        for iface in os.listdir('/sys/class/net'):
            if iface == 'lo':
                continue
            try:
                # Get IP address
                result = subprocess.run(
                    ['ip', '-4', 'addr', 'show', iface],
                    capture_output=True, text=True, timeout=5
                )
                ip = None
                for line in result.stdout.split('\n'):
                    if 'inet ' in line:
                        ip = line.strip().split()[1].split('/')[0]
                        break

                # Check if interface is up
                with open(f'/sys/class/net/{iface}/operstate', 'r') as f:
                    state = f.read().strip()

                interfaces[iface] = {
                    'ip': ip,
                    'state': state
                }
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"Network info error: {e}")
    return interfaces


def get_all_health():
    """Get all system health metrics"""
    return {
        'cpu': get_cpu_usage_avg(),
        'memory': get_memory_usage(),
        'disk': get_disk_usage(),
        'temperature': get_cpu_temperature(),
        'uptime': get_uptime(),
        'network': get_network_info()
    }
