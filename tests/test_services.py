"""Test the feed and set_schedule_days services."""

from datetime import datetime, timezone

import pytest
import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from custom_components.hgsmart.const import DOMAIN
from custom_components.hgsmart.helpers import build_plan_value

from .common import ENTRY_ID, FEEDER


@pytest.fixture
async def clock(hass, freezer):
    await hass.config.async_set_time_zone("Europe/Rome")
    freezer.move_to(datetime(2026, 11, 23, 12, tzinfo=timezone.utc))


def device_id(hass, our_id=FEEDER):
    return dr.async_get(hass).async_get_device_by_identifier((DOMAIN, our_id), ENTRY_ID).id


def foreign_device(hass):
    entry = hass.config_entries.async_get_entry(ENTRY_ID)
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("other", "thing")}
    ).id


async def feed(hass, data):
    await hass.services.async_call(DOMAIN, "feed", data, blocking=True)


async def set_days(hass, data):
    await hass.services.async_call(DOMAIN, "set_schedule_days", data, blocking=True)


@pytest.mark.parametrize("as_target", [False, True])
async def test_feed(hass, cloud, setup_integration, as_target):
    await setup_integration()
    target = {"device_id": [device_id(hass)]}
    await feed(hass, {"target": target, "portions": 3} if as_target else {**target, "portions": 3})
    cloud.api.send_feed_command.assert_awaited_once_with(FEEDER, 3)


async def test_feed_defaults_to_one_portion_per_device(hass, cloud, setup_integration):
    cloud.add_device("second", "S25D")
    await setup_integration()
    await feed(hass, {"device_id": [device_id(hass), device_id(hass, "second")]})
    assert [c.args for c in cloud.api.send_feed_command.await_args_list] == [
        (FEEDER, 1),
        ("second", 1),
    ]


async def test_feed_skips_other_devices(hass, cloud, setup_integration):
    await setup_integration()
    await feed(hass, {"device_id": [foreign_device(hass), device_id(hass)]})
    cloud.api.send_feed_command.assert_awaited_once_with(FEEDER, 1)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({}, "No devices"),
        ({"device_id": "missing"}, "None of the selected devices"),
    ],
)
async def test_feed_without_feeders_fails(hass, cloud, setup_integration, data, message):
    await setup_integration()
    with pytest.raises(HomeAssistantError, match=message):
        await feed(hass, data)
    cloud.api.send_feed_command.assert_not_awaited()


async def test_feed_refused_by_the_cloud(hass, cloud, setup_integration):
    await setup_integration()
    cloud.api.send_feed_command.return_value = False
    with pytest.raises(HomeAssistantError, match="Failed to send feed command"):
        await feed(hass, {"device_id": device_id(hass)})


async def test_set_schedule_days(hass, cloud, clock, setup_integration):
    cloud.devices[FEEDER]["type"] = "SF35"
    cloud.attributes[FEEDER] = {"plan0": build_plan_value(23, 30, 4, 0, True, "_1111111")}
    await setup_integration()
    # 23:30 UTC is 00:30 the next day in Rome: Tue/Sat local are Mon/Fri UTC
    await set_days(hass, {"device_id": device_id(hass), "slot": 1, "days": ["TUE", "sat"]})
    assert cloud.attributes[FEEDER]["plan0"] == build_plan_value(23, 30, 4, 0, True, "_1000100")
    days = hass.states.get("switch.feeder_schedule_1_enabled").attributes["days"]
    assert days == ["tue", "sat"]


async def test_set_schedule_days_needs_a_weekly_schedule(hass, cloud, setup_integration):
    await setup_integration()
    with pytest.raises(HomeAssistantError, match="does not support weekly schedules"):
        await set_days(hass, {"device_id": device_id(hass), "slot": 1, "days": ["mon"]})
    cloud.api.set_schedule.assert_not_awaited()


@pytest.mark.parametrize(
    "data",
    [
        {"slot": 1, "days": ["funday"]},
        {"slot": 11, "days": ["mon"]},
        {"slot": 1, "days": []},
    ],
)
async def test_set_schedule_days_validates_input(hass, cloud, setup_integration, data):
    await setup_integration()
    with pytest.raises(vol.Invalid):
        await set_days(hass, {"device_id": device_id(hass), **data})


async def test_set_schedule_days_without_feeders_fails(hass, cloud, setup_integration):
    await setup_integration()
    with pytest.raises(HomeAssistantError, match="None of the selected devices"):
        await set_days(hass, {"device_id": foreign_device(hass), "slot": 1, "days": ["mon"]})
