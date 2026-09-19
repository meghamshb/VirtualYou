from pathlib import Path

from virtual_you.ingest.env_secrets import discover_env_secrets


def test_loads_sensitive_dotenv_values_and_ignores_ports(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=sk-test-51Qx9ZaBcDeFgHiJkLmNoPqR",
                'NEXTAUTH_SECRET="plain-local-app-secret-value"',
                "PORT=3000",
                "DEBUG=true",
                "# COMMENT=ignore",
                "export DB_PASSWORD=hunter2",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    secrets = discover_env_secrets(tmp_path)

    assert "plain-local-app-secret-value" in secrets
    assert "sk-test-51Qx9ZaBcDeFgHiJkLmNoPqR" in secrets
    assert "hunter2" in secrets
    assert "3000" not in secrets
    assert "true" not in secrets
