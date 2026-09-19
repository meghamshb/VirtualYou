from typing import Dict, List

import pytest
from pydantic import BaseModel

from virtual_you.ingest.errors import IngestionError, IngestionErrorCode
from virtual_you.ingest.redact import (
    REDACTED,
    assert_safe_serialized,
    contains_secret,
    is_sensitive_key,
    redact_text,
    redact_value,
)

FAKE_OPENAI_TOKEN = "sk-" + "proj-" + "AbCdEfGhIjKlMnOpQrStUvWx"
FAKE_ANTHROPIC_TOKEN = "sk-" + "ant-api03-" + "AbCdEfGhIjKlMnOpQrStUvWx"
FAKE_GITHUB_TOKEN = "github_" + "pat_" + "11AA22BB33CC44DD55EE66FF77"
FAKE_GITHUB_CLASSIC_TOKEN = "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
FAKE_SLACK_TOKEN = "xoxb-" + "123456789012-" + "abcdefghijklmnopqrstuv"
FAKE_AWS_ACCESS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"


class NestedPayload(BaseModel):
    messages: List[str]
    metadata: Dict[str, str]


class Envelope(BaseModel):
    payload: NestedPayload
    note: str


@pytest.mark.parametrize(
    "secret",
    [
        FAKE_OPENAI_TOKEN,
        FAKE_ANTHROPIC_TOKEN,
        FAKE_GITHUB_TOKEN,
        FAKE_GITHUB_CLASSIC_TOKEN,
        FAKE_SLACK_TOKEN,
        FAKE_AWS_ACCESS_KEY,
    ],
)
def test_redacts_provider_token_formats(secret: str) -> None:
    result = redact_text("credential={}".format(secret))

    assert secret not in result
    assert REDACTED in result


@pytest.mark.parametrize(
    "source",
    [
        "api_key='ordinary-but-sensitive-value'",
        'client-secret: "do-not-print-this"',
        "password=hunter2",
        "access_token: token-value-123",
        "OPENAI_API_KEY=not-a-provider-token-value",
        "DB_PASSWORD=hunter2",
        "NEXTAUTH_SECRET=my-app-secret",
        'DATABASE_URL="postgres://example.internal/app"',
    ],
)
def test_redacts_generic_key_value_secrets(source: str) -> None:
    result = redact_text(source)

    assert REDACTED in result
    assert source not in result


@pytest.mark.parametrize(
    ("header", "credential"),
    [
        (
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature",
            "eyJhbGciOiJIUzI1NiJ9.payload.signature",
        ),
        (
            "Proxy-Authorization: Basic dXNlcjpwYXNzd29yZA==",
            "dXNlcjpwYXNzd29yZA==",
        ),
        (
            "use bearer AbCdEf1234567890 for this request",
            "AbCdEf1234567890",
        ),
    ],
)
def test_redacts_authorization_credentials(
    header: str,
    credential: str,
) -> None:
    result = redact_text(header)

    assert REDACTED in result
    assert credential not in result


def test_redacts_complete_pem_private_key() -> None:
    private_key = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEAuJ7b8P4n2Z6q1X9m\n"
        "-----END RSA PRIVATE KEY-----"
    )

    result = redact_text("before\n{}\nafter".format(private_key))

    assert private_key not in result
    assert result == "before\n{}\nafter".format(REDACTED)


def test_redacts_sensitive_url_params_but_keeps_other_params() -> None:
    url = (
        "https://example.test/callback?project=demo&api_key=top-secret-value"
        "&page=2&signature=deadbeef123456"
    )

    result = redact_text(url)

    assert "top-secret-value" not in result
    assert "deadbeef123456" not in result
    assert "project=demo" in result
    assert "page=2" in result
    assert result.count(REDACTED) == 2


def test_redacts_high_entropy_string() -> None:
    secret = "q7Vn2Lm9Kp4Rx8Tc6Yw3Za1Hd5Fs0BjE"

    assert redact_text("session {}".format(secret)) == "session {}".format(REDACTED)
    assert contains_secret(secret)


@pytest.mark.parametrize(
    "ordinary",
    [
        "This is ordinary prose about a project status update.",
        "/Users/alice/projects/virtual-you/src/main.py",
        "src/virtual_you/ingest/redact.py",
        "123e4567-e89b-12d3-a456-426614174000",
        "a_really_long_but_readable_filename.py",
    ],
)
def test_does_not_redact_ordinary_prose_paths_or_ids(ordinary: str) -> None:
    assert redact_text(ordinary) == ordinary
    assert not contains_secret(ordinary)


def test_recursively_redacts_nested_models_dicts_and_lists() -> None:
    openai_key = FAKE_OPENAI_TOKEN
    slack_token = FAKE_SLACK_TOKEN
    model = Envelope(
        payload=NestedPayload(
            messages=["safe", "Authorization: Bearer {}".format(openai_key)],
            metadata={"slack": slack_token},
        ),
        note="ordinary note",
    )

    result = redact_value(model)

    assert isinstance(result, Envelope)
    assert isinstance(result.payload, NestedPayload)
    assert result.payload.messages == ["safe", "Authorization: Bearer [REDACTED]"]
    assert result.payload.metadata["slack"] == REDACTED
    assert result.note == "ordinary note"
    assert openai_key not in result.model_dump_json()
    assert slack_token not in result.model_dump_json()


def test_recursively_preserves_container_shapes() -> None:
    value = {
        "items": ["password=hidden-value", ("safe", "api_key=hidden-key")],
        "labels": {"safe"},
    }

    result = redact_value(value)

    assert isinstance(result, dict)
    assert isinstance(result["items"], list)
    assert isinstance(result["items"][1], tuple)
    assert isinstance(result["labels"], set)
    assert result["items"][0] == "password=[REDACTED]"
    assert result["items"][1][1] == "api_key=[REDACTED]"


def test_redacts_short_secrets_under_sensitive_mapping_keys() -> None:
    value = {
        "password": "short",
        "aws_secret_access_key": "brief",
        "nested": {"token": "tiny"},
    }

    result = redact_value(value)

    assert result == {
        "password": REDACTED,
        "aws_secret_access_key": REDACTED,
        "nested": {"token": REDACTED},
    }
    assert contains_secret(value)


def test_redacts_prefixed_env_mapping_keys() -> None:
    result = redact_value(
        {
            "OPENAI_API_KEY": "not-a-provider-token-value",
            "DB_PASSWORD": "hunter2",
            "PORT": "3000",
        }
    )

    assert result == {
        "OPENAI_API_KEY": REDACTED,
        "DB_PASSWORD": REDACTED,
        "PORT": "3000",
    }


def test_redacts_known_dotenv_values_even_without_the_key_name() -> None:
    secret = "plain-local-app-secret-value"
    result = redact_text(
        "do not repeat {}".format(secret),
        extra_secrets=(secret,),
    )

    assert result == "do not repeat {}".format(REDACTED)
    assert is_sensitive_key("NEXTAUTH_SECRET")
    assert is_sensitive_key("OPENAI_API_KEY")
    assert not is_sensitive_key("PORT")


def test_assert_safe_serialized_accepts_clean_output() -> None:
    assert assert_safe_serialized({"status": "complete", "path": "src/app.py"}) is None


def test_assert_safe_serialized_fails_closed_without_leaking_secret() -> None:
    secret = FAKE_ANTHROPIC_TOKEN

    with pytest.raises(IngestionError) as captured:
        assert_safe_serialized({"message": "credential {}".format(secret)})

    error = captured.value
    assert error.code is IngestionErrorCode.UNSAFE_OUTPUT
    assert secret not in str(error)
    assert secret not in str(error.as_dict())


def test_recursive_cycle_fails_closed_without_values_in_error() -> None:
    value = []
    value.append(value)

    with pytest.raises(IngestionError) as captured:
        redact_value(value)

    assert captured.value.code is IngestionErrorCode.UNSAFE_OUTPUT
    assert "secret" not in str(captured.value).lower()
