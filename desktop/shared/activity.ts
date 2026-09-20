import type { Activity, Collection, SourceCount } from "./model";
import { z } from "zod";

export const activityDetailSchema = z.object({
  session_id: z.string(),
  source: z.string(),
  project_id: z.string().nullable(),
  started_at: z.string(),
  ended_at: z.string(),
  ingested_at: z.string().nullable(),
  summary: z.string(),
  start_state: z.string(),
  end_state: z.string(),
  prompts: z.array(z.string()),
  reasoning_summary: z.string(),
  files_changed: z.array(z.object({ path: z.string(), operation: z.string() })),
  tool_calls: z.array(
    z.object({
      name: z.string(),
      input_summary: z.string(),
      result_summary: z.string(),
      status: z.string(),
      timestamp: z.string().nullable(),
    }),
  ),
  diffs: z.array(z.string()),
  redacted: z.literal(true),
  truncated: z.boolean(),
});
export const sourceCountSchema = z.object({
  source: z.string(),
  count: z.number().int().nonnegative(),
  latest_at: z.string().nullable(),
  configured: z.boolean(),
  scan_state: z.enum(["healthy", "empty", "failed", "not_started"]),
  last_scan_at: z.string().nullable(),
});
export const activityIdSchema = z.string().regex(/^activity:\d+$/);

export function sourceScanLabel(source: SourceCount): string {
  if (!source.configured) return "Not configured for collection";
  return {
    healthy: "Source read successfully",
    empty: "Checked · no activity found",
    failed: "Source needs attention",
    not_started: "Configured · not checked yet",
  }[source.scan_state];
}

export function sourceLabel(source: string): string {
  const labels: Record<string, string> = {
    claude: "Claude Code",
    codex: "Codex",
    cursor: "Cursor",
    git: "Git",
    github: "GitHub",
    jira: "Jira",
    drive: "Google Drive",
    voice: "Voice notes",
  };
  return labels[source] || source;
}

export function activityKind(item: Activity): "activity" | "draft_event" {
  // Older saved previews predate the event-kind field.
  return item.kind || (item.source === "Preview" ? "draft_event" : "activity");
}

export function filterActivity(
  items: Activity[],
  kind: string,
  project: string,
): Activity[] {
  return items.filter(
    (item) =>
      (kind === "all" || activityKind(item) === kind) &&
      (project === "all" ||
        kind === "draft_event" ||
        item.projectId === project),
  );
}

export function collectionLabel(collection: Collection | null): string {
  if (!collection) return "Not checked";
  return {
    healthy: "Up to date",
    empty: "No activity found",
    degraded: "Some sources need attention",
    failed: "Collection failed",
    not_started: "Not checked yet",
  }[collection.state];
}
export function timestamp(value: string | null): string {
  if (!value) return "Not yet";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Unknown" : date.toLocaleString();
}
