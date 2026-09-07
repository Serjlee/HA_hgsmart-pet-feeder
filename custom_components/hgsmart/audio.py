"""Audio conversion and local transfer helpers for HGSmart feeders."""

from __future__ import annotations

import asyncio
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

# The app UI limits recordings to ten seconds. A captured S30D transfer was
# 494,670 bytes (11.22 seconds including the recorder's padding), so keep a
# 512 KiB hard ceiling while producing at most ten seconds ourselves.
AUDIO_MAX_DURATION = 10
AUDIO_MAX_FILE_SIZE = 524_288
AUDIO_SAMPLE_RATE = 22_050
AUDIO_FORMAT_PCM = 1
AUDIO_VOLUME_DEFAULT = 100.0
AUDIO_VOLUME_MIN = 0.0
AUDIO_VOLUME_MAX = 200.0
AUDIO_TCP_PORT = 3_333
AUDIO_CHUNK_SIZE = 4_096
AUDIO_CHUNK_DELAY = 0.05
AUDIO_CONNECT_TIMEOUT = 20.0
AUDIO_RESPONSE_TIMEOUT = 20.0
AUDIO_TRANSFER_END_MARKER = b"DSY-AUDIO"
AUDIO_TRANSFER_ACK = "完成接收".encode()

# Precomputed 46-byte PCM WAV containing one zero-amplitude sample. This is the
# shortest non-empty audio file representable in the feeder's validated format.
SILENT_WAV = (
    b"RIFF\x26\x00\x00\x00WAVE"
    b"fmt \x10\x00\x00\x00"
    b"\x01\x00\x01\x00\x22\x56\x00\x00\x44\xac\x00\x00\x02\x00\x10\x00"
    b"data\x02\x00\x00\x00\x00\x00"
)


class HGSmartAudioError(Exception):
    """Raised when a custom feeder sound cannot be prepared or transferred."""


@dataclass(frozen=True)
class WavFormat:
    """Relevant fields from a WAV fmt chunk."""

    audio_format: int
    channels: int
    sample_rate: int
    byte_rate: int
    block_align: int
    bits_per_sample: int
    data_size: int


def find_local_endpoint(device_data: object) -> str | None:
    """Find a LAN endpoint exposed by different HGSmart firmware variants."""
    endpoint_keys = {
        "audioaddress",
        "audiohost",
        "deviceip",
        "host",
        "ip",
        "ipaddress",
        "localip",
    }

    if isinstance(device_data, dict):
        for key, child in device_data.items():
            normalized = str(key).replace("_", "").lower()
            if normalized in endpoint_keys and isinstance(child, str):
                candidate = child.strip()
                if candidate:
                    return candidate
        for child in device_data.values():
            found = find_local_endpoint(child)
            if found:
                return found
    elif isinstance(device_data, list):
        for child in device_data:
            found = find_local_endpoint(child)
            if found:
                return found
    return None


def build_ffmpeg_command(
    ffmpeg_binary: str,
    input_source: str,
    output_path: Path,
    *,
    volume_percent: float = AUDIO_VOLUME_DEFAULT,
) -> tuple[str, ...]:
    """Build a conversion command matching a captured S30D app transfer."""
    volume_percent = validate_volume_percent(volume_percent)
    command = [
        ffmpeg_binary,
        "-y",
        "-i",
        input_source,
    ]
    if volume_percent != AUDIO_VOLUME_DEFAULT:
        volume_filter = f"volume={volume_percent / 100:g}"
        if volume_percent > AUDIO_VOLUME_DEFAULT:
            volume_filter += ",alimiter=limit=0.95"
        command.extend(("-filter:a", volume_filter))
    command.extend(
        (
            "-ar",
            str(AUDIO_SAMPLE_RATE),
            "-ac",
            "1",
            "-sample_fmt",
            "s16",
            "-acodec",
            "pcm_s16le",
            "-t",
            str(AUDIO_MAX_DURATION),
            str(output_path),
        )
    )
    return tuple(command)


def validate_volume_percent(value: float) -> float:
    """Validate and normalize a requested source-volume percentage."""
    try:
        normalized = float(value)
    except (TypeError, ValueError) as err:
        raise HGSmartAudioError("Volume must be a percentage from 0 to 200") from err
    if not AUDIO_VOLUME_MIN <= normalized <= AUDIO_VOLUME_MAX:
        raise HGSmartAudioError("Volume must be a percentage from 0 to 200")
    return normalized


async def async_convert_audio(
    ffmpeg_binary: str,
    input_source: str,
    output_path: Path,
    *,
    volume_percent: float = AUDIO_VOLUME_DEFAULT,
    timeout: float = 60.0,
) -> None:
    """Convert an audio source to the feeder's PCM WAV format."""
    try:
        process = await asyncio.create_subprocess_exec(
            *build_ffmpeg_command(
                ffmpeg_binary,
                input_source,
                output_path,
                volume_percent=volume_percent,
            ),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as err:
        raise HGSmartAudioError(f"Could not start FFmpeg: {err}") from err
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError as err:
        process.kill()
        await process.communicate()
        raise HGSmartAudioError("Audio conversion timed out") from err

    if process.returncode != 0:
        detail = stderr.decode(errors="replace").strip().splitlines()
        message = detail[-1] if detail else "unknown ffmpeg error"
        raise HGSmartAudioError(f"Audio conversion failed: {message}")


def inspect_wav(data: bytes) -> WavFormat:
    """Parse enough of a RIFF/WAVE file to validate feeder compatibility."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise HGSmartAudioError("Converted audio is not a RIFF/WAVE file")

    fmt: tuple[int, int, int, int, int, int] | None = None
    data_size: int | None = None
    offset = 12
    while offset + 8 <= len(data):
        chunk_id = data[offset : offset + 4]
        chunk_size = struct.unpack_from("<I", data, offset + 4)[0]
        chunk_start = offset + 8
        chunk_end = chunk_start + chunk_size
        if chunk_end > len(data):
            raise HGSmartAudioError("Converted WAV contains a truncated chunk")

        if chunk_id == b"fmt " and chunk_size >= 16:
            fmt = struct.unpack_from("<HHIIHH", data, chunk_start)
        elif chunk_id == b"data":
            data_size = chunk_size

        offset = chunk_end + (chunk_size & 1)

    if fmt is None or data_size is None:
        raise HGSmartAudioError("Converted WAV is missing its fmt or data chunk")

    result = WavFormat(*fmt, data_size)
    if result.audio_format != AUDIO_FORMAT_PCM:
        raise HGSmartAudioError(
            f"Converted WAV codec is {result.audio_format}, expected PCM"
        )
    if result.channels != 1 or result.sample_rate != AUDIO_SAMPLE_RATE:
        raise HGSmartAudioError("Converted WAV must be mono at 22050 Hz")
    if result.bits_per_sample != 16:
        raise HGSmartAudioError("Converted PCM WAV must use 16-bit samples")
    if result.block_align != 2 or result.byte_rate != 44_100:
        raise HGSmartAudioError("Converted PCM WAV has inconsistent stream rates")
    if len(data) > AUDIO_MAX_FILE_SIZE:
        raise HGSmartAudioError(
            f"Converted WAV is {len(data)} bytes; maximum is {AUDIO_MAX_FILE_SIZE}"
        )
    if result.data_size == 0:
        raise HGSmartAudioError("Converted WAV contains no audio data")
    return result


def parse_endpoint(value: str, default_port: int = AUDIO_TCP_PORT) -> tuple[str, int]:
    """Parse a hostname or host:port endpoint, including bracketed IPv6."""
    endpoint = value.strip()
    if not endpoint:
        raise HGSmartAudioError("The feeder's local address is empty")

    if endpoint.startswith("["):
        closing = endpoint.find("]")
        if closing == -1:
            raise HGSmartAudioError(f"Invalid feeder address: {value}")
        host = endpoint[1:closing]
        suffix = endpoint[closing + 1 :]
        if suffix and not suffix.startswith(":"):
            raise HGSmartAudioError(f"Invalid feeder address: {value}")
        port_text = suffix[1:] if suffix else ""
    elif endpoint.count(":") == 1:
        host, port_text = endpoint.rsplit(":", 1)
    else:
        host, port_text = endpoint, ""

    host = host.strip()
    if not host:
        raise HGSmartAudioError(f"Invalid feeder address: {value}")
    if not port_text:
        return host, default_port
    try:
        port = int(port_text)
    except ValueError as err:
        raise HGSmartAudioError(f"Invalid feeder port: {port_text}") from err
    if not 1 <= port <= 65_535:
        raise HGSmartAudioError(f"Invalid feeder port: {port}")
    return host, port


async def async_send_audio(
    endpoint: str,
    audio: bytes,
    *,
    connect_timeout: float = AUDIO_CONNECT_TIMEOUT,
    response_timeout: float = AUDIO_RESPONSE_TIMEOUT,
    chunk_size: int = AUDIO_CHUNK_SIZE,
    chunk_delay: float = AUDIO_CHUNK_DELAY,
    finalize_transfer: Callable[[], Awaitable[bool]] | None = None,
) -> None:
    """Send a framed WAV and wait for the feeder's completion response."""
    inspect_wav(audio)
    host, port = parse_endpoint(endpoint)
    payload = audio + AUDIO_TRANSFER_END_MARKER
    transfer_error: BaseException | None = None
    finalize_error: Exception | None = None
    finalized = True
    writer: asyncio.StreamWriter | None = None
    try:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=connect_timeout
            )
        except (OSError, TimeoutError) as err:
            raise HGSmartAudioError(
                f"Could not connect to feeder at {host}:{port}"
            ) from err

        for offset in range(0, len(payload), chunk_size):
            writer.write(payload[offset : offset + chunk_size])
            await writer.drain()
            await asyncio.sleep(chunk_delay)

        response = bytearray()
        deadline = asyncio.get_running_loop().time() + response_timeout
        while AUDIO_TRANSFER_ACK not in response:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError
            chunk = await asyncio.wait_for(reader.read(1_024), timeout=remaining)
            if not chunk:
                raise HGSmartAudioError(
                    f"Feeder at {host}:{port} closed the connection without "
                    "confirming the audio transfer"
                )
            response.extend(chunk)
            if len(response) > 8_192:
                raise HGSmartAudioError(
                    f"Feeder at {host}:{port} returned an invalid audio response"
                )
    except TimeoutError as err:
        transfer_error = HGSmartAudioError(
            f"Feeder at {host}:{port} did not confirm the audio transfer"
        )
        transfer_error.__cause__ = err
    except (ConnectionError, OSError) as err:
        transfer_error = HGSmartAudioError(
            f"Audio transfer to feeder at {host}:{port} failed"
        )
        transfer_error.__cause__ = err
    finally:
        if finalize_transfer is not None:
            try:
                finalized = await finalize_transfer()
            except Exception as err:  # noqa: BLE001 - caller-provided cleanup
                finalized = False
                finalize_error = err
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    if transfer_error is not None:
        raise transfer_error
    if finalize_error is not None:
        raise HGSmartAudioError(
            f"Feeder at {host}:{port} confirmed the audio, but transfer mode "
            "could not be closed"
        ) from finalize_error
    if not finalized:
        raise HGSmartAudioError(
            f"Feeder at {host}:{port} confirmed the audio, but transfer mode "
            "could not be closed"
        )
