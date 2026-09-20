import { freshWorkspace, transition, diagnosticText } from "../shared/preview";
import type { Bridge, Workspace } from "../shared/model";
declare global {
  interface Window {
    virtualYou?: Bridge;
  }
}
const key = "virtualyou-ui-preview-v1";
function load(): Workspace {
  try {
    const saved = JSON.parse(localStorage.getItem(key) || "null");
    if (
      saved?.mode === "preview" &&
      Array.isArray(saved.integrations) &&
      Array.isArray(saved.projects)
    )
      return saved;
  } catch {
    /* Corrupt sample preferences reset safely. */
  }
  return freshWorkspace();
}
let state = load();
const preview: Bridge = {
  snapshot: async () => structuredClone(state),
  activityDetail: async () => {
    throw new Error(
      "Connect a local backend to inspect real recorded context.",
    );
  },
  act: async (action) => {
    state = transition(state, action);
    localStorage.setItem(key, JSON.stringify(state));
    return structuredClone(state);
  },
  connectLocal: async () => {
    throw new Error(
      "Open the desktop application to choose your local backend. Browser preview cannot access credentials.",
    );
  },
  diagnostics: async () => diagnosticText(state),
};
export const bridge = window.virtualYou || preview;
