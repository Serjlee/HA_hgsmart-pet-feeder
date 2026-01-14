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

    async def _request(
        self, method: str, url: str, **kwargs: Any
    ) -> dict[str, Any] | None:
        """Execute an API request with token refresh logic."""
        headers = kwargs.pop("headers", self._get_headers())
        
        # Ensure authorization header is set if not present and we have a token
        if "Authorization" not in headers and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method, url, headers=headers, timeout=aiohttp.ClientTimeout(total=10), **kwargs
                ) as response:
                    try:
                        data = await response.json()
                    except aiohttp.ContentTypeError:
                        _LOGGER.error("Failed to parse JSON response from %s", url)
                        return None

                    if data.get("code") == 200:
                        return data
                    elif data.get("code") == 401:
                        # Token expired, try refresh
                        _LOGGER.info("Token expired, attempting refresh")
                        if await self.refresh_access_token():
                            # Update headers with new token
                            headers["Authorization"] = f"Bearer {self.access_token}"
                            # Retry request
                            async with session.request(
                                method, url, headers=headers, timeout=aiohttp.ClientTimeout(total=10), **kwargs
                            ) as retry_response:
                                try:
                                    retry_data = await retry_response.json()
                                    if retry_data.get("code") == 200:
                                        return retry_data
                                    else:
                                        _LOGGER.error("Request failed after refresh: %s", retry_data.get("msg"))
                                        return None
                                except aiohttp.ContentTypeError:
                                    _LOGGER.error("Failed to parse JSON response on retry from %s", url)
                                    return None
                        else:
                            _LOGGER.error("Token refresh failed, cannot retry request")
                            return None
                    else:
                        _LOGGER.error("Request failed: %s", data.get("msg"))
                        return None

        except Exception as e:
            _LOGGER.exception("Request error to %s: %s", url, e)
            return None

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
        data = await self._request("GET", url)
        if data:
            return data.get("data", [])
        return []

    async def get_feeder_stats(self, device_id: str) -> dict[str, Any] | None:
        """Get feeder statistics (remaining food, desiccant expiration)."""
        url = f"{BASE_URL}/app/device/feeder/summary/{device_id}"
        data = await self._request("GET", url)
        if data:
            return data.get("data")
        return None

    async def get_device_attributes(self, device_id: str) -> dict[str, Any] | None:
        """Get device attributes including feeding schedules."""
        url = f"{BASE_URL}/app/device/attribute/{device_id}"
        data = await self._request("GET", url)
        if data:
            return data.get("data")
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

        # Override headers because we are sending form data, not json
        headers = self._get_headers()
        # Remove Content-Type so aiohttp can set it for multipart/form-data
        if "Content-Type" in headers:
            del headers["Content-Type"]

        payload_json = json.dumps(payload_dict)

        # Create multipart form data
        data = aiohttp.FormData()
        data.add_field('command', payload_json, content_type='application/json')

        result = await self._request("PUT", url, headers=headers, data=data)

        if result and result.get("code") == 200:
            _LOGGER.info("Feed command sent successfully to %s (%d portions)", device_id, portions)
            return True
        return False

    async def reset_desiccant(self, device_id: str) -> bool:
        """Reset desiccant expiration timer."""
        url = f"{BASE_URL}/app/device/feeder/desiccant/{device_id}"
        result = await self._request("PUT", url)

        if result and result.get("code") == 200:
            _LOGGER.info("Desiccant reset successfully for %s", device_id)
            return True
        return False

    async def set_food_remaining(self, device_id: str, percentage: int) -> bool:
        """Set food remaining percentage (0-100)."""
        url = f"{BASE_URL}/app/device/feeder/refill"

        # API uses capacity of 200, so convert percentage to that scale
        capacity = 200
        surplus = int((percentage / 100.0) * capacity)

        payload = {
            "deviceId": device_id,
            "capacity": capacity,
            "surplus": surplus,
            "capacityModel": "",
        }

        result = await self._request("PUT", url, json=payload)

        if result and result.get("code") == 200:
            _LOGGER.info("Food remaining set to %d%% for %s", percentage, device_id)
            return True
        return False