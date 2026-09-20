import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { filterActivity } from "../shared/activity";
import { freshWorkspace } from "../shared/preview";
import { isLocalPort, type Activity } from "../shared/model";
import { Settings } from "../src/views/WorkspaceViews";
import { IntegrationList } from "../src/components/IntegrationList";

describe("workspace controls", () => {
  it("keeps unscoped decisions visible when switching from a project filter", () => {
    const items: Activity[] = [
      {
        id: "work",
        kind: "activity",
        title: "Git work",
        source: "git",
        projectId: "project-1",
        at: "2026-09-20T00:00:00Z",
      },
      {
        id: "decision",
        kind: "draft_event",
        draftId: "report-1",
        title: "Approved",
        source: "Review workflow",
        at: "2026-09-20T00:00:00Z",
      },
      {
        id: "old-preview",
        title: "Sample approved",
        source: "Preview",
        at: "2026-09-20T00:00:00Z",
      },
    ];
    expect(
      filterActivity(items, "activity", "project-1").map((i) => i.id),
    ).toEqual(["work"]);
    expect(
      filterActivity(items, "draft_event", "project-1").map((i) => i.id),
    ).toEqual(["decision", "old-preview"]);
  });

  it("retains setup details for configured integrations without calling them connected", () => {
    const html = renderToStaticMarkup(
      <IntegrationList
        items={[
          { id: "jira", state: "configured", account: "Access not checked" },
        ]}
        onConnect={() => {}}
        busy={false}
      />,
    );
    expect(html).toContain("Configured");
    expect(html).toContain('aria-label="View Jira setup"');
    expect(html).not.toContain(">Connected<");
    expect(html).toContain("Access not checked");
  });

  it.each([1, 1023, 65536, NaN, 8000.5])(
    "refuses invalid local port %s before opening a picker",
    (port) => {
      expect(isLocalPort(port)).toBe(false);
      const state = { ...freshWorkspace(), backendPort: port };
      const html = renderToStaticMarkup(
        <Settings
          state={state}
          act={() => {}}
          onConnect={() => {}}
          busy={false}
        />,
      );
      // A missing/non-finite initial port falls back to 8000; the validator still rejects it directly.
      if (Number.isFinite(port)) {
        expect(html).toContain('aria-invalid="true"');
        const button = Array.from(
          html.matchAll(/<button([^>]*)>([\s\S]*?)<\/button>/g),
        ).find((m) => m[2].includes("Choose data folder"));
        expect(button?.[1]).toContain("disabled");
      }
    },
  );

  it("prevents disconnect while a backend action is in progress", () => {
    const state = {
      ...freshWorkspace(),
      mode: "local" as const,
      health: "ready" as const,
    };
    const html = renderToStaticMarkup(
      <Settings state={state} act={() => {}} onConnect={() => {}} busy />,
    );
    const button = Array.from(
      html.matchAll(/<button([^>]*)>([\s\S]*?)<\/button>/g),
    ).find((m) => m[2].includes("Disconnect desktop"));
    expect(button?.[1]).toContain("disabled");
  });
});
