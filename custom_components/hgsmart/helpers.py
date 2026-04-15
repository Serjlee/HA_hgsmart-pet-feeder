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


# Button lockout lives in GET /attribute payload only (ctrl identifier is still ``child``).
_CHILD_LOCK_KEYS = (
    "child",
    "Child",
    "childLock",
    "ChildLock",
    "childlock",
    "child_lock",
    "childLockOut",
    "childlockout",
)


def find_child_lock_attr_key(attrs: dict[str, Any]) -> str | None:
    """Return the attribute key used for child lock, if any."""
    for key in _CHILD_LOCK_KEYS:
        if key in attrs:
            return key
    for key in attrs:
        lk = str(key).lower()
        if "child" in lk and "lock" in lk:
            return str(key)
    return None


def read_child_lock_raw(device_data: dict[str, Any]) -> str | None:
    """Read child lock value from the attribute payload (GET /device/attribute)."""
    attrs = device_data.get("attributes")
    if not isinstance(attrs, dict):
        return None
    key = find_child_lock_attr_key(attrs)
    if key is None:
        _LOGGER.debug(
            "Child lock: no known key in attributes (sample keys: %s)",
            list(attrs.keys())[:25],
        )
        return None
    val = attrs[key]
    if val is None:
        return None
    return str(val).strip()


def is_child_lock_active(raw: str | None) -> bool:
    """Return True when API reports button lockout enabled."""
    if raw is None:
        return False
    s = str(raw).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    try:
        return int(float(s)) == 1
    except (TypeError, ValueError):
        return False


def snapshot_child_lock_keys(device_data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Copy current child-lock-related keys from attributes only."""
    attrs = device_data.get("attributes") or {}
    attr_keys = {k: attrs[k] for k in attrs if k in _CHILD_LOCK_KEYS or k == ATTR_CHILD}
    # Also snapshot any *lock* key matched by find
    for k in attrs:
        lk = str(k).lower()
        if "child" in lk and "lock" in lk and k not in attr_keys:
            attr_keys[k] = attrs[k]
    return (attr_keys, {})


def write_child_lock_optimistic(device_data: dict[str, Any], value: str) -> None:
    """Mirror child lock into attributes only (same key the API uses in GET)."""
    attrs = device_data.setdefault("attributes", {})
    key = find_child_lock_attr_key(attrs)
    if key is not None:
        attrs[key] = value
    else:
        attrs[ATTR_CHILD] = value


def restore_child_lock_keys(
    device_data: dict[str, Any],
    attr_keys: dict[str, Any],
    info_keys: dict[str, Any],
) -> None:
    """Restore attribute keys from a prior snapshot (``info_keys`` unused)."""
    del info_keys  # attributes-only; signature kept for call sites
    attrs = device_data.setdefault("attributes", {})
    for k in list(attrs.keys()):
        if k in _CHILD_LOCK_KEYS or k == ATTR_CHILD:
            if k not in attr_keys:
                attrs.pop(k, None)
        elif "child" in str(k).lower() and "lock" in str(k).lower():
            if k not in attr_keys:
                attrs.pop(k, None)
    attrs.update(attr_keys)
