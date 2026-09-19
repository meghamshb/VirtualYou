from unittest.mock import Mock

import pytest
from slack_sdk.errors import SlackApiError
from slack_sdk.web import SlackResponse

from virtualyou_workflow.config import SlackSettings
from virtualyou_workflow.history import HistoryCollector, RetryLater


def history_client():
    client = Mock()
    client.auth_test.return_value = {"user_id": "UOWNER", "team_id": "TTEAM"}
    client.conversations_list.return_value = {
        "channels": [{"id": "DHUMAN", "is_im": True, "user": "UFRIEND"}],
        "response_metadata": {},
    }
    return client


def messages(count=20, start=100):
    return [
        {
            "user": "UOWNER",
            "text": f"Hi, I reviewed the task and sent update {i}. Thanks.",
            "ts": f"{start + i}.000001",
        }
        for i in range(count)
    ]


def test_collects_latest_twenty_owner_messages_only_and_redacts():
    client = history_client()
    source = messages(25)
    source[-1]["text"] += " api_key=sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    source += [
        {"user": "UFRIEND", "text": "Other person's private content", "ts": "999.0"},
        {"user": "UOWNER", "bot_id": "BBOT", "text": "bot content", "ts": "1000.0"},
        {"user": "UOWNER", "subtype": "message_changed", "text": "edit event", "ts": "1001.0"},
    ]
    client.conversations_history.return_value = {"messages": source}
    progress = []
    result = HistoryCollector(SlackSettings("UOWNER", "TTEAM")).collect(
        client, "UFRIEND", {}, progress.append
    )
    assert len(result) == 20 and "update 24" in result[0]
    assert "[REDACTED]" in result[0]
    assert "sk-proj-" not in str(progress)
    assert "Other person's" not in str(progress) and "bot content" not in str(progress)
    client.conversations_open.assert_not_called()


def test_paginates_dm_list_and_history_until_enough_own_examples():
    client = history_client()
    client.conversations_list.side_effect = [
        {"channels": [], "response_metadata": {"next_cursor": "list-next"}},
        {"channels": [{"id": "DHUMAN", "is_im": True, "user": "UFRIEND"}]},
    ]
    client.conversations_history.side_effect = [
        {"messages": messages(8, 200), "response_metadata": {"next_cursor": "history-next"}},
        {"messages": messages(12, 100)},
    ]
    result = HistoryCollector(SlackSettings("UOWNER", "TTEAM")).collect(
        client, "UFRIEND", {}, lambda p: None
    )
    assert len(result) == 20
    assert client.conversations_list.call_args.kwargs["cursor"] == "list-next"
    assert client.conversations_history.call_args.kwargs["cursor"] == "history-next"


def test_rate_limit_resumes_from_sanitized_checkpoint():
    client = history_client()
    response = SlackResponse(
        client=client,
        http_verb="GET",
        api_url="test",
        req_args={},
        data={"ok": False, "error": "ratelimited"},
        headers={"Retry-After": "61"},
        status_code=429,
    )
    client.conversations_history.side_effect = [
        {"messages": messages(10, 200), "response_metadata": {"next_cursor": "resume"}},
        SlackApiError("limited", response),
    ]
    saved = []
    collector = HistoryCollector(SlackSettings("UOWNER", "TTEAM"))
    with pytest.raises(RetryLater) as error:
        collector.collect(client, "UFRIEND", {}, lambda p: saved.append(dict(p)))
    assert error.value.seconds == 61
    client.conversations_history.side_effect = None
    client.conversations_history.return_value = {"messages": messages(10, 100)}
    result = collector.collect(client, "UFRIEND", saved[-1], lambda p: None)
    assert len(result) == 20
    assert client.conversations_history.call_args.kwargs["cursor"] == "resume"


@pytest.mark.parametrize(
    "identity",
    [
        {"user_id": "UOTHER", "team_id": "TTEAM"},
        {"user_id": "UOWNER", "team_id": "TOTHER"},
        {"user_id": "UOWNER", "team_id": "TTEAM", "bot_id": "BOTHER"},
    ],
)
def test_wrong_account_or_bot_token_is_rejected(identity):
    client = history_client()
    client.auth_test.return_value = identity
    from virtual_you.backend.errors import ServiceError

    with pytest.raises(ServiceError, match="Authorize your own"):
        HistoryCollector(SlackSettings("UOWNER", "TTEAM")).collect(
            client, "UFRIEND", {}, lambda p: None
        )
    client.conversations_history.assert_not_called()


@pytest.mark.parametrize("count", [0, 1, 9, 10, 20])
def test_short_history_uses_all_available_owner_examples(count):
    client = history_client()
    client.conversations_history.return_value = {"messages": messages(count)}
    result = HistoryCollector(SlackSettings("UOWNER", "TTEAM")).collect(
        client, "UFRIEND", {}, lambda p: None
    )
    assert len(result) == count
    assert len(set(result)) == count
