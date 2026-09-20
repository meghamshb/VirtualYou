import {
  actionSchema,
  providerIds,
  unreachable,
  type Action,
  type Workspace,
} from "./model";
export function freshWorkspace(): Workspace {
  return {
    mode: "preview",
    deliveryMode: "simulation",
    step: "connect",
    integrations: providerIds.map((id) => ({ id, state: "disconnected" })),
    projects: [
      {
        id: "virtualyou",
        name: "VirtualYou",
        detail: "GitHub · product engineering",
        selected: false,
      },
      {
        id: "website",
        name: "Website",
        detail: "GitHub · public website",
        selected: false,
      },
      {
        id: "planning",
        name: "Product planning",
        detail: "Jira & Google Drive",
        selected: false,
      },
    ],
    drafts: [],
    activity: [],
    activityHasMore: false,
    collection: null,
    provider: null,
    backendPort: null,
    workflowAvailable: false,
    paused: false,
    version: "0.1.0",
    health: "preview",
    lastRefresh: null,
    welcomed: false,
  };
}
export function transition(input: Workspace, raw: Action): Workspace {
  const action = actionSchema.parse(raw);
  const state = structuredClone(input);
  const now = new Date().toISOString();
  switch (action.type) {
    case "welcome":
      state.welcomed = true;
      break;
    case "connect":
      state.integrations = state.integrations.map((i) =>
        i.id === action.provider
          ? {
              ...i,
              state: "connected",
              account: "Example workspace",
              checkedAt: now,
            }
          : i,
      );
      break;
    case "disconnect":
      state.integrations = state.integrations.map((i) =>
        i.id === action.provider ? { id: i.id, state: "disconnected" } : i,
      );
      break;
    case "project":
      if (!state.projects.some((p) => p.id === action.id))
        throw new Error("Choose a project from this workspace.");
      state.projects = state.projects.map((p) =>
        p.id === action.id ? { ...p, selected: action.selected } : p,
      );
      break;
    case "next":
      switch (state.step) {
        case "connect":
          if (
            !state.integrations.some(
              (i) => i.id === "slack" && i.state === "connected",
            )
          )
            throw new Error(
              "Connect Slack to continue, or keep exploring the other views.",
            );
          state.step = "projects";
          break;
        case "projects":
          if (!state.projects.some((p) => p.selected))
            throw new Error("Choose at least one project to continue.");
          state.step = "review";
          break;
        case "review":
          state.step = "complete";
          break;
        case "complete":
          break;
        default:
          unreachable(state.step);
      }
      break;
    case "back":
      switch (state.step) {
        case "connect":
          break;
        case "projects":
          state.step = "connect";
          break;
        case "review":
          state.step = "projects";
          break;
        case "complete":
          state.step = "review";
          break;
        default:
          unreachable(state.step);
      }
      break;
    case "pause":
      state.paused = action.value;
      break;
    case "refresh":
      state.lastRefresh = now;
      break;
    case "sample":
      if (state.paused)
        throw new Error("Resume the preview before preparing a sample.");
      if (!state.projects.some((p) => p.selected))
        throw new Error("Choose a project before preparing a sample.");
      if (!state.drafts.some((d) => d.id === "sample-1")) {
        state.drafts.push({
          id: "sample-1",
          recipient: "Alex · example colleague",
          target: "Example personal DM",
          status: "pending",
          revision: 1,
          text: "Hey Alex — the ingestion pipeline is connected to Slack replies now. Each draft uses the relevant project evidence and still comes to me for approval. Deployment hasn’t been verified yet.",
          evidence: [
            {
              id: "example-commit",
              source: "GitHub · example commit",
              text: "Connect normalized activity records to scoped retrieval for Slack drafts.",
            },
            {
              id: "example-policy",
              source: "VirtualYou · example policy",
              text: "Human approval is required before delivery. Deployment status is not recorded.",
            },
          ],
        });
        state.activity.unshift({
          id: "sample-event",
          kind: "draft_event",
          draftId: "sample-1",
          title: "Sample evidence indexed and draft prepared",
          source: "Preview",
          at: now,
        });
      }
      break;
    case "decision": {
      const draft = state.drafts.find((d) => d.id === action.id);
      if (
        !draft ||
        draft.revision !== action.revision ||
        draft.status !== "pending"
      )
        throw new Error("This draft changed. Refresh before reviewing it.");
      if (state.paused && action.action === "approve")
        throw new Error("Resume the preview before approving.");
      draft.status = action.action === "approve" ? "simulated" : "rejected";
      state.activity.unshift({
        id: `decision-${now}`,
        kind: "draft_event",
        draftId: draft.id,
        title:
          action.action === "approve"
            ? "Sample approved — no message sent"
            : "Sample rejected",
        source: "Preview",
        at: now,
      });
      break;
    }
    case "preview":
      return { ...freshWorkspace(), welcomed: true };
    default:
      unreachable(action);
  }
  return state;
}
export function diagnosticText(state: Workspace): string {
  return JSON.stringify(
    {
      app: "VirtualYou",
      version: state.version,
      mode: state.mode,
      health: state.health,
      step: state.step,
      lastRefresh: state.lastRefresh,
      integrations: state.integrations.map((i) => ({
        provider: i.id,
        state: i.state,
      })),
      projectCount: state.projects.length,
      pendingCount: state.drafts.filter((d) => d.status === "pending").length,
      collection: state.collection
        ? {
            state: state.collection.state,
            recordCount: state.collection.record_count,
            errorCodes: state.collection.errors.map((e) => e.code),
            lastSuccessAt: state.collection.last_success_at,
          }
        : null,
      deliveryMode: state.deliveryMode,
      paused: state.paused,
    },
    null,
    2,
  );
}
