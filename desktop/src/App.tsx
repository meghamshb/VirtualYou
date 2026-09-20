import { useEffect, useState } from "react";
import {
  Settings2,
  Link2,
  Folder,
  CircleCheck,
  Activity,
  Stethoscope,
  Settings as SettingsIcon,
  UserRound,
  ArrowRight,
  X,
  Info,
  ShieldCheck,
  ExternalLink,
} from "lucide-react";
import { bridge } from "./bridge";
import { freshWorkspace } from "../shared/preview";
import { approvalCopy, reviewUnavailable } from "../shared/review";
import type {
  Workspace,
  View,
  Action,
  ProviderId,
  Draft,
} from "../shared/model";
import { unreachable } from "../shared/model";
import { Button, Modal } from "./components/ui";
import { IntegrationList, providers } from "./components/IntegrationList";
import { Setup } from "./views/Setup";
import { Approvals } from "./views/Approvals";
import {
  ProjectView,
  ActivityView,
  Diagnostics,
  Settings,
} from "./views/WorkspaceViews";
const navigation: { id: View; label: string; icon: typeof Settings2 }[] = [
  { id: "setup", label: "Setup", icon: Settings2 },
  { id: "integrations", label: "Integrations", icon: Link2 },
  { id: "projects", label: "Projects", icon: Folder },
  { id: "approvals", label: "Approvals", icon: CircleCheck },
  { id: "activity", label: "Activity", icon: Activity },
  { id: "diagnostics", label: "Diagnostics", icon: Stethoscope },
  { id: "settings", label: "Settings", icon: SettingsIcon },
];
type Dialog =
  | { kind: "connect"; provider: ProviderId }
  | { kind: "decision"; draft: Draft; approve: boolean }
  | null;
export default function App() {
  const [state, setState] = useState<Workspace>(freshWorkspace);
  const [view, setView] = useState<View>("setup");
  const [reviewDraftId, setReviewDraftId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState(false);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    bridge
      .snapshot()
      .then(setState)
      .catch(() => {
        setMessage(
          "Your saved setup could not be loaded. Reopen the desktop app.",
        );
        setError(true);
      })
      .finally(() => setLoaded(true));
  }, []);
  async function run(operation: () => Promise<Workspace>, success = "") {
    setBusy(true);
    setMessage("");
    try {
      setState(await operation());
      setError(false);
      if (success) setMessage(success);
    } catch (e) {
      // Reconcile an uncertain mutation without retrying it or hiding its error.
      try {
        setState(await bridge.snapshot());
      } catch {
        /* Keep original error. */
      }
      setError(true);
      setMessage(
        e instanceof Error
          ? e.message
          : "This action could not be completed. Please try again.",
      );
    } finally {
      setBusy(false);
    }
  }
  function act(action: Action) {
    void run(() => bridge.act(action));
  }
  function connect(provider: ProviderId) {
    setDialog({ kind: "connect", provider });
  }
  async function copy() {
    try {
      await navigator.clipboard.writeText(await bridge.diagnostics());
      setError(false);
      setMessage(
        "Safe diagnostics copied. No credentials or message content included.",
      );
    } catch {
      setError(true);
      setMessage(
        "Clipboard access was unavailable. Try from the desktop application.",
      );
    }
  }
  function body() {
    switch (view) {
      case "setup":
        return state.mode === "local" ? (
          <>
            <div className="page-intro">
              <h1>Your local workspace is connected.</h1>
              <p>
                Review report drafts, check recent work, and see your backend’s
                health.
              </p>
            </div>
            <div className="connection-notice">
              <ShieldCheck />
              <div>
                <h2>Your existing workflow stays in charge.</h2>
                <p>
                  Configure integrations and recipient access in Slack. This
                  build adds a desktop view without changing those permissions.
                </p>
                <Button variant="primary" onClick={() => setView("approvals")}>
                  Open approvals <ArrowRight size={17} />
                </Button>
              </div>
            </div>
          </>
        ) : (
          <Setup
            state={state}
            act={act}
            onConnect={connect}
            goApprovals={() => {
              setView("approvals");
              act({ type: "sample" });
            }}
            busy={busy}
          />
        );
      case "integrations":
        return (
          <>
            <div className="page-intro">
              <h1>A workspace that works together.</h1>
              <p>Connect the places where your work already happens.</p>
            </div>
            <IntegrationList
              items={state.integrations}
              onConnect={connect}
              onDisconnect={
                state.mode === "preview"
                  ? (provider) => act({ type: "disconnect", provider })
                  : undefined
              }
              busy={busy}
            />
            <div className="privacy-note">
              <ShieldCheck size={19} />
              <span>
                {state.mode === "preview"
                  ? "These are example connections. OAuth permissions are not requested in preview."
                  : "Integration authorization remains managed by your existing deployment. Configured means credentials are present; it does not claim a live connection test."}
              </span>
            </div>
          </>
        );
      case "projects":
        return <ProjectView state={state} act={act} busy={busy} />;
      case "approvals":
        return (
          <Approvals
            state={state}
            initialDraftId={reviewDraftId}
            onSample={() => act({ type: "sample" })}
            onRefresh={() => void run(() => bridge.snapshot())}
            onDecision={(draft, approve) =>
              setDialog({ kind: "decision", draft, approve })
            }
            busy={busy}
          />
        );
      case "activity":
        return (
          <ActivityView
            state={state}
            loadActivity={bridge.activityDetail}
            busy={busy}
            onRefresh={() => void run(() => bridge.snapshot())}
            onCollect={() => act({ type: "refresh" })}
            onReview={(draftId) => {
              setReviewDraftId(draftId);
              setView("approvals");
            }}
          />
        );
      case "diagnostics":
        return (
          <Diagnostics
            state={state}
            onCopy={() => void copy()}
            onRefresh={() => void run(() => bridge.snapshot())}
            busy={busy}
          />
        );
      case "settings":
        return (
          <Settings
            state={state}
            act={act}
            onConnect={(port) =>
              void run(
                () => bridge.connectLocal(port),
                "Local backend connected.",
              )
            }
            busy={busy}
          />
        );
      default:
        return unreachable(view);
    }
  }
  const pending = state.drafts.filter((d) => d.status === "pending").length;
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="wordmark"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setView("setup");
          }}
        >
          <img src="./favicon.svg" width="32" height="32" alt="" />
          <span>VirtualYou</span>
        </a>
        <nav aria-label="Workspace navigation">
          {navigation.slice(0, 5).map((item) => (
            <button
              key={item.id}
              className={`nav-item ${view === item.id ? "selected" : ""}`}
              aria-current={view === item.id ? "page" : undefined}
              onClick={() => setView(item.id)}
            >
              <item.icon size={20} />
              <span>{item.label}</span>
              {item.id === "approvals" && pending > 0 && (
                <span className="nav-count">{pending}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          {navigation.slice(5).map((item) => (
            <button
              key={item.id}
              className={`nav-item ${view === item.id ? "selected" : ""}`}
              aria-current={view === item.id ? "page" : undefined}
              onClick={() => setView(item.id)}
            >
              <item.icon size={20} />
              <span>{item.label}</span>
            </button>
          ))}
          <div className="collector-status">
            <span
              className={`status-dot ${state.health === "ready" ? "ready" : ""}`}
            />
            <span>
              {state.mode === "preview"
                ? "Preview workspace"
                : state.health === "ready"
                  ? "Local backend connected"
                  : "Backend unavailable"}
            </span>
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div>
            Your workspace <span>/</span>{" "}
            <strong>{navigation.find((n) => n.id === view)?.label}</strong>
          </div>
          <div className="topbar-right">
            <span className="mode-label">
              {state.mode === "preview"
                ? "Preview · no messages sent"
                : "Local backend"}
            </span>
            <span className="account-icon" aria-label="Workspace account">
              <UserRound size={18} />
            </span>
          </div>
        </header>
        <main
          id="main-content"
          className={view === "approvals" ? "wide-content" : ""}
        >
          {message && (
            <div
              className={`notice ${error ? "error" : ""}`}
              role={error ? "alert" : "status"}
            >
              <Info size={18} />
              <span>{message}</span>
              <button
                aria-label="Dismiss notification"
                onClick={() => setMessage("")}
              >
                <X size={16} />
              </button>
            </div>
          )}
          {reviewUnavailable(state) && (
            <div className="notice error" role="alert">
              <Info size={18} />
              <span>
                The backend is offline or not ready. Displayed drafts may be
                outdated, so decisions are disabled. Use Refresh drafts in
                Approvals after the backend recovers.
              </span>
            </div>
          )}
          {loaded && state.mode === "preview" && state.welcomed && (
            <div className="notice" role="status">
              <Info size={18} />
              <span>
                You’re viewing sample data.{" "}
                {window.virtualYou
                  ? "Connect your local backend to see real activity and drafts."
                  : "Open the Electron desktop app to connect your local backend."}
              </span>
              {window.virtualYou && (
                <Button onClick={() => setView("settings")}>
                  Connect local backend
                </Button>
              )}
            </div>
          )}
          {busy && (
            <div className="loading-line" role="status" aria-label="Working" />
          )}
          {loaded ? body() : <p role="status">Loading your workspace…</p>}
        </main>
        <div className="bottom-note">
          {state.mode === "preview"
            ? "A preview of your calmer workday. Your accounts are not connected."
            : "Connected to your existing backend. Its approval and delivery rules still apply."}
        </div>
      </div>
      {loaded && !state.welcomed && (
        <Modal
          title="Meet your VirtualYou."
          onClose={() => act({ type: "welcome" })}
        >
          <div className="welcome-mark">
            <img src="./favicon.svg" width="40" height="40" alt="" />
          </div>
          <p className="modal-lead">
            Your work, in your voice.
            <br />
            With you in control.
          </p>
          <p>
            Explore a visual setup flow for connecting tools, choosing projects,
            and reviewing evidence-backed replies.
          </p>
          <div className="preview-callout">
            <Info size={18} />
            <span>
              This build opens in preview. No accounts are authorized and no
              messages are sent. An existing local backend can be connected in
              Settings.
            </span>
          </div>
          <div className="row-actions">
            {window.virtualYou && (
              <Button
                variant="primary"
                onClick={() => {
                  act({ type: "welcome" });
                  setView("settings");
                }}
              >
                Connect local backend <ArrowRight size={17} />
              </Button>
            )}
            <Button onClick={() => act({ type: "welcome" })}>
              Explore sample preview
            </Button>
          </div>
        </Modal>
      )}
      {dialog?.kind === "connect" && (
        <Modal
          title={
            state.mode === "local"
              ? `${providers[dialog.provider].name} setup`
              : `Connect ${providers[dialog.provider].name}`
          }
          onClose={() => setDialog(null)}
        >
          <p>{providers[dialog.provider].permission}</p>
          {state.mode === "preview" ? (
            <>
              <div className="preview-callout">
                <Info size={18} />
                <span>
                  Preview connection only. The production flow will open your
                  provider’s authorization page in your browser.
                </span>
              </div>
              <p className="fine-print">
                You will never be asked for a developer secret or a
                configuration file in normal onboarding.
              </p>
              <div className="modal-actions">
                <Button onClick={() => setDialog(null)}>Cancel</Button>
                <Button
                  variant="primary"
                  onClick={() => {
                    const provider = dialog.provider;
                    setDialog(null);
                    void run(
                      () => bridge.act({ type: "connect", provider }),
                      "Example connection added. No account was authorized.",
                    );
                  }}
                >
                  Use example connection <ArrowRight size={16} />
                </Button>
              </div>
            </>
          ) : (
            <>
              <div className="preview-callout">
                <Info size={18} />
                <span>
                  Your backend manages this integration. Configuration status
                  appears on the Integrations screen; new account authorization
                  is separate from this local workspace.
                </span>
              </div>
              <Button onClick={() => setDialog(null)}>Got it</Button>
            </>
          )}
        </Modal>
      )}
      {dialog?.kind === "decision" && (
        <Modal
          title={
            dialog.approve ? approvalCopy(state).title : "Reject this draft?"
          }
          onClose={() => setDialog(null)}
        >
          <p>
            {dialog.approve
              ? approvalCopy(state, dialog.draft.target).description
              : "The draft will be rejected. Nothing will be sent."}
          </p>
          <div className="confirmation-message">{dialog.draft.text}</div>
          <div className="modal-actions">
            <Button onClick={() => setDialog(null)}>Keep reviewing</Button>
            <Button
              variant={dialog.approve ? "primary" : "danger"}
              disabled={
                busy ||
                reviewUnavailable(state) ||
                (dialog.approve && state.paused)
              }
              onClick={() => {
                const { draft, approve } = dialog;
                setDialog(null);
                void run(() =>
                  bridge.act({
                    type: "decision",
                    id: draft.id,
                    revision: draft.revision,
                    action: approve ? "approve" : "reject",
                  }),
                );
              }}
            >
              {dialog.approve ? approvalCopy(state).label : "Reject draft"}
              {dialog.approve &&
                state.mode === "local" &&
                state.deliveryMode === "live" && <ExternalLink size={15} />}
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}
