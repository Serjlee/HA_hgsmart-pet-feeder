"""Test the dates of the SF35 last-feed sensors, which only get a time of day."""

from datetime import datetime, timedelta, timezone

import pytest
from homeassistant.core import State
from pytest_homeassistant.common import mock_restore_cache_with_extra_data

from custom_components.hgsmart.helpers import build_plan_value

from .common import FEEDER, poll

DAY1 = datetime(2026, 11, 23, 13, tzinfo=timezone.utc)  # a Monday
DAY2 = DAY1 + timedelta(days=1)
SCHEDULED = "sensor.feeder_last_scheduled_feed"
MANUAL = "sensor.feeder_last_manual_feed"
EVERY_DAY = "_1111111"
MONDAY_ONLY = "_1000000"


@pytest.fixture
def cloud(cloud):
    cloud.devices[FEEDER]["type"] = "SF35"
    cloud.attributes[FEEDER] = {
        "plan0": build_plan_value(12, 15, 14, 0, True, EVERY_DAY),
        "autofoodframe": "0121614",  # slot 0, dispensed 12:16 UTC, 14 portions
        "userfoodframe": "01120002",  # 2 portions at 12:00 UTC
    }
    return cloud


def at(day, hour, minute):
    return day.replace(hour=hour, minute=minute).isoformat()


async def test_manual_feed_keeps_its_date_until_the_frame_changes(hass, cloud, freezer, setup_integration):
    freezer.move_to(DAY1)
    coordinator = await setup_integration()
    assert hass.states.get(MANUAL).state == at(DAY1, 12, 0)

    freezer.move_to(DAY2)
    await poll(hass, coordinator)
    assert hass.states.get(MANUAL).state == at(DAY1, 12, 0)

    cloud.attributes[FEEDER]["userfoodframe"] = "01125003"
    await poll(hass, coordinator)
    assert hass.states.get(MANUAL).state == at(DAY2, 12, 50)


@pytest.mark.parametrize(
    ("plan", "expected_day"),
    [
        (build_plan_value(12, 15, 14, 0, True, EVERY_DAY), DAY2),
        (build_plan_value(12, 15, 14, 0, False, EVERY_DAY), DAY1),
        (build_plan_value(12, 15, 14, 0, True, MONDAY_ONLY), DAY1),
        ("0", DAY1),
    ],
    ids=["daily", "disabled", "not-today", "deleted"],
)
async def test_unchanged_scheduled_frame_moves_only_if_the_schedule_ran(
    hass, cloud, freezer, setup_integration, plan, expected_day
):
    freezer.move_to(DAY1)
    coordinator = await setup_integration()
    assert hass.states.get(SCHEDULED).state == at(DAY1, 12, 16)

    cloud.attributes[FEEDER]["plan0"] = plan
    freezer.move_to(DAY2.replace(hour=12))  # before today's feed
    await poll(hass, coordinator)
    assert hass.states.get(SCHEDULED).state == at(DAY1, 12, 16)

    freezer.move_to(DAY2)
    await poll(hass, coordinator)
    assert hass.states.get(SCHEDULED).state == at(expected_day, 12, 16)


@pytest.mark.parametrize(
    ("restored", "expected"),
    [(DAY1.replace(hour=12), at(DAY1, 12, 0)), (DAY1.replace(hour=11), at(DAY2, 12, 0))],
    ids=["same-frame", "other-frame"],
)
async def test_restart_restores_the_date_of_an_unchanged_frame(
    hass, cloud, freezer, setup_integration, restored, expected
):
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(MANUAL, restored.isoformat()),
                {
                    "native_value": {
                        "__type": "<class 'datetime.datetime'>",
                        "isoformat": restored.isoformat(),
                    },
                    "native_unit_of_measurement": None,
                },
            )
        ],
    )
    freezer.move_to(DAY2)
    await setup_integration()
    assert hass.states.get(MANUAL).state == expected
