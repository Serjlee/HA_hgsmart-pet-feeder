"""Tests for HGSmart custom sound helpers without requiring Home Assistant."""

from __future__ import annotations

import asyncio
import importlib.util
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

MODULE_PATH = Path(__file__).parents[1] / "custom_components" / "hgsmart" / "audio.py"
SPEC = importlib.util.spec_from_file_location("hgsmart_audio", MODULE_PATH)
assert SPEC and SPEC.loader
audio = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audio
SPEC.loader.exec_module(audio)


def pcm_wav(payload: bytes = b"\x00\x00" * 128) -> bytes:
    """Build a minimal PCM 16-bit/22050 Hz WAV fixture."""
    fmt = struct.pack("<HHIIHH", 1, 1, 22_050, 44_100, 2, 16)
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body


class AudioFormatTests(unittest.TestCase):
    """Test deterministic format and endpoint helpers."""

    def test_builds_shortest_valid_silent_wav(self) -> None:
        value = audio.SILENT_WAV
        result = audio.inspect_wav(value)

        self.assertEqual(len(value), 46)
        self.assertEqual(result.data_size, 2)
        self.assertEqual(value[-2:], b"\x00\x00")

    def test_inspect_valid_pcm_wav(self) -> None:
        result = audio.inspect_wav(pcm_wav())
        self.assertEqual(result.audio_format, 1)
        self.assertEqual(result.channels, 1)
        self.assertEqual(result.sample_rate, 22_050)
        self.assertEqual(result.byte_rate, 44_100)
        self.assertEqual(result.block_align, 2)
        self.assertEqual(result.bits_per_sample, 16)
        self.assertEqual(result.data_size, 256)

    def test_rejects_alaw_wav(self) -> None:
        value = bytearray(pcm_wav())
        struct.pack_into("<H", value, 20, 6)
        with self.assertRaisesRegex(audio.HGSmartAudioError, "expected PCM"):
            audio.inspect_wav(bytes(value))

    def test_rejects_oversized_wav(self) -> None:
        value = pcm_wav(b"\x00" * audio.AUDIO_MAX_FILE_SIZE)
        with self.assertRaisesRegex(audio.HGSmartAudioError, "maximum"):
            audio.inspect_wav(value)

    def test_parse_endpoint(self) -> None:
        self.assertEqual(audio.parse_endpoint("192.168.1.42"), ("192.168.1.42", 3333))
        self.assertEqual(
            audio.parse_endpoint("feeder.local:4444"), ("feeder.local", 4444)
        )
        self.assertEqual(audio.parse_endpoint("[fe80::1]:3333"), ("fe80::1", 3333))
        with self.assertRaisesRegex(audio.HGSmartAudioError, "Invalid feeder port"):
            audio.parse_endpoint("192.168.1.42:70000")

    def test_finds_nested_firmware_ip(self) -> None:
        device_data = {
            "device_info": {"name": "Feeder"},
            "attributes": {"network": {"localIp": "192.168.1.42:3333"}},
        }
        self.assertEqual(audio.find_local_endpoint(device_data), "192.168.1.42:3333")

    def test_ffmpeg_command_matches_captured_s30d_format(self) -> None:
        command = audio.build_ffmpeg_command("ffmpeg", "input.mp3", Path("out.wav"))
        self.assertEqual(
            command,
            (
                "ffmpeg",
                "-y",
                "-i",
                "input.mp3",
                "-ar",
                "22050",
                "-ac",
                "1",
                "-sample_fmt",
                "s16",
                "-acodec",
                "pcm_s16le",
                "-t",
                "10",
                "out.wav",
            ),
        )

    def test_ffmpeg_command_applies_volume_percentage(self) -> None:
        command = audio.build_ffmpeg_command(
            "ffmpeg",
            "input.mp3",
            Path("out.wav"),
            volume_percent=25,
        )
        self.assertEqual(command[4:6], ("-filter:a", "volume=0.25"))

    def test_ffmpeg_command_limits_amplification(self) -> None:
        command = audio.build_ffmpeg_command(
            "ffmpeg",
            "input.mp3",
            Path("out.wav"),
            volume_percent=150,
        )
        self.assertEqual(
            command[4:6],
            ("-filter:a", "volume=1.5,alimiter=limit=0.95"),
        )

    def test_rejects_invalid_volume_percentage(self) -> None:
        with self.assertRaisesRegex(audio.HGSmartAudioError, "0 to 200"):
            audio.build_ffmpeg_command(
                "ffmpeg",
                "input.mp3",
                Path("out.wav"),
                volume_percent=201,
            )


class AudioTransferTests(unittest.IsolatedAsyncioTestCase):
    """Test the official app's framing and completion acknowledgement."""

    async def test_sends_framed_wav_and_waits_for_ack(self) -> None:
        expected = pcm_wav(b"\x01\x02\x03\x04" * 400)
        expected_payload = expected + audio.AUDIO_TRANSFER_END_MARKER
        received = bytearray()
        done = asyncio.Event()
        finalize_transfer = AsyncMock(return_value=True)

        async def receive(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            while len(received) < len(expected_payload):
                chunk = await reader.read(4096)
                if not chunk:
                    break
                received.extend(chunk)
            writer.write(audio.AUDIO_TRANSFER_ACK)
            await writer.drain()
            await reader.read()
            writer.close()
            await writer.wait_closed()
            done.set()

        server = await asyncio.start_server(receive, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            await audio.async_send_audio(
                f"127.0.0.1:{port}",
                expected,
                chunk_size=257,
                chunk_delay=0,
                finalize_transfer=finalize_transfer,
            )
            await asyncio.wait_for(done.wait(), timeout=1)
        finally:
            server.close()
            await server.wait_closed()

        self.assertEqual(bytes(received), expected_payload)
        finalize_transfer.assert_awaited_once_with()

    async def test_rejects_transfer_without_ack(self) -> None:
        expected = pcm_wav()
        finalize_transfer = AsyncMock(return_value=True)

        async def receive(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            await reader.read(len(expected) + len(audio.AUDIO_TRANSFER_END_MARKER))
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(receive, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            with self.assertRaisesRegex(audio.HGSmartAudioError, "without confirming"):
                await audio.async_send_audio(
                    f"127.0.0.1:{port}",
                    expected,
                    chunk_delay=0,
                    response_timeout=0.1,
                    finalize_transfer=finalize_transfer,
                )
        finally:
            server.close()
            await server.wait_closed()

        finalize_transfer.assert_awaited_once_with()

    async def test_closes_transfer_mode_when_connection_fails(self) -> None:
        finalize_transfer = AsyncMock(return_value=True)

        with self.assertRaisesRegex(audio.HGSmartAudioError, "Could not connect"):
            await audio.async_send_audio(
                "127.0.0.1:1",
                pcm_wav(),
                connect_timeout=0.1,
                finalize_transfer=finalize_transfer,
            )

        finalize_transfer.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
