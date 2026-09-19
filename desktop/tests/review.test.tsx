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

function render(state: Workspace) {
  return renderToStaticMarkup(
    <Approvals
      state={state}
      onSample={() => {}}
      onDecision={() => {}}
      onRefresh={() => {}}
      busy={false}
    />,
  );
}

describe("report approval presentation", () => {
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
