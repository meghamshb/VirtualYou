import type { Draft, Workspace } from "./model";

export function draftOutcome(draft: Draft, state: Workspace) {
  if (draft.status === "simulated")
    return {
      label:
        state.mode === "preview" ? "Approved in preview" : "Delivery simulated",
      description:
        state.mode === "preview"
          ? "Approved in preview. No message was sent."
          : "Delivery simulated by your backend. No message was sent.",
    };
  const outcomes: Record<string, { label: string; description: string }> = {
    pending: {
      label: "Ready for review",
      description: "Waiting for your review.",
    },
    approved: {
      label: "Approved · awaiting delivery",
      description:
        "Approved, but delivery has not completed. Refresh to check the latest result.",
    },
    delivering: {
      label: "Sending",
      description: "Delivery is in progress. Refresh to check the result.",
    },
    delivered: { label: "Delivered", description: "This draft was delivered." },
    rejected: {
      label: "Rejected",
      description:
        "This draft was rejected. Nothing was sent by this decision.",
    },
    delivery_failed: {
      label: "Delivery failed",
      description:
        "Delivery failed. Check the backend’s delivery details before retrying through your existing workflow.",
    },
    delivery_unknown: {
      label: "Delivery unconfirmed",
      description:
        "The delivery result is unknown. Check the destination before trying again to avoid a duplicate message.",
    },
  };
  return (
    outcomes[draft.status] || {
      label: draft.status.replaceAll("_", " "),
      description: "Refresh to check this draft’s current status.",
    }
  );
}

export function reviewUnavailable(state: Workspace): boolean {
  return state.mode === "local" && state.health !== "ready";
}

export function approvalCopy(state: Workspace, target = "") {
  if (state.mode === "preview")
    return {
      label: "Approve sample",
      title: "Approve this sample?",
      description:
        "This records an approval in the preview. Nothing will be sent.",
    };
  if (state.deliveryMode === "live")
    return {
      label: "Approve & send",
      title: "Approve and send this reply?",
      description: `This sends the reviewed text to ${target} through your existing backend’s delivery workflow.`,
    };
  return {
    label: "Approve simulation",
    title: "Approve this simulated delivery?",
    description: `Your backend will record approval and simulate delivery to ${target}. No message will be sent.`,
  };
}
