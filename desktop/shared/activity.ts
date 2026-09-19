import type { Collection } from "./model";

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
