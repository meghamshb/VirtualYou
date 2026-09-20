import { z } from "zod";
import { freshWorkspace } from "../shared/preview";
import type { Workspace, Action, Draft } from "../shared/model";
import { isLocalPort } from "../shared/model";
import {
  activityDetailSchema,
  activityIdSchema,
  sourceCountSchema,
} from "../shared/activity";
export function localOrigin(port: number): string {
  if (!isLocalPort(port))
    throw new Error("Choose a port between 1024 and 65535.");
  return `http://127.0.0.1:${port}`;
}
export class LocalBackend {
  constructor(
    private port: number,
    private key: string,
  ) {
    localOrigin(port);
    if (key.length < 24)
      throw new Error(
        "The selected folder does not contain a valid backend access key.",
      );
  }
  async request(
    path: string,
    body?: unknown,
    timeout = 10000,
  ): Promise<unknown> {
    const response = await fetch(localOrigin(this.port) + path, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        Authorization: `Bearer ${this.key}`,
        "Content-Type": "application/json",
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      redirect: "error",
      signal: AbortSignal.timeout(timeout),
    }).catch(() => {
      throw new Error(
        "Cannot reach the local backend. Start your existing VirtualYou service, then try again.",
      );
    });
    if (!response.ok) {
      if (response.status === 401 || response.status === 403)
        throw new Error(
          "The backend rejected this connection. Choose its current private data folder again.",
        );
      throw new Error(
        `The backend could not complete this action (HTTP ${response.status}). Refresh and review the current state.`,
      );
    }
    return response.json();
  }
  async activityDetail(activityId: string) {
    const id = activityIdSchema.parse(activityId);
    return activityDetailSchema.parse(
      await this.request(`/api/activity/${encodeURIComponent(id)}`),
    );
  }
  async snapshot(): Promise<Workspace> {
    const [statusData, draftData, profileData, activityData] =
      await Promise.all([
        this.request("/api/status"),
        this.request("/api/drafts"),
        this.request("/api/personas"),
        this.request("/api/activity?limit=50"),
      ]);
    const status = z
      .object({
        provider: z.string(),
        delivery_mode: z.enum(["live", "simulation"]),
        projects: z.array(z.string()),
        workflow: z.object({ paused: z.boolean(), available: z.boolean() }),
        integrations: z.record(
          z.string(),
          z.object({
            status: z.enum(["configured", "not_configured"]),
            enabled: z.boolean(),
            verification: z.literal("not_checked"),
          }),
        ),
        heartbeat: z
          .object({ state: z.string(), finished_at: z.string().optional() })
          .nullable(),
      })
      .passthrough()
      .parse(statusData);
    const raw = z
      .array(
        z
          .object({
            id: z.string(),
            request: z.object({ recipient_id: z.string() }).passthrough(),
            text: z.string(),
            status: z.string(),
            revision: z.number(),
            destination: z
              .object({ target: z.string(), platform: z.string() })
              .passthrough(),
            evidence: z
              .array(
                z
                  .object({
                    evidence_id: z.string(),
                    text: z.string(),
                    source: z.string(),
                  })
                  .passthrough(),
              )
              .default([]),
          })
          .passthrough(),
      )
      .parse(draftData);
    const profiles = z
      .array(z.object({ recipient_id: z.string(), display_name: z.string() }))
      .parse(profileData);
    const names = new Map(
      profiles.map((p) => [p.recipient_id, p.display_name]),
    );
    const feed = z
      .object({
        items: z.array(
          z.object({
            id: z.string(),
            kind: z.enum(["activity", "draft_event"]),
            at: z.string(),
            title: z.string(),
            summary: z.string(),
            source: z.string().optional(),
            session_id: z.string().optional(),
            project_id: z.string().nullable().optional(),
            draft_id: z.string().optional(),
            files_changed_count: z.number().optional(),
            tool_calls_count: z.number().optional(),
          }),
        ),
        has_more: z.boolean(),
        source_counts: z.array(sourceCountSchema).optional(),
        collection: z.object({
          state: z.enum([
            "healthy",
            "empty",
            "degraded",
            "failed",
            "not_started",
          ]),
          enabled: z.boolean(),
          configured: z.boolean(),
          last_attempt_at: z.string().nullable(),
          last_success_at: z.string().nullable(),
          record_count: z.number(),
          changed: z.number(),
          unchanged: z.number(),
          removed: z.number(),
          error_count: z.number(),
          errors: z.array(
            z.object({
              source: z.string(),
              code: z.string(),
              message: z.string(),
            }),
          ),
        }),
      })
      .parse(activityData);
    const drafts: Draft[] = raw.map((d) => ({
      id: d.id,
      recipient: names.get(d.request.recipient_id) || d.request.recipient_id,
      text: d.text,
      status: d.status,
      revision: d.revision,
      target: `${d.destination.platform}: ${d.destination.target}`,
      evidence: d.evidence.map((e) => ({
        id: e.evidence_id,
        text: e.text,
        source: e.source,
      })),
    }));
    return {
      ...freshWorkspace(),
      mode: "local",
      deliveryMode: status.delivery_mode,
      welcomed: true,
      // Successful authenticated API reads prove connectivity; collection health is separate.
      health: "ready",
      lastRefresh: new Date().toISOString(),
      provider: status.provider,
      backendPort: this.port,
      collection: feed.collection,
      activityHasMore: feed.has_more,
      sourceCounts: feed.source_counts ?? null,
      paused: status.workflow.paused,
      workflowAvailable: status.workflow.available,
      activity: feed.items.map((item) => ({
        id: item.id,
        title: item.title,
        source: item.source || "Review workflow",
        at: item.at,
        kind: item.kind,
        summary: item.summary,
        projectId: item.project_id,
        draftId: item.draft_id,
        filesChanged: item.files_changed_count,
        toolCalls: item.tool_calls_count,
        sessionId: item.session_id,
      })),
      projects: status.projects.map((id) => ({
        id,
        name: id,
        detail: "Indexed by your backend · access managed in Slack",
        selected: true,
      })),
      drafts,
      integrations: (["slack", "github", "jira", "drive"] as const).map(
        (id) => {
          const integration = status.integrations[id];
          return {
            id,
            state:
              integration?.status === "configured"
                ? "configured"
                : "unavailable",
            account:
              integration?.status === "configured"
                ? `${integration.enabled ? "Enabled" : "Not enabled"} · credentials configured; access not checked`
                : "Not configured on this backend",
          };
        },
      ),
    };
  }
  async act(action: Action): Promise<Workspace> {
    if (action.type === "refresh") {
      await this.request("/api/refresh", {}, 120000);
      return this.snapshot();
    }
    if (action.type === "pause") {
      await this.request("/api/workflow/pause", { paused: action.value });
      return this.snapshot();
    }
    if (action.type === "decision") {
      // Approval and delivery stay in the existing workflow; no bypass or retry of an uncertain send.
      await this.request(
        `/api/drafts/${encodeURIComponent(action.id)}/decision`,
        { expected_revision: action.revision, action: action.action },
      );
      if (action.action === "approve")
        await this.request(
          `/api/drafts/${encodeURIComponent(action.id)}/deliver`,
          { expected_revision: action.revision },
        );
      return this.snapshot();
    }
    throw new Error(
      "Manage this setting in your existing Slack app. Hosted onboarding is not connected in this UI build.",
    );
  }
}
