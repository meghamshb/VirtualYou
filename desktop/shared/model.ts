import { z } from "zod";
export const providerIds = ["slack", "github", "jira", "drive"] as const;
export type ProviderId = (typeof providerIds)[number];
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
  state: "disconnected" | "connected" | "unavailable";
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
}
export interface Workspace {
  mode: "preview" | "local";
  step: Step;
  integrations: Integration[];
  projects: Project[];
  drafts: Draft[];
  activity: Activity[];
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
  act(action: Action): Promise<Workspace>;
  connectLocal(port: number): Promise<Workspace>;
  diagnostics(): Promise<string>;
}
export function unreachable(value: never): never {
  throw new Error(`Unsupported operation: ${String(value)}`);
}
