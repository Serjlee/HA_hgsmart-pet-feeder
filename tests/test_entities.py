"""Test entity states and actions against the simulated cloud."""

from datetime import datetime, timezone

import pytest
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.hgsmart.const import MEAL_CALL_OPTION_CUSTOM, MEAL_CALL_OPTION_DEFAULT
from custom_components.hgsmart.helpers import build_plan_value

from .common import ENTRY_ID, FEEDER, poll

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)  # Monday, CET (+1)


@pytest.fixture
async def clock(hass, freezer):
    await hass.config.async_set_time_zone("Europe/Rome")
    freezer.move_to(NOW)
    return freezer


@pytest.fixture
def cloud(cloud):
    """An S30D with two bowls, a backup battery and a custom meal call."""
    cloud.attributes[FEEDER].update(electric="80", batstate="1", child="1", choosevoice="1")
    cloud.stats[FEEDER] = {
        "remaining": 40,
        "desiccantExpire": 12,
        "eating": [
            {"type": "1", "time": 3, "duration": 120},
            {"type": "2", "time": 1, "duration": 30},
        ],
    }
    cloud.events[FEEDER] = [
        {"createTime": "2026-11-23 12:00", "eventDesc": "Meal", "event": "2_1"},
        {"createTime": "2026-11-23 08:00", "eventDesc": "Feeding", "event": "1_1"},
    ]
    return cloud


@pytest.fixture
def sf35(cloud):
    cloud.devices[FEEDER]["type"] = "SF35"
    cloud.attributes[FEEDER] = {
        "plan0": build_plan_value(18, 30, 12, 0, True, "_1010100"),
        "batinfo": "21085",
        "capstatus": "1",
        "eattime": "206_134167160033221",
    }
    cloud.stats[FEEDER] = {"remaining": 0, "eating": [{"type": "0", "time": 2, "duration": 40}]}
    return cloud


def state(hass, entity_id):
    return hass.states.get(entity_id).state


def entity_ids(hass):
    return {
        entry.entity_id
        for entry in er.async_entries_for_config_entry(er.async_get(hass), ENTRY_ID)
    }


def cloud_plan(cloud, slot=0):
    return cloud.attributes[FEEDER][f"plan{slot}"]


async def call(hass, domain, service, entity_id, **data):
    await hass.services.async_call(
        domain, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True
    )


async def test_s30d_states(hass, cloud, clock, setup_integration):
    await setup_integration()
    assert state(hass, "sensor.feeder_food_remaining") == "40"
    assert state(hass, "sensor.feeder_desiccant_expiry") == "12"
    assert state(hass, "sensor.feeder_battery") == "80"
    assert state(hass, "sensor.feeder_left_bowl_eating_count") == "3"
    assert state(hass, "sensor.feeder_left_bowl_eating_duration") == "120"
    assert state(hass, "sensor.feeder_right_bowl_eating_count") == "1"
    assert state(hass, "sensor.feeder_right_bowl_eating_duration") == "30"
    # Non-hopper-2 models count every event except feedings
    events = hass.states.get("sensor.feeder_today_s_events")
    assert events.state == "1"
    assert events.attributes["last_event_desc"] == "Meal"

    assert state(hass, "binary_sensor.feeder_online") == STATE_ON
    assert state(hass, "binary_sensor.feeder_battery_backup") == STATE_ON
    assert state(hass, "switch.feeder_button_lockout") == STATE_ON
    assert state(hass, "select.feeder_meal_call_sound") == MEAL_CALL_OPTION_CUSTOM

    # 18:30 UTC is 19:30 in Rome in winter
    assert state(hass, "time.feeder_schedule_1_time") == "19:30:00"
    assert state(hass, "switch.feeder_schedule_1_enabled") == STATE_ON
    assert state(hass, "number.feeder_schedule_1_portions") == "2"
    assert state(hass, "switch.feeder_schedule_2_enabled") == STATE_OFF
    assert hass.states.get("number.feeder_schedule_1_portions").attributes["max"] == 9
    # S-series models have no weekday mask
    assert "days" not in hass.states.get("switch.feeder_schedule_1_enabled").attributes

    ids = entity_ids(hass)
    assert "time.feeder_schedule_6_time" in ids
    assert "time.feeder_schedule_7_time" not in ids
    assert not any("lid" in i or "battery_charging" in i or "last_" in i for i in ids)


async def test_sf35_states(hass, sf35, clock, setup_integration):
    await setup_integration()
    assert state(hass, "sensor.feeder_battery") == "85"
    assert state(hass, "binary_sensor.feeder_battery_backup") == STATE_ON
    assert state(hass, "binary_sensor.feeder_battery_charging") == STATE_ON
    assert state(hass, "binary_sensor.feeder_lid") == STATE_ON
    # SF-series report their single bowl as type "0"
    assert state(hass, "sensor.feeder_eating_count") == "2"
    assert state(hass, "sensor.feeder_eating_duration") == "40"
    meal = hass.states.get("sensor.feeder_last_meal_duration")
    assert meal.state == "206"
    assert meal.attributes["rfid_tag"] == "134167160033221"

    # 18:30 UTC Mon/Wed/Fri is 19:30 Mon/Wed/Fri in Rome
    schedule = hass.states.get("switch.feeder_schedule_1_enabled")
    assert schedule.attributes["days"] == ["mon", "wed", "fri"]
    assert hass.states.get("number.feeder_schedule_1_portions").attributes["max"] == 24

    ids = entity_ids(hass)
    assert "time.feeder_schedule_10_time" in ids
    for missing in (
        "sensor.feeder_food_remaining",
        "number.feeder_set_food_remaining",
        "switch.feeder_button_lockout",
        "sensor.feeder_left_bowl_eating_count",
    ):
        assert missing not in ids


async def test_sf35_without_battery(hass, sf35, clock, setup_integration):
    sf35.attributes[FEEDER].update(batinfo="00255", capstatus="0")
    await setup_integration()
    assert state(hass, "sensor.feeder_battery") == "unknown"
    assert state(hass, "binary_sensor.feeder_battery_backup") == STATE_OFF
    assert state(hass, "binary_sensor.feeder_battery_charging") == STATE_OFF
    assert state(hass, "binary_sensor.feeder_lid") == STATE_OFF


async def test_unknown_lid_value_makes_the_sensor_unavailable(
    hass, sf35, clock, setup_integration
):
    coordinator = await setup_integration()
    sf35.attributes[FEEDER]["capstatus"] = "9"
    await poll(hass, coordinator)
    assert state(hass, "binary_sensor.feeder_lid") == STATE_UNAVAILABLE


async def test_single_hopper_model_counts_feedings_only(hass, cloud, clock, setup_integration):
    cloud.devices[FEEDER]["type"] = "S25D"
    await setup_integration()
    assert state(hass, "sensor.feeder_today_s_events") == "1"
    assert not any("eating" in i for i in entity_ids(hass))


async def test_failed_poll_makes_entities_unavailable(hass, cloud, clock, setup_integration):
    coordinator = await setup_integration()
    cloud.api.get_feeder_stats.side_effect = RuntimeError("cloud down")
    await poll(hass, coordinator)
    assert state(hass, "sensor.feeder_food_remaining") == STATE_UNAVAILABLE
    assert state(hass, "switch.feeder_schedule_1_enabled") == STATE_UNAVAILABLE


async def test_schedule_switch_writes_the_plan(hass, cloud, clock, setup_integration):
    await setup_integration()
    await call(hass, "switch", "turn_off", "switch.feeder_schedule_1_enabled")
    assert cloud.attributes[FEEDER]["plan0"] == build_plan_value(18, 30, 2, 0, False)
    assert state(hass, "switch.feeder_schedule_1_enabled") == STATE_OFF


async def test_schedule_portions_write_the_plan(hass, cloud, clock, setup_integration):
    await setup_integration()
    await call(hass, "number", "set_value", "number.feeder_schedule_1_portions", value=7)
    assert cloud.attributes[FEEDER]["plan0"] == build_plan_value(18, 30, 7, 0)
    assert state(hass, "number.feeder_schedule_1_portions") == "7"


async def test_schedule_time_is_written_in_utc(hass, cloud, clock, setup_integration):
    await setup_integration()
    await call(hass, "time", "set_value", "time.feeder_schedule_1_time", time="07:45:00")
    assert cloud.attributes[FEEDER]["plan0"] == build_plan_value(6, 45, 2, 0)
    assert state(hass, "time.feeder_schedule_1_time") == "07:45:00"


async def test_sf35_time_across_utc_midnight_keeps_local_days(
    hass, sf35, clock, setup_integration
):
    await setup_integration()
    # 00:30 Rome on Mon/Wed/Fri is 23:30 UTC on Sun/Tue/Thu
    await call(hass, "time", "set_value", "time.feeder_schedule_1_time", time="00:30:00")
    assert cloud_plan(sf35) == build_plan_value(23, 30, 12, 0, True, "_0101001")
    schedule = hass.states.get("switch.feeder_schedule_1_enabled")
    assert schedule.attributes["days"] == ["mon", "wed", "fri"]


async def test_feed_button_uses_manual_feed_portions(hass, cloud, clock, setup_integration):
    await setup_integration()
    await call(hass, "number", "set_value", "number.feeder_manual_feed_portions", value=4)
    await call(hass, "button", "press", "button.feeder_feed")
    cloud.api.send_feed_command.assert_awaited_once_with(FEEDER, 4)


async def test_buttons_raise_when_the_cloud_refuses(hass, cloud, clock, setup_integration):
    await setup_integration()
    cloud.api.send_feed_command.return_value = False
    cloud.api.reset_desiccant.return_value = False
    with pytest.raises(HomeAssistantError):
        await call(hass, "button", "press", "button.feeder_feed")
    with pytest.raises(HomeAssistantError):
        await call(hass, "button", "press", "button.feeder_reset_desiccant")


async def test_reset_desiccant(hass, cloud, clock, setup_integration):
    await setup_integration()
    await call(hass, "button", "press", "button.feeder_reset_desiccant")
    cloud.api.reset_desiccant.assert_awaited_once_with(FEEDER)


async def test_set_food_remaining(hass, cloud, clock, setup_integration):
    await setup_integration()
    await call(hass, "number", "set_value", "number.feeder_set_food_remaining", value=75)
    cloud.api.set_food_remaining.assert_awaited_once_with(FEEDER, 75)
    await hass.async_block_till_done()
    assert state(hass, "sensor.feeder_food_remaining") == "75"


async def test_button_lockout(hass, cloud, clock, setup_integration):
    await setup_integration()
    await call(hass, "switch", "turn_off", "switch.feeder_button_lockout")
    cloud.api.set_button_lockout.assert_awaited_once_with(FEEDER, False)
    assert state(hass, "switch.feeder_button_lockout") == STATE_OFF


async def test_failed_button_lockout_rolls_back(hass, cloud, clock, setup_integration):
    await setup_integration()
    cloud.api.set_button_lockout.side_effect = None
    cloud.api.set_button_lockout.return_value = False
    with pytest.raises(HomeAssistantError):
        await call(hass, "switch", "turn_off", "switch.feeder_button_lockout")
    assert state(hass, "switch.feeder_button_lockout") == STATE_ON


async def test_meal_call_sound(hass, cloud, clock, setup_integration):
    await setup_integration()
    await call(
        hass, "select", "select_option", "select.feeder_meal_call_sound",
        option=MEAL_CALL_OPTION_DEFAULT,
    )
    cloud.api.set_meal_call_sound_mode.assert_awaited_once_with(FEEDER, False)
    assert state(hass, "select.feeder_meal_call_sound") == MEAL_CALL_OPTION_DEFAULT


async def test_failed_meal_call_sound_rolls_back(hass, cloud, clock, setup_integration):
    await setup_integration()
    cloud.api.set_meal_call_sound_mode.side_effect = RuntimeError("timeout")
    with pytest.raises(RuntimeError):
        await call(
            hass, "select", "select_option", "select.feeder_meal_call_sound",
            option=MEAL_CALL_OPTION_DEFAULT,
        )
    assert state(hass, "select.feeder_meal_call_sound") == MEAL_CALL_OPTION_CUSTOM
