import type { Workspace } from "./model";

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
