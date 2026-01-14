"""API client for HGSmart Pet Feeder."""
import json
import logging
import time
import uuid
from typing import Any

import aiohttp

from .const import BASE_URL, CLIENT_ID, CLIENT_SECRET

_LOGGER = logging.getLogger(__name__)


class HGSmartApiClient:
    """API client for HGSmart devices."""

    def __init__(self, username: str, password: str) -> None:
        """Initialize the API client."""
        self.username = username
        self.password = password
        self.access_token: str | None = None
        self.refresh_token: str | None = None

    def _get_headers(self, use_token: bool = True) -> dict[str, str]:
        """Build standard headers for API calls."""
        headers = {
            "User-Agent": "Dart/3.6 (dart:io)",
            "Accept-Language": "it-IT",
            "Zoneid": "Europe/Rome",
            "Client": CLIENT_ID,
            "Wunit": "0",
            "Tunit": "0",
            "Content-Type": "application/json",
        }
        if use_token and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    async def login(self) -> bool:
        """Login with username and password."""
        url = f"{BASE_URL}/oauth/login"
        payload = {
            "account_num": self.username,
            "pwd": self.password,
            "captcha_uuid": "",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        }

        headers = self._get_headers(use_token=False)
        headers["Authorization"] = "Bearer null"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    data = await response.json()

                    if data.get("code") == 200:
                        self.access_token = data["data"]["accessToken"]
                        self.refresh_token = data["data"]["refreshToken"]
                        _LOGGER.info("Successfully logged in to HGSmart")
                        return True
                    else:
                        _LOGGER.error("Login failed: %s", data.get("msg"))
                        return False
        except Exception as e:
            _LOGGER.exception("Login error: %s", e)
            return False

    async def refresh_access_token(self) -> bool:
        """Refresh access token using refresh token."""
        if not self.refresh_token:
            return False

        url = f"{BASE_URL}/oauth/refreshToken"
        payload = {"refreshtoken": self.refresh_token}

        headers = self._get_headers()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    data = await response.json()

                    if data.get("code") == 200:
                        self.access_token = data["data"]["accessToken"]
                        self.refresh_token = data["data"]["refreshToken"]
                        _LOGGER.info("Successfully refreshed token")
                        return True
                    else:
                        _LOGGER.error("Token refresh failed: %s", data.get("msg"))
                        return False
        except Exception as e:
            _LOGGER.exception("Token refresh error: %s", e)
            return False

    async def get_devices(self) -> list[dict[str, Any]]:
        """Get list of all devices."""
        url = f"{BASE_URL}/app/device/list"
        headers = self._get_headers()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    data = await response.json()

                    if data.get("code") == 200:
                        return data.get("data", [])
                    elif data.get("code") == 401:
                        # Token expired, try refresh
                        if await self.refresh_access_token():
                            return await self.get_devices()
                        return []
                    else:
                        _LOGGER.error("Get devices failed: %s", data.get("msg"))
                        return []
        except Exception as e:
            _LOGGER.exception("Get devices error: %s", e)
            return []

    async def get_feeder_stats(self, device_id: str) -> dict[str, Any] | None:
        """Get feeder statistics (remaining food, desiccant expiration)."""
        url = f"{BASE_URL}/app/device/feeder/summary/{device_id}"
        headers = self._get_headers()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    data = await response.json()

                    if data.get("code") == 200:
                        return data.get("data")
                    else:
                        _LOGGER.error("Get feeder stats failed: %s", data.get("msg"))
                        return None
        except Exception as e:
            _LOGGER.exception("Get feeder stats error: %s", e)
            return None

    async def get_device_attributes(self, device_id: str) -> dict[str, Any] | None:
        """Get device attributes including feeding schedules."""
        url = f"{BASE_URL}/app/device/attribute/{device_id}"
        headers = self._get_headers()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    data = await response.json()

                    if data.get("code") == 200:
                        return data.get("data")
                    else:
                        _LOGGER.error("Get device attributes failed: %s", data.get("msg"))
                        return None
        except Exception as e:
            _LOGGER.exception("Get device attributes error: %s", e)
            return None

    async def send_feed_command(self, device_id: str, portions: int = 1) -> bool:
        """Send feed command to device."""
        url = f"{BASE_URL}/app/device/attribute/{device_id}"

        # Build command payload
        current_time_ms = int(time.time() * 1000)
        spoofed_uuid = uuid.uuid1(node=0x8DD711617773, clock_seq=0x8697)
        message_id = spoofed_uuid.hex

        current_minute = time.localtime().tm_min
        minute_hex = f"{current_minute:02x}"
        portions_hex = f"{portions:02x}"
        command_value = f"0120{minute_hex}{portions_hex}"

        payload_dict = {
            "ctrl": {"identifier": "userfoodframe", "value": command_value},
            "ctrl_time": str(current_time_ms),
            "message_id": message_id,
        }

        headers = {
            "User-Agent": "Dart/3.6 (dart:io)",
            "Authorization": f"Bearer {self.access_token}",
            "Accept-Language": "it-IT",
            "Zoneid": "Europe/Rome",
            "Client": CLIENT_ID,
            "Wunit": "0",
            "Tunit": "0",
        }

        try:
            payload_json = json.dumps(payload_dict)
            
            # Create multipart form data
            data = aiohttp.FormData()
            data.add_field('command', payload_json, content_type='application/json')
            
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    url, headers=headers, data=data, timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    result = await response.json()
                    
                    if result.get("code") == 200:
                        _LOGGER.info("Feed command sent successfully to %s (%d portions)", device_id, portions)
                        return True
                    else:
                        _LOGGER.error("Feed command failed: %s", result.get("msg"))
                        return False
        except Exception as e:
            _LOGGER.exception("Feed command error: %s", e)
            return False
