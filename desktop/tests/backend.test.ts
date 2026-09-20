import { describe, it, expect, vi, afterEach } from "vitest";
import { LocalBackend } from "../electron/backend";
afterEach(() => vi.unstubAllGlobals());
const collection = {
  state: "degraded",
  enabled: true,
  configured: true,
  last_attempt_at: "2026-09-20T00:00:00Z",
  last_success_at: null,
  record_count: 23,
  changed: 1,
  unchanged: 22,
  removed: 0,
  error_count: 1,
  errors: [
    {
      source: "git",
      code: "source_unavailable",
      message: "Check source access.",
    },
  ],
};
const feed = {
  items: [
    {
      id: "activity:1",
      kind: "activity",
      at: "2026-09-20T00:00:00Z",
      title: "Git activity",
      summary: "Recorded change",
      source: "git",
      project_id: "virtualyou",
      files_changed_count: 2,
      tool_calls_count: 0,
    },
    {
      id: "draft-event:1",
      kind: "draft_event",
      at: "2026-09-19T23:00:00Z",
      title: "Draft rejected",
      summary: "Revision 1 · reject",
      draft_id: "draft-1",
    },
  ],
  has_more: true,
  source_counts: [
    {
      source: "git",
      count: 23,
      latest_at: "2026-09-20T00:00:00Z",
      configured: true,
      scan_state: "failed",
      last_scan_at: "2026-09-20T01:00:00Z",
    },
    {
      source: "claude",
      count: 0,
      latest_at: null,
      configured: true,
      scan_state: "empty",
      last_scan_at: "2026-09-20T01:00:00Z",
    },
  ],
  collection,
};
describe("existing backend boundary", () => {
  it("refreshes evidence with the backend's POST contract", async () => {
    const fetcher = vi.fn(async () => Response.json({}));
    vi.stubGlobal("fetch", fetcher);
    const client = new LocalBackend(
      3000,
      "test-secret-with-at-least-24-characters",
    );
    vi.spyOn(client, "snapshot").mockResolvedValue({} as never);
    await client.act({ type: "refresh" });
    expect(fetcher.mock.calls[0]).toEqual([
      "http://127.0.0.1:3000/api/refresh",
      expect.objectContaining({ method: "POST", body: "{}" }),
    ]);
  });
  it.each(["live", "simulation"])(
    "maps %s delivery, keeping keys outside the UI snapshot",
    async (deliveryMode) => {
      const calls: { url: string; init: RequestInit }[] = [];
      vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
        calls.push({ url, init });
        if (url.endsWith("/api/activity?limit=50")) return Response.json(feed);
        if (url.endsWith("/api/personas"))
          return Response.json([
            { recipient_id: "colleague", display_name: "Meghamsh Balantrapu" },
          ]);
        return Response.json(
          url.endsWith("/api/status")
            ? {
                provider: "openai:gpt-4o-mini",
                delivery_mode: deliveryMode,
                projects: ["virtualyou"],
                workflow: { available: true, paused: true },
                integrations: {
                  slack: {
                    status: "configured",
                    enabled: true,
                    verification: "not_checked",
                  },
                },
                heartbeat: {
                  state: "degraded",
                  finished_at: "2026-09-20T00:00:00Z",
                },
              }
            : [
                {
                  id: "draft-1",
                  request: { recipient_id: "colleague" },
                  text: "Recorded change.",
                  status: "pending",
                  revision: 1,
                  destination: { platform: "slack", target: "D123" },
                  evidence: [
                    {
                      evidence_id: "e1",
                      text: "Recorded change.",
                      source: "git",
                    },
                  ],
                },
              ],
        );
      });
      const client = new LocalBackend(
        3000,
        "test-secret-with-at-least-24-characters",
      );
      const state = await client.snapshot();
      expect(state.mode).toBe("local");
      expect(state.health).toBe("ready"); // Reachable API despite a failed source.
      expect(state.collection?.state).toBe("degraded");
      expect(state.collection?.error_count).toBe(1);
      expect(state.activity[0].projectId).toBe("virtualyou");
      expect(state.activity[1].draftId).toBe("draft-1");
      expect(state.activityHasMore).toBe(true);
      expect(state.sourceCounts).toEqual(feed.source_counts);
      expect(state.paused).toBe(true);
      expect(state.integrations.find((i) => i.id === "slack")?.state).toBe(
        "configured",
      );
      expect(state.integrations.find((i) => i.id === "jira")?.state).toBe(
        "unavailable",
      );
      expect(state.deliveryMode).toBe(deliveryMode);
      expect(state.drafts[0].recipient).toBe("Meghamsh Balantrapu");
      expect(state.drafts[0].id).toBe("draft-1");
      expect(state.drafts[0].target).toBe("slack: D123");
      expect(JSON.stringify(state)).not.toContain("test-secret");
      expect(
        (calls[0].init.headers as Record<string, string>).Authorization,
      ).toContain("test-secret");
    },
  );
  it("sends only the requested pause setting to the workflow endpoint", async () => {
    const fetcher = vi.fn(async () =>
      Response.json({ available: true, paused: true }),
    );
    vi.stubGlobal("fetch", fetcher);
    const client = new LocalBackend(
      3000,
      "test-secret-with-at-least-24-characters",
    );
    vi.spyOn(client, "snapshot").mockResolvedValue({} as never);
    await client.act({ type: "pause", value: true });
    expect(fetcher.mock.calls[0]).toEqual([
      "http://127.0.0.1:3000/api/workflow/pause",
      expect.objectContaining({ method: "POST", body: '{"paused":true}' }),
    ]);
  });
  it("does not deliver if approval is rejected by the backend", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      calls.push(url);
      return new Response("", { status: 409 });
    });
    const client = new LocalBackend(
      3000,
      "test-secret-with-at-least-24-characters",
    );
    await expect(
      client.act({
        type: "decision",
        id: "draft-1",
        action: "approve",
        revision: 1,
      }),
    ).rejects.toThrow("409");
    expect(calls).toHaveLength(1);
    expect(calls[0]).toContain("/decision");
  });
  it("does not retry a failed delivery", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      calls.push(url);
      if (url.endsWith("/decision"))
        return Response.json({ status: "approved" });
      throw new Error("uncertain");
    });
    await expect(
      new LocalBackend(3000, "test-secret-with-at-least-24-characters").act({
        type: "decision",
        id: "draft-1",
        action: "approve",
        revision: 1,
      }),
    ).rejects.toThrow();
    expect(calls).toHaveLength(2);
  });
});
