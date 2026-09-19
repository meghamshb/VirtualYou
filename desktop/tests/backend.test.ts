import { describe, it, expect, vi, afterEach } from "vitest";
import { LocalBackend } from "../electron/backend";
afterEach(() => vi.unstubAllGlobals());
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
  it("keeps keys in main-process headers and never in a UI snapshot", async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return Response.json(
        url.endsWith("/api/status")
          ? {
              provider: "openai:gpt-4o-mini",
              delivery_mode: "live",
              projects: ["virtualyou"],
              heartbeat: {
                state: "healthy",
                finished_at: "2026-09-20T00:00:00Z",
              },
            }
          : [
              {
                id: "draft-1",
                recipient_id: "colleague",
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
    expect(state.drafts[0].target).toBe("slack: D123");
    expect(JSON.stringify(state)).not.toContain("test-secret");
    expect(
      (calls[0].init.headers as Record<string, string>).Authorization,
    ).toContain("test-secret");
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
