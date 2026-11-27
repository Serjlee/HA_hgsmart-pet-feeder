"""Test plan-string and attribute parsing helpers."""

from datetime import datetime, timezone

import pytest

from custom_components.hgsmart.helpers import (
    build_plan_value,
    get_max_schedule_portions,
    get_schedule_slot_count,
    has_weekday_mask,
    is_child_lock_active,
    is_plan_attribute,
    is_sf_model,
    is_supported_model,
    local_days_to_mask,
    mask_to_local_days,
    parse_auto_feed_frame,
    parse_batinfo,
    parse_eattime,
    parse_manual_feed_frame,
    parse_plan_value,
    read_child_lock_raw,
    shift_weekday_mask,
    utc_day_offset,
)

NOW = datetime(2026, 11, 23, 13, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10940033", {"enabled": True, "hour": 9, "minute": 40, "portions": 3, "slot": 3, "suffix": ""}),
        ("00800011", {"enabled": False, "hour": 8, "minute": 0, "portions": 1, "slot": 1, "suffix": ""}),
        (
            "11100121_1111111",
            {"enabled": True, "hour": 11, "minute": 0, "portions": 12, "slot": 1, "suffix": "_1111111"},
        ),
    ],
)
def test_parse_plan_value(raw, expected):
    assert parse_plan_value(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "0",
        "1094003",  # too short
        "30940033",  # deleted on S25/S30
        "20940033_1111111",  # deleted on SF35
        "10940003",  # zero portions
        "12540033",  # hour 25
        "10960033",  # minute 96
        "1x940033",
    ],
)
def test_parse_plan_value_rejects(raw):
    assert parse_plan_value(raw) is None


@pytest.mark.parametrize("suffix", ["", "_0101000"])
def test_build_plan_value_round_trips(suffix):
    raw = build_plan_value(7, 5, 24, 9, False, suffix)
    assert raw == "00705249" + suffix
    assert parse_plan_value(raw) == {
        "enabled": False, "hour": 7, "minute": 5, "portions": 24, "slot": 9, "suffix": suffix
    }


@pytest.mark.parametrize(
    ("suffix", "days", "expected"),
    [
        ("_1000000", 1, "_0100000"),  # Monday -> Tuesday
        ("_1000000", -1, "_0000001"),  # Monday -> Sunday
        ("_0000001", 1, "_1000000"),  # Sunday wraps to Monday
        ("_1100000", 7, "_1100000"),
        ("", 1, ""),
        ("_abc", 1, "_abc"),  # not a mask: left alone
    ],
)
def test_shift_weekday_mask(suffix, days, expected):
    assert shift_weekday_mask(suffix, days) == expected


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [("_1010101", True), ("_101010", False), ("1010101_", False), ("_10101a1", False), (None, False)],
)
def test_has_weekday_mask(suffix, expected):
    assert has_weekday_mask(suffix) is expected


@pytest.mark.parametrize(
    ("hour", "minute", "offset", "expected"),
    [
        (12, 0, 60, 0),
        (23, 30, 60, -1),  # 00:30 local is the next day, so UTC is a day behind
        (0, 30, -300, 1),  # 19:30 local is the previous day
        (22, 30, 90, -1),
    ],
)
def test_utc_day_offset_with_explicit_offset(hour, minute, offset, expected):
    assert utc_day_offset(hour, minute, offset=offset) == expected


async def test_weekday_masks_follow_local_days(hass, freezer):
    await hass.config.async_set_time_zone("America/New_York")
    freezer.move_to(NOW)
    # 20:30 New York on Mon/Wed is 01:30 UTC on Tue/Thu
    assert local_days_to_mask(["mon", "wed"], 1, 30) == "_0101000"
    assert mask_to_local_days("_0101000", 1, 30) == ["mon", "wed"]
    # Without crossing midnight the days are the same
    assert local_days_to_mask(["sun"], 15, 0) == "_0000001"
    assert mask_to_local_days("_0000001", 15, 0) == ["sun"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("20100", {"present": True, "charging": False, "level": 100}),
        ("21085", {"present": True, "charging": True, "level": 85}),
        ("00255", {"present": False, "charging": False, "level": None}),
        ("20255", {"present": False, "charging": False, "level": None}),
    ],
)
def test_parse_batinfo(raw, expected):
    assert parse_batinfo({"batinfo": raw}) == expected


@pytest.mark.parametrize("attributes", [None, {}, {"batinfo": "2010"}, {"batinfo": "2x100"}])
def test_parse_batinfo_rejects(attributes):
    assert parse_batinfo(attributes) is None


def test_parse_auto_feed_frame(freezer):
    freezer.move_to(NOW)
    assert parse_auto_feed_frame("1121614") == {
        "time": datetime(2026, 11, 23, 12, 16, tzinfo=timezone.utc),
        "slot": 1,
        "portions": 14,
    }
    # A time later than now happened yesterday
    assert parse_auto_feed_frame("0140002")["time"] == datetime(
        2026, 11, 22, 14, 0, tzinfo=timezone.utc
    )


def test_parse_manual_feed_frame(freezer):
    freezer.move_to(NOW)
    assert parse_manual_feed_frame("01034602") == {
        "time": datetime(2026, 11, 23, 3, 46, tzinfo=timezone.utc),
        "portions": 2,
        "source": "01",
    }


@pytest.mark.parametrize("raw", [None, "0", "112161", "11216145", "1256014", "abcdefg"])
def test_parse_auto_feed_frame_rejects(raw):
    assert parse_auto_feed_frame(raw) is None


@pytest.mark.parametrize("raw", [None, "0", "0103460", "01250002", "0103460x"])
def test_parse_manual_feed_frame_rejects(raw):
    assert parse_manual_feed_frame(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("206_134167160033221", {"seconds": 206, "tag": "134167160033221"}),
        ("15_", {"seconds": 15, "tag": None}),
        ("206", None),
        ("abc_123", None),
        (None, None),
    ],
)
def test_parse_eattime(raw, expected):
    assert parse_eattime(raw) == expected


@pytest.mark.parametrize(
    ("type_", "attributes", "expected"),
    [
        ("S25D", None, 6),
        ("S30", {"plan0": "0", "plan5": "0"}, 6),
        ("S30", {"plan7": "0"}, 8),  # an unexpected extra slot is still exposed
        ("SF35", None, 10),  # SF gets every slot even before attributes load
        ("S30", {"plan12": "0"}, 10),
    ],
)
def test_get_schedule_slot_count(type_, attributes, expected):
    assert get_schedule_slot_count({"type": type_}, attributes) == expected


@pytest.mark.parametrize(
    ("type_", "supported", "sf", "max_portions"),
    [
        ("S25D", True, False, 9),
        ("S30", True, False, 9),
        ("SF35", False, True, 24),
        ("sf35", False, True, 24),
        ("SW28", False, False, 9),
    ],
)
def test_model_capabilities(type_, supported, sf, max_portions):
    info = {"type": type_}
    assert is_supported_model(info) is supported
    assert is_sf_model(info) is sf
    assert get_max_schedule_portions(info) == max_portions


@pytest.mark.parametrize(
    ("key", "expected"), [("plan0", True), ("plan9", True), ("plan", False), ("planx", False)]
)
def test_is_plan_attribute(key, expected):
    assert is_plan_attribute(key) is expected


@pytest.mark.parametrize(
    ("device_data", "raw", "active"),
    [
        ({"attributes": {"child": "1"}}, "1", True),
        ({"attributes": {"child": 0}}, "0", False),
        ({"attributes": {}}, None, False),
        ({"attributes": None}, None, False),
    ],
)
def test_child_lock(device_data, raw, active):
    assert read_child_lock_raw(device_data) == raw
    assert is_child_lock_active(raw) is active
