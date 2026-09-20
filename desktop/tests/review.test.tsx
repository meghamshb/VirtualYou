import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { freshWorkspace } from "../shared/preview";
import { approvalCopy } from "../shared/review";
import type { Workspace } from "../shared/model";
import { Approvals } from "../src/views/Approvals";

function workspace(overrides: Partial<Workspace> = {}): Workspace {
  return {
    ...freshWorkspace(),
    mode: "local",
    health: "ready",
    deliveryMode: "live",
    drafts: [
      {
        id: "report-1",
        recipient: "Meghamsh",
        target: "slack: D123",
        text: "The integration test is ready for review.",
        status: "pending",
        revision: 1,
        evidence: [],
      },
    ],
    ...overrides,
  };
}

function render(state: Workspace, initialDraftId?: string) {
  return renderToStaticMarkup(
    <Approvals
      state={state}
      initialDraftId={initialDraftId}
      onSample={() => {}}
      onDecision={() => {}}
      onRefresh={() => {}}
      busy={false}
    />,
  );
}

describe("report approval presentation", () => {
  it("opens the report selected from an activity event instead of the first pending report", () => {
    const state = workspace();
    state.drafts.push({
      ...state.drafts[0],
      id: "report-2",
      text: "Selected report content.",
    });
    const html = render(state, "report-2");
    expect(html).toContain("Selected report content.");
    expect(html).not.toContain("The integration test is ready for review.");
    expect(html).toContain('id="draft-report-2"');
  });

  it("explains an uncertain delivery and offers no automatic retry control", () => {
    const state = workspace();
    state.drafts[0].status = "delivery_unknown";
    const html = render(state);
    expect(html).toContain("Delivery unconfirmed");
    expect(html).toContain(
      "Check the destination before trying again to avoid a duplicate message.",
    );
    expect(html).not.toContain("Approve &amp; send");
  });
  it("opens a resolved draft linked from activity, with resolved history visible", () => {
    const state = workspace();
    state.drafts.push({
      ...state.drafts[0],
      id: "rejected-report",
      status: "rejected",
      text: "Rejected report selected from history.",
    });
    const html = render(state, "rejected-report");
    expect(html).toContain("Rejected report selected from history.");
    expect(html).not.toContain("The integration test is ready for review.");
    expect(html).toContain('type="checkbox" checked=""');
    expect(html).toContain(
      "This draft was rejected. Nothing was sent by this decision.",
    );
  });
  it("does not promise rejection is available while a paused backend is offline", () => {
    const html = render(workspace({ paused: true, health: "offline" }));
    expect(html).toContain("The Slack workflow is paused.");
    expect(html).not.toContain("You can still reject a draft.");
  });
  it("names the live destination in the confirmation", () => {
    const copy = approvalCopy(workspace(), "slack: D123");
    expect(copy.label).toBe("Approve & send");
    expect(copy.description).toContain(
      "This sends the reviewed text to slack: D123",
    );
  });

  it("distinguishes backend simulation from the sample preview before approval", () => {
    const state = workspace({ deliveryMode: "simulation" });
    const copy = approvalCopy(state, "slack: D123");
    expect(copy.label).toBe("Approve simulation");
    expect(copy.description).toContain("No message will be sent.");
    expect(render(state)).toContain("Approve simulation");
    expect(render(state)).not.toContain("Approve &amp; send");
    const preview = approvalCopy(workspace({ mode: "preview" }));
    expect(preview.label).toBe("Approve sample");
    expect(preview.description).toContain("Nothing will be sent.");
  });

  it("labels a completed backend simulation without claiming delivery or preview", () => {
    const state = workspace({ deliveryMode: "simulation" });
    state.drafts[0].status = "simulated";
    const html = render(state);
    expect(html).toContain(
      "Delivery simulated by your backend. No message was sent.",
    );
    expect(html).not.toContain("Approved in preview");
  });

  it.each(["offline", "ready"] as const)(
    "keeps refresh available while gating %s decisions",
    (health) => {
      const buttons = Array.from(
        render(workspace({ health })).matchAll(
          /<button([^>]*)>([\s\S]*?)<\/button>/g,
        ),
      );
      for (const label of ["Approve", "Reject"]) {
        const button = buttons.find((b) => b[2].includes(label));
        expect(button).toBeDefined();
        expect(button![1].includes('disabled=""')).toBe(health === "offline");
      }
      const refresh = buttons.find((b) => b[2].includes("Refresh drafts"));
      expect(refresh).toBeDefined();
      expect(refresh![1]).not.toContain("disabled");
    },
  );
});
