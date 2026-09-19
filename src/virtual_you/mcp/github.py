"""Read-only GitHub access: Protocol, fake, and optional REST client."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Protocol, Sequence, Union
from urllib.parse import quote

from virtual_you.mcp.oauth import load_token

try:
    import httpx
except ImportError:
    httpx = None

GITHUB_REPO_ENV = "VIRTUAL_YOU_GITHUB_REPO"
GITHUB_TOKEN_ENV = "GITHUB_TOKEN"
DATA_DIR_ENV = "VIRTUAL_YOU_DATA_DIR"
DEFAULT_TIMEOUT = 10.0
PathLike = Union[str, Path]


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    state: str
    title: str
    head_sha: str
    merged: bool = False


@dataclass(frozen=True)
class CheckRun:
    name: str
    conclusion: str
    url: str
    sha: str
    event_id: str = ""


@dataclass(frozen=True)
class Review:
    review_id: str
    state: str
    submitted_at: str
    sha: str
    url: str = ""


@dataclass(frozen=True)
class Issue:
    number: int
    url: str
    title: str
    state: str


@dataclass(frozen=True)
class GitHubSnapshot:
    sha: str
    commit_url: str = ""
    commit_time: str = ""
    pull_requests: List[PullRequest] = field(default_factory=list)
    checks: List[CheckRun] = field(default_factory=list)
    reviews: List[Review] = field(default_factory=list)
    issues: List[Issue] = field(default_factory=list)


class GitHubClient(Protocol):
    def snapshot_for_sha(self, sha: str) -> Optional[GitHubSnapshot]:
        """Return PR/review/issue/CI facts for one SHA, or None on miss/error."""


class FakeGitHubClient:
    """In-memory client for tests. Does not perform HTTP."""

    def __init__(self, snapshots: Optional[Sequence[GitHubSnapshot]] = None) -> None:
        self.snapshots = list(snapshots or [])
        self.calls: List[str] = []

    def snapshot_for_sha(self, sha: str) -> Optional[GitHubSnapshot]:
        self.calls.append(sha)
        needle = sha.lower()
        for item in self.snapshots:
            if item.sha.lower().startswith(needle) or needle.startswith(
                item.sha.lower()
            ):
                return item
        return None


class RestGitHubClient:
    """Read-only GitHub REST for one repo. Never dispatches workflows."""

    def __init__(
        self,
        repo: str,
        token: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        http_get=None,
    ) -> None:
        if "/" not in repo:
            raise ValueError("VIRTUAL_YOU_GITHUB_REPO must be owner/name")
        self.repo = repo.strip("/")
        self.token = token
        self.timeout = timeout
        self._http_get = http_get

    @classmethod
    def from_env(
        cls,
        environ,
        data_directory: Optional[PathLike] = None,
    ) -> Optional["RestGitHubClient"]:
        repo = (environ.get(GITHUB_REPO_ENV) or "").strip()
        token = (environ.get(GITHUB_TOKEN_ENV) or "").strip()
        if not token:
            stored_root = data_directory
            if stored_root is None:
                configured = (environ.get(DATA_DIR_ENV) or "").strip()
                stored_root = Path(configured) if configured else Path.home() / ".virtual-you"
            token = load_token(stored_root)
        if not repo or not token:
            return None
        return cls(repo, token)

    def snapshot_for_sha(self, sha: str) -> Optional[GitHubSnapshot]:
        if not sha:
            return None
        commit = self._get("/repos/{}/commits/{}".format(self.repo, quote(sha)))
        if commit is None:
            return None
        full_sha = str(commit.get("sha") or sha)
        commit_url = str((commit.get("html_url") or ""))
        commit_time = str(
            ((commit.get("commit") or {}).get("committer") or {}).get("date") or ""
        )
        pulls = self._pulls_for_sha(full_sha)
        checks = self._checks_for_sha(full_sha)
        reviews: List[Review] = []
        issues: List[Issue] = []
        for pull in pulls:
            reviews.extend(self._reviews(pull.number, full_sha))
            issues.extend(self._issues_for_pull(pull))
        return GitHubSnapshot(
            sha=full_sha,
            commit_url=commit_url,
            commit_time=commit_time,
            pull_requests=pulls,
            checks=checks,
            reviews=reviews,
            issues=issues,
        )

    def _pulls_for_sha(self, sha: str) -> List[PullRequest]:
        payload = self._get("/repos/{}/commits/{}/pulls".format(self.repo, quote(sha)))
        if not isinstance(payload, list):
            return []
        result: List[PullRequest] = []
        for item in payload[:3]:
            if not isinstance(item, dict):
                continue
            head = item.get("head") or {}
            result.append(
                PullRequest(
                    number=int(item.get("number") or 0),
                    url=str(item.get("html_url") or ""),
                    state="merged" if item.get("merged_at") else str(item.get("state") or "open"),
                    title=str(item.get("title") or ""),
                    head_sha=str(head.get("sha") or sha),
                    merged=bool(item.get("merged_at")),
                )
            )
        return result

    def _checks_for_sha(self, sha: str) -> List[CheckRun]:
        payload = self._get(
            "/repos/{}/commits/{}/check-runs".format(self.repo, quote(sha)),
            accept="application/vnd.github+json",
        )
        runs = (payload or {}).get("check_runs") if isinstance(payload, dict) else None
        if not isinstance(runs, list):
            return []
        result: List[CheckRun] = []
        for item in runs[:20]:
            if not isinstance(item, dict):
                continue
            html = item.get("html_url") or ""
            result.append(
                CheckRun(
                    name=str(item.get("name") or "check"),
                    conclusion=str(item.get("conclusion") or item.get("status") or ""),
                    url=str(html),
                    sha=sha,
                    event_id="check:{}".format(item.get("id") or ""),
                )
            )
        return result

    def _reviews(self, number: int, sha: str) -> List[Review]:
        if number <= 0:
            return []
        payload = self._get(
            "/repos/{}/pulls/{}/reviews".format(self.repo, number)
        )
        if not isinstance(payload, list):
            return []
        result: List[Review] = []
        for item in payload[-10:]:
            if not isinstance(item, dict):
                continue
            result.append(
                Review(
                    review_id=str(item.get("id") or ""),
                    state=str(item.get("state") or ""),
                    submitted_at=str(item.get("submitted_at") or ""),
                    sha=str(item.get("commit_id") or sha),
                    url=str(item.get("html_url") or ""),
                )
            )
        return result

    def _issues_for_pull(self, pull: PullRequest) -> List[Issue]:
        if pull.number <= 0:
            return []
        payload = self._get("/repos/{}/issues/{}".format(self.repo, pull.number))
        if not isinstance(payload, dict):
            return []
        labels = payload.get("labels") or []
        titled = str(payload.get("title") or pull.title)
        return [
            Issue(
                number=pull.number,
                url=str(payload.get("html_url") or pull.url),
                title=titled,
                state=str(payload.get("state") or pull.state),
            )
        ] if labels or titled else []

    def _get(self, path: str, accept: str = "application/vnd.github+json"):
        getter = self._http_get
        if getter is None:
            getter = _httpx_get
        return getter(
            "https://api.github.com" + path,
            headers={
                "Accept": accept,
                "Authorization": "Bearer {}".format(self.token),
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=self.timeout,
        )


def _httpx_get(url: str, headers: dict, timeout: float):
    if httpx is None:
        return None
    try:
        response = httpx.get(url, headers=headers, timeout=timeout)
        if response.status_code in (401, 403, 404):
            return None
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError:
        return None
