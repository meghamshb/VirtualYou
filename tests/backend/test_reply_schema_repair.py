import asyncio
import json

import pytest

from virtual_you.backend.errors import ServiceError
from virtual_you.contracts.reporting import RetrievalRequest


def run_reply(client, record, responses):
    client.app.state.retrieval.upsert(record)
    calls = []

    class Model:
        async def generate(self, **kwargs):
            data = json.loads(kwargs["user"])
            calls.append({"data": data, "schema": kwargs["schema"]})
            return responses[len(calls) - 1](data)

    engine = client.app.state.engine
    engine.provider = Model()
    return engine, calls


def valid(data):
    source = next(item for item in data["evidence"] if item["field"] == "end_state")
    return {
        "paragraphs": [
            {"text": source["text"], "citations": [{"evidence_id": source["evidence_id"]}]}
        ],
        "search_query": "",
    }


def wrong_citation_shape(data):
    answer = valid(data)
    answer["paragraphs"][0]["citations"] = [answer["paragraphs"][0]["citations"][0]["evidence_id"]]
    return answer


def ask(engine):
    return asyncio.run(engine.reply(question="What changed?", scope=RetrievalRequest(), style={}))


def test_schema_failure_retries_same_evidence_and_schema_before_review(client, record):
    engine, calls = run_reply(client, record, [wrong_citation_shape, valid])
    result = ask(engine)
    assert result["model_calls"] == len(calls) == 2
    assert calls[0]["schema"] == calls[1]["schema"]
    assert calls[0]["data"]["evidence"] == calls[1]["data"]["evidence"]
    feedback = calls[1]["data"]["validation_feedback"]
    assert "invalid_reply" in feedback and "model_type" in feedback
    assert '["paragraphs", 0, "citations", 0]' in feedback
    assert calls[1]["data"]["search_available"] is False
    assert result["paragraphs"][0]["citations"]
    assert not client.app.state.store.list_drafts()


def test_schema_feedback_does_not_echo_rejected_values_or_unknown_property_names(client, record):
    private_value = "PRIVATE_REJECTED_MODEL_TEXT"
    private_key = "PRIVATE_UNEXPECTED_PROPERTY"

    def invalid(data):
        result = valid(data)
        result["paragraphs"][0]["citations"] = [private_value]
        result[private_key] = private_value
        return result

    engine, calls = run_reply(client, record, [invalid, valid])
    ask(engine)
    feedback = calls[1]["data"]["validation_feedback"]
    assert private_value not in feedback and private_key not in feedback
    assert "unexpected_field" in feedback and "extra_forbidden" in feedback


def test_repeated_schema_failure_stops_after_single_repair(client, record):
    engine, calls = run_reply(client, record, [wrong_citation_shape, wrong_citation_shape])
    with pytest.raises(ServiceError) as error:
        ask(engine)
    assert error.value.code == "invalid_reply"
    assert len(calls) == 2
    assert error.value.__cause__ is None
    assert not client.app.state.store.list_drafts()


@pytest.mark.parametrize("schema_first", [True, False])
def test_schema_repair_shares_budget_with_grounding_repair(client, record, schema_first):
    def unsupported(data):
        result = valid(data)
        result["paragraphs"][0]["citations"] = []
        return result

    responses = (
        [wrong_citation_shape, unsupported] if schema_first else [unsupported, wrong_citation_shape]
    )
    engine, calls = run_reply(client, record, responses)
    with pytest.raises(ServiceError) as error:
        ask(engine)
    assert error.value.code == ("unsupported_claim" if schema_first else "invalid_reply")
    assert len(calls) == 2
    assert not client.app.state.store.list_drafts()


def test_search_then_schema_repair_has_at_most_three_calls(client, record):
    def search(data):
        return {
            "paragraphs": [{"text": "Not recorded in the selected activity.", "citations": []}],
            "search_query": "callback",
        }

    engine, calls = run_reply(client, record, [search, wrong_citation_shape, valid])
    result = ask(engine)
    assert result["model_calls"] == len(calls) == 3
    assert calls[1]["data"]["evidence"] == calls[2]["data"]["evidence"]
    assert calls[2]["data"]["search_available"] is False


def test_uncited_intro_is_repaired_without_weakening_grounding(client, record):
    def intro_then_fact(data):
        result = valid(data)
        result["paragraphs"].insert(0, {"text": "Recent changes include:", "citations": []})
        return result

    engine, calls = run_reply(client, record, [intro_then_fact, valid])
    result = ask(engine)
    assert result["model_calls"] == 2
    assert "unsupported_claim" in calls[1]["data"]["validation_feedback"]
    assert "Omit standalone introductory/closing filler" in calls[1]["data"]["validation_feedback"]
    assert all(paragraph["citations"] for paragraph in result["paragraphs"])
    assert result["text"] == record["end_state"]
    assert not client.app.state.store.list_drafts()


def test_trusted_prompt_makes_citation_object_and_intro_contract_explicit(client, record):
    client.app.state.retrieval.upsert(record)

    class Model:
        async def generate(self, **kwargs):
            system = kwargs["system"]
            assert '{"evidence_id":"S1"}' in system
            assert "Never return citations as strings" in system
            assert "Do not return an uncited introduction" in system
            return valid(json.loads(kwargs["user"]))

    client.app.state.engine.provider = Model()
    assert ask(client.app.state.engine)["paragraphs"][0]["citations"]
