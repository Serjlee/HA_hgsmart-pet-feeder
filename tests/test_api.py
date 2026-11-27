"""Test the API client against a local HTTP server imitating the HGSmart cloud."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from custom_components.hgsmart import api as api_module
from custom_components.hgsmart.api import (
    HGSmartApiClient,
    HGSmartAuthError,
    HGSmartConnectionError,
)
from custom_components.hgsmart.audio import SILENT_WAV
from custom_components.hgsmart.const import CLIENT_ID, CLIENT_SECRET

DEVICE = "dev1"
TOKENS = {"accessToken": "access-2", "refreshToken": "refresh-2"}
HTML = "<html>maintenance</html>"


@dataclass
class Request:
    method: str
    path: str
    headers: dict[str, str]
    json: Any = None
    command: dict[str, Any] | None = None  # multipart "command" field, decoded
    voice_file: tuple[str, str, bytes] | None = None
    content_type: str = ""


@dataclass
class FakeHGSmart:
    """Answer like the cloud: JSON envelopes with a "code", 401 for a stale token."""

    access_token: str = "access-1"
    requests: list[Request] = field(default_factory=list)
    replies: dict[tuple[str, str], list[Any]] = field(default_factory=dict)

    def reply(self, method: str, path: str, *replies: Any) -> None:
        """Queue replies: a dict (JSON) or a str (HTML). The last one repeats."""
        self.replies[(method, path)] = list(replies)

    def paths(self) -> list[str]:
        return [f"{r.method} {r.path}" for r in self.requests]

    async def handle(self, request: web.Request) -> web.StreamResponse:
        recorded = Request(
            request.method,
            request.path,
            dict(request.headers),
            content_type=request.content_type,
        )
        if request.content_type == "application/json" and request.can_read_body:
            recorded.json = await request.json()
        elif request.content_type == "multipart/form-data":
            form = await request.post()
            if "command" in form:
                recorded.command = json.loads(form["command"])
            if "voiceFile" in form:
                voice_file = form["voiceFile"]
                recorded.voice_file = (
                    voice_file.filename,
                    voice_file.content_type,
                    voice_file.file.read(),
                )
        self.requests.append(recorded)

        if not request.path.startswith("/oauth/") and (
            request.headers.get("Authorization") != f"Bearer {self.access_token}"
        ):
            return web.json_response({"code": 401, "msg": "token expired"})

        queue = self.replies.get((request.method, request.path), [{"code": 200, "data": None}])
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, str):
            return web.Response(text=reply, content_type="text/html", status=503)
        return web.json_response(reply)


@pytest.fixture
async def cloud(monkeypatch, socket_enabled):
    fake = FakeHGSmart()
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", fake.handle)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setattr(api_module, "BASE_URL", str(server.make_url("")).rstrip("/"))
    yield fake
    await server.close()


@pytest.fixture
async def client():
    refreshed: list[str] = []
    client = HGSmartApiClient(
        "user@example.com",
        "secret",
        refresh_token="refresh-1",
        locale="it-IT",
        timezone="Europe/Rome",
        on_token_refresh=refreshed.append,
    )
    client.access_token = "access-1"
    client.refreshed = refreshed
    yield client
    await client.close()


@pytest.fixture
async def unreachable(monkeypatch, socket_enabled):
    """Point the client at a port nobody listens on."""
    server = TestServer(web.Application())
    await server.start_server()
    url = str(server.make_url("")).rstrip("/")
    await server.close()
    monkeypatch.setattr(api_module, "BASE_URL", url)


# --- login and tokens ---------------------------------------------------------


async def test_login_sends_credentials_and_stores_tokens(cloud, client):
    cloud.reply("POST", "/oauth/login", {"code": 200, "data": TOKENS})
    assert await client.login()

    request = cloud.requests[-1]
    assert request.json == {
        "account_num": "user@example.com",
        "pwd": "secret",
        "captcha_uuid": "",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    assert request.headers["Authorization"] == "Bearer null"
    assert request.headers["Client"] == CLIENT_ID
    assert request.headers["Accept-Language"] == "it-IT"
    assert request.headers["Zoneid"] == "Europe/Rome"
    assert request.headers["User-Agent"] == "Dart/3.6 (dart:io)"
    assert (client.access_token, client.refresh_token) == ("access-2", "refresh-2")
    assert client.refreshed == ["refresh-2"]


@pytest.mark.parametrize(
    "reply",
    [
        {"code": 500, "msg": "wrong password"},
        {"code": 200, "data": {"accessToken": "only-access"}},
        {"code": 200, "data": None},
        HTML,
    ],
)
async def test_login_failures(cloud, client, reply):
    cloud.reply("POST", "/oauth/login", reply)
    assert not await client.login()
    assert client.refresh_token == "refresh-1"
    assert client.refreshed == []


async def test_login_without_password_sends_nothing(cloud):
    client = HGSmartApiClient("user@example.com")
    assert not await client.login()
    assert cloud.requests == []


async def test_login_when_unreachable(unreachable, client):
    assert not await client.login()


async def test_refresh_stores_new_tokens(cloud, client):
    cloud.reply("POST", "/oauth/refreshToken", {"code": 200, "data": TOKENS})
    assert await client.refresh_access_token()
    assert cloud.requests[-1].json == {"refreshtoken": "refresh-1"}
    assert client.access_token == "access-2"
    assert client.refreshed == ["refresh-2"]


async def test_refresh_with_the_same_refresh_token_does_not_report_it(cloud, client):
    cloud.reply(
        "POST", "/oauth/refreshToken",
        {"code": 200, "data": {"accessToken": "access-2", "refreshToken": "refresh-1"}},
    )
    assert await client.refresh_access_token()
    assert client.refreshed == []


@pytest.mark.parametrize(
    "reply",
    [{"code": 500, "msg": "invalid refresh token"}, {"code": 200, "data": {}}],
)
async def test_rejected_refresh(cloud, client, reply):
    cloud.reply("POST", "/oauth/refreshToken", reply)
    assert not await client.refresh_access_token()


async def test_refresh_during_an_outage_raises(cloud, client):
    cloud.reply("POST", "/oauth/refreshToken", HTML)
    with pytest.raises(HGSmartConnectionError, match="HTTP 503"):
        await client.refresh_access_token()


async def test_refresh_when_unreachable_raises(unreachable, client):
    with pytest.raises(HGSmartConnectionError):
        await client.refresh_access_token()


async def test_refresh_without_refresh_token(cloud):
    client = HGSmartApiClient("user@example.com", "secret")
    assert not await client.refresh_access_token()
    assert cloud.requests == []


async def test_authenticate_prefers_the_refresh_token(cloud, client):
    cloud.reply("POST", "/oauth/refreshToken", {"code": 200, "data": TOKENS})
    assert await client.authenticate()
    assert cloud.paths() == ["POST /oauth/refreshToken"]


async def test_authenticate_falls_back_to_login(cloud, client):
    cloud.reply("POST", "/oauth/refreshToken", {"code": 500, "msg": "expired"})
    cloud.reply("POST", "/oauth/login", {"code": 200, "data": TOKENS})
    assert await client.authenticate()
    assert cloud.paths() == ["POST /oauth/refreshToken", "POST /oauth/login"]


async def test_authenticate_without_credentials(cloud):
    assert not await HGSmartApiClient("user@example.com").authenticate()
    assert cloud.requests == []


# --- requests and token expiry -----------------------------------------------


async def test_expired_token_is_refreshed_and_the_request_retried(cloud, client):
    cloud.access_token = "access-2"
    cloud.reply("POST", "/oauth/refreshToken", {"code": 200, "data": TOKENS})
    cloud.reply("GET", "/app/device/list", {"code": 200, "data": [{"deviceId": DEVICE}]})

    assert await client.get_devices() == [{"deviceId": DEVICE}]
    assert cloud.paths() == [
        "GET /app/device/list",
        "POST /oauth/refreshToken",
        "GET /app/device/list",
    ]
    assert cloud.requests[-1].headers["Authorization"] == "Bearer access-2"


async def test_expired_token_with_a_rejected_refresh_needs_reauth(cloud, client):
    cloud.access_token = "access-2"
    cloud.reply("POST", "/oauth/refreshToken", {"code": 500, "msg": "invalid"})
    with pytest.raises(HGSmartAuthError):
        await client.get_devices()


async def test_expired_token_during_an_outage_is_retried_later(cloud, client):
    cloud.access_token = "access-2"
    cloud.reply("POST", "/oauth/refreshToken", HTML)
    assert await client.get_devices() == []


@pytest.mark.parametrize("retry", [{"code": 500, "msg": "busy"}, HTML])
async def test_failed_retry_after_refresh(cloud, client, retry):
    cloud.access_token = "access-2"
    cloud.reply("POST", "/oauth/refreshToken", {"code": 200, "data": TOKENS})
    cloud.reply("GET", f"/app/device/attribute/{DEVICE}", retry)
    assert await client.get_device_attributes(DEVICE) is None


@pytest.mark.parametrize("reply", [{"code": 500, "msg": "server error"}, HTML])
async def test_failed_requests_return_nothing(cloud, client, reply):
    for path in ("list", f"feeder/summary/{DEVICE}", f"attribute/{DEVICE}", f"today/{DEVICE}"):
        cloud.reply("GET", f"/app/device/{path}", reply)
    assert await client.get_devices() == []
    assert await client.get_feeder_stats(DEVICE) is None
    assert await client.get_device_attributes(DEVICE) is None
    assert await client.get_device_today_events(DEVICE) is None


async def test_requests_when_unreachable(unreachable, client):
    assert await client.get_devices() == []
    assert not await client.reset_desiccant(DEVICE)


async def test_reads(cloud, client):
    stats = {"remaining": 40, "eating": []}
    attributes = {"plan0": "10940033", "child": "0"}
    events = [{"event": "1_1", "createTime": "08:00"}]
    cloud.reply("GET", f"/app/device/feeder/summary/{DEVICE}", {"code": 200, "data": stats})
    cloud.reply("GET", f"/app/device/attribute/{DEVICE}", {"code": 200, "data": attributes})
    cloud.reply("GET", f"/app/device/today/{DEVICE}", {"code": 200, "data": events})

    assert await client.get_feeder_stats(DEVICE) == stats
    assert await client.get_device_attributes(DEVICE) == attributes
    assert await client.get_device_today_events(DEVICE) == {"events": events, "total": 1}
    assert cloud.requests[-1].headers["Authorization"] == "Bearer access-1"


@pytest.mark.parametrize("data", [None, {"deviceId": DEVICE}])
async def test_devices_without_a_list(cloud, client, data):
    cloud.reply("GET", "/app/device/list", {"code": 200, "data": data})
    assert await client.get_devices() == []


async def test_today_events_without_a_list(cloud, client):
    cloud.reply("GET", f"/app/device/today/{DEVICE}", {"code": 200, "data": None})
    assert await client.get_device_today_events(DEVICE) == {"events": [], "total": 0}


async def test_session_is_reopened_after_close(cloud, client):
    await client.get_devices()
    await client.close()
    await client.close()
    await client.get_devices()
    assert cloud.paths() == ["GET /app/device/list"] * 2


# --- commands ----------------------------------------------------------------


def assert_command(request: Request, identifier: str, value: str) -> None:
    assert request.method == "PUT"
    assert request.path == f"/app/device/attribute/{DEVICE}"
    assert request.content_type == "multipart/form-data"
    command = request.command
    assert command["ctrl"] == {"identifier": identifier, "value": value}
    assert command["ctrl_time"].isdigit()
    assert len(command["message_id"]) == 32


async def test_set_schedule(cloud, client):
    assert await client.set_schedule(DEVICE, 9, 1, 30, 13, False, suffix="_0101000")
    assert_command(cloud.requests[-1], "plan", "00130139_0101000")


async def test_set_schedule_refused(cloud, client):
    cloud.reply("PUT", f"/app/device/attribute/{DEVICE}", {"code": 500, "msg": "offline"})
    assert not await client.set_schedule(DEVICE, 0, 8, 0, 1)


async def test_feed_sends_the_current_utc_time(cloud, client):
    before = datetime.now(timezone.utc)
    assert await client.send_feed_command(DEVICE, 3)
    after = datetime.now(timezone.utc)
    expected = {f"01{t.hour:02d}{t.minute:02d}03" for t in (before, after)}
    request = cloud.requests[-1]
    assert_command(request, "userfoodframe", request.command["ctrl"]["value"])
    assert request.command["ctrl"]["value"] in expected


@pytest.mark.parametrize("portions", [0, 10])
async def test_feed_rejects_invalid_portions(cloud, client, portions):
    assert not await client.send_feed_command(DEVICE, portions)
    assert cloud.requests == []


@pytest.mark.parametrize(("locked", "value"), [(True, "1"), (False, "0")])
async def test_button_lockout(cloud, client, locked, value):
    assert await client.set_button_lockout(DEVICE, locked)
    assert_command(cloud.requests[-1], "child", value)


@pytest.mark.parametrize(("custom", "voice"), [(True, "1"), (False, "0")])
async def test_meal_call_sound_stops_music_then_picks_the_voice(cloud, client, custom, voice):
    assert await client.set_meal_call_sound_mode(DEVICE, custom)
    assert_command(cloud.requests[0], "music", "0")
    assert_command(cloud.requests[1], "choosevoice", voice)


async def test_meal_call_sound_stops_after_a_failed_step(cloud, client):
    cloud.reply("PUT", f"/app/device/attribute/{DEVICE}", {"code": 500, "msg": "offline"})
    assert not await client.set_meal_call_sound_mode(DEVICE, True)
    assert len(cloud.requests) == 1


async def test_voice_transfer_commands(cloud, client):
    url = "https://example.invalid/recording.wav"
    assert await client.set_custom_voice_url(DEVICE, url)
    assert await client.prepare_custom_voice_transfer(DEVICE)
    assert await client.finish_custom_voice_transfer(DEVICE)
    assert await client.activate_custom_voice(DEVICE)

    for request, (identifier, value) in zip(
        cloud.requests,
        [("getmusic", url), ("music", "1"), ("music", "0"), ("choosevoice", "1")],
        strict=True,
    ):
        assert_command(request, identifier, value)


async def test_voice_upload_resends_multipart_file_after_refresh(cloud, client):
    url = "https://example.invalid/recording.wav"
    path = "/app/device/uploadVoiceFile"
    cloud.access_token = "access-2"
    cloud.reply("POST", "/oauth/refreshToken", {"code": 200, "data": TOKENS})
    cloud.reply("POST", path, {"code": 200, "data": {"voiceUrl": url}})

    assert await client.upload_voice_file(SILENT_WAV, "recording.wav") == url
    assert cloud.paths() == [f"POST {path}", "POST /oauth/refreshToken", f"POST {path}"]
    original, _, retry = cloud.requests
    for request in (original, retry):
        assert request.content_type == "multipart/form-data"
        assert request.voice_file == ("recording.wav", "audio/wav", SILENT_WAV)
    assert retry.headers["Authorization"] == "Bearer access-2"


async def test_voice_upload_requires_a_url(cloud, client):
    cloud.reply("POST", "/app/device/uploadVoiceFile", {"code": 200, "data": {}})
    assert await client.upload_voice_file(SILENT_WAV) is None


async def test_reset_desiccant(cloud, client):
    assert await client.reset_desiccant(DEVICE)
    assert cloud.paths() == [f"PUT /app/device/feeder/desiccant/{DEVICE}"]


async def test_set_food_remaining_uses_a_capacity_of_200(cloud, client):
    assert await client.set_food_remaining(DEVICE, 75)
    request = cloud.requests[-1]
    assert request.path == "/app/device/feeder/refill"
    assert request.json == {
        "deviceId": DEVICE, "capacity": 200, "surplus": 150, "capacityModel": ""
    }


async def test_meal_call_sound_fails_when_the_voice_is_refused(cloud, client):
    cloud.reply(
        "PUT", f"/app/device/attribute/{DEVICE}",
        {"code": 200, "data": None}, {"code": 500, "msg": "offline"},
    )
    assert not await client.set_meal_call_sound_mode(DEVICE, True)
    assert len(cloud.requests) == 2


@pytest.mark.parametrize(
    "command",
    [
        lambda c: c.set_button_lockout(DEVICE, True),
        lambda c: c.send_feed_command(DEVICE, 1),
        lambda c: c.set_food_remaining(DEVICE, 50),
        lambda c: c.reset_desiccant(DEVICE),
    ],
    ids=["lockout", "feed", "food_remaining", "desiccant"],
)
async def test_refused_commands(cloud, client, command):
    refused = {"code": 500, "msg": "offline"}
    for path in (
        f"/app/device/attribute/{DEVICE}",
        "/app/device/feeder/refill",
        f"/app/device/feeder/desiccant/{DEVICE}",
    ):
        cloud.reply("PUT", path, refused)
    assert not await command(client)
