import { z } from "zod";
export const providerIds = ["slack", "github", "jira", "drive"] as const;
export type ProviderId = (typeof providerIds)[number];
export function isLocalPort(port: number): boolean {
  return Number.isInteger(port) && port >= 1024 && port <= 65535;
}
export type View =
  | "setup"
  | "integrations"
  | "projects"
  | "approvals"
  | "activity"
  | "diagnostics"
  | "settings";
export type Step = "connect" | "projects" | "review" | "complete";
export interface Integration {
  id: ProviderId;
  state: "disconnected" | "connected" | "configured" | "unavailable";
  account?: string;
  checkedAt?: string;
}
export interface Project {
  id: string;
  name: string;
  detail: string;
  selected: boolean;
}
export interface Draft {
  id: string;
  recipient: string;
  text: string;
  status: string;
  revision: number;
  target: string;
  evidence: { id: string; text: string; source: string }[];
}
export interface Activity {
  id: string;
  title: string;
  source: string;
  at: string;
  kind?: "activity" | "draft_event";
  summary?: string;
  projectId?: string | null;
  draftId?: string;
  filesChanged?: number;
  toolCalls?: number;
  sessionId?: string;
}
export interface ActivityDetail {
  session_id: string;
  source: string;
  project_id: string | null;
  started_at: string;
  ended_at: string;
  ingested_at: string | null;
  summary: string;
  start_state: string;
  end_state: string;
  prompts: string[];
  reasoning_summary: string;
  files_changed: { path: string; operation: string }[];
  tool_calls: {
    name: string;
    input_summary: string;
    result_summary: string;
    status: string;
    timestamp: string | null;
  }[];
  diffs: string[];
  redacted: true;
  truncated: boolean;
}
export interface SourceCount {
  source: string;
  count: number;
  latest_at: string | null;
  configured: boolean;
  scan_state: "healthy" | "empty" | "failed" | "not_started";
  last_scan_at: string | null;
}
export interface Collection {
  state: "healthy" | "empty" | "degraded" | "failed" | "not_started";
  enabled: boolean;
  configured: boolean;
  last_attempt_at: string | null;
  last_success_at: string | null;
  record_count: number;
  changed: number;
  unchanged: number;
  removed: number;
  error_count: number;
  errors: { source: string; code: string; message: string }[];
}
export interface Workspace {
  mode: "preview" | "local";
  deliveryMode: "live" | "simulation";
  step: Step;
  integrations: Integration[];
  projects: Project[];
  drafts: Draft[];
  activity: Activity[];
  activityHasMore: boolean;
  sourceCounts: SourceCount[] | null;
  collection: Collection | null;
  provider: string | null;
  backendPort: number | null;
  workflowAvailable: boolean;
  paused: boolean;
  version: string;
  health: "preview" | "ready" | "offline";
  lastRefresh: string | null;
  welcomed: boolean;
}
export const actionSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("welcome") }).strict(),
  z
    .object({ type: z.literal("connect"), provider: z.enum(providerIds) })
    .strict(),
  z
    .object({ type: z.literal("disconnect"), provider: z.enum(providerIds) })
    .strict(),
  z
    .object({
      type: z.literal("project"),
      id: z.string().min(1).max(100),
      selected: z.boolean(),
    })
    .strict(),
  z.object({ type: z.literal("next") }).strict(),
  z.object({ type: z.literal("back") }).strict(),
  z.object({ type: z.literal("pause"), value: z.boolean() }).strict(),
  z.object({ type: z.literal("refresh") }).strict(),
  z.object({ type: z.literal("sample") }).strict(),
  z
    .object({
      type: z.literal("decision"),
      id: z.string().min(1).max(100),
      action: z.enum(["approve", "reject"]),
      revision: z.number().int().positive(),
    })
    .strict(),
  z.object({ type: z.literal("preview") }).strict(),
]);
export type Action = z.infer<typeof actionSchema>;
export interface Bridge {
  snapshot(): Promise<Workspace>;
  activityDetail(activityId: string): Promise<ActivityDetail>;
  act(action: Action): Promise<Workspace>;
  connectLocal(port: number): Promise<Workspace>;
  diagnostics(): Promise<string>;
}
export function unreachable(value: never): never {
  throw new Error(`Unsupported operation: ${String(value)}`);
}
