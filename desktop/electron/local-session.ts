import type { Action, Workspace } from "../shared/model";
import { freshWorkspace } from "../shared/preview";

interface LocalSession {
  snapshot(): Promise<Workspace>;
  act(action: Action): Promise<Workspace>;
}

export function offlineLocalWorkspace(previous: Workspace): Workspace {
  if (previous.mode === "local") return { ...previous, health: "offline" };
  // A saved local connection must never inherit sample drafts from preview.
  return {
    ...freshWorkspace(),
    mode: "local",
    health: "offline",
    welcomed: true,
    projects: [],
    drafts: [],
    activity: [],
    integrations: previous.integrations.map(({ id }) => ({
      id,
      state: "unavailable",
    })),
  };
}

export async function refreshLocalSnapshot(
  backend: Pick<LocalSession, "snapshot">,
  previous: Workspace,
): Promise<Workspace> {
  try {
    return await backend.snapshot();
  } catch {
    return offlineLocalWorkspace(previous);
  }
}

export async function applyLocalAction(
  backend: LocalSession,
  action: Action,
  previous: Workspace,
): Promise<
  | { ok: true; state: Workspace }
  | { ok: false; state: Workspace; error: unknown }
> {
  try {
    return { ok: true, state: await backend.act(action) };
  } catch (error) {
    // Reconcile state without retrying the action: a send may have succeeded
    // before its response failed, or a different client may have changed it.
    return {
      ok: false,
      state: await refreshLocalSnapshot(backend, previous),
      error,
    };
  }
}
