"""Test schedule compensation with Home Assistant and a simulated HGSmart API."""

import asyncio
from copy import deepcopy
from datetime import datetime, time, timedelta, timezone

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.storage import WriteError
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.hgsmart.const import CONF_COMPENSATE_TIMEZONE_OFFSET, DOMAIN
from custom_components.hgsmart.coordinator import HGSmartDataUpdateCoordinator
from custom_components.hgsmart.helpers import build_plan_value, parse_plan_value
from custom_components.hgsmart.schedule_offsets import (
    HGSmartScheduleOffsets,
    schedule_offset_store,
)
from custom_components.hgsmart.time import HGSmartScheduleTime

SUMMER = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
WINTER = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
FEEDER = "feeder"
ENTRY_ID = "offset-entry"
MONDAY = "_1000000"
TUESDAY = "_0100000"
SUNDAY = "_0000001"


@pytest.fixture
async def clock(hass, freezer):
    await hass.config.async_set_time_zone("Europe/Paris")
    freezer.move_to(SUMMER)
    return freezer


@pytest.fixture
def coordinator(hass, cloud, clock):
    coordinator = HGSmartDataUpdateCoordinator(hass, cloud.api, 15)
    coordinator.data = {}
    return coordinator


@pytest.fixture
def storage(hass):
    return schedule_offset_store(hass, ENTRY_ID)


async def load_manager(hass, coordinator):
    manager = HGSmartScheduleOffsets(hass, ENTRY_ID, coordinator)
    await manager.async_load()
    coordinator.schedule_offsets = manager
    return manager


async def start(hass, coordinator, cloud):
    await coordinator.async_refresh()
    cloud.api.get_device_attributes.reset_mock()
    manager = await load_manager(hass, coordinator)
    await manager.async_check()
    return manager


def add_entry(hass, *, enabled=True):
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id=ENTRY_ID,
        version=2,
        data={"username": "test@example.com", "update_interval": 42},
        options={CONF_COMPENSATE_TIMEZONE_OFFSET: True} if enabled else {},
    )
    entry.add_to_hass(hass)
    return entry


async def test_baseline_and_unchanged_offset_do_not_read_cloud(
    hass, coordinator, cloud, storage
):
    manager = await start(hass, coordinator, cloud)
    await manager.async_check()
    assert await storage.async_load() == {FEEDER: {"0": {"offset": 120}}}
    cloud.api.get_device_attributes.assert_not_awaited()
    cloud.api.set_schedule.assert_not_awaited()


@pytest.mark.parametrize(
    ("before", "after", "hour", "minute", "suffix", "expected"),
    [
        # Tuesday 00:30 local remains Tuesday; both UTC times fall on Monday.
        (SUMMER, WINTER, 22, 30, MONDAY, (23, 30, MONDAY)),
        (SUMMER, WINTER, 23, 45, MONDAY, (0, 45, TUESDAY)),
        (SUMMER, WINTER, 23, 45, SUNDAY, (0, 45, MONDAY)),
        (WINTER, SUMMER, 0, 15, MONDAY, (23, 15, SUNDAY)),
        (SUMMER, WINTER, 23, 45, "", (0, 45, "")),
    ],
)
async def test_compensates_weekdays_only_if_utc_midnight_is_crossed(
    hass, coordinator, cloud, clock, before, after, hour, minute, suffix, expected
):
    clock.move_to(before)
    cloud.attributes[FEEDER]["plan0"] = build_plan_value(hour, minute, 2, 0, True, suffix)
    manager = await start(hass, coordinator, cloud)
    clock.move_to(after)
    await manager.async_check()
    hour, minute, suffix = expected
    cloud.api.set_schedule.assert_awaited_once_with(
        FEEDER, 0, hour, minute, 2, True, suffix=suffix
    )


async def test_half_hour_change(hass, coordinator, cloud, clock):
    await hass.config.async_set_time_zone("Australia/Lord_Howe")
    clock.move_to(datetime(2026, 10, 3, 12, tzinfo=timezone.utc))
    manager = await start(hass, coordinator, cloud)
    clock.move_to(datetime(2026, 10, 4, 12, tzinfo=timezone.utc))
    await manager.async_check()
    cloud.api.set_schedule.assert_awaited_once_with(FEEDER, 0, 18, 0, 2, True, suffix="")


async def test_sf35_batches_ten_slots_in_two_reads(hass, coordinator, cloud, clock, storage):
    cloud.devices[FEEDER]["type"] = "SF35"
    cloud.attributes[FEEDER] = {
        f"plan{slot}": build_plan_value(18, 30, slot + 1, slot, slot % 2 == 0, MONDAY)
        for slot in range(10)
    }
    manager = await start(hass, coordinator, cloud)
    clock.move_to(WINTER)
    await manager.async_check()

    assert cloud.api.get_device_attributes.await_count == 2
    assert cloud.api.set_schedule.await_count == 10
    for slot in range(10):
        cloud.api.set_schedule.assert_any_await(
            FEEDER, slot, 19, 30, slot + 1, slot % 2 == 0, suffix=MONDAY
        )
        assert coordinator.data[FEEDER]["schedules"][slot]["hour"] == 19
    assert await storage.async_load() == {
        FEEDER: {str(slot): {"offset": 60} for slot in range(10)}
    }
    await manager.async_check()
    assert cloud.api.get_device_attributes.await_count == 2


async def test_retries_only_unconfirmed_slots(hass, coordinator, cloud, clock, storage):
    cloud.attributes[FEEDER]["plan1"] = build_plan_value(10, 15, 3, 1, False)
    manager = await start(hass, coordinator, cloud)

    async def fail_second(device, slot, *args, **kwargs):
        if slot == 1:
            return False
        return await cloud.write(device, slot, *args, **kwargs)

    cloud.api.set_schedule.side_effect = fail_second
    clock.move_to(WINTER)
    await manager.async_check()
    saved = (await storage.async_load())[FEEDER]
    assert saved["0"] == {"offset": 60}
    assert saved["1"]["pending"]["target"] == 11 * 60 + 15

    cloud.api.set_schedule.reset_mock()
    cloud.api.set_schedule.side_effect = cloud.write
    clock.tick(timedelta(minutes=10))
    await manager.async_check()
    cloud.api.set_schedule.assert_awaited_once_with(FEEDER, 1, 11, 15, 3, False, suffix="")


async def test_lost_reply_after_acceptance_does_not_shift_twice(
    hass, coordinator, cloud, clock, storage
):
    await start(hass, coordinator, cloud)

    async def lose_reply(*args, **kwargs):
        pending = (await storage.async_load())[FEEDER]["0"]["pending"]
        assert pending["target"] == 19 * 60 + 30
        await cloud.write(*args, **kwargs)
        raise TimeoutError("Reply lost")

    cloud.api.set_schedule.side_effect = lose_reply
    clock.move_to(WINTER)
    manager = await load_manager(hass, coordinator)
    await manager.async_check()
    manager = await load_manager(hass, coordinator)
    await manager.async_check()
    cloud.api.set_schedule.assert_awaited_once()
    assert await storage.async_load() == {FEEDER: {"0": {"offset": 60}}}


async def test_retries_when_time_is_applied_before_mask(hass, coordinator, cloud, clock, storage):
    cloud.attributes[FEEDER]["plan0"] = build_plan_value(23, 45, 2, 0, True, MONDAY)
    manager = await start(hass, coordinator, cloud)

    async def write_time_only(device, slot, hour, minute, portions, enabled, suffix):
        return await cloud.write(device, slot, hour, minute, portions, enabled, MONDAY)

    cloud.api.set_schedule.side_effect = write_time_only
    clock.move_to(WINTER)
    await manager.async_check()
    assert (await storage.async_load())[FEEDER]["0"]["pending"]["target_suffix"] == TUESDAY

    cloud.api.set_schedule.side_effect = cloud.write
    clock.tick(timedelta(minutes=10))
    await manager.async_check()
    assert cloud.api.set_schedule.await_count == 2
    assert await storage.async_load() == {FEEDER: {"0": {"offset": 60}}}


@pytest.mark.parametrize("replacement", ["0", build_plan_value(21, 45, 4, 0)])
async def test_preserves_deleted_slots_and_external_edits(
    hass, coordinator, cloud, clock, storage, replacement
):
    manager = await start(hass, coordinator, cloud)
    cloud.api.set_schedule.side_effect = None
    cloud.api.set_schedule.return_value = False
    clock.move_to(WINTER)
    await manager.async_check()
    cloud.attributes[FEEDER]["plan0"] = replacement
    cloud.api.set_schedule.reset_mock()
    clock.tick(timedelta(minutes=10))
    await manager.async_check()
    cloud.api.set_schedule.assert_not_awaited()
    expected = {} if replacement == "0" else {"0": {"offset": 60}}
    assert (await storage.async_load())[FEEDER] == expected


async def test_new_slot_uses_current_offset_without_retroactive_shift(
    hass, coordinator, cloud, clock, storage
):
    manager = await start(hass, coordinator, cloud)
    cloud.attributes[FEEDER]["plan1"] = build_plan_value(8, 0, 3, 1)
    clock.move_to(WINTER)
    await manager.async_check()
    cloud.api.set_schedule.assert_awaited_once_with(FEEDER, 0, 19, 30, 2, True, suffix="")
    assert (await storage.async_load())[FEEDER]["1"] == {"offset": 60}


async def test_retry_backoff_is_per_device_and_caps_at_one_hour(
    hass, coordinator, cloud, clock, storage
):
    other = "other"
    cloud.devices[other] = {"deviceId": other, "name": "Other", "type": "S30D"}
    cloud.attributes[other] = deepcopy(cloud.attributes[FEEDER])
    manager = await start(hass, coordinator, cloud)

    async def read_available(device):
        if device == other:
            return await cloud.read(device)
        return None

    cloud.api.get_device_attributes.side_effect = read_available
    clock.move_to(WINTER)
    await manager.async_check()
    cloud.api.set_schedule.assert_awaited_once_with(other, 0, 19, 30, 2, True, suffix="")
    assert (await storage.async_load())[FEEDER]["0"] == {"offset": 120}
    for minutes in (10, 20, 40, 60, 60):
        cloud.api.get_device_attributes.reset_mock()
        clock.tick(timedelta(minutes=minutes - 5))
        await manager.async_check()
        cloud.api.get_device_attributes.assert_not_awaited()
        clock.tick(timedelta(minutes=5))
        await manager.async_check()
        cloud.api.get_device_attributes.assert_awaited_once_with(FEEDER)

    cloud.api.get_device_attributes.side_effect = cloud.read
    clock.tick(timedelta(hours=1))
    await manager.async_check()
    assert (await storage.async_load())[FEEDER]["0"] == {"offset": 60}


async def test_offset_return_cancels_an_unapplied_correction(
    hass, coordinator, cloud, clock, storage
):
    manager = await start(hass, coordinator, cloud)
    cloud.api.set_schedule.side_effect = None
    cloud.api.set_schedule.return_value = False
    clock.move_to(WINTER)
    await manager.async_check()
    clock.move_to(datetime(2027, 3, 28, 12, tzinfo=timezone.utc))
    cloud.api.set_schedule.reset_mock()
    await manager.async_check()
    cloud.api.set_schedule.assert_not_awaited()
    assert await storage.async_load() == {FEEDER: {"0": {"offset": 120}}}


async def test_simultaneous_checks_do_not_duplicate_correction(hass, coordinator, cloud, clock):
    manager = await start(hass, coordinator, cloud)
    clock.move_to(WINTER)
    await asyncio.gather(manager.async_check(), manager.async_check())
    cloud.api.set_schedule.assert_awaited_once()


async def test_queued_manual_update_uses_confirmed_schedule(
    hass, coordinator, cloud, clock
):
    manager = await start(hass, coordinator, cloud)
    clock.move_to(WINTER)
    reading = asyncio.Event()
    resume = asyncio.Event()

    async def read(device):
        reading.set()
        await resume.wait()
        return await cloud.read(device)

    cloud.api.get_device_attributes.side_effect = read
    check = asyncio.create_task(manager.async_check())
    try:
        await asyncio.wait_for(reading.wait(), 5)
        manual = asyncio.create_task(
            coordinator.async_update_schedule(FEEDER, 0, lambda _: {"portions": 3})
        )
        await asyncio.sleep(0)
        cloud.api.set_schedule.assert_not_awaited()
    finally:
        resume.set()
    await asyncio.gather(check, manual)

    assert cloud.api.set_schedule.await_count == 2
    cloud.api.set_schedule.assert_awaited_with(FEEDER, 0, 19, 30, 3, True, suffix="")


async def test_older_poll_cannot_restore_pre_correction_schedule(
    hass, coordinator, cloud, clock, storage
):
    manager = await start(hass, coordinator, cloud)
    clock.move_to(WINTER)
    reading_events = asyncio.Event()
    finish_poll = asyncio.Event()

    async def block_events(device):
        reading_events.set()
        await finish_poll.wait()
        return {"events": [{"id": "latest-event"}]}

    cloud.attributes[FEEDER]["child"] = "1"
    cloud.api.get_device_today_events.side_effect = block_events
    poll = asyncio.create_task(coordinator.async_refresh())
    try:
        await asyncio.wait_for(reading_events.wait(), 5)
        await manager.async_check()
    finally:
        finish_poll.set()
        await poll

    data = coordinator.data[FEEDER]
    assert data["today_events"] == {"events": [{"id": "latest-event"}]}
    assert data["attributes"]["child"] == "1"
    assert data["schedules"][0]["hour"] == 19
    await coordinator.async_update_schedule(FEEDER, 0, lambda _: {"portions": 3})
    await manager.async_check()
    assert (await storage.async_load())[FEEDER]["0"] == {"offset": 60}
    cloud.api.set_schedule.assert_awaited_with(FEEDER, 0, 19, 30, 3, True, suffix="")


@pytest.mark.parametrize("removed_plan", [{"plan0": "0"}, {"plan1": "0"}])
async def test_poll_observes_deleted_slot_before_a_recreation(
    hass, coordinator, cloud, clock, storage, removed_plan
):
    manager = await start(hass, coordinator, cloud)
    cloud.attributes[FEEDER] = removed_plan
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert (await storage.async_load())[FEEDER] == {}

    manager = await load_manager(hass, coordinator)
    clock.move_to(WINTER)
    cloud.attributes[FEEDER] = {"plan0": build_plan_value(17, 45, 3, 0)}
    await coordinator.async_refresh()
    cloud.api.get_device_attributes.reset_mock()
    await manager.async_check()

    cloud.api.get_device_attributes.assert_not_awaited()
    cloud.api.set_schedule.assert_not_awaited()
    assert (await storage.async_load())[FEEDER]["0"] == {"offset": 60}


async def test_reply_without_plans_keeps_reference_and_correction(
    hass, coordinator, cloud, clock, storage
):
    manager = await start(hass, coordinator, cloud)
    cloud.api.get_device_attributes.side_effect = None
    cloud.api.get_device_attributes.return_value = {}
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    clock.move_to(WINTER)
    await manager.async_check()

    cloud.api.set_schedule.assert_not_awaited()
    assert await storage.async_load() == {FEEDER: {"0": {"offset": 120}}}

    cloud.api.get_device_attributes.side_effect = cloud.read
    clock.tick(timedelta(minutes=10))
    await manager.async_check()
    cloud.api.set_schedule.assert_awaited_once_with(FEEDER, 0, 19, 30, 2, True, suffix="")
    assert await storage.async_load() == {FEEDER: {"0": {"offset": 60}}}


async def test_confirmation_without_plans_keeps_cached_schedules(
    hass, coordinator, cloud, clock, storage
):
    manager = await start(hass, coordinator, cloud)
    replies = iter([None, {}])

    async def read(device):
        reply = next(replies, None)
        return await cloud.read(device) if reply is None else reply

    cloud.api.get_device_attributes.side_effect = read
    clock.move_to(WINTER)
    await manager.async_check()

    cloud.api.set_schedule.assert_awaited_once()
    assert coordinator.data[FEEDER]["schedules"][0]["hour"] == 18
    assert (await storage.async_load())[FEEDER]["0"]["pending"]["target"] == 19 * 60 + 30


async def test_unparsable_slot_is_retired_without_rereading_cloud(
    hass, coordinator, cloud, clock, storage
):
    manager = await start(hass, coordinator, cloud)
    cloud.attributes[FEEDER]["plan0"] = "11830000"  # zero portions
    clock.move_to(WINTER)
    await manager.async_check()
    cloud.api.set_schedule.assert_not_awaited()
    assert (await storage.async_load())[FEEDER] == {}

    cloud.api.get_device_attributes.reset_mock()
    clock.tick(timedelta(minutes=5))
    await manager.async_check()
    cloud.api.get_device_attributes.assert_not_awaited()


async def test_unavailable_poll_does_not_retire_schedule_reference(
    hass, coordinator, cloud, clock
):
    manager = await start(hass, coordinator, cloud)
    cloud.api.get_device_attributes.side_effect = None
    cloud.api.get_device_attributes.return_value = None
    await coordinator.async_refresh()
    await manager.async_check()

    cloud.api.get_device_attributes.side_effect = cloud.read
    clock.move_to(WINTER)
    await manager.async_check()
    cloud.api.set_schedule.assert_awaited_once_with(FEEDER, 0, 19, 30, 2, True, suffix="")


@pytest.mark.parametrize(
    ("hour", "minute", "expected_hour", "expected_minute", "expected_mask"),
    [(18, 30, 19, 30, TUESDAY), (22, 30, 23, 30, MONDAY)],
)
async def test_weekday_edit_during_backoff_keeps_time_correction_and_local_days(
    hass, coordinator, cloud, clock, storage,
    hour, minute, expected_hour, expected_minute, expected_mask,
):
    cloud.devices[FEEDER]["type"] = "SF35"
    cloud.attributes[FEEDER]["plan0"] = build_plan_value(hour, minute, 2, 0, True, SUNDAY)
    manager = await start(hass, coordinator, cloud)
    clock.move_to(WINTER)
    cloud.api.set_schedule.side_effect = None
    cloud.api.set_schedule.return_value = False
    await manager.async_check()

    cloud.api.set_schedule.side_effect = cloud.write
    await coordinator.async_set_schedule_days(FEEDER, 0, ["tue"])
    clock.tick(timedelta(minutes=10))
    await manager.async_check()

    assert (await storage.async_load())[FEEDER]["0"] == {"offset": 60}
    cloud.api.set_schedule.assert_awaited_with(
        FEEDER, 0, expected_hour, expected_minute, 2, True, suffix=expected_mask
    )


async def test_failed_journal_write_does_not_send_correction(
    hass, coordinator, cloud, clock, storage, monkeypatch
):
    manager = await start(hass, coordinator, cloud)
    clock.move_to(WINTER)

    async def fail_write(data):
        raise WriteError("simulated disk write failure")

    # Exercise the actual Store behavior: it logs the failure and returns.
    with monkeypatch.context() as patch:
        patch.setattr(manager._store, "_async_write_data", fail_write)
        await manager.async_check()
    cloud.api.set_schedule.assert_not_awaited()
    assert (await storage.async_load())[FEEDER]["0"] == {"offset": 120}

    manager = await load_manager(hass, coordinator)
    await manager.async_check()
    cloud.api.set_schedule.assert_awaited_once_with(FEEDER, 0, 19, 30, 2, True, suffix="")


async def test_weekday_edit_wins_when_utc_time_has_already_moved(
    hass, coordinator, cloud, clock, storage
):
    cloud.devices[FEEDER]["type"] = "SF35"
    cloud.attributes[FEEDER]["plan0"] = build_plan_value(23, 45, 2, 0, True, MONDAY)
    manager = await start(hass, coordinator, cloud)
    clock.move_to(WINTER)

    async def write_time_only(device, slot, hour, minute, portions, enabled, suffix):
        return await cloud.write(device, slot, hour, minute, portions, enabled, MONDAY)

    cloud.api.set_schedule.side_effect = write_time_only
    await manager.async_check()
    cloud.api.set_schedule.side_effect = cloud.write
    await coordinator.async_set_schedule_days(FEEDER, 0, ["mon"])
    clock.tick(timedelta(minutes=10))
    await manager.async_check()

    assert cloud.api.set_schedule.await_count == 2
    cloud.api.set_schedule.assert_awaited_with(FEEDER, 0, 0, 45, 2, True, suffix=MONDAY)
    assert (await storage.async_load())[FEEDER]["0"] == {"offset": 60}


async def test_manual_time_records_the_offset_used_before_cloud_write(
    hass, coordinator, cloud, clock, storage
):
    manager = await start(hass, coordinator, cloud)
    entity = HGSmartScheduleTime(hass, coordinator, cloud.api, FEEDER, cloud.devices[FEEDER], 0)

    async def write_across_transition(*args, **kwargs):
        result = await cloud.write(*args, **kwargs)
        clock.move_to(WINTER)
        return result

    cloud.api.set_schedule.side_effect = write_across_transition
    await entity.async_set_value(time(20, 30))
    assert (await storage.async_load())[FEEDER]["0"] == {"offset": 120}

    cloud.api.set_schedule.side_effect = cloud.write
    await manager.async_check()
    schedule = parse_plan_value(cloud.attributes[FEEDER]["plan0"])
    assert (schedule["hour"], schedule["minute"]) == (19, 30)


async def test_manual_time_before_compensation_preserves_local_weekday(
    hass, coordinator, cloud, clock, storage
):
    cloud.devices[FEEDER]["type"] = "SF35"
    cloud.attributes[FEEDER]["plan0"] = build_plan_value(22, 30, 2, 0, True, MONDAY)
    manager = await start(hass, coordinator, cloud)
    entity = HGSmartScheduleTime(hass, coordinator, cloud.api, FEEDER, cloud.devices[FEEDER], 0)
    clock.move_to(WINTER)

    await entity.async_set_value(time(0, 30))
    await manager.async_check()

    cloud.api.set_schedule.assert_awaited_once_with(
        FEEDER, 0, 23, 30, 2, True, suffix=MONDAY
    )
    assert (await storage.async_load())[FEEDER]["0"] == {"offset": 60}


async def test_setup_finishes_while_correction_waits_for_cloud(
    hass, cloud, clock, storage
):
    entry = add_entry(hass)
    await storage.async_save({FEEDER: {"0": {"offset": 60}}})
    reading = asyncio.Event()
    resume = asyncio.Event()
    read_count = 0

    async def read(device):
        nonlocal read_count
        read_count += 1
        if read_count == 2:
            reading.set()
            await resume.wait()
        return await cloud.read(device)

    cloud.api.get_device_attributes.side_effect = read
    try:
        assert await asyncio.wait_for(hass.config_entries.async_setup(entry.entry_id), 5)
        await asyncio.wait_for(reading.wait(), 5)
        assert entry.state is ConfigEntryState.LOADED
        cloud.api.set_schedule.assert_not_awaited()

        unload = asyncio.create_task(hass.config_entries.async_unload(entry.entry_id))
        await hass.async_block_till_done()
        assert not unload.done()
        resume.set()
        assert await asyncio.wait_for(unload, 5)
        cloud.api.set_schedule.assert_not_awaited()
        cloud.api.close.assert_awaited_once()
    finally:
        resume.set()


async def test_manual_time_via_ha_service_uses_current_offset(hass, cloud, clock, storage):
    entry = add_entry(hass)
    await storage.async_save({FEEDER: {"0": {"offset": 120}}})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    clock.move_to(WINTER)
    await hass.services.async_call(
        "time",
        "set_value",
        {"entity_id": "time.feeder_schedule_1_time", "time": "20:30:00"},
        blocking=True,
    )
    cloud.api.set_schedule.assert_awaited_once_with(FEEDER, 0, 19, 30, 2, True, suffix="")
    assert (await storage.async_load())[FEEDER]["0"] == {"offset": 60}
    await hass.data[DOMAIN][entry.entry_id]["coordinator"].schedule_offsets.async_check()
    assert cloud.api.set_schedule.await_count == 1


async def test_disabled_option_clears_reference_and_starts_no_timer(hass, cloud, clock, storage):
    entry = add_entry(hass, enabled=False)
    await storage.async_save({FEEDER: {"0": {"offset": 60}}})
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert await storage.async_load() is None
    clock.tick(timedelta(minutes=5))
    async_fire_time_changed(hass, clock() + timedelta(minutes=5))
    await hass.async_block_till_done(wait_background_tasks=True)
    cloud.api.set_schedule.assert_not_awaited()
    cloud.api.get_device_attributes.assert_awaited_once()


async def test_enabled_entry_checks_offset_on_the_timer(hass, cloud, clock):
    entry = add_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    cloud.api.get_device_attributes.assert_awaited_once()

    await hass.config.async_set_time_zone("Europe/London")
    clock.tick(timedelta(minutes=5))
    async_fire_time_changed(hass, clock(), fire_all=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    cloud.api.set_schedule.assert_awaited_once_with(FEEDER, 0, 19, 30, 2, True, suffix="")


async def test_options_flow_defaults_and_round_trip(hass):
    entry = add_entry(hass, enabled=False)
    form = await hass.config_entries.options.async_init(entry.entry_id)
    assert form["type"] is FlowResultType.FORM
    schema = form["data_schema"]
    assert schema({}) == {"update_interval": 42, CONF_COMPENSATE_TIMEZONE_OFFSET: False}
    with pytest.raises(vol.Invalid):
        schema({"update_interval": 0})
    values = {"update_interval": 30, CONF_COMPENSATE_TIMEZONE_OFFSET: True}
    result = await hass.config_entries.options.async_configure(form["flow_id"], values)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert dict(entry.options) == values
