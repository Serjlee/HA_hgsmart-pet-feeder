"""A simulated HGSmart cloud behind a mocked API client, shared by the tests."""

from copy import deepcopy
from typing import Any
from unittest.mock import create_autospec

from pytest_homeassistant.common import MockConfigEntry

from custom_components.hgsmart.api import HGSmartApiClient
from custom_components.hgsmart.const import DOMAIN
from custom_components.hgsmart.helpers import build_plan_value

FEEDER = "feeder"
ENTRY_ID = "test-entry"


class FakeCloud:
    """Hold device state like the cloud does and apply API commands to it.

    Tests change ``devices``, ``attributes``, ``stats`` and ``events`` freely;
    every read returns a copy, so the integration never shares state with them.
    """

    def __init__(self) -> None:
        self.devices: dict[str, dict[str, Any]] = {
            FEEDER: {"deviceId": FEEDER, "name": "Feeder", "type": "S30D", "online": True}
        }
        self.attributes: dict[str, dict[str, Any]] = {
            FEEDER: {"plan0": build_plan_value(18, 30, 2, 0)}
        }
        self.stats: dict[str, dict[str, Any] | None] = {}
        self.events: dict[str, list[dict[str, Any]]] = {}

        self.api = create_autospec(HGSmartApiClient, instance=True)
        self.api.refresh_token = "refresh-token"
        self.api.authenticate.return_value = True
        self.api.login.return_value = True
        self.api.get_devices.side_effect = self.get_devices
        self.api.get_feeder_stats.side_effect = self.get_stats
        self.api.get_device_attributes.side_effect = self.read
        self.api.get_device_today_events.side_effect = self.get_events
        self.api.set_schedule.side_effect = self.write
        self.api.set_button_lockout.side_effect = self.set_lockout
        self.api.set_meal_call_sound_mode.side_effect = self.set_meal_call
        self.api.set_food_remaining.side_effect = self.set_food_remaining
        self.api.send_feed_command.return_value = True
        self.api.reset_desiccant.return_value = True

    def add_device(self, device_id: str, type_: str, name: str | None = None, **attributes):
        self.devices[device_id] = {
            "deviceId": device_id,
            "name": name or device_id.title(),
            "type": type_,
            "online": True,
        }
        self.attributes[device_id] = attributes

    async def get_devices(self):
        return deepcopy(list(self.devices.values()))

    async def get_stats(self, device):
        return deepcopy(self.stats.get(device))

    async def read(self, device):
        return deepcopy(self.attributes[device])

    async def get_events(self, device):
        events = self.events.get(device)
        if events is None:
            return None
        return {"events": deepcopy(events), "total": len(events)}

    async def write(self, device, slot, hour, minute, portions, enabled, suffix=""):
        self.attributes[device][f"plan{slot}"] = build_plan_value(
            hour, minute, portions, slot, enabled, suffix
        )
        return True

    async def set_lockout(self, device, locked):
        self.attributes[device]["child"] = "1" if locked else "0"
        return True

    async def set_meal_call(self, device, custom):
        self.attributes[device]["choosevoice"] = "1" if custom else "0"
        return True

    async def set_food_remaining(self, device, percentage):
        self.stats.setdefault(device, {})["remaining"] = percentage
        return True


def add_entry(hass, *, options: dict[str, Any] | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id=ENTRY_ID,
        version=2,
        unique_id="test@example.com",
        data={"username": "test@example.com", "update_interval": 15},
        options=options or {},
    )
    entry.add_to_hass(hass)
    return entry


async def poll(hass, coordinator) -> None:
    """Run one coordinator update and let entities write their state."""
    await coordinator.async_refresh()
    await hass.async_block_till_done()
