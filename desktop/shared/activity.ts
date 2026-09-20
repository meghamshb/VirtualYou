import type { Activity, Collection } from "./model";

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
