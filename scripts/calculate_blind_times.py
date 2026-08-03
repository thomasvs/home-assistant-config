#!/usr/bin/env python3
"""
calculate_blind_times.py — Calculates daily morning sunlight window for northern blinds.
"""
import os
import sys
import math
import datetime
import urllib.request
import urllib.parse
import json
import ssl
from pathlib import Path

# Try importing zoneinfo (Python 3.9+)
try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Fallback to pytz or basic timezone offsets if needed, but ZoneInfo is standard in modern python
    import sys
    sys.stderr.write("ZoneInfo library not found. Please ensure Python 3.9+ is installed.\n")
    sys.exit(1)

DEFAULT_REFRESH_TOKEN = '84ab77b7e1b8953fa1938af9329e4bc2414ab33bde64d1bbd2ebe5b2ed1c6b1bad47eec626372207cf36f51ea051639b3c3d1fc5417bdcb2fd7850090806c007'

def get_hass_client():
    token = os.getenv('HASS_REFRESH_TOKEN', DEFAULT_REFRESH_TOKEN)
    
    # Try localhost first (when running inside the HA Core container), then fallback to external URL
    url = 'http://localhost:8123'
    try:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.connect(('127.0.0.1', 8123))
    except Exception:
        url = os.getenv('HASS_URL', 'https://amantes.duckdns.org:8123').rstrip('/')
        
    return HASSClient(url, token, is_supervisor=False)


class HASSClient:
    def __init__(self, base_url: str, token: str, is_supervisor: bool = False):
        self.base_url = base_url
        self.token = token
        self.is_supervisor = is_supervisor
        self.access_token = None
        self.ssl_ctx = ssl.create_default_context()
        self.ssl_ctx.check_hostname = False
        self.ssl_ctx.verify_mode = ssl.CERT_NONE

    def get_access_token(self) -> str:
        if self.is_supervisor:
            return self.token
        if self.access_token:
            return self.access_token
            
        data = urllib.parse.urlencode({
            'grant_type': 'refresh_token',
            'refresh_token': self.token,
            'client_id': 'https://home-assistant.io/iOS'
        }).encode()
        
        req = urllib.request.Request(f"{self.base_url}/auth/token", data=data)
        try:
            with urllib.request.urlopen(req, context=self.ssl_ctx) as res:
                token_data = json.loads(res.read().decode())
                self.access_token = token_data['access_token']
                return self.access_token
        except Exception as e:
            # Fallback to local access token if refresh fails (useful if base_url is internal)
            if self.base_url == 'http://localhost:8123':
                return self.token
            sys.stderr.write(f"Error obtaining access token: {e}\n")
            sys.exit(1)

    def get_url(self, endpoint: str) -> str:
        if self.is_supervisor:
            return f"{self.base_url}/{endpoint.lstrip('/')}"
        return f"{self.base_url}/api/{endpoint.lstrip('/')}"

    def make_api_request(self, endpoint: str, method: str = 'GET', post_data: dict = None) -> str:
        token = self.get_access_token()
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        
        data_bytes = None
        if post_data is not None:
            data_bytes = json.dumps(post_data).encode('utf-8')
            
        req = urllib.request.Request(
            self.get_url(endpoint), 
            headers=headers, 
            data=data_bytes,
            method=method
        )
        with urllib.request.urlopen(req, context=self.ssl_ctx) as res:
            return res.read().decode()

def get_sun_position(dt: datetime.datetime, latitude: float, longitude: float):
    # dt must be a UTC datetime
    day_of_year = dt.timetuple().tm_yday
    gamma = 2.0 * math.pi / 365.0 * (day_of_year - 1 + (dt.hour - 12.0) / 24.0)
    
    decl = 0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma) \
           - 0.006758 * math.cos(2.0 * gamma) + 0.000907 * math.sin(2.0 * gamma) \
           - 0.002697 * math.cos(3.0 * gamma) + 0.00148 * math.sin(3.0 * gamma)
           
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma) \
             - 0.014615 * math.cos(2.0 * gamma) - 0.040849 * math.sin(2.0 * gamma))
             
    time_offset = eqtime + 4.0 * longitude
    t_solar = dt.hour * 60.0 + dt.minute + dt.second / 60.0 + time_offset
    ha = 15.0 * ((t_solar / 60.0) - 12.0)
    
    lat_rad = math.radians(latitude)
    ha_rad = math.radians(ha)
    
    cos_zenith = math.sin(lat_rad) * math.sin(decl) + math.cos(lat_rad) * math.cos(decl) * math.cos(ha_rad)
    cos_zenith = max(-1.0, min(1.0, cos_zenith))
    zenith = math.acos(cos_zenith)
    elevation = 90.0 - math.degrees(zenith)
    
    sin_zenith = math.sin(zenith)
    if sin_zenith < 1e-6:
        azimuth = 180.0
    else:
        cos_az = (math.sin(decl) - math.sin(lat_rad) * cos_zenith) / (math.cos(lat_rad) * sin_zenith)
        cos_az = max(-1.0, min(1.0, cos_az))
        azimuth = math.degrees(math.acos(cos_az))
        if ha > 0:
            azimuth = 360.0 - azimuth
            
    return elevation, azimuth

def main():
    client = get_hass_client()
    print("🌍 Retrieving Home Assistant configuration and timezone...")
    try:
        config = json.loads(client.make_api_request('config'))
    except Exception as e:
        # Fallback to local server if external DNS is failing/not looping back inside shell_command
        print(f"⚠️ External connection failed ({e}). Falling back to localhost...")
        client.base_url = 'http://localhost:8123'
        client.is_supervisor = False
        config = json.loads(client.make_api_request('config'))

    lat = float(config['latitude'])
    lon = float(config['longitude'])
    tz_name = config['time_zone']
    tz = ZoneInfo(tz_name)
    
    print(f"✓ Location: lat={lat}, lon={lon}, tz={tz_name}")
    
    # Retrieve settings from helpers (with default fallbacks)
    facade_orientation = 29.0
    min_elevation = 5.0
    
    try:
        state = json.loads(client.make_api_request('states/input_number.building_north_orientation'))
        facade_orientation = float(state['state'])
    except Exception:
        print("⚠️ input_number.building_north_orientation helper not found, using default 29.0°")
        
    try:
        state = json.loads(client.make_api_request('states/input_number.blinds_min_sun_elevation'))
        min_elevation = float(state['state'])
    except Exception:
        print("⚠️ input_number.blinds_min_sun_elevation helper not found, using default 5.0°")
        
    print(f"✓ Facade heading: {facade_orientation}°, Min elevation: {min_elevation}°")
    
    # Simulate the current day minute-by-minute in local time
    local_now = datetime.datetime.now(tz)
    local_today = local_now.date()
    
    start_time = datetime.datetime.combine(local_today, datetime.time(4, 0), tz) # start at 4:00 AM local
    end_time = datetime.datetime.combine(local_today, datetime.time(13, 0), tz)  # morning ends by 1:00 PM local
    
    close_time = None
    open_time = None
    
    current = start_time
    delta = datetime.timedelta(minutes=1)
    
    print(f"☀️ Simulating morning sun window for {local_today}...")
    while current <= end_time:
        utc_dt = current.astimezone(datetime.timezone.utc)
        el, az = get_sun_position(utc_dt, lat, lon)
        
        # Check angle relative to facade
        # Facing is facade_orientation (e.g. 29°). Facade normal points at 29°.
        # Facade plane is perpendicular, going from 29 - 90 = -61 (299°) to 29 + 90 = 119°.
        # The sun shines on the facade if the difference is between -90° and +90°.
        diff = (az - facade_orientation + 180) % 360 - 180
        
        # We only care about the morning sun (east side, azimuth <= facade + 90)
        # So diff should be between -90 and +90
        is_hitting = (abs(diff) < 90.0) and (el >= min_elevation)
        
        if is_hitting:
            # We found the morning window start
            if close_time is None:
                close_time = current
            # Keep advancing open_time as long as it hits the facade
            open_time = current
            
        current += delta

    # Update input datetimes in Home Assistant
    if close_time and open_time:
        # We found a sunlight window!
        close_str = close_time.strftime("%H:%M:00")
        open_str = open_time.strftime("%H:%M:00")
        print(f"✅ Calculated morning window: Close blinds at {close_str}, Open blinds at {open_str}")
    else:
        # No window (e.g. in winter)
        close_str = "00:00:00"
        open_str = "00:00:00"
        print("❄️ No morning sunlight window found (winter/cloud simulation). setting helper times to 00:00:00.")

    # Call service input_datetime.set_datetime
    try:
        client.make_api_request('services/input_datetime/set_datetime', method='POST', post_data={
            'entity_id': 'input_datetime.north_blinds_close_time',
            'time': close_str
        })
        client.make_api_request('services/input_datetime/set_datetime', method='POST', post_data={
            'entity_id': 'input_datetime.north_blinds_open_time',
            'time': open_str
        })
        print("✓ Helpers successfully updated in Home Assistant.")
    except Exception as e:
        print(f"❌ Failed to set input_datetime helpers: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
