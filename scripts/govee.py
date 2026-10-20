#!/usr/bin/env python3
"""Govee Device Control Script — called by Hermes terminal tool or cron."""
import sys, json, uuid, urllib.request, urllib.error

from _lib import load_env

BASE_URL = 'https://openapi.api.govee.com/router/api/v1'
DEVICES_URL = f'{BASE_URL}/user/devices'
CONTROL_URL = f'{BASE_URL}/device/control'
STATES_URL = f'{BASE_URL}/device/state'

def get_api_key():
    return load_env().get('GOVEE_API_KEY')

def api_request(method, url, body=None):
    key = get_api_key()
    if not key:
        return {'error': 'Govee API key not found'}
    req = urllib.request.Request(url, method=method)
    req.add_header('Content-Type', 'application/json')
    req.add_header('Govee-API-Key', key)
    data_bytes = json.dumps(body).encode() if body else None
    try:
        with urllib.request.urlopen(req, data=data_bytes, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {'error': f'HTTP {e.code}', 'body': e.read().decode()}
    except Exception as e:
        return {'error': str(e)}

def list_devices():
    result = api_request('GET', DEVICES_URL)
    if 'error' in result:
        return result
    devices = result.get('data', [])
    out = []
    for d in devices:
        sku = d.get('sku', '?')
        device = d.get('device', '?')
        name = d.get('deviceName', 'Unnamed')
        caps = [c.get('instance') for c in d.get('capabilities', [])]
        caps_str = ', '.join(caps)
        out.append({'name': name, 'sku': sku, 'device': device, 'capabilities': caps_str})
    return out

def control_device(device_id, sku, capability_type, instance, value):
    body = {
        'requestId': str(uuid.uuid4()),
        'payload': {
            'sku': sku,
            'device': device_id,
            'capability': {
                'type': capability_type,
                'instance': instance,
                'value': value
            }
        }
    }
    return api_request('POST', CONTROL_URL, body)

def find_device(name):
    """Find a device by name. Exact match first, then partial."""
    result = api_request('GET', DEVICES_URL)
    if 'error' in result:
        return result
    devices = result.get('data', [])
    name_lower = name.lower()

    # Exact match first
    exact = [d for d in devices if d.get('deviceName', '').lower() == name_lower]
    if exact:
        return exact[0]

    # Partial match as fallback
    matches = [d for d in devices if name_lower in d.get('deviceName', '').lower()]
    if not matches:
        return {'error': f'No device matching "{name}"'}
    if len(matches) > 1:
        names = [f'{d["deviceName"]} ({d["sku"]} {d["device"][:12]}...)'
                 for d in matches]
        return {'error': f'Multiple matches: {"; ".join(names)}'}
    return matches[0]

def turn_on(name):
    d = find_device(name)
    if 'error' in d:
        return d
    return control_device(d['device'], d['sku'],
                         'devices.capabilities.on_off', 'powerSwitch', 1)

def turn_off(name):
    d = find_device(name)
    if 'error' in d:
        return d
    return control_device(d['device'], d['sku'],
                         'devices.capabilities.on_off', 'powerSwitch', 0)

def set_color(name, r, g, b):
    """Set RGB color (0-255 each)."""
    d = find_device(name)
    if 'error' in d:
        return d
    rgb_int = (r << 16) | (g << 8) | b
    return control_device(d['device'], d['sku'],
                         'devices.capabilities.color_setting', 'colorRgb', rgb_int)

def set_brightness(name, pct):
    """Set brightness (1-100)."""
    d = find_device(name)
    if 'error' in d:
        return d
    return control_device(d['device'], d['sku'],
                         'devices.capabilities.range', 'brightness', pct)

def set_temperature(name, kelvin):
    """Set color temperature in Kelvin (2000-9000)."""
    d = find_device(name)
    if 'error' in d:
        return d
    return control_device(d['device'], d['sku'],
                         'devices.capabilities.color_setting', 'colorTemperatureK', kelvin)

def get_status(name):
    """Check device state. Returns 'on', 'off', or 'unavailable' if offline."""
    d = find_device(name)
    if 'error' in d:
        return d
    body = {
        'requestId': str(uuid.uuid4()),
        'payload': {
            'sku': d['sku'],
            'device': d['device']
        }
    }
    result = api_request('POST', STATES_URL, body)
    if 'error' in result:
        return result
    caps = result.get('payload', {}).get('capabilities', [])
    online = True
    power = 0
    for cap in caps:
        if cap.get('instance') == 'online':
            online = bool(cap.get('state', {}).get('value', True))
        if cap.get('instance') == 'powerSwitch':
            power = cap.get('state', {}).get('value', 0)
    if not online:
        return 'unavailable'
    return 'on' if power else 'off'

def get_state_json(name):
    """Full state as JSON: power, brightness(1-100), rgb [r,g,b], online. For HA template lights."""
    d = find_device(name)
    if 'error' in d:
        return d
    body = {'requestId': str(uuid.uuid4()),
            'payload': {'sku': d['sku'], 'device': d['device']}}
    result = api_request('POST', STATES_URL, body)
    if 'error' in result:
        return result
    caps = result.get('payload', {}).get('capabilities', [])
    out = {'online': True, 'power': 0, 'brightness': 0, 'rgb': [255, 255, 255]}
    for cap in caps:
        inst = cap.get('instance')
        val = cap.get('state', {}).get('value')
        if inst == 'online':
            out['online'] = bool(val if val is not None else True)
        elif inst == 'powerSwitch':
            out['power'] = int(val or 0)
        elif inst == 'brightness' and val is not None:
            out['brightness'] = int(val)
        elif inst == 'colorRgb' and val is not None:
            iv = int(val)
            out['rgb'] = [(iv >> 16) & 255, (iv >> 8) & 255, iv & 255]
    return out

# CLI
if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'list'
    if cmd == 'list':
        devices = list_devices()
        if isinstance(devices, list):
            for d in devices:
                print(f"{d['name']:<25} {d['sku']:<12} {d['capabilities']}")
        else:
            print(json.dumps(devices, indent=2))
    elif cmd == 'on' and len(sys.argv) > 2:
        print(json.dumps(turn_on(' '.join(sys.argv[2:])), indent=2))
    elif cmd == 'off' and len(sys.argv) > 2:
        print(json.dumps(turn_off(' '.join(sys.argv[2:])), indent=2))
    elif cmd == 'color' and len(sys.argv) > 5:
        r, g, b = int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
        print(json.dumps(set_color(' '.join(sys.argv[5:]), r, g, b), indent=2))
    elif cmd == 'brightness' and len(sys.argv) > 3:
        print(json.dumps(set_brightness(' '.join(sys.argv[3:]), int(sys.argv[2])), indent=2))
    elif cmd == 'temp' and len(sys.argv) > 3:
        print(json.dumps(set_temperature(' '.join(sys.argv[3:]), int(sys.argv[2])), indent=2))
    elif cmd == 'status' and len(sys.argv) > 2:
        print(get_status(' '.join(sys.argv[2:])))
    elif cmd == 'state' and len(sys.argv) > 2:
        print(json.dumps(get_state_json(' '.join(sys.argv[2:]))))
    else:
        print("Usage: govee.py [list|on <name>|off <name>|color <r> <g> <b> <name>|brightness <pct> <name>|temp <kelvin> <name>|status <name>|state <name>]")
