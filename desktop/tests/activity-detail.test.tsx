import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { LocalBackend } from "../electron/backend";
import { activityDetailSchema, sourceScanLabel } from "../shared/activity";
import type { ActivityDetail, SourceCount } from "../shared/model";
import {
  RecordedContext,
  SourceBreakdown,
} from "../src/components/ActivityContext";

const detail: ActivityDetail = {
  session_id: "session-display-17",
  source: "codex",
  project_id: "virtualyou",
  started_at: "2026-09-20T01:00:00Z",
  ended_at: "2026-09-20T01:05:00Z",
  ingested_at: "2026-09-20T02:00:00Z",
  summary: "A recorded integration change.",
  start_state: "Review the activity display.",
  end_state: "The recorded files were updated.",
  prompts: [
    "Show the actual source context. token=[REDACTED]",
    "<script>must remain text</script>",
  ],
  reasoning_summary: "Recorded summary from the session.",
  files_changed: [{ path: "activity.ts", operation: "modified" }],
  tool_calls: [
    {
      name: "exec_command",
      input_summary: "npm test",
      result_summary: "Tests passed in this recorded run.",
      status: "succeeded",
      timestamp: null,
    },
  ],
  diffs: ["+ show recorded source context"],
  redacted: true,
  truncated: true,
};

afterEach(() => vi.unstubAllGlobals());

describe("recorded activity context", () => {
  it("fetches an opaque activity id with owner authentication, excluding extra raw fields", async () => {
    const fetcher = vi.fn(async () =>
      Response.json({
        ...detail,
        source_path: "private path",
        raw_log: "private log",
      }),
    );
    vi.stubGlobal("fetch", fetcher);
    const result = await new LocalBackend(
      3000,
      "test-secret-at-least-24-characters",
    ).activityDetail("activity:17");
    expect(fetcher.mock.calls[0]).toEqual([
      "http://127.0.0.1:3000/api/activity/activity%3A17",
      expect.objectContaining({
        method: "GET",
        headers: expect.objectContaining({
          Authorization: "Bearer test-secret-at-least-24-characters",
        }),
      }),
    ]);
    expect(result).toEqual(detail);
    expect(result).not.toHaveProperty("source_path");
    expect(result).not.toHaveProperty("raw_log");
  });

  it("rejects raw session paths and unredacted detail responses", async () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    await expect(
      new LocalBackend(
        3000,
        "test-secret-at-least-24-characters",
      ).activityDetail("/private/session.jsonl"),
    ).rejects.toThrow();
    expect(fetcher).not.toHaveBeenCalled();
    expect(() =>
      activityDetailSchema.parse({ ...detail, redacted: false }),
    ).toThrow();
  });

  it("shows actual normalized evidence and separates work times from index time", () => {
    const html = renderToStaticMarkup(<RecordedContext detail={detail} />);
    for (const text of [
      "Session started",
      "Session ended",
      "Last indexed",
      "session-display-17",
      "virtualyou",
      "Show the actual source context.",
      "[REDACTED]",
      "activity.ts",
      "exec_command",
      "npm test",
      "Tests passed in this recorded run.",
      "+ show recorded source context",
      "Some recorded context is omitted",
      "does not prove that an AI reply used every item",
    ]) {
      expect(html).toContain(text);
    }
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
  });

  it("distinguishes configured empty sources, failures and unconfigured sources with retained records", () => {
    const sources: SourceCount[] = [
      {
        source: "claude",
        configured: true,
        count: 0,
        latest_at: null,
        scan_state: "empty",
        last_scan_at: "2026-09-20T02:00:00Z",
      },
      {
        source: "codex",
        configured: true,
        count: 2,
        latest_at: detail.ended_at,
        scan_state: "failed",
        last_scan_at: "2026-09-20T02:00:00Z",
      },
      {
        source: "git",
        configured: false,
        count: 3,
        latest_at: detail.ended_at,
        scan_state: "not_started",
        last_scan_at: null,
      },
    ];
    expect(sourceScanLabel(sources[0])).toBe("Checked · no activity found");
    expect(sourceScanLabel(sources[1])).toBe("Source needs attention");
    expect(sourceScanLabel(sources[2])).toBe("Not configured for collection");
    const html = renderToStaticMarkup(<SourceBreakdown sources={sources} />);
    for (const text of [
      "Claude Code",
      "Codex",
      "Git",
      "Counts cover all indexed records",
      "Last source check",
      "Latest session",
      "No indexed session",
      "Previously indexed records remain available.",
    ])
      expect(html).toContain(text);
    expect(html).not.toContain("Connected");
    expect(html).toContain('class="source-state" data-state="empty"');
    expect(html).not.toContain('class="source-state empty"');
  });
});
