"""Exercise offset compensation against a simulated cloud, without HA installed."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import voluptuous as vol

COMPONENT = Path(__file__).parents[1] / "custom_components" / "hgsmart"
PACKAGE_NAME = "hgsmart_offsets_test"


def module(name, **attributes):
    result = types.ModuleType(name)
    result.__dict__.update(attributes)
    return result


class ConfigFlow:
    def __init_subclass__(cls, **kwargs):
        pass


class OptionsFlow:
    def async_show_form(self, **kwargs):
        return kwargs

    def async_create_entry(self, **kwargs):
        return kwargs


class CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    def async_write_ha_state(self):
        pass


ha_dt = module("homeassistant.util.dt")
ha_dt.now = lambda: datetime.now(ZoneInfo("Europe/Paris"))
ha_dt.as_utc = lambda value: value.astimezone(ZoneInfo("UTC"))
ha_dt.as_local = lambda value: value.astimezone(ha_dt.now().tzinfo)
stubs = {
    "homeassistant": module("homeassistant"),
    "homeassistant.config_entries": module(
        "homeassistant.config_entries",
        ConfigEntry=object,
        ConfigFlow=ConfigFlow,
        OptionsFlow=OptionsFlow,
    ),
    "homeassistant.const": module(
        "homeassistant.const", CONF_PASSWORD="password", CONF_USERNAME="username"
    ),
    "homeassistant.core": module("homeassistant.core", HomeAssistant=object),
    "homeassistant.data_entry_flow": module(
        "homeassistant.data_entry_flow", FlowResult=dict
    ),
    "homeassistant.exceptions": module(
        "homeassistant.exceptions", HomeAssistantError=RuntimeError
    ),
    "homeassistant.helpers": module("homeassistant.helpers"),
    "homeassistant.helpers.entity": module(
        "homeassistant.helpers.entity", DeviceInfo=dict
    ),
    "homeassistant.helpers.entity_platform": module(
        "homeassistant.helpers.entity_platform", AddEntitiesCallback=object
    ),
    "homeassistant.helpers.storage": module(
        "homeassistant.helpers.storage", Store=object
    ),
    "homeassistant.helpers.update_coordinator": module(
        "homeassistant.helpers.update_coordinator", CoordinatorEntity=CoordinatorEntity
    ),
    "homeassistant.util": module("homeassistant.util", dt=ha_dt),
    "homeassistant.util.dt": ha_dt,
    "homeassistant.components": module("homeassistant.components"),
    "homeassistant.components.time": module(
        "homeassistant.components.time", TimeEntity=type("TimeEntity", (), {})
    ),
    f"{PACKAGE_NAME}.coordinator": module(
        f"{PACKAGE_NAME}.coordinator", HGSmartDataUpdateCoordinator=object
    ),
}
package = module(PACKAGE_NAME, __path__=[str(COMPONENT)])
sys.modules[PACKAGE_NAME] = package
loaded_modules = {}
with patch.dict(sys.modules, stubs):
    for filename in (
        "const",
        "helpers",
        "api",
        "schedule_offsets",
        "config_flow",
        "time",
    ):
        name = f"{PACKAGE_NAME}.{filename}"
        spec = importlib.util.spec_from_file_location(
            name, COMPONENT / f"{filename}.py"
        )
        assert spec and spec.loader
        loaded = importlib.util.module_from_spec(spec)
        sys.modules[name] = loaded
        spec.loader.exec_module(loaded)
        loaded_modules[name] = loaded
sys.modules.update(loaded_modules)

offsets = sys.modules[f"{PACKAGE_NAME}.schedule_offsets"]
helpers = sys.modules[f"{PACKAGE_NAME}.helpers"]
config_flow = sys.modules[f"{PACKAGE_NAME}.config_flow"]
time_platform = sys.modules[f"{PACKAGE_NAME}.time"]
OPTION = "compensate_timezone_offset"


class MemoryStore:
    def __init__(self):
        self.data = None

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)


class OffsetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = datetime(2026, 10, 24, 12, tzinfo=ZoneInfo("Europe/Paris"))
        self.clock = patch.object(ha_dt, "now", lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.store = MemoryStore()
        self.storage = patch.object(
            offsets, "schedule_offset_store", return_value=self.store
        )
        self.storage.start()
        self.addCleanup(self.storage.stop)
        self.remote = {"feeder": {"plan0": helpers.build_plan_value(18, 30, 2, 0)}}
        self.locks = {}
        self.api = types.SimpleNamespace(
            get_device_attributes=AsyncMock(side_effect=self.read),
            set_schedule=AsyncMock(side_effect=self.write),
        )
        self.coordinator = types.SimpleNamespace(
            data={
                "feeder": {
                    "attributes": deepcopy(self.remote["feeder"]),
                    "schedule_slots": 6,
                }
            },
            api=self.api,
            get_schedule_lock=lambda device, slot: self.locks.setdefault(
                (device, slot), asyncio.Lock()
            ),
            async_request_refresh=AsyncMock(side_effect=self.refresh),
            async_update_schedule=self.update_schedule,
        )
        self.manager = offsets.HGSmartScheduleOffsets(None, "entry", self.coordinator)
        self.coordinator.schedule_offsets = self.manager
        await self.manager.async_load()

    async def read(self, device):
        return deepcopy(self.remote[device])

    async def write(
        self, device, slot, hour, minute, portions, enabled, suffix=""
    ):
        # The absolute target must already be durable when PUT is sent.
        pending = self.store.data[device][str(slot)]["pending"]
        self.assertEqual(pending["target"], hour * 60 + minute)
        self.assertEqual(pending["target_suffix"], suffix)
        self.remote[device][f"plan{slot}"] = helpers.build_plan_value(
            hour, minute, portions, slot, enabled, suffix
        )
        return True

    async def update_schedule(
        self, device, slot, update, *, record_time_offset=False
    ):
        schedule = self.coordinator.data[device]["schedules"][slot]
        schedule.update(update(schedule))
        suffix = schedule["suffix"]
        suffix_args = {"suffix": suffix} if suffix else {}
        await self.api.set_schedule(
            device,
            slot,
            schedule["hour"],
            schedule["minute"],
            schedule["portions"],
            schedule["enabled"],
            **suffix_args,
        )
        if record_time_offset and self.coordinator.schedule_offsets is not None:
            await self.coordinator.schedule_offsets.async_record_time(device, slot)

    async def refresh(self):
        for device in self.remote:
            self.coordinator.data[device]["attributes"] = deepcopy(self.remote[device])

    async def winter(self):
        self.now = datetime(2026, 10, 25, 12, tzinfo=ZoneInfo("Europe/Paris"))
        await self.manager.async_check()

    async def restart(self):
        self.manager = offsets.HGSmartScheduleOffsets(None, "entry", self.coordinator)
        self.coordinator.schedule_offsets = self.manager
        await self.manager.async_load()

    async def test_activation_and_unchanged_offset_do_not_touch_cloud(self):
        await self.manager.async_check()
        await self.manager.async_check()
        self.api.set_schedule.assert_not_awaited()
        self.api.get_device_attributes.assert_not_awaited()
        self.coordinator.async_request_refresh.assert_not_awaited()
        self.assertEqual(self.store.data, {"feeder": {"0": {"offset": 120}}})

    async def test_autumn_preserves_all_six_slots_portions_and_enabled(self):
        self.remote["feeder"] = {
            f"plan{slot}": helpers.build_plan_value(
                18, 30, slot + 1, slot, slot % 2 == 0
            )
            for slot in range(6)
        }
        await self.refresh()
        await self.manager.async_check()
        await self.winter()
        for slot in range(6):
            self.api.set_schedule.assert_any_await(
                "feeder", slot, 19, 30, slot + 1, slot % 2 == 0
            )
        self.assertEqual(self.api.set_schedule.await_count, 6)
        await self.manager.async_check()
        self.assertEqual(self.api.set_schedule.await_count, 6)

    async def test_sf35_preserves_all_ten_slots_and_weekday_masks(self):
        suffix = "_1010101"
        self.remote["feeder"] = {
            f"plan{slot}": helpers.build_plan_value(
                18, 30, min(slot + 1, 9), slot, True, suffix
            )
            for slot in range(10)
        }
        self.coordinator.data["feeder"]["schedule_slots"] = 10
        await self.refresh()
        await self.manager.async_check()
        await self.winter()
        self.assertEqual(self.api.set_schedule.await_count, 10)
        for slot in range(10):
            parsed = helpers.parse_plan_value(self.remote["feeder"][f"plan{slot}"])
            self.assertIsNotNone(parsed)
            self.assertEqual(parsed["suffix"], suffix)

    async def test_sf35_retries_when_time_changes_but_weekday_mask_does_not(self):
        suffix = "_1000000"
        self.remote["feeder"]["plan0"] = helpers.build_plan_value(
            23, 45, 2, 0, True, suffix
        )
        await self.refresh()
        await self.manager.async_check()

        async def apply_time_only(
            device, slot, hour, minute, portions, enabled, suffix=""
        ):
            self.remote[device][f"plan{slot}"] = helpers.build_plan_value(
                hour, minute, portions, slot, enabled, "_1000000"
            )
            return True

        self.api.set_schedule.side_effect = apply_time_only
        with self.assertLogs(offsets.__name__, level="WARNING"):
            await self.winter()
        pending = self.store.data["feeder"]["0"]["pending"]
        self.assertNotEqual(pending["source_suffix"], pending["target_suffix"])

        self.api.set_schedule.side_effect = self.write
        await self.manager.async_check()
        self.assertEqual(self.api.set_schedule.await_count, 2)
        self.assertEqual(self.store.data["feeder"]["0"], {"offset": 60})

    async def test_spring_wraps_backwards_over_midnight(self):
        self.now = datetime(2026, 3, 28, 12, tzinfo=ZoneInfo("Europe/Paris"))
        self.remote["feeder"]["plan0"] = helpers.build_plan_value(0, 15, 2, 0)
        await self.refresh()
        await self.manager.async_check()
        self.now = datetime(2026, 3, 29, 12, tzinfo=ZoneInfo("Europe/Paris"))
        await self.manager.async_check()
        self.api.set_schedule.assert_awaited_once_with("feeder", 0, 23, 15, 2, True)

    async def test_autumn_wraps_forwards_over_midnight(self):
        self.remote["feeder"]["plan0"] = helpers.build_plan_value(23, 45, 2, 0)
        await self.refresh()
        await self.manager.async_check()
        await self.winter()
        self.api.set_schedule.assert_awaited_once_with("feeder", 0, 0, 45, 2, True)

    async def test_half_hour_change(self):
        self.now = datetime(2026, 10, 3, 12, tzinfo=ZoneInfo("Australia/Lord_Howe"))
        await self.manager.async_check()
        self.now = datetime(2026, 10, 4, 12, tzinfo=ZoneInfo("Australia/Lord_Howe"))
        await self.manager.async_check()
        self.api.set_schedule.assert_awaited_once_with("feeder", 0, 18, 0, 2, True)

    async def test_change_while_ha_stopped_is_caught_after_restart(self):
        await self.manager.async_check()
        await self.restart()
        await self.winter()
        self.api.set_schedule.assert_awaited_once_with("feeder", 0, 19, 30, 2, True)
        await self.restart()
        await self.manager.async_check()
        self.assertEqual(self.api.set_schedule.await_count, 1)

    async def test_lost_reply_after_acceptance_does_not_shift_twice(self):
        await self.manager.async_check()

        async def lose_reply(*args):
            await self.write(*args)
            raise TimeoutError("Reply lost")

        self.api.set_schedule.side_effect = lose_reply
        with self.assertLogs(offsets.__name__, level="ERROR"):
            await self.winter()
        await self.restart()
        self.api.set_schedule.side_effect = self.write
        await self.manager.async_check()
        self.assertEqual(self.api.set_schedule.await_count, 1)
        self.assertEqual(self.store.data["feeder"]["0"], {"offset": 60})

    async def test_partial_failure_and_restart_retry_only_failed_slot(self):
        self.remote["feeder"]["plan1"] = helpers.build_plan_value(10, 15, 3, 1, False)
        await self.refresh()
        await self.manager.async_check()

        async def fail_second(*args):
            if args[1] == 1:
                return False
            return await self.write(*args)

        self.api.set_schedule.side_effect = fail_second
        with self.assertLogs(offsets.__name__, level="WARNING"):
            await self.winter()
        await self.restart()
        self.api.set_schedule.reset_mock(side_effect=True)
        self.api.set_schedule.side_effect = self.write
        await self.manager.async_check()
        self.api.set_schedule.assert_awaited_once_with("feeder", 1, 11, 15, 3, False)

    async def test_delayed_confirmation_resends_same_absolute_target(self):
        await self.manager.async_check()
        self.api.set_schedule.side_effect = None
        self.api.set_schedule.return_value = True
        with self.assertLogs(offsets.__name__, level="WARNING"):
            await self.winter()
            await self.manager.async_check()
        self.assertEqual(self.store.data["feeder"]["0"]["offset"], 120)
        for call in self.api.set_schedule.await_args_list:
            self.assertEqual(call.args, ("feeder", 0, 19, 30, 2, True))
        # The cloud eventually publishes the first command.
        self.remote["feeder"]["plan0"] = helpers.build_plan_value(19, 30, 2, 0)
        await self.manager.async_check()
        self.assertEqual(self.api.set_schedule.await_count, 2)
        self.assertEqual(self.store.data["feeder"]["0"], {"offset": 60})

    async def test_unavailable_cloud_keeps_old_offset_for_retry(self):
        await self.manager.async_check()
        self.api.get_device_attributes.side_effect = None
        self.api.get_device_attributes.return_value = None
        await self.winter()
        self.api.set_schedule.assert_not_awaited()
        self.assertEqual(self.store.data["feeder"]["0"], {"offset": 120})
        self.api.get_device_attributes.side_effect = self.read
        await self.manager.async_check()
        self.api.set_schedule.assert_awaited_once()

    async def test_empty_or_deleted_slots_are_never_created(self):
        self.remote["feeder"] = {"plan0": "0", "plan1": "31830021"}
        await self.refresh()
        await self.manager.async_check()
        await self.winter()
        self.api.set_schedule.assert_not_awaited()

    async def test_deleted_pending_slot_is_not_resurrected(self):
        await self.manager.async_check()
        self.api.set_schedule.side_effect = None
        self.api.set_schedule.return_value = False
        with self.assertLogs(offsets.__name__, level="WARNING"):
            await self.winter()
        self.remote["feeder"]["plan0"] = "0"
        self.api.set_schedule.reset_mock()
        await self.manager.async_check()
        self.api.set_schedule.assert_not_awaited()
        self.assertNotIn("0", self.store.data["feeder"])

    async def test_external_edit_while_pending_is_kept(self):
        await self.manager.async_check()
        self.api.set_schedule.side_effect = None
        self.api.set_schedule.return_value = False
        with self.assertLogs(offsets.__name__, level="WARNING"):
            await self.winter()
        self.remote["feeder"]["plan0"] = helpers.build_plan_value(21, 45, 4, 0)
        self.api.set_schedule.reset_mock()
        with self.assertLogs(offsets.__name__, level="WARNING"):
            await self.manager.async_check()
        self.api.set_schedule.assert_not_awaited()
        self.assertEqual(self.store.data["feeder"]["0"], {"offset": 60})

    async def test_manual_ha_time_change_uses_current_offset(self):
        await self.manager.async_check()
        self.now = datetime(2026, 10, 25, 12, tzinfo=ZoneInfo("Europe/Paris"))
        self.coordinator.data["feeder"]["schedules"] = {
            0: helpers.parse_plan_value(self.remote["feeder"]["plan0"])
        }
        entity = time_platform.HGSmartScheduleTime(
            None,
            self.coordinator,
            self.api,
            "feeder",
            {"name": "Feeder", "type": "S30D"},
            0,
        )
        # A manual edit is not a compensation PUT, so it has no pending journal.
        self.api.set_schedule.side_effect = None
        self.api.set_schedule.return_value = True
        await entity.async_set_value(datetime.strptime("20:30", "%H:%M").time())
        self.api.set_schedule.assert_awaited_once_with("feeder", 0, 19, 30, 2, True)
        self.api.set_schedule.reset_mock()
        await self.manager.async_check()
        self.api.set_schedule.assert_not_awaited()

    async def test_new_slot_is_baselined_without_retroactive_shift(self):
        await self.manager.async_check()
        await self.winter()
        self.remote["feeder"]["plan1"] = helpers.build_plan_value(8, 0, 3, 1)
        await self.refresh()
        self.api.set_schedule.reset_mock()
        await self.manager.async_check()
        self.api.set_schedule.assert_not_awaited()
        self.assertEqual(self.store.data["feeder"]["1"], {"offset": 60})

    async def test_simultaneous_checks_do_not_duplicate_correction(self):
        await self.manager.async_check()
        self.now = datetime(2026, 10, 25, 12, tzinfo=ZoneInfo("Europe/Paris"))
        await asyncio.gather(self.manager.async_check(), self.manager.async_check())
        self.api.set_schedule.assert_awaited_once()

    async def test_offset_returns_before_unapplied_correction(self):
        await self.manager.async_check()
        self.api.set_schedule.side_effect = None
        self.api.set_schedule.return_value = False
        with self.assertLogs(offsets.__name__, level="WARNING"):
            await self.winter()
        self.now = datetime(2027, 3, 28, 12, tzinfo=ZoneInfo("Europe/Paris"))
        self.api.set_schedule.reset_mock()
        await self.manager.async_check()
        self.api.set_schedule.assert_not_awaited()
        self.assertEqual(self.store.data["feeder"]["0"], {"offset": 120})

    async def test_unload_waits_for_check_and_prevents_further_writes(self):
        await self.manager.async_check()
        self.now = datetime(2026, 10, 25, 12, tzinfo=ZoneInfo("Europe/Paris"))
        reading = asyncio.Event()
        resume = asyncio.Event()

        async def blocked_read(device):
            reading.set()
            await resume.wait()
            return await self.read(device)

        self.api.get_device_attributes.side_effect = blocked_read
        check = asyncio.create_task(self.manager.async_check())
        await reading.wait()
        stop = asyncio.create_task(self.manager.async_stop())
        await asyncio.sleep(0)
        self.assertFalse(stop.done())
        resume.set()
        await asyncio.gather(check, stop)
        await self.manager.async_check()
        self.api.set_schedule.assert_not_awaited()


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the actual integration setup/unload with HA boundaries replaced."""

    async def asyncSetUp(self):
        config_validation = module(
            "homeassistant.helpers.config_validation",
            ensure_list=lambda value: value if isinstance(value, list) else [value],
            string=str,
        )
        device_registry = module(
            "homeassistant.helpers.device_registry", async_get=Mock()
        )
        extra_stubs = {
            **stubs,
            "homeassistant.const": module(
                "homeassistant.const",
                CONF_USERNAME="username",
                Platform=types.SimpleNamespace(
                    **{
                        name: name.lower()
                        for name in (
                            "SENSOR",
                            "BINARY_SENSOR",
                            "BUTTON",
                            "NUMBER",
                            "SELECT",
                            "SWITCH",
                            "TIME",
                        )
                    }
                ),
            ),
            "homeassistant.core": module(
                "homeassistant.core", HomeAssistant=object, ServiceCall=object
            ),
            "homeassistant.exceptions": module(
                "homeassistant.exceptions",
                ConfigEntryAuthFailed=RuntimeError,
                ConfigEntryNotReady=RuntimeError,
                HomeAssistantError=RuntimeError,
            ),
            "homeassistant.helpers": module(
                "homeassistant.helpers",
                config_validation=config_validation,
                device_registry=device_registry,
            ),
            "homeassistant.helpers.config_validation": config_validation,
            "homeassistant.components.ffmpeg": module(
                "homeassistant.components.ffmpeg", get_ffmpeg_manager=Mock()
            ),
            "homeassistant.components.media_source": module(
                "homeassistant.components.media_source", async_resolve_media=AsyncMock()
            ),
            "homeassistant.helpers.device_registry": device_registry,
            "homeassistant.helpers.event": module(
                "homeassistant.helpers.event", async_track_time_interval=Mock()
            ),
        }
        spec = importlib.util.spec_from_file_location(
            PACKAGE_NAME,
            COMPONENT / "__init__.py",
            submodule_search_locations=[str(COMPONENT)],
        )
        assert spec and spec.loader
        integration = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {**extra_stubs, PACKAGE_NAME: integration}):
            spec.loader.exec_module(integration)
        self.integration = integration
        self.api = Mock(authenticate=AsyncMock(return_value=True), close=AsyncMock())
        self.coordinator = Mock(
            data={}, async_config_entry_first_refresh=AsyncMock(), schedule_offsets=None
        )
        self.manager = Mock(
            async_load=AsyncMock(), async_check=AsyncMock(), async_stop=AsyncMock()
        )
        self.store = Mock(async_remove=AsyncMock())
        self.entry = Mock(
            entry_id="entry", data={"username": "test@example.com"}, options={}
        )
        self.hass = types.SimpleNamespace(
            data={},
            config_entries=Mock(
                async_forward_entry_setups=AsyncMock(),
                async_unload_platforms=AsyncMock(return_value=True),
            ),
            services=Mock(),
        )
        for name, replacement in (
            ("HGSmartApiClient", Mock(return_value=self.api)),
            ("HGSmartDataUpdateCoordinator", Mock(return_value=self.coordinator)),
            ("HGSmartScheduleOffsets", Mock(return_value=self.manager)),
            ("schedule_offset_store", Mock(return_value=self.store)),
            ("api_locale_from_hass", Mock(return_value="en-US")),
            ("api_timezone_from_hass", Mock(return_value="Europe/Paris")),
        ):
            patcher = patch.object(integration, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_disabled_option_resets_reference_and_starts_no_timer(self):
        self.assertTrue(await self.integration.async_setup_entry(self.hass, self.entry))
        self.store.async_remove.assert_awaited_once()
        self.integration.HGSmartScheduleOffsets.assert_not_called()
        self.integration.async_track_time_interval.assert_not_called()

    async def test_enabled_option_checks_at_start_and_registers_timer(self):
        self.entry.options = {OPTION: True}
        self.assertTrue(await self.integration.async_setup_entry(self.hass, self.entry))
        self.store.async_remove.assert_not_awaited()
        self.manager.async_load.assert_awaited_once()
        self.manager.async_check.assert_awaited_once()
        timer = self.integration.async_track_time_interval
        timer.assert_called_once_with(
            self.hass, self.manager.async_check, offsets.OFFSET_CHECK_INTERVAL
        )
        self.entry.async_on_unload.assert_any_call(timer.return_value)
        self.assertTrue(
            await self.integration.async_unload_entry(self.hass, self.entry)
        )
        self.manager.async_stop.assert_awaited_once()
        self.api.close.assert_awaited_once()


class OptionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_option_defaults_to_disabled_and_preserves_interval(self):
        flow = config_flow.HGSmartOptionsFlow()
        flow.config_entry = types.SimpleNamespace(
            options={}, data={"update_interval": 42}
        )
        form = await flow.async_step_init()
        self.assertEqual(
            form["data_schema"]({}), {"update_interval": 42, OPTION: False}
        )

    async def test_option_round_trip_and_interval_validation(self):
        flow = config_flow.HGSmartOptionsFlow()
        flow.config_entry = types.SimpleNamespace(
            options={"update_interval": 30, OPTION: True}, data={}
        )
        form = await flow.async_step_init()
        self.assertTrue(form["data_schema"]({})[OPTION])
        with self.assertRaises(vol.Invalid):
            form["data_schema"]({"update_interval": 0})
        values = {"update_interval": 30, OPTION: False}
        result = await flow.async_step_init(values)
        self.assertEqual(result["data"], values)


if __name__ == "__main__":
    unittest.main()
