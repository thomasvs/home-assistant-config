import asyncio
import logging
import json
import time
from typing import Dict, Optional, Any
import aiohttp
from pathlib import Path
try:
    from asyncio import timeout as async_timeout_ctx
except ImportError:
    import async_timeout
    async_timeout_ctx = async_timeout.timeout

from aiohttp.client_exceptions import ClientResponseError

from homeassistant.exceptions import HomeAssistantError

from .netgear_auth import (
    NetgearAuthenticator,
    InvalidAuth,
    CannotConnect,
    AuthenticationBlocked,
    PasswordResetRequired,
    PendingChallenge,
    ChallengeRequired,
    InvalidChallenge,
    AuthResult,
)

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://api.meural.com/v0/"

AUTH_RETRY_BACKOFF_BASE = 300.0  # 5 minutes minimum backoff on failure
AUTH_RETRY_BACKOFF_MAX = 3600.0   # 1 hour maximum backoff

# Account-level auth backoff state surviving PyMeural recreations
_AUTH_BACKOFF: Dict[str, Dict[str, Any]] = {}


def _get_backoff_state(username: str) -> Dict[str, Any]:
    return _AUTH_BACKOFF.setdefault(username, {"last_failure": 0.0, "failure_count": 0, "error": None})


def _record_auth_failure(username: str, err: Exception) -> None:
    state = _get_backoff_state(username)
    state["last_failure"] = time.monotonic()
    state["failure_count"] += 1
    state["error"] = err


def _in_backoff(username: str) -> bool:
    state = _get_backoff_state(username)
    if not state["last_failure"]:
        return False
    delay = min(AUTH_RETRY_BACKOFF_BASE * (2 ** (state["failure_count"] - 1)), AUTH_RETRY_BACKOFF_MAX)
    return (time.monotonic() - state["last_failure"]) < delay


async def authenticate(
    session: aiohttp.ClientSession,
    username: str,
    password: str,
    authenticator: Optional[NetgearAuthenticator] = None,
) -> str:
    """Authenticate and return a Meural access token via Netgear Accounts / Cognito."""
    _LOGGER.info("Meural: Authenticating via Netgear Accounts")
    authenticator = authenticator or NetgearAuthenticator(session)
    try:
        auth_result: AuthResult = await authenticator.authenticate(username, password)
        _LOGGER.info("Meural: Authentication successful")
        state = _get_backoff_state(username)
        state["last_failure"] = 0.0
        state["failure_count"] = 0
        state["error"] = None
        return auth_result.access_token
    except PasswordResetRequired as err:
        _LOGGER.error("Meural: %s", err)
        _record_auth_failure(username, err)
        raise
    except ChallengeRequired as err:
        _LOGGER.warning("Meural: Interactive authentication challenge required (MFA/OTP): %s", err)
        _record_auth_failure(username, err)
        raise
    except InvalidAuth as err:
        _LOGGER.warning("Meural: Invalid credentials: %s", err)
        _record_auth_failure(username, err)
        raise
    except CannotConnect as err:
        _LOGGER.warning("Meural: Could not connect to Netgear auth: %s", err)
        _record_auth_failure(username, err)
        raise
    except Exception as err:
        _LOGGER.error("Meural: Unexpected auth error: %s", err)
        wrapped = CannotConnect(str(err))
        _record_auth_failure(username, wrapped)
        raise wrapped from err


class PyMeural:
    def __init__(self, username, password, token, token_update_callback, session: aiohttp.ClientSession):
        self.username = username
        self.password = password
        self.session = session
        self.token = token
        self.token_update_callback = token_update_callback

    async def request(self, method, path, data=None) -> Dict:
        fetched_new_token = self.token is None
        if self.token is None:
            if _in_backoff(self.username):
                raise CannotConnect(f"Meural cloud auth currently in backoff for {self.username}")
            await self.get_new_token()
        url = f"{BASE_URL}{path}"
        kwargs = {}
        if data:
            if method == "get":
                kwargs["query"] = data
            else:
                kwargs["json"] = data
        async with async_timeout_ctx(10):
            try:
                resp = await self.session.request(
                    method,
                    url,
                    headers={
                        "Authorization": f"Token {self.token}",
                        "x-meural-api-version": "3",
                    },
                    raise_for_status=True,
                    **kwargs,
                )
            except ClientResponseError as err:
                if err.status != 401:
                    raise
                # If a new token was just fetched and it fails again, just raise
                if fetched_new_token:
                    raise
                _LOGGER.info("Meural: Sending Request failed (401). Re-Authenticating")
                self.token = None
                if _in_backoff(self.username):
                    raise CannotConnect(f"Meural cloud auth currently in backoff for {self.username}")
                return await self.request(method, path, data)
            except Exception as err:
                _LOGGER.error("Meural: Sending Request failed. Raising: %s", err)
                raise
        response = await resp.json()
        return response["data"]

    async def get_new_token(self):
        if _in_backoff(self.username):
            _LOGGER.debug("Meural: Skipping get_new_token while in backoff")
            raise CannotConnect(f"Meural auth in backoff for {self.username}")
        try:
            self.token = await authenticate(self.session, self.username, self.password)
            self.token_update_callback(self.token)
        except ChallengeRequired as err:
            raise CannotConnect(f"Interactive challenge required: {err}") from err

    async def get_user(self):
        return await self.request("get", "user")

    async def get_user_items(self):
        return await self.request("get", "user/items")

    async def get_user_galleries(self):
        return await self.request("get", "user/galleries")

    async def get_user_devices(self):
        return await self.request("get", "user/devices")

    async def get_user_feedback(self):
        return await self.request("get", "user/feedback")

    async def device_load_gallery(self, device_id, gallery_id):
        return await self.request("post", f"devices/{device_id}/galleries/{gallery_id}")

    async def device_load_item(self, device_id, item_id):
        return await self.request("post", f"devices/{device_id}/items/{item_id}")

    async def get_device(self, device_id):
        return await self.request("get", f"devices/{device_id}")

    async def get_device_galleries(self, device_id):
        return await self.request("get", f"devices/{device_id}/galleries")

    async def update_device(self, device_id, data):
        return await self.request("put", f"devices/{device_id}", data)

    async def sync_device(self, device_id):
        return await self.request("post", f"devices/{device_id}/sync")

    async def get_item(self, item_id):
        if not self.token or _in_backoff(self.username):
            # Avoid blocking the 10s local frame polling cycle when cloud auth is unavailable or in backoff
            return {}
        url = f"{BASE_URL}items/{item_id}"
        headers = {"x-meural-api-version": "3", "Authorization": f"Token {self.token}"}
        try:
            async with async_timeout_ctx(5):
                resp = await self.session.get(url, headers=headers)
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", {})
                elif resp.status == 401:
                    # Token invalid; mark for re-auth and enter backoff
                    _LOGGER.info("Meural: Item token rejected (401); invalidating token")
                    self.token = None
                    _record_auth_failure(self.username, InvalidAuth("Token expired"))
                    return {}
        except Exception:
            pass
        return {}


class LocalMeural:
    def __init__(self, device, session: aiohttp.ClientSession):
        self.ip = device["localIp"]
        self.device = device
        self.session = session

    async def request(self, method, path, data=None) -> Dict:
        url = f"http://{self.ip}/remote/{path}"
        kwargs = {}
        if data:
            if method == "get":
                kwargs["query"] = data
            else:
                kwargs["data"] = data
        try:
            async with async_timeout_ctx(10):
                resp = await self.session.request(
                    method,
                    url,
                    raise_for_status=True,
                    **kwargs,
                )
            response = await resp.json(content_type=None)
            return response["response"]
        except aiohttp.client_exceptions.ClientConnectorError:
            raise DeviceTurnedOff

    async def send_key_right(self):
        return await self.request("get", f"control_command/set_key/right/")

    async def send_key_left(self):
        return await self.request("get", f"control_command/set_key/left/")

    async def send_key_up(self):
        return await self.request("get", f"control_command/set_key/up/")

    async def send_key_down(self):
        return await self.request("get", f"control_command/set_key/down/")

    async def send_key_suspend(self):
        return await self.request("get", f"control_command/suspend")

    async def send_key_resume(self):
        return await self.request("get", f"control_command/resume")

    async def send_control_backlight(self, brightness):
        return await self.request("get", f"control_command/set_backlight/{brightness}/")

    async def send_als_calibrate_off(self):
        return await self.request("get", f"control_command/als_calibrate/off/")

    async def send_set_portrait(self):
        return await self.request("get", f"control_command/set_orientation/portrait")

    async def send_set_landscape(self):
        return await self.request("get", f"control_command/set_orientation/landscape")

    async def send_change_gallery(self, gallery_id):
        return await self.request("get", f"control_command/change_gallery/{gallery_id}")

    async def send_change_item(self, item_id):
        return await self.request("get", f"control_command/change_item/{item_id}")

    async def send_get_backlight(self):
        return await self.request("get", f"get_backlight/")

    async def send_get_sleep(self):
        return await self.request("get", f"control_check/sleep/")

    async def send_get_system(self):
        return await self.request("get", f"control_check/system/")

    async def send_identify(self):
        return await self.request("get", f"identify/")

    async def send_get_wifi_connections(self):
        return await self.request("get", f"get_wifi_connections_json/")

    async def send_get_galleries(self):
        return await self.request("get", f"get_galleries_json/")

    async def send_get_gallery_status(self):
        return await self.request("get", f"get_gallery_status_json/")

    async def send_get_items_by_gallery(self, gallery_id):
        return await self.request("get", f"get_frame_items_by_gallery_json/{gallery_id}")

    async def send_postcard(self, url, content_type):
        # photo uploads are done doing a multipart/form-data form
        # with key 'photo' and value being the image data

        if content_type in ('image/jpg', 'image/jpeg'):
            content_type = 'image/jpeg'
        elif content_type == 'image/png':
            content_type = 'image/png'

        _LOGGER.info('Meural device %s: Sending postcard. URL is %s',
                     self.device.get('alias', 'meural'), url)

        image = None
        # Check if URL refers to local Home Assistant storage (/local/ -> /config/www/ or /config/...)
        local_path = None
        if url.startswith('/config/'):
            local_path = Path(url.split('?')[0])
        elif url.startswith('/local/'):
            local_path = Path('/config/www') / url[7:].split('?')[0]
        elif '/local/' in url:
            subpath = url.split('/local/', 1)[1].split('?')[0]
            local_path = Path('/config/www') / subpath

        if local_path and (local_path.exists() or local_path.parent.exists()):
            # Wait briefly if snapshot is still being written to disk
            for _ in range(5):
                if local_path.is_file() and local_path.stat().st_size > 0:
                    break
                await asyncio.sleep(0.1)

            # Fallback from item-specific <item_id>_<effect>.jpg to generic <effect>.jpg
            if not local_path.is_file() and '_' in local_path.stem:
                fallback_name = local_path.stem.split('_')[-1] + local_path.suffix
                alt_path = local_path.parent / fallback_name
                if alt_path.is_file():
                    local_path = alt_path

            if local_path.is_file():
                try:
                    def _read_file():
                        with open(local_path, 'rb') as f:
                            return f.read()
                    image = await asyncio.get_running_loop().run_in_executor(None, _read_file)
                    _LOGGER.info('Meural device %s: Read %d bytes directly from local file %s',
                                 self.device.get('alias', 'meural'), len(image), local_path)
                except Exception as e:
                    _LOGGER.warning('Meural device %s: Could not read local file %s: %s',
                                    self.device.get('alias', 'meural'), local_path, e)

        # Fallback to downloading over HTTP/HTTPS if not available locally
        if image is None:
            try:
                async with async_timeout_ctx(10):
                    async with self.session.get(url, ssl=False) as response:
                        image = await response.read()
                _LOGGER.info('Meural device %s: Downloaded %d bytes of image from %s',
                             self.device.get('alias', 'meural'), len(image), url)
            except Exception as err:
                _LOGGER.error('Meural device %s: Failed to fetch image from %s: %s',
                              self.device.get('alias', 'meural'), url, err)
                return None

        # Ensure Meural screen is awake/resumed to show postcard
        try:
            await self.send_key_resume()
        except Exception as e:
            _LOGGER.debug('Meural device %s: Resume before postcard returned: %s',
                          self.device.get('alias', 'meural'), e)

        import time
        ts_ms = int(time.time() * 1000)
        filename = f'postcard_{ts_ms}.jpg' if content_type == 'image/jpeg' else f'postcard_{ts_ms}.png'
        data = aiohttp.FormData()
        data.add_field('photo', image, content_type=content_type, filename=filename)

        try:
            async with async_timeout_ctx(15):
                async with self.session.post(f"http://{self.ip}/remote/postcard", data=data) as response:
                    text = await response.text()
                    try:
                        r = json.loads(text)
                        _LOGGER.info('Meural device %s: Image uploaded, status: %s, response: %s',
                                     self.device.get('alias', 'meural'), r.get('status'), r.get('response'))
                    except Exception:
                        _LOGGER.info('Meural device %s: Postcard response: %s',
                                     self.device.get('alias', 'meural'), text)
                    return response
        except Exception as err:
            _LOGGER.error('Meural device %s: Failed to upload postcard to http://%s/remote/postcard: %s',
                          self.device.get('alias', 'meural'), self.ip, err)
            return None


class DeviceTurnedOff(HomeAssistantError):
    """Error to indicate device turned off or not connected to the network."""
