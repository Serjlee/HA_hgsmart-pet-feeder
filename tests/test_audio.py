"""Test audio conversion, payload validation and the local transfer."""

import asyncio
import struct
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.hgsmart import audio


def pcm_wav(payload: bytes = b"\x00\x00" * 128) -> bytes:
    """Build a minimal PCM 16-bit/22050 Hz WAV fixture."""
    fmt = struct.pack("<HHIIHH", 1, 1, 22_050, 44_100, 2, 16)
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body


def test_silent_wav_is_the_shortest_supported_sample():
    value = audio.SILENT_WAV
    assert len(value) == 46
    assert audio.inspect_wav(value).data_size == 2
    assert value[-2:] == b"\x00\x00"


def test_inspect_valid_pcm_wav():
    result = audio.inspect_wav(pcm_wav())
    assert (result.audio_format, result.channels, result.sample_rate) == (1, 1, 22_050)
    assert (result.block_align, result.bits_per_sample, result.data_size) == (2, 16, 256)


def test_rejects_alaw_wav():
    value = bytearray(pcm_wav())
    struct.pack_into("<H", value, 20, 6)
    with pytest.raises(audio.HGSmartAudioError, match="expected PCM"):
        audio.inspect_wav(bytes(value))


def test_rejects_oversized_wav():
    value = pcm_wav(b"\x00" * audio.AUDIO_MAX_FILE_SIZE)
    with pytest.raises(audio.HGSmartAudioError, match="maximum"):
        audio.inspect_wav(value)


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("192.168.1.42", ("192.168.1.42", 3333)),
        ("feeder.local:4444", ("feeder.local", 4444)),
        ("[fe80::1]:3333", ("fe80::1", 3333)),
    ],
)
def test_parse_endpoint(endpoint, expected):
    assert audio.parse_endpoint(endpoint) == expected


def test_rejects_invalid_port():
    with pytest.raises(audio.HGSmartAudioError, match="Invalid feeder port"):
        audio.parse_endpoint("192.168.1.42:70000")


def test_finds_nested_firmware_ip():
    device = {
        "device_info": {"name": "Feeder"},
        "attributes": {"network": {"localIp": "192.168.1.42:3333"}},
    }
    assert audio.find_local_endpoint(device) == "192.168.1.42:3333"


def test_ffmpeg_command_matches_captured_s30d_format():
    assert audio.build_ffmpeg_command("ffmpeg", "input.mp3", Path("out.wav")) == (
        "ffmpeg",
        "-y",
        "-i",
        "input.mp3",
        "-filter:a",
        "atrim=duration=10,asetpts=PTS-STARTPTS,"
        "adelay=delays=250:all=1,apad=pad_dur=0.25",
        "-ar",
        "22050",
        "-ac",
        "1",
        "-sample_fmt",
        "s16",
        "-acodec",
        "pcm_s16le",
        "-t",
        "10.5",
        "out.wav",
    )


@pytest.mark.parametrize(
    ("volume", "filters"),
    [
        (25, "volume=0.25,"),
        (150, "volume=1.5,alimiter=limit=0.95,"),
    ],
)
def test_ffmpeg_applies_volume_and_limits_amplification(volume, filters):
    command = audio.build_ffmpeg_command(
        "ffmpeg", "input.mp3", Path("out.wav"), volume_percent=volume
    )
    assert command[4:6] == (
        "-filter:a",
        f"atrim=duration=10,asetpts=PTS-STARTPTS,{filters}"
        "adelay=delays=250:all=1,apad=pad_dur=0.25",
    )


def test_padding_does_not_reduce_useful_audio_limit():
    assert audio.AUDIO_MAX_DURATION == 10
    assert audio.AUDIO_MAX_OUTPUT_DURATION == 10.5


def test_rejects_invalid_volume_percentage():
    with pytest.raises(audio.HGSmartAudioError, match="0 to 200"):
        audio.build_ffmpeg_command(
            "ffmpeg", "input.mp3", Path("out.wav"), volume_percent=201
        )


async def test_sends_framed_wav_and_waits_for_ack(socket_enabled):
    wav = pcm_wav(b"\x01\x02\x03\x04" * 400)
    expected = wav + audio.AUDIO_TRANSFER_END_MARKER
    received = asyncio.get_running_loop().create_future()
    finalize_transfer = AsyncMock(return_value=True)

    async def receive(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            received.set_result(await reader.readexactly(len(expected)))
            writer.write(audio.AUDIO_TRANSFER_ACK)
            await writer.drain()
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()

    async with await asyncio.start_server(receive, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        await audio.async_send_audio(
            f"127.0.0.1:{port}", wav, chunk_size=257, chunk_delay=0,
            finalize_transfer=finalize_transfer,
        )

    assert received.result() == expected
    finalize_transfer.assert_awaited_once_with()


async def test_rejects_transfer_without_ack(socket_enabled):
    wav = pcm_wav()
    finalize_transfer = AsyncMock(return_value=True)

    async def receive(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            await reader.readexactly(len(wav) + len(audio.AUDIO_TRANSFER_END_MARKER))
        finally:
            writer.close()
            await writer.wait_closed()

    async with await asyncio.start_server(receive, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(audio.HGSmartAudioError, match="without confirming"):
            await audio.async_send_audio(
                f"127.0.0.1:{port}", wav, chunk_delay=0, response_timeout=0.1,
                finalize_transfer=finalize_transfer,
            )

    finalize_transfer.assert_awaited_once_with()


async def test_closes_transfer_mode_when_connection_fails(monkeypatch):
    finalize_transfer = AsyncMock(return_value=True)
    monkeypatch.setattr(asyncio, "open_connection", AsyncMock(side_effect=ConnectionRefusedError))

    with pytest.raises(audio.HGSmartAudioError, match="Could not connect"):
        await audio.async_send_audio(
            "127.0.0.1", pcm_wav(), finalize_transfer=finalize_transfer
        )

    finalize_transfer.assert_awaited_once_with()


async def test_closes_transfer_mode_when_socket_write_stalls(monkeypatch):
    async def stall():
        await asyncio.Future()

    writer = Mock(drain=AsyncMock(side_effect=stall), wait_closed=AsyncMock())
    finalize_transfer = AsyncMock(return_value=True)
    monkeypatch.setattr(asyncio, "open_connection", AsyncMock(return_value=(Mock(), writer)))

    with pytest.raises(audio.HGSmartAudioError, match="Sending audio.*timed out"):
        await audio.async_send_audio(
            "127.0.0.1", pcm_wav(), send_timeout=0.01,
            finalize_transfer=finalize_transfer,
        )

    finalize_transfer.assert_awaited_once_with()
    writer.close.assert_called_once_with()


async def test_cancellation_kills_and_reaps_ffmpeg(monkeypatch):
    started = asyncio.Event()

    async def communicate():
        if not started.is_set():
            started.set()
            await asyncio.Future()
        return b"", b""

    process = Mock(communicate=AsyncMock(side_effect=communicate))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    task = asyncio.create_task(audio.async_convert_audio("ffmpeg", "input.mp3", Path("out.wav")))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    process.kill.assert_called_once_with()
    assert process.communicate.await_count == 2
