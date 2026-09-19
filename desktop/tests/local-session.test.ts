import { describe, expect, it, vi } from "vitest";
import {
  applyLocalAction,
  refreshLocalSnapshot,
} from "../electron/local-session";
import { freshWorkspace, transition } from "../shared/preview";
import type { Action, Workspace } from "../shared/model";

const decision: Action = {
  type: "decision",
  id: "draft-1",
  revision: 1,
  action: "approve",
};

function local(): Workspace {
  return {
    ...freshWorkspace(),
    mode: "local",
    health: "ready",
    deliveryMode: "live",
    drafts: [
      {
        id: "draft-1",
        revision: 1,
        status: "pending",
        recipient: "Colleague",
        target: "slack: D123",
        text: "Ready for review.",
        evidence: [],
      },
    ],
  };
}

describe("local connection recovery", () => {
  it("keeps an unavailable saved connection local without showing preview samples", async () => {
    let preview = transition(freshWorkspace(), {
      type: "project",
      id: "virtualyou",
      selected: true,
    });
    preview = transition(preview, { type: "sample" });
    const backend = {
      snapshot: vi
        .fn()
        .mockRejectedValueOnce(new Error("offline"))
        .mockResolvedValueOnce(local()),
    };
    const offline = await refreshLocalSnapshot(backend, preview);
    expect(offline.mode).toBe("local");
    expect(offline.health).toBe("offline");
    expect(offline.drafts).toEqual([]);
    expect(offline.projects).toEqual([]);
    expect(offline.activity).toEqual([]);
    expect(offline.integrations.every((i) => i.state === "unavailable")).toBe(
      true,
    );
    const recovered = await refreshLocalSnapshot(backend, offline);
    expect(recovered.health).toBe("ready");
    expect(recovered.drafts[0].id).toBe("draft-1");
  });

  it("marks retained local drafts offline after a failed action without retrying it", async () => {
    const original = local();
    const error = new Error("delivery outcome uncertain");
    const backend = {
      act: vi.fn().mockRejectedValue(error),
      snapshot: vi.fn().mockRejectedValue(new Error("offline")),
    };
    const result = await applyLocalAction(backend, decision, original);
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("Expected the original action failure");
    expect(result.error).toBe(error);
    expect(result.state.health).toBe("offline");
    expect(result.state.drafts).toEqual(original.drafts);
    expect(backend.act).toHaveBeenCalledTimes(1);
    expect(backend.snapshot).toHaveBeenCalledTimes(1);
  });

  it("refreshes authoritative state after a conflict without marking a healthy backend offline", async () => {
    const updated = local();
    updated.drafts[0].status = "delivered";
    const backend = {
      act: vi.fn().mockRejectedValue(new Error("revision conflict")),
      snapshot: vi.fn().mockResolvedValue(updated),
    };
    const result = await applyLocalAction(backend, decision, local());
    expect(result.ok).toBe(false);
    expect(result.state.health).toBe("ready");
    expect(result.state.drafts[0].status).toBe("delivered");
    expect(backend.act).toHaveBeenCalledTimes(1);
  });
});
