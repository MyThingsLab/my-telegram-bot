from __future__ import annotations

import json
import urllib.error

import pytest

from mytelegrambot import transport as t
from mytelegrambot.transport import (
    HTTPTelegramTransport,
    _decision_from_update,
    describe,
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
    # Advancing the clock per call lets poll_decision's deadline loop terminate
    # deterministically without real time passing.
    def __init__(self, clock: _Clock, *, advance_per_call: float = 0.0) -> None:
        self._clock = clock
        self._advance = advance_per_call
        self._queue: list[dict | Exception] = []
        self.calls: list[tuple[str, dict, float | None]] = []

    def queue(self, *items: dict | Exception) -> None:
        self._queue.extend(items)

    def __call__(self, req: object, timeout: float | None = None) -> _FakeResponse:
        payload = json.loads(req.data.decode("utf-8"))  # type: ignore[attr-defined]
        self.calls.append((req.full_url, payload, timeout))  # type: ignore[attr-defined]
        self._clock.advance(self._advance)
        if not self._queue:
            raise AssertionError("urlopen called more times than responses were queued")
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)


class _Clock:
    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(t.time, "monotonic", c)
    return c


@pytest.fixture
def urlopen(monkeypatch: pytest.MonkeyPatch, clock: _Clock) -> _FakeUrlopen:
    fake = _FakeUrlopen(clock)
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


def test_send_message_with_buttons_builds_allow_deny_inline_keyboard(
    urlopen: _FakeUrlopen,
) -> None:
    urlopen.queue({"ok": True, "result": {"message_id": 7}})

    _transport().send_message("confirm?", buttons=("Allow", "Deny"))

    _url, payload, _timeout = urlopen.calls[0]
    assert payload["reply_markup"] == {
        "inline_keyboard": [
            [
                {"text": "Allow", "callback_data": "allow"},
                {"text": "Deny", "callback_data": "deny"},
            ]
        ]
    }


def test_poll_decision_returns_allow_on_matching_callback(urlopen: _FakeUrlopen) -> None:
    urlopen.queue(
        {
            "ok": True,
            "result": [
                {
                    "update_id": 100,
                    "callback_query": {"message": {"message_id": 9}, "data": "allow"},
                }
            ],
        }
    )

    assert _transport().poll_decision(9, timeout=5) == "allow"
    url, _payload, sock_timeout = urlopen.calls[0]
    assert url == "https://api.telegram.org/botTOKEN/getUpdates"
    assert sock_timeout is not None and sock_timeout > 5  # exceeds the long-poll window


def test_poll_decision_ignores_callback_for_a_different_message(
    urlopen: _FakeUrlopen, clock: _Clock
) -> None:
    urlopen._advance = 10  # one poll, then the deadline is blown
    urlopen.queue(
        {
            "ok": True,
            "result": [
                {
                    "update_id": 100,
                    "callback_query": {"message": {"message_id": 999}, "data": "allow"},
                }
            ],
        }
    )

    assert _transport().poll_decision(9, timeout=5) is None


def test_poll_decision_advances_offset_past_already_seen_updates(
    urlopen: _FakeUrlopen,
) -> None:
    urlopen._advance = 1  # keep the deadline far off so a second poll happens
    urlopen.queue(
        {"ok": True, "result": [{"update_id": 5, "message": {"text": "not a callback"}}]},
        {
            "ok": True,
            "result": [
                {
                    "update_id": 6,
                    "callback_query": {"message": {"message_id": 9}, "data": "deny"},
                }
            ],
        },
    )

    assert _transport().poll_decision(9, timeout=30) == "deny"
    assert "offset" not in urlopen.calls[0][1]  # first poll has no offset
    assert urlopen.calls[1][1]["offset"] == 6  # second poll confirms update_id 5


def test_poll_decision_fails_closed_on_network_error(urlopen: _FakeUrlopen) -> None:
    urlopen.queue(urllib.error.URLError("connection refused"))

    assert _transport().poll_decision(9, timeout=5) is None


def test_poll_decision_fails_closed_on_socket_timeout(urlopen: _FakeUrlopen) -> None:
    urlopen.queue(TimeoutError("read timed out"))

    assert _transport().poll_decision(9, timeout=5) is None


def test_poll_decision_returns_none_when_deadline_passes_with_no_reply(
    urlopen: _FakeUrlopen,
) -> None:
    urlopen._advance = 10
    urlopen.queue({"ok": True, "result": []})

    assert _transport().poll_decision(9, timeout=5) is None


def test_decision_from_update_accepts_allow_and_deny() -> None:
    for value in ("allow", "deny"):
        update = {"callback_query": {"message": {"message_id": 3}, "data": value}}
        assert _decision_from_update(update, 3) == value


def test_decision_from_update_rejects_non_callback_updates() -> None:
    assert _decision_from_update({"message": {"text": "hi"}}, 3) is None


def test_decision_from_update_rejects_wrong_message_id() -> None:
    update = {"callback_query": {"message": {"message_id": 4}, "data": "allow"}}
    assert _decision_from_update(update, 3) is None


def test_decision_from_update_rejects_unknown_callback_data() -> None:
    update = {"callback_query": {"message": {"message_id": 3}, "data": "maybe"}}
    assert _decision_from_update(update, 3) is None


def test_describe_includes_http_error_body() -> None:
    err = urllib.error.HTTPError(url="u", code=403, msg="Forbidden", hdrs=None, fp=None)
    err.read = lambda: b'{"description":"bot was blocked"}'  # type: ignore[method-assign]
    described = describe(err)
    assert "403" in described
    assert "bot was blocked" in described


def test_describe_falls_back_to_repr_for_other_errors() -> None:
    assert describe(ValueError("boom")) == "ValueError('boom')"
