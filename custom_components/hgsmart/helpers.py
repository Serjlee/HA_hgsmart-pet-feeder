"""Helper functions for the HGSmart Pet Feeder integration."""
from datetime import datetime, timezone
import logging
from typing import Any, TypedDict

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_CHILD,
    DOMAIN,
    MAX_PORTIONS,
    SCHEDULE_SLOTS,
    SF_MAX_SCHEDULE_PORTIONS,
    SUPPORTED_MODEL_PREFIXES,
    WEEKDAYS,
)

WEEKDAY_NAMES = {
    "mon": "monday",
    "tue": "tuesday",
    "wed": "wednesday",
    "thu": "thursday",
    "fri": "friday",
    "sat": "saturday",
    "sun": "sunday",
}

_LOGGER = logging.getLogger(__name__)


class ScheduleSlotData(TypedDict):
    """Typed dictionary for schedule slot data."""

    enabled: bool
    hour: int  # UTC hour
    minute: int
    portions: int
    slot: int
    suffix: str  # trailing data, e.g. "_1111111" UTC weekday mask on SF35


def api_locale_from_hass(hass: HomeAssistant) -> str:
    """Map Home Assistant language to ``Accept-Language`` for the HGSmart API."""
    lang = hass.config.language or "en"
    country = hass.config.country or "US"
    return f"{lang}-{country}"


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


def is_supported_model(device_info: dict[str, Any]) -> bool:
    """Return True for models that have been tested with this integration."""
    return str(device_info.get("type", "")).startswith(SUPPORTED_MODEL_PREFIXES)


def parse_plan_value(plan_value: str) -> ScheduleSlotData | None:
    """Parse plan value string from API response.

    Format: SHHMMPPD[_MTWTFSS]
    - S: Status (1=Enabled, 0=Disabled, 3=Delete on S25/S30, 2=Deleted on SF35)
    - HH: Hour (00-23) in UTC
    - MM: Minute (00-59)
    - PP: Portions (01-09 on S25/S30, 01-24 on SF35)
    - D: Slot ID (0-5 on S25/S30, 0-9 on SF35)
    - _MTWTFSS: SF35 only, weekday mask Monday..Sunday (1=active), in UTC days

    Example: "10940033" = Enabled, 09:40 UTC, 3 portions, slot 3
    Example: "11100121_1111111" = Enabled, 11:00 UTC, 12 portions, slot 1, every day

    The suffix is kept so it can be written back unchanged.
    """
    if not plan_value or plan_value == "0" or len(plan_value) < 8:
        return None

    try:
        status = int(plan_value[0])
        hour = int(plan_value[1:3])
        minute = int(plan_value[3:5])
        portions = int(plan_value[5:7])
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
            "suffix": plan_value[8:],
        }
    except (ValueError, IndexError) as e:
        _LOGGER.error("Error parsing plan value %s: %s", plan_value, e)
        return None


def build_plan_value(
    hour: int,
    minute: int,
    portions: int,
    slot: int,
    enabled: bool = True,
    suffix: str = "",
) -> str:
    """Build plan value string for API. See parse_plan_value() for format details."""
    status = 1 if enabled else 0
    return f"{status}{hour:02d}{minute:02d}{portions:02d}{slot}{suffix}"


def shift_weekday_mask(suffix: str, days: int) -> str:
    """Shift an SF35 "_MTWTFSS" weekday mask by ``days`` (positive = later).

    The mask is in UTC days, so it must move when a time change makes the
    schedule cross midnight UTC. Other suffixes are returned unchanged.
    """
    mask = suffix[1:]
    if days % 7 == 0 or not suffix.startswith("_") or len(mask) != 7:
        return suffix
    return "_" + "".join(mask[(i - days) % 7] for i in range(7))


def has_weekday_mask(suffix: str | None) -> bool:
    """Return True if a plan suffix is an SF35 "_MTWTFSS" weekday mask."""
    return (
        isinstance(suffix, str)
        and len(suffix) == 8
        and suffix[0] == "_"
        and set(suffix[1:]) <= {"0", "1"}
    )


def utc_day_offset(utc_hour: int, utc_minute: int) -> int:
    """Return how many days the UTC date is ahead of the local date at this time.

    E.g. 01:22 UTC is 19:22 the previous day in US Central, so the offset is 1.
    """
    utc_dt = datetime.now(timezone.utc).replace(
        hour=utc_hour, minute=utc_minute, second=0, microsecond=0
    )
    return (utc_dt.date() - dt_util.as_local(utc_dt).date()).days


def mask_to_local_days(suffix: str, utc_hour: int, utc_minute: int) -> list[str]:
    """Convert a UTC weekday mask to local weekday names (mon..sun)."""
    local = shift_weekday_mask(suffix, -utc_day_offset(utc_hour, utc_minute))
    return [day for day, bit in zip(WEEKDAYS, local[1:]) if bit == "1"]


def local_days_to_mask(days: list[str], utc_hour: int, utc_minute: int) -> str:
    """Convert local weekday names (mon..sun) to a UTC "_MTWTFSS" mask."""
    local = "_" + "".join("1" if day in days else "0" for day in WEEKDAYS)
    return shift_weekday_mask(local, utc_day_offset(utc_hour, utc_minute))


DAY_SHORTCUTS = {
    "daily": WEEKDAYS,
    "everyday": WEEKDAYS,
    "weekdays": WEEKDAYS[:5],
    "weekends": WEEKDAYS[5:],
}


def parse_days(value: str) -> list[str]:
    """Parse "mon,wed,fri", "Monday Friday", "weekdays", ... into weekday names.

    Raises ValueError on unknown names or an empty list.
    """
    days: set[str] = set()
    for token in value.lower().replace(",", " ").split():
        if token in DAY_SHORTCUTS:
            days.update(DAY_SHORTCUTS[token])
            continue
        day = token[:3]
        full = WEEKDAY_NAMES.get(day)
        if full is None or not full.startswith(token):
            raise ValueError(f"Unknown day '{token}'")
        days.add(day)
    if not days:
        raise ValueError("At least one day is required")
    return [day for day in WEEKDAYS if day in days]


def format_days(days: list[str]) -> str:
    """Format weekday names for display, using shortcuts where they fit."""
    for name, shortcut in DAY_SHORTCUTS.items():
        if name != "everyday" and days == shortcut:
            return name
    return ",".join(days)


def get_max_schedule_portions(device_info: dict[str, Any]) -> int:
    """Return the max portions per scheduled meal (SF35 allows 24)."""
    if str(device_info.get("type", "")).upper().startswith("SF"):
        return SF_MAX_SCHEDULE_PORTIONS
    return MAX_PORTIONS


def get_schedule_slot_count(attributes: dict[str, Any] | None) -> int:
    """Return how many schedule slots a device exposes.

    S25/S30 report plan0-plan5; SF35 reports plan0-plan9. Slot IDs are a single
    digit in the plan string, so cap at 10.
    """
    count = SCHEDULE_SLOTS
    for key in attributes or {}:
        if key.startswith("plan") and key[4:].isdigit():
            count = max(count, int(key[4:]) + 1)
    return min(count, 10)

def read_child_lock_raw(device_data: dict[str, Any]) -> str | None:
    """Read child lock value from the attribute payload (GET /device/attribute)."""
    attrs = device_data.get("attributes")
    if not isinstance(attrs, dict):
        return None
    val = attrs.get(ATTR_CHILD)
    if val is None:
        return None
    return str(val).strip()


def is_child_lock_active(raw: str | None) -> bool:
    """Return True when API reports button lockout enabled."""
    if raw is None:
        return False
    return str(raw).strip() == "1"


