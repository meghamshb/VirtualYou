import { describe, it, expect } from "vitest";
import { freshWorkspace, transition, diagnosticText } from "../shared/preview";
import { actionSchema } from "../shared/model";
import { localOrigin } from "../electron/backend";
describe("onboarding and review", () => {
  it("requires Slack and a project before completing", () => {
    let state = freshWorkspace();
    expect(() => transition(state, { type: "next" })).toThrow("Connect Slack");
    state = transition(state, { type: "connect", provider: "slack" });
    state = transition(state, { type: "next" });
    expect(state.step).toBe("projects");
    expect(() => transition(state, { type: "next" })).toThrow(
      "Choose at least one",
    );
    state = transition(state, {
      type: "project",
      id: "virtualyou",
      selected: true,
    });
    state = transition(state, { type: "next" });
    expect(state.step).toBe("review");
    state = transition(state, { type: "next" });
    expect(state.step).toBe("complete");
    expect(JSON.parse(JSON.stringify(state))).toEqual(state);
  });
  it("does not mutate the prior state and can go back", () => {
    const original = freshWorkspace();
    const state = transition(original, { type: "connect", provider: "slack" });
    expect(original.integrations[0].state).toBe("disconnected");
    expect(
      transition(transition(state, { type: "next" }), { type: "back" }).step,
    ).toBe("connect");
  });
  it("requires review and refuses duplicate or stale approval", () => {
    let s = freshWorkspace();
    s = transition(s, { type: "project", id: "virtualyou", selected: true });
    s = transition(s, { type: "sample" });
    expect(s.drafts[0].status).toBe("pending");
    expect(() =>
      transition(s, {
        type: "decision",
        id: "sample-1",
        action: "approve",
        revision: 2,
      }),
    ).toThrow("changed");
    s = transition(s, {
      type: "decision",
      id: "sample-1",
      action: "approve",
      revision: 1,
    });
    expect(s.drafts[0].status).toBe("simulated");
    expect(() =>
      transition(s, {
        type: "decision",
        id: "sample-1",
        action: "approve",
        revision: 1,
      }),
    ).toThrow("changed");
  });
  it("pause prevents generation and approval, but allows rejection", () => {
    let s = freshWorkspace();
    s = transition(s, { type: "project", id: "virtualyou", selected: true });
    s = transition(s, { type: "sample" });
    s = transition(s, { type: "pause", value: true });
    expect(() => transition(s, { type: "sample" })).toThrow("Resume");
    expect(() =>
      transition(s, {
        type: "decision",
        id: "sample-1",
        action: "approve",
        revision: 1,
      }),
    ).toThrow("Resume");
    expect(
      transition(s, {
        type: "decision",
        id: "sample-1",
        action: "reject",
        revision: 1,
      }).drafts[0].status,
    ).toBe("rejected");
  });
  it("diagnostics omit identities, message content, and evidence", () => {
    const s = freshWorkspace();
    s.integrations[0].account = "PRIVATE_ACCOUNT";
    s.projects[0].name = "PRIVATE_PROJECT";
    expect(diagnosticText(s)).not.toContain("PRIVATE");
  });
  it("rejects unsupported IPC actions and arbitrary server locations", () => {
    expect(
      actionSchema.safeParse({ type: "execute", command: "rm -rf /" }).success,
    ).toBe(false);
    expect(
      actionSchema.safeParse({ type: "refresh", token: "secret" }).success,
    ).toBe(false);
    expect(
      actionSchema.safeParse({ type: "connect", provider: "unknown" }).success,
    ).toBe(false);
    expect(() => localOrigin(80)).toThrow();
    expect(() => localOrigin(NaN)).toThrow();
    expect(localOrigin(3000)).toBe("http://127.0.0.1:3000");
  });
});
