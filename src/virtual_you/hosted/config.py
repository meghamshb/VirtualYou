import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


@dataclass
class RelaySettings:
    base_url: str
    encryption_key: str
    data_dir: Path = Path(".virtual-you-hosted")
    clients: dict = field(default_factory=dict)
    openai_key: str = ""
    model: str = "gpt-4o-mini"
    signing_secret: str = ""
    development: bool = False

    def __post_init__(self):
        self.base_url = self.base_url.rstrip("/")
        p = urlparse(self.base_url)
        if (
            p.username
            or p.password
            or p.query
            or p.fragment
            or p.path
            or not p.hostname
            or (
                p.scheme != "https"
                and not (self.development and p.scheme == "http" and p.hostname == "127.0.0.1")
            )
        ):
            raise ValueError("Configure a stable HTTPS origin for the hosted service.")

    @classmethod
    def from_env(cls):
        return cls(
            base_url=os.environ["VY_PUBLIC_URL"],
            encryption_key=os.environ["VY_ENCRYPTION_KEY"],
            data_dir=Path(os.getenv("VY_HOSTED_DATA_DIR", "/data")),
            clients={
                p: (os.getenv(f"{prefix}_CLIENT_ID", ""), os.getenv(f"{prefix}_CLIENT_SECRET", ""))
                for p, prefix in [
                    ("slack", "SLACK"),
                    ("github", "GITHUB"),
                    ("jira", "ATLASSIAN"),
                    ("drive", "GOOGLE"),
                ]
            },
            openai_key=os.getenv("OPENAI_API_KEY", ""),
            signing_secret=os.getenv("SLACK_SIGNING_SECRET", ""),
        )
