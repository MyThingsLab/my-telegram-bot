from __future__ import annotations

import json
import urllib.error

import pytest

from mytelegrambot import transport as t
from mytelegrambot.transport import (
    HTTPTelegramTransport,
    callback_from_update,
    chat_id_of,
    describe,
    text_from_update,
)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _FakeUrlopen:
    # Stands in for the one real system boundary: the Telegram HTTP call. Each
    # queued item is either a response payload (dict) or an exception to raise.
    def __init__(self) -> None:
        self._queue: list[dict | Exception] = []
        self.calls: list[tuple[str, dict, float | None]] = []

    def queue(self, *items: dict | Exception) -> None:
        self._queue.extend(items)

    def __call__(self, req: object, timeout: float | None = None) -> _FakeResponse:
        payload = json.loads(req.data.decode("utf-8"))  # type: ignore[attr-defined]
        self.calls.append((req.full_url, payload, timeout))  # type: ignore[attr-defined]
        if not self._queue:
            raise AssertionError("urlopen called more times than responses were queued")
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)


@pytest.fixture
def urlopen(monkeypatch: pytest.MonkeyPatch) -> _FakeUrlopen:
    fake = _FakeUrlopen()
    monkeypatch.setattr(t.urllib.request, "urlopen", fake)
    return fake


def _transport() -> HTTPTelegramTransport:
    return HTTPTelegramTransport("TOKEN", "CHAT")


def test_send_message_posts_text_to_the_right_url_and_returns_id(urlopen: _FakeUrlopen) -> None:
    urlopen.queue({"ok": True, "result": {"message_id": 42}})

    message_id = _transport().send_message("hello")

    assert message_id == 42
    url, payload, _timeout = urlopen.calls[0]
    assert url == "https://api.telegram.org/botTOKEN/sendMessage"
    assert payload == {"chat_id": "CHAT", "text": "hello"}


def test_send_message_addresses_an_explicit_chat(urlopen: _FakeUrlopen) -> None:
    # A tester's reply must go back to the tester, not to the operator.
    urlopen.queue({"ok": True, "result": {"message_id": 1}})

    _transport().send_message("your brief", chat_id="999")

    assert urlopen.calls[0][1]["chat_id"] == "999"


def test_send_message_with_inline_builds_a_callback_keyboard(
    urlopen: _FakeUrlopen,
) -> None:
    urlopen.queue({"ok": True, "result": {"message_id": 7}})

    _transport().send_message("confirm?", inline=((("Allow", "allow"), ("Deny", "deny")),))

    _url, payload, _timeout = urlopen.calls[0]
    assert payload["reply_markup"] == {
        "inline_keyboard": [
            [
                {"text": "Allow", "callback_data": "allow"},
                {"text": "Deny", "callback_data": "deny"},
            ]
        ]
    }


def test_send_message_with_keyboard_builds_a_persistent_reply_keyboard(
    urlopen: _FakeUrlopen,
) -> None:
    urlopen.queue({"ok": True, "result": {"message_id": 3}})

    _transport().send_message("menu", keyboard=(("/idea",), ("/status", "/help")))

    _url, payload, _timeout = urlopen.calls[0]
    assert payload["reply_markup"] == {
        "keyboard": [[{"text": "/idea"}], [{"text": "/status"}, {"text": "/help"}]],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def test_set_my_commands_posts_the_command_descriptions(urlopen: _FakeUrlopen) -> None:
    urlopen.queue({"ok": True, "result": True})

    _transport().set_my_commands((("idea", "File an idea"), ("help", "Show help")))

    url, payload, _timeout = urlopen.calls[0]
    assert url == "https://api.telegram.org/botTOKEN/setMyCommands"
    assert payload == {
        "commands": [
            {"command": "idea", "description": "File an idea"},
            {"command": "help", "description": "Show help"},
        ]
    }


def test_fetch_updates_returns_every_update_unfiltered(urlopen: _FakeUrlopen) -> None:
    # Chat filtering used to live here. It is now an authorization decision made
    # by ChatAuthorizer, so the transport hands back whatever Telegram sent.
    urlopen.queue(
        {
            "ok": True,
            "result": [
                {"update_id": 1, "message": {"chat": {"id": "OTHER"}, "text": "hi"}},
                {"update_id": 2, "message": {"chat": {"id": "CHAT"}, "text": "mine"}},
            ],
        }
    )

    updates = _transport().fetch_updates(offset=None, timeout=1)

    assert [u["update_id"] for u in updates] == [1, 2]
    url, payload, sock_timeout = urlopen.calls[0]
    assert url == "https://api.telegram.org/botTOKEN/getUpdates"
    assert "offset" not in payload
    # The client socket must outlive Telegram's server-side long poll.
    assert sock_timeout is not None and sock_timeout > 1


def test_fetch_updates_passes_offset_when_given(urlopen: _FakeUrlopen) -> None:
    urlopen.queue({"ok": True, "result": []})

    _transport().fetch_updates(offset=42, timeout=1)

    _url, payload, _timeout = urlopen.calls[0]
    assert payload["offset"] == 42


def test_fetch_updates_returns_empty_list_on_network_error(urlopen: _FakeUrlopen) -> None:
    urlopen.queue(urllib.error.URLError("connection refused"))

    assert _transport().fetch_updates(offset=None, timeout=1) == []


def test_fetch_updates_returns_empty_list_on_socket_timeout(urlopen: _FakeUrlopen) -> None:
    urlopen.queue(TimeoutError("read timed out"))

    assert _transport().fetch_updates(offset=None, timeout=1) == []


def test_chat_id_of_reads_message_and_callback_updates() -> None:
    assert chat_id_of({"message": {"chat": {"id": 55}}}) == "55"
    assert chat_id_of({"callback_query": {"message": {"chat": {"id": 66}}}}) == "66"


def test_chat_id_of_returns_none_when_absent() -> None:
    assert chat_id_of({"update_id": 1}) is None


def test_callback_from_update_reads_any_callback_data() -> None:
    # The transport does not judge the payload -- routing is the daemon's job.
    for value in ("allow", "deny", "idea:12:explore"):
        update = {"callback_query": {"id": "q", "message": {"message_id": 3}, "data": value}}
        cb = callback_from_update(update)
        assert (cb.query_id, cb.message_id, cb.data) == ("q", 3, value)


def test_callback_from_update_rejects_non_callback_updates() -> None:
    assert callback_from_update({"message": {"text": "hi"}}) is None


def test_callback_from_update_rejects_empty_callback_data() -> None:
    update = {"callback_query": {"id": "q", "message": {"message_id": 3}, "data": ""}}
    assert callback_from_update(update) is None


def test_callback_from_update_rejects_a_missing_message_id_or_query_id() -> None:
    assert callback_from_update({"callback_query": {"id": "q", "data": "allow"}}) is None
    assert (
        callback_from_update({"callback_query": {"message": {"message_id": 3}, "data": "allow"}})
        is None
    )


def test_text_from_update_extracts_plain_message_text() -> None:
    update = {"update_id": 1, "message": {"chat": {"id": "CHAT"}, "text": "/idea x"}}
    assert text_from_update(update) == "/idea x"


def test_text_from_update_returns_none_for_callback_query() -> None:
    update = {
        "update_id": 1,
        "callback_query": {"data": "allow", "message": {"message_id": 9}},
    }
    assert text_from_update(update) is None


def test_describe_includes_http_error_body() -> None:
    err = urllib.error.HTTPError(url="u", code=403, msg="Forbidden", hdrs=None, fp=None)
    err.read = lambda: b'{"description":"bot was blocked"}'  # type: ignore[method-assign]
    described = describe(err)
    assert "403" in described
    assert "bot was blocked" in described


def test_describe_falls_back_to_repr_for_other_errors() -> None:
    assert describe(ValueError("boom")) == "ValueError('boom')"


def test_answer_callback_query_stops_the_spinner(urlopen: _FakeUrlopen) -> None:
    urlopen.queue({"ok": True, "result": True})

    _transport().answer_callback_query("q123", text="done")

    url, payload, _timeout = urlopen.calls[0]
    assert url == "https://api.telegram.org/botTOKEN/answerCallbackQuery"
    assert payload == {"callback_query_id": "q123", "text": "done"}


def test_answer_callback_query_swallows_a_transport_error(urlopen: _FakeUrlopen) -> None:
    # Cosmetic only: a hung spinner must never undo an action that already ran.
    urlopen.queue(urllib.error.URLError("connection refused"))

    _transport().answer_callback_query("q123")  # does not raise
