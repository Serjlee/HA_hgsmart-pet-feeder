"""Tests for the HGSmart cloud controls used by custom voice transfer."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

COMPONENT = Path(__file__).parents[1] / "custom_components" / "hgsmart"
PACKAGE_NAME = "hgsmart_api_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE_NAME] = package

for module_name in ("const", "api"):
    full_name = f"{PACKAGE_NAME}.{module_name}"
    spec = importlib.util.spec_from_file_location(
        full_name, COMPONENT / f"{module_name}.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)

api = sys.modules[f"{PACKAGE_NAME}.api"]


class VoiceTransferControlTests(unittest.IsolatedAsyncioTestCase):
    """Lock down the transfer-mode values recovered from the official app."""

    async def test_opens_and_closes_transfer_listener(self) -> None:
        client = api.HGSmartApiClient("test@example.com")
        client._put_ctrl_command = AsyncMock(return_value=True)

        self.assertTrue(await client.prepare_custom_voice_transfer("device-1"))
        self.assertTrue(await client.finish_custom_voice_transfer("device-1"))

        self.assertEqual(
            client._put_ctrl_command.await_args_list,
            [
                unittest.mock.call("device-1", "music", "1"),
                unittest.mock.call("device-1", "music", "0"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
