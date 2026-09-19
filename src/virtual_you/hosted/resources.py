"""Bounded, read-only resource selection. Selected metadata becomes sanitized evidence."""

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import quote

from virtual_you.contracts.activity import ActivityRecord, TimestampRange
from virtual_you.ingest.redact import redact_value


def choices(oauth, account, provider, parent=""):
    token = oauth.tokens(account, provider)
    headers = {
        "Authorization": "Bearer "
        + (token["authed_user"]["access_token"] if provider == "slack" else token["access_token"])
    }

    def get(url, **params):
        return oauth.json("GET", url, headers=headers, params=params)

    if provider == "github":
        return [
            {"id": x["full_name"], "name": x["full_name"]}
            for x in get("https://api.github.com/user/repos", per_page=100, sort="updated")
        ]
    if provider == "jira":
        if not parent:
            return token["sites"]
        if parent not in {s["id"] for s in token["sites"]}:
            raise ValueError("invalid_site")
        return [
            {"id": parent + ":" + x["key"], "name": x["name"]}
            for x in get(
                f"https://api.atlassian.com/ex/jira/{quote(parent, safe='')}/rest/api/3/project/search",
                maxResults=50,
            )["values"]
        ]
    if provider == "drive":
        rows = get(
            "https://www.googleapis.com/drive/v3/files",
            q="mimeType='application/vnd.google-apps.folder' and trashed=false",
            pageSize=100,
            fields="files(id,name)",
        )["files"]
        return [{"id": x["id"], "name": x["name"]} for x in rows]
    rows = get("https://slack.com/api/users.list", limit=200)["members"]
    return [
        {"id": x["id"], "name": x.get("real_name", x["id"])}
        for x in rows
        if not x.get("is_bot") and not x.get("deleted") and x["id"] != token["user_id"]
    ]


def evidence(oauth, account, selection):
    provider, resource = selection["provider"], selection["resource"]
    token = oauth.tokens(account, provider)
    headers = {"Authorization": "Bearer " + token["access_token"]}
    observed_times = []
    if provider == "github":
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", resource):
            raise ValueError("invalid_repository")
        rows = oauth.json(
            "GET",
            f"https://api.github.com/repos/{resource}/commits",
            headers=headers,
            params={"per_page": 10},
        )
        observed_times = [x["commit"].get("committer", {}).get("date", "") for x in rows]
        texts = [
            f"GitHub {resource}: {x['commit']['message'][:1500]} ({x['html_url']})" for x in rows
        ]
    elif provider == "jira":
        site, key = resource.split(":", 1)
        if site not in {s["id"] for s in token["sites"]} or not re.fullmatch(
            r"[A-Z][A-Z0-9_]*", key
        ):
            raise ValueError("invalid_jira_project")
        rows = oauth.json(
            "GET",
            f"https://api.atlassian.com/ex/jira/{quote(site, safe='')}/rest/api/3/search/jql",
            headers=headers,
            params={
                "jql": f'project = "{key}" ORDER BY updated DESC',
                "maxResults": 10,
                "fields": "summary,status,updated",
            },
        )["issues"]
        observed_times = [x["fields"].get("updated", "") for x in rows]
        texts = [
            f"Jira {x['key']}: {x['fields']['summary']} — {x['fields']['status']['name']}; updated {x['fields']['updated']}"
            for x in rows
        ]
    elif provider == "drive":
        if not re.fullmatch(r"[A-Za-z0-9_-]+", resource):
            raise ValueError("invalid_folder")
        rows = oauth.json(
            "GET",
            "https://www.googleapis.com/drive/v3/files",
            headers=headers,
            params={
                "q": f"'{resource}' in parents and trashed=false",
                "pageSize": 20,
                "fields": "files(id,name,webViewLink,modifiedTime)",
            },
        )["files"]
        observed_times = [x.get("modifiedTime", "") for x in rows]
        texts = [
            f"Drive: {x['name']} updated {x.get('modifiedTime', '')} {x.get('webViewLink', '')}"
            for x in rows
        ]
    else:
        raise ValueError("invalid_source")
    if not texts:
        return None
    times = []
    for value in observed_times:
        try:
            times.append(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            pass
    now = max(times) if times else datetime(1970, 1, 1, tzinfo=timezone.utc)
    return ActivityRecord(
        session_id=hashlib.sha256((provider + resource).encode()).hexdigest(),
        source=provider,
        end_state="\n".join(redact_value(texts)),
        timestamp_range=TimestampRange(started_at=now, ended_at=now),
    ).model_dump(mode="json")
