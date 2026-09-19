import asyncio

import pytest
from virtual_you.backend.errors import ServiceError

from virtualyou_workflow.history import slack_call, slack_history_bound

from .test_dm_inbox import setup_inbox
from .test_dm_replies import make_monitor
from .test_self_test import event, rows, setup_test


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1789861716.9633222", "1789861716.963322"),
        ("1789861716.9999999", "1789861716.999999"),
        ("1789861716.000001", "1789861716.000001"),
        ("1789861716", "1789861716.000000"),
        ("0", "0.000000"),
    ],
)
def test_history_bound_floors_to_six_decimal_places(value, expected):
    assert slack_history_bound(value) == expected


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "invalid"])
def test_invalid_checkpoint_is_rejected_without_exposing_value(value):
    with pytest.raises(ServiceError) as error:
        slack_history_bound(value)
    assert error.value.code == "invalid_history_timestamp"
    assert error.value.message == "The Slack history checkpoint is invalid."


def test_wire_bounds_are_normalized_without_changing_message_identifiers():
    request = {
        "oldest": "1789861716.9633222",
        "latest": "1789861718.9999999",
        "ts": "1789861717.1234567",
        "thread_ts": "1789861717.7654321",
    }
    result = slack_call(lambda **kwargs: kwargs, **request)
    assert result["oldest"] == "1789861716.963322"
    assert result["latest"] == "1789861718.999999"
    assert result["ts"] == request["ts"] and result["thread_ts"] == request["thread_ts"]
    assert request["oldest"] == "1789861716.9633222"


def strict_slack_history(received, requests):
    def history(**kwargs):
        requests.append(kwargs)
        if len(kwargs["oldest"].partition(".")[2]) > 6:
            return {"messages": []}  # Reproduce Slack's observed silent empty response.
        return {"messages": [received]}

    return history


def test_selected_dm_recovers_from_high_precision_persisted_checkpoint(tmp_path):
    monitor, calls = make_monitor(tmp_path)
    checkpoint = {"oldest": "1789861716.9633222", "cursor": "", "newest": "0"}
    monitor.c.backend.store.set_metadata(monitor.key, checkpoint)
    requests = []
    received = {"user": "UFRIEND", "ts": "2000000001.000123", "text": "Current status?"}
    monitor.c.bot().conversations_history = strict_slack_history(received, requests)

    async def run():
        await monitor.poll()
        monitor.next_poll = 0
        await monitor.poll()

    asyncio.run(run())
    assert requests[0]["oldest"] == "1789861716.963322"
    assert len(rows(monitor)) == 1 and rows(monitor)[0]["source_ts"] == received["ts"]
    assert monitor.c.backend.store.metadata(monitor.key)["oldest"] == received["ts"]
    assert not calls


def test_all_dm_inbox_recovers_from_high_precision_persisted_checkpoint(tmp_path):
    inbox, _, _, _, calls = setup_inbox(tmp_path)
    inbox.c.backend.store.set_metadata(
        "dm_inbox_poll:DHUMAN", {"oldest": "1789861716.9633222", "cursor": "", "newest": "0"}
    )
    requests = []
    received = {"user": "UFRIEND", "ts": "2000000001.000123", "text": "Current status?"}
    inbox.c.bot().conversations_history = strict_slack_history(received, requests)

    async def run():
        await inbox.poll_one()
        await inbox.route_one()

    asyncio.run(run())
    assert requests[0]["oldest"] == "1789861716.963322"
    assert len(rows(inbox)) == 1 and rows(inbox)[0]["source_ts"] == received["ts"]
    assert not calls


def test_self_test_recovery_poll_reads_precise_checkpoint_without_signed_event(tmp_path):
    router, normal, calls = setup_test(tmp_path)
    monitor = router.self_test
    monitor.c.backend.store.set_metadata(
        monitor.key, {"oldest": "1789861716.9633222", "cursor": "", "newest": "0"}
    )
    requests = []
    received = event(ts="2000000001.000123")
    normal.c.bot().conversations_history = strict_slack_history(received, requests)
    asyncio.run(monitor.poll())
    assert requests[0]["oldest"] == "1789861716.963322"
    assert len(rows(router)) == 1 and rows(router)[0]["source_ts"] == received["ts"]
    assert not calls
