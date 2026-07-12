from __future__ import annotations

import json
import urllib.error

import pytest

from mytelegrambot import transport as t
from mytelegrambot.transport import (
    HTTPTelegramTransport,
    callback_from_update,
    chat_id_of,
    chunk_for_telegram,
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


def test_send_message_with_reply_to_sets_reply_parameters(urlopen: _FakeUrlopen) -> None:
    urlopen.queue({"ok": True, "result": {"message_id": 8}})

    _transport().send_message("chained", reply_to_message_id=5)

    payload = urlopen.calls[0][1]
    assert payload["reply_parameters"] == {"message_id": 5, "allow_sending_without_reply": True}


def test_send_message_without_reply_to_omits_reply_parameters(urlopen: _FakeUrlopen) -> None:
    urlopen.queue({"ok": True, "result": {"message_id": 9}})

    _transport().send_message("standalone")

    assert "reply_parameters" not in urlopen.calls[0][1]


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


# ---------------------------------------------------------------- chunking
#
# Telegram hard-rejects text past 4096 chars. Two things this tool relays are
# unbounded -- an Engine-composed brief and a digest of an arbitrarily long
# ledger backlog -- so the cap has to be handled by splitting, not truncating:
# the tail of a brief is the part the human waited a whole Engine call for.


def test_chunk_leaves_a_short_text_as_one_message() -> None:
    assert chunk_for_telegram("hello") == ["hello"]


def test_chunk_splits_on_a_paragraph_boundary() -> None:
    text = "a" * 3000 + "\n\n" + "b" * 3000

    chunks = chunk_for_telegram(text)

    assert chunks == ["a" * 3000, "b" * 3000]
    assert all(len(c) <= 4096 for c in chunks)


def test_chunk_falls_back_to_a_line_boundary_when_no_paragraph_break_fits() -> None:
    text = "\n".join("x" * 500 for _ in range(20))  # 20 lines, no blank lines

    chunks = chunk_for_telegram(text)

    assert len(chunks) > 1
    assert all(len(c) <= 4096 for c in chunks)
    assert all(set(c) <= {"x", "\n"} for c in chunks)  # no line was cut mid-run


def test_chunk_hard_cuts_a_single_oversized_line() -> None:
    # Nothing to split on: the cap still has to be respected.
    chunks = chunk_for_telegram("y" * 10000)

    assert all(len(c) <= 4096 for c in chunks)
    assert "".join(chunks) == "y" * 10000


def test_chunk_loses_nothing_it_was_given() -> None:
    text = "\n\n".join(f"paragraph {i} " + "z" * 400 for i in range(30))

    assert "".join(chunk_for_telegram(text)).replace("\n", "") == text.replace("\n", "")


# ---------------------------------------------------------------- markdown


def test_send_message_asks_for_markdown_when_told_to(urlopen: _FakeUrlopen) -> None:
    urlopen.queue({"result": {"message_id": 5}})

    _transport().send_message("*bold*", markdown=True)

    _url, payload, _timeout = urlopen.calls[0]
    assert payload["parse_mode"] == "Markdown"


def test_send_message_resends_unformatted_when_telegram_rejects_the_entities(
    urlopen: _FakeUrlopen,
) -> None:
    # Most Markdown this tool sends was composed by an Engine, not by us: a stray
    # `*` is a malformed entity and Telegram 400s the whole message. Losing the
    # styling is fine; losing the brief the human waited for is not.
    urlopen.queue(
        urllib.error.HTTPError("url", 400, "Bad Request: can't parse entities", {}, None),
        {"result": {"message_id": 7}},
    )

    message_id = _transport().send_message("an unbalanced * brief", markdown=True)

    assert message_id == 7
    first, second = urlopen.calls
    assert first[1]["parse_mode"] == "Markdown"
    assert "parse_mode" not in second[1]
    assert second[1]["text"] == "an unbalanced * brief"


def test_send_message_does_not_retry_a_plain_text_failure(urlopen: _FakeUrlopen) -> None:
    # Only a parse failure is worth a second attempt; a plain send that 400s has
    # some other problem and must surface, not be silently sent twice.
    urlopen.queue(urllib.error.HTTPError("url", 400, "Bad Request", {}, None))

    with pytest.raises(urllib.error.HTTPError):
        _transport().send_message("plain")

    assert len(urlopen.calls) == 1


def test_send_message_does_not_swallow_a_non_400_markdown_failure(urlopen: _FakeUrlopen) -> None:
    urlopen.queue(urllib.error.HTTPError("url", 429, "Too Many Requests", {}, None))

    with pytest.raises(urllib.error.HTTPError):
        _transport().send_message("*bold*", markdown=True)

    assert len(urlopen.calls) == 1


# ---------------------------------------------------------------- chat action


def test_send_chat_action_posts_the_action(urlopen: _FakeUrlopen) -> None:
    urlopen.queue({"result": True})

    _transport().send_chat_action("typing", chat_id="42")

    url, payload, _timeout = urlopen.calls[0]
    assert url.endswith("/sendChatAction")
    assert payload == {"chat_id": "42", "action": "typing"}


def test_send_chat_action_swallows_a_network_error(urlopen: _FakeUrlopen) -> None:
    # Purely cosmetic: a missing "typing…" must never take down a command that is
    # otherwise working.
    urlopen.queue(urllib.error.URLError("down"))

    _transport().send_chat_action("typing")


def test_clear_inline_keyboard_strips_the_buttons_off_an_answered_prompt(
    urlopen: _FakeUrlopen,
) -> None:
    # Without this an Allow/Deny prompt keeps its buttons forever and reads as still
    # pending, so a human who already tapped cannot tell their tap did anything.
    urlopen.queue({"result": True})

    _transport().clear_inline_keyboard(77, chat_id="42")

    url, payload, _timeout = urlopen.calls[0]
    assert url.endswith("/editMessageReplyMarkup")
    assert payload == {"chat_id": "42", "message_id": 77}
    # No reply_markup at all: that is what removes the keyboard.
    assert "reply_markup" not in payload


def test_clear_inline_keyboard_swallows_a_network_error(urlopen: _FakeUrlopen) -> None:
    # The decision is already durable in the ledger and a separate `ask` process is
    # unblocking on it. Cosmetics must never be able to undo that.
    urlopen.queue(urllib.error.URLError("down"))

    _transport().clear_inline_keyboard(77)


def test_answer_callback_query_can_demand_a_modal(urlopen: _FakeUrlopen) -> None:
    # Without show_alert, Telegram renders the text as a banner that auto-dismisses
    # in about a second -- easy to miss entirely, which is exactly what happened when
    # the first person to approve a merge from their phone saw nothing at all.
    urlopen.queue({"result": True})

    _transport().answer_callback_query("q1", text="Allowed ✓", alert=True)

    _url, payload, _timeout = urlopen.calls[0]
    assert payload["show_alert"] is True
    assert payload["text"] == "Allowed ✓"


def test_answer_callback_query_stays_a_toast_by_default(urlopen: _FakeUrlopen) -> None:
    urlopen.queue({"result": True})

    _transport().answer_callback_query("q1")

    _url, payload, _timeout = urlopen.calls[0]
    assert "show_alert" not in payload


def test_edit_message_text_records_the_decision_and_drops_the_buttons(
    urlopen: _FakeUrlopen,
) -> None:
    urlopen.queue({"result": True})

    _transport().edit_message_text(77, "Action: pr-merge\n\n✅ You allowed this.", chat_id="42")

    url, payload, _timeout = urlopen.calls[0]
    assert url.endswith("/editMessageText")
    assert payload["message_id"] == 77
    assert "You allowed this" in payload["text"]
    # No reply_markup: that is what removes the keyboard in the same call.
    assert "reply_markup" not in payload


def test_edit_message_text_swallows_a_network_error(urlopen: _FakeUrlopen) -> None:
    urlopen.queue(urllib.error.URLError("down"))

    _transport().edit_message_text(77, "text")


def test_callback_carries_the_prompts_own_words() -> None:
    # So an answered prompt can be rewritten to record the decision without this tool
    # composing any prose about it.
    callback = callback_from_update(
        {
            "callback_query": {
                "id": "q1",
                "data": "allow",
                "message": {"message_id": 77, "chat": {"id": "c"}, "text": "Action: pr-merge"},
            }
        }
    )

    assert callback.message_text == "Action: pr-merge"
