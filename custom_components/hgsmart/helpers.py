"""Helper functions for the HGSmart Pet Feeder integration."""
import logging
from typing import Any, TypedDict

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

from .const import ATTR_CHILD, DOMAIN

_LOGGER = logging.getLogger(__name__)


class ScheduleSlotData(TypedDict):
    """Typed dictionary for schedule slot data."""

    enabled: bool
    hour: int  # UTC hour
    minute: int
    portions: int
    slot: int


def api_locale_from_hass(hass: HomeAssistant) -> str:
    """Map Home Assistant language to ``Accept-Language`` for the HGSmart API."""
    lang = hass.config.language or "en"
    low = lang.lower()
    if low == "en":
        return "en-US"
    if low.startswith("en-"):
        return lang.replace("_", "-")
    if "-" in lang:
        return lang.replace("_", "-")
    if "_" in lang:
        return lang.replace("_", "-")
    return f"{lang}-{lang.upper()}"


def api_timezone_from_hass(hass: HomeAssistant) -> str:
    """Use Home Assistant ``time_zone`` for API ``Zoneid`` header."""
    return str(hass.config.time_zone or "UTC")


def get_device_info(device_id: str, device_info: dict[str, Any]) -> DeviceInfo:
    """Build device info dictionary for entities."""
    return DeviceInfo(
        identifiers={(DOMAIN, device_id)},
        name=device_info["name"],
        manufacturer="HGSmart",
        model=device_info["type"],
        sw_version=device_info.get("fwVersion"),
    )


def parse_plan_value(plan_value: str) -> ScheduleSlotData | None:
    """Parse plan value string from API response.

    Format: SHHMMXPD (8 characters)
    - S: Status (1=Enabled, 0=Disabled, 3=Delete)
    - HH: Hour (00-23) in UTC
    - MM: Minute (00-59)
    - X: Spacer (always 0)
    - P: Portions (1-9)
    - D: Slot ID (0-5)

    Example: "10940033" = Enabled, 09:40 UTC, 3 portions, slot 3
    """
    if not plan_value or plan_value == "0" or len(plan_value) < 8:
        return None

    try:
        status = int(plan_value[0])
        hour = int(plan_value[1:3])
        minute = int(plan_value[3:5])
        portions = int(plan_value[6])
        slot = int(plan_value[7])

        # Validate ranges
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            _LOGGER.warning(
                "Invalid plan time values (raw: %s): hour=%d, minute=%d",
                plan_value,
                hour,
                minute,
            )
            return None

        if portions == 0:
            return None

        return {
            "enabled": status == 1,
            "hour": hour,
            "minute": minute,
            "portions": portions,
            "slot": slot,
        }
    except (ValueError, IndexError) as e:
        _LOGGER.error("Error parsing plan value %s: %s", plan_value, e)
        return None


def build_plan_value(
    hour: int, minute: int, portions: int, slot: int, enabled: bool = True
) -> str:
    """Build plan value string for API. See parse_plan_value() for format details."""
    status = 1 if enabled else 0
    return f"{status}{hour:02d}{minute:02d}0{portions}{slot}"

def read_child_lock_raw(device_data: dict[str, Any]) -> str | None:
    """Read child lock value from the attribute payload (GET /device/attribute)."""
    attrs = device_data.get("attributes")
    if not isinstance(attrs, dict):
        return None
    val = attrs[ATTR_CHILD]
    if val is None:
        return None
    return str(val).strip()


def is_child_lock_active(raw: str | None) -> bool:
    """Return True when API reports button lockout enabled."""
    if raw is None:
        return False
    return str(raw).strip() == "1"


