#!/usr/bin/env python3
"""Upload a custom HGSmart sound without importing Home Assistant."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib.util
import json
import logging
import os
import shutil
import sys
import tempfile
import types
from functools import partial
from pathlib import Path
from types import ModuleType

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = REPOSITORY_ROOT / "custom_components" / "hgsmart"
STANDALONE_PACKAGE = "_hgsmart_standalone"
_LOGGER = logging.getLogger(__name__)


def _load_component_module(name: str) -> ModuleType:
    """Load one integration module without executing its HA package entrypoint."""
    package = sys.modules.get(STANDALONE_PACKAGE)
    if package is None:
        package = types.ModuleType(STANDALONE_PACKAGE)
        package.__path__ = [str(COMPONENT_ROOT)]
        sys.modules[STANDALONE_PACKAGE] = package

    qualified_name = f"{STANDALONE_PACKAGE}.{name}"
    spec = importlib.util.spec_from_file_location(
        qualified_name, COMPONENT_ROOT / f"{name}.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load HGSmart module {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified_name] = module
    spec.loader.exec_module(module)
    return module


_load_component_module("const")
audio_module = _load_component_module("audio")
api_module = _load_component_module("api")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Upload a custom meal-call sound to an HGSmart feeder. "
            "This utility has no feeding command."
        )
    )
    parser.add_argument(
        "audio_file",
        type=Path,
        nargs="?",
        help="audio file to upload; omit with --silence",
    )
    parser.add_argument("--device-id", help="cloud device ID; inferred if unique")
    parser.add_argument(
        "--host",
        help="feeder LAN address with optional port; defaults to API data",
    )
    parser.add_argument(
        "--ffmpeg",
        default="ffmpeg",
        help="FFmpeg executable used for incompatible inputs (default: ffmpeg)",
    )
    parser.add_argument(
        "--credentials-file",
        type=Path,
        help="JSON file containing username and password or refresh_token",
    )
    parser.add_argument(
        "--volume",
        type=float,
        default=audio_module.AUDIO_VOLUME_DEFAULT,
        help="source amplitude percentage from 0 to 200 (default: 100)",
    )
    parser.add_argument(
        "--silence",
        action="store_true",
        help="upload the precomputed shortest valid zero-volume WAV",
    )
    parser.add_argument("--verbose", action="store_true", help="enable debug logs")
    return parser


def _credentials(
    credentials_file: Path | None,
) -> tuple[str, str | None, str | None]:
    values: dict[str, object] = {}
    if credentials_file is not None:
        try:
            values = json.loads(credentials_file.read_text())
        except (OSError, json.JSONDecodeError) as err:
            raise RuntimeError(
                f"Could not read credentials file {credentials_file}: {err}"
            ) from err
        if not isinstance(values, dict):
            raise RuntimeError("The credentials file must contain a JSON object")

    username = str(
        values.get("username") or os.environ.get("HGSMART_USERNAME", "")
    ).strip()
    if not username:
        raise RuntimeError("Set HGSMART_USERNAME before running this utility")

    refresh_token_value = values.get("refresh_token") or os.environ.get(
        "HGSMART_REFRESH_TOKEN"
    )
    refresh_token = str(refresh_token_value) if refresh_token_value else None
    password_value = values.get("password") or os.environ.get("HGSMART_PASSWORD")
    password = str(password_value) if password_value else None
    if refresh_token is None and password is None:
        password = getpass.getpass("HGSmart password: ")
    return username, password, refresh_token


def _select_device(devices: list[dict[str, object]], requested_id: str | None):
    supported = [
        device
        for device in devices
        if str(device.get("type", "")).startswith(("S25", "S30"))
    ]
    if requested_id:
        for device in supported:
            if str(device.get("deviceId")) == requested_id:
                return device
        raise RuntimeError(f"Supported device {requested_id!r} was not found")
    if len(supported) != 1:
        choices = ", ".join(
            f"{device.get('name', 'Unnamed')}={device.get('deviceId')}"
            for device in supported
        )
        raise RuntimeError(
            "Pass --device-id because the supported feeder is not unique"
            + (f": {choices}" if choices else "")
        )
    return supported[0]


async def _prepare_audio(
    source: Path,
    ffmpeg_binary: str,
    target: Path,
    volume_percent: float,
) -> bytes:
    if not source.is_file():
        raise RuntimeError(f"Audio file does not exist: {source}")
    volume_percent = audio_module.validate_volume_percent(volume_percent)
    source_data = source.read_bytes()
    try:
        audio_module.inspect_wav(source_data)
    except audio_module.HGSmartAudioError:
        pass
    else:
        if volume_percent == audio_module.AUDIO_VOLUME_DEFAULT:
            return source_data

    executable = shutil.which(ffmpeg_binary)
    if executable is None:
        raise RuntimeError(
            "This input needs conversion, but FFmpeg was not found on PATH"
        ) from None
    await audio_module.async_convert_audio(
        executable,
        str(source),
        target,
        volume_percent=volume_percent,
    )
    source_data = target.read_bytes()
    audio_module.inspect_wav(source_data)
    return source_data


async def _run(args: argparse.Namespace) -> None:
    username, password, refresh_token = _credentials(args.credentials_file)
    client = api_module.HGSmartApiClient(
        username,
        password=password,
        refresh_token=refresh_token,
    )
    device_id: str | None = None
    transfer_prepared = False
    try:
        if not await client.authenticate():
            raise RuntimeError("HGSmart authentication failed")
        print("authentication=ok")

        device = _select_device(await client.get_devices(), args.device_id)
        device_id = str(device["deviceId"])
        print(
            f"device={device.get('name', 'Unnamed')!r} "
            f"model={device.get('type', 'Unknown')!r}"
        )

        attributes = await client.get_device_attributes(device_id) or {}
        endpoint = args.host or audio_module.find_local_endpoint(
            {"device_info": device, "attributes": attributes}
        )
        if not endpoint:
            raise RuntimeError("No feeder LAN address was found; pass --host")
        audio_module.parse_endpoint(endpoint)
        print(f"endpoint={endpoint}")

        with tempfile.TemporaryDirectory(prefix="hgsmart_voice_") as temp_dir:
            converted_path = Path(temp_dir) / "custom_voice.wav"
            if args.silence:
                audio = audio_module.SILENT_WAV
                displayed_volume = 0.0
            else:
                audio = await _prepare_audio(
                    args.audio_file,
                    args.ffmpeg,
                    converted_path,
                    args.volume,
                )
                displayed_volume = args.volume
            wav_format = audio_module.inspect_wav(audio)
            print(
                f"audio=ok bytes={len(audio)} rate={wav_format.sample_rate} "
                f"channels={wav_format.channels} bits={wav_format.bits_per_sample} "
                f"volume={displayed_volume:g}%"
            )

            voice_url = await client.upload_voice_file(device_id, audio)
            if not voice_url:
                raise RuntimeError("HGSmart rejected the cloud upload")
            print("cloud_upload=ok")

            if not await client.set_custom_voice_url(device_id, voice_url):
                raise RuntimeError("Could not send the custom-sound URL")
            print("getmusic=ok")

            # The command may reach the feeder even if its response is lost, so
            # the outer finally block sends music=0 whenever this was attempted.
            transfer_prepared = True
            if not await client.prepare_custom_voice_transfer(device_id):
                raise RuntimeError("Could not open the feeder transfer listener")
            print("transfer_prepare=ok")

            await asyncio.sleep(0.5)
            await audio_module.async_send_audio(
                endpoint,
                audio,
                reopen_transfer=partial(
                    client.prepare_custom_voice_transfer,
                    device_id,
                ),
                finalize_transfer=partial(
                    client.finish_custom_voice_transfer,
                    device_id,
                ),
            )
            transfer_prepared = False
            print("tcp_transfer=ok")
            print("transfer_finish=ok")

            if not await client.activate_custom_voice(device_id):
                raise RuntimeError("The sound transferred but activation failed")
            print("custom_voice_activation=ok")
            print("feed_command=not_available")
    finally:
        if transfer_prepared and device_id is not None:
            try:
                await client.finish_custom_voice_transfer(device_id)
            except Exception:
                _LOGGER.exception("Could not close feeder transfer mode")
        await client.close()


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    if args.silence and args.audio_file is not None:
        parser.error("audio_file and --silence are mutually exclusive")
    if not args.silence and args.audio_file is None:
        parser.error("audio_file is required unless --silence is used")
    if args.silence and args.volume != audio_module.AUDIO_VOLUME_DEFAULT:
        parser.error("--volume cannot be combined with --silence")
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    try:
        asyncio.run(_run(args))
    except (RuntimeError, audio_module.HGSmartAudioError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
