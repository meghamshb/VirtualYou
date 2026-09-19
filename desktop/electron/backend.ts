import { z } from "zod";
import { freshWorkspace } from "../shared/preview";
import type { Workspace, Action, Draft } from "../shared/model";
export function localOrigin(port: number): string {
  if (!Number.isInteger(port) || port < 1024 || port > 65535)
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
  async request(path: string, body?: unknown): Promise<unknown> {
    const response = await fetch(localOrigin(this.port) + path, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        Authorization: `Bearer ${this.key}`,
        "Content-Type": "application/json",
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      redirect: "error",
      signal: AbortSignal.timeout(10000),
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
  async snapshot(): Promise<Workspace> {
    const status = z
      .object({
        provider: z.string(),
        delivery_mode: z.enum(["live", "simulation"]),
        projects: z.array(z.string()),
        heartbeat: z
          .object({ state: z.string(), finished_at: z.string().optional() })
          .nullable(),
      })
      .passthrough()
      .parse(await this.request("/api/status"));
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
      .parse(await this.request("/api/drafts"));
    const profiles = z
      .array(z.object({ recipient_id: z.string(), display_name: z.string() }))
      .parse(await this.request("/api/personas"));
    const names = new Map(
      profiles.map((p) => [p.recipient_id, p.display_name]),
    );
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
      health: status.heartbeat?.state === "healthy" ? "ready" : "offline",
      lastRefresh: status.heartbeat?.finished_at || null,
      projects: status.projects.map((id) => ({
        id,
        name: id,
        detail: "Indexed by your backend · access managed in Slack",
        selected: true,
      })),
      drafts,
      integrations: [
        { id: "slack", state: "unavailable" },
        { id: "github", state: "unavailable" },
        { id: "jira", state: "unavailable" },
        { id: "drive", state: "unavailable" },
      ],
    };
  }
  async act(action: Action): Promise<Workspace> {
    if (action.type === "refresh") {
      await this.request("/api/refresh", {});
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
