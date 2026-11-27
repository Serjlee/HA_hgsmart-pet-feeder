"""Test coordinator polling, schedule parsing and optimistic schedule writes."""

from copy import deepcopy
from datetime import timedelta

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.exceptions import HomeAssistantError

from custom_components.hgsmart.api import HGSmartAuthError
from custom_components.hgsmart.const import DOMAIN
from custom_components.hgsmart.coordinator import PENDING_SCHEDULE_TTL
from custom_components.hgsmart.helpers import build_plan_value

from .common import FEEDER, add_entry, poll

DEFAULT_SLOT = {"enabled": False, "hour": 8, "minute": 0, "portions": 1}


def lagging_reads(cloud, count=1):
    """Make the cloud report the previous plans for ``count`` reads after a write."""
    stale = {}
    write = cloud.write

    async def lagging_write(device, *args, **kwargs):
        stale[device] = [deepcopy(cloud.attributes[device])] * count
        return await write(device, *args, **kwargs)

    async def lagging_read(device):
        if stale.get(device):
            return stale[device].pop()
        return await cloud.read(device)

    cloud.api.set_schedule.side_effect = lagging_write
    cloud.api.get_device_attributes.side_effect = lagging_read


async def test_empty_slots_get_default_schedules(hass, cloud, setup_integration):
    coordinator = await setup_integration()
    data = coordinator.data[FEEDER]
    assert data["schedule_slots"] == 6
    assert data["schedules"][0] == {
        "enabled": True, "hour": 18, "minute": 30, "portions": 2, "slot": 0, "suffix": ""
    }
    assert data["schedules"][5] == {**DEFAULT_SLOT, "slot": 5, "suffix": ""}


async def test_sf35_empty_slots_run_every_day(hass, cloud, setup_integration):
    cloud.devices[FEEDER]["type"] = "SF35"
    cloud.attributes[FEEDER] = {"plan0": build_plan_value(7, 0, 3, 0, True, "_0101000")}
    coordinator = await setup_integration()
    data = coordinator.data[FEEDER]
    assert data["schedule_slots"] == 10
    assert data["schedules"][0]["suffix"] == "_0101000"
    assert data["schedules"][9] == {**DEFAULT_SLOT, "slot": 9, "suffix": "_1111111"}


async def test_missing_attributes_leave_no_schedules(hass, cloud, setup_integration):
    cloud.api.get_device_attributes.side_effect = None
    cloud.api.get_device_attributes.return_value = None
    coordinator = await setup_integration()
    data = coordinator.data[FEEDER]
    assert data["schedules"] == {}
    assert data["attributes"] == {}
    assert data["schedule_attributes_available"] is False


async def test_devices_without_id_are_skipped_and_names_defaulted(
    hass, cloud, setup_integration
):
    cloud.devices["broken"] = {"name": "No id"}
    cloud.add_device("unnamed", "S25D")
    cloud.devices["unnamed"].update(name=None, type=None)
    coordinator = await setup_integration()
    assert set(coordinator.data) == {FEEDER, "unnamed"}
    info = coordinator.data["unnamed"]["device_info"]
    assert (info["name"], info["type"]) == ("HGSmart unnamed", "Unknown")


async def test_failing_untested_model_does_not_block_supported_ones(
    hass, cloud, setup_integration
):
    cloud.add_device("fountain", "SW28")
    stats = cloud.get_stats

    async def get_stats(device):
        if device == "fountain":
            raise RuntimeError("unexpected payload")
        return await stats(device)

    cloud.api.get_feeder_stats.side_effect = get_stats
    coordinator = await setup_integration()
    assert set(coordinator.data) == {FEEDER}


async def test_failing_supported_model_retries_setup(hass, cloud):
    cloud.api.get_feeder_stats.side_effect = RuntimeError("cloud down")
    entry = add_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_no_devices_retries_setup(hass, cloud):
    cloud.devices.clear()
    entry = add_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_auth_failure_during_poll_starts_reauth(hass, cloud, setup_integration):
    coordinator = await setup_integration()
    cloud.api.get_devices.side_effect = HGSmartAuthError("refresh token expired")
    await poll(hass, coordinator)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


@pytest.mark.parametrize("failure", [False, RuntimeError("timeout")])
async def test_failed_schedule_write_rolls_back(hass, cloud, setup_integration, failure):
    coordinator = await setup_integration()
    if isinstance(failure, Exception):
        cloud.api.set_schedule.side_effect = failure
    else:
        cloud.api.set_schedule.side_effect = None
        cloud.api.set_schedule.return_value = failure

    with pytest.raises((HomeAssistantError, RuntimeError)):
        await coordinator.async_update_schedule(FEEDER, 0, lambda _: {"portions": 5})
    assert coordinator.data[FEEDER]["schedules"][0]["portions"] == 2


async def test_write_to_unknown_slot_is_rejected(hass, cloud, setup_integration):
    coordinator = await setup_integration()
    with pytest.raises(HomeAssistantError, match="has no schedule 7"):
        await coordinator.async_update_schedule(FEEDER, 6, lambda _: {"portions": 5})
    with pytest.raises(HomeAssistantError, match="not loaded"):
        await coordinator.async_update_schedule("other", 0, lambda _: {"portions": 5})
    cloud.api.set_schedule.assert_not_awaited()


async def test_stale_read_after_write_does_not_revert_it(hass, cloud, setup_integration):
    coordinator = await setup_integration()
    lagging_reads(cloud)

    # Portions setter: write, then refresh right away (number.py)
    await coordinator.async_update_schedule(FEEDER, 0, lambda _: {"portions": 5})
    await poll(hass, coordinator)
    assert coordinator.data[FEEDER]["schedules"][0]["portions"] == 5
    assert coordinator.data[FEEDER]["attributes"]["plan0"] == build_plan_value(18, 30, 5, 0)

    # The next write is built from the written schedule, not the stale read
    await coordinator.async_update_schedule(FEEDER, 0, lambda _: {"enabled": False})
    assert cloud.attributes[FEEDER]["plan0"] == build_plan_value(18, 30, 5, 0, False)


async def test_pending_write_gives_way_to_the_cloud_after_its_ttl(
    hass, cloud, setup_integration, freezer, caplog
):
    coordinator = await setup_integration()
    lagging_reads(cloud, count=2)
    await coordinator.async_update_schedule(FEEDER, 0, lambda _: {"portions": 5})

    freezer.tick(timedelta(seconds=PENDING_SCHEDULE_TTL + 1))
    await poll(hass, coordinator)
    assert coordinator.data[FEEDER]["schedules"][0]["portions"] == 2
    assert "still reads" in caplog.text
