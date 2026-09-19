import { useState } from "react";
import {
  Activity as ActivityIcon,
  RefreshCw,
  ShieldCheck,
  Copy,
  ChevronRight,
  Monitor,
  Link2,
} from "lucide-react";
import type { Action, Workspace } from "../../shared/model";
import { Button, EmptyState } from "../components/ui";
import { Projects } from "./Setup";
export function ProjectView({
  state,
  act,
  busy,
}: {
  state: Workspace;
  act: (a: Action) => void;
  busy: boolean;
}) {
  return (
    <>
      <div className="page-intro">
        <h1>Choose what’s in the picture.</h1>
        <p>Your projects are the boundaries of your assistant’s knowledge.</p>
      </div>
      <h2>
        {state.mode === "preview" ? "Example projects" : "Indexed projects"}
      </h2>
      <p className="section-description">
        {state.mode === "preview"
          ? "Select a project to include it in the guided preview."
          : "These projects come from your existing backend. Manage collection and recipient access in Slack."}
      </p>
      {state.projects.length ? (
        <Projects state={state} act={act} busy={busy} />
      ) : (
        <EmptyState title="No project evidence yet">
          <p>
            Connect your local collector and allow a project in Slack to get
            started.
          </p>
        </EmptyState>
      )}
      <div className="privacy-note">
        <ShieldCheck size={19} />
        <span>
          A colleague only gets evidence from projects you have allowed them to
          access.
        </span>
      </div>
    </>
  );
}
export function ActivityView({ state }: { state: Workspace }) {
  return (
    <>
      <div className="page-intro">
        <h1>A clear view of what’s happening.</h1>
        <p>Collection, drafts, and decisions. No raw editor history here.</p>
      </div>
      {state.activity.length ? (
        <div className="timeline">
          {state.activity.map((item) => (
            <div className="timeline-row" key={item.id}>
              <span className="timeline-icon">
                <ActivityIcon size={18} />
              </span>
              <div>
                <strong>{item.title}</strong>
                <small>{item.source}</small>
              </div>
              <time>
                {new Date(item.at).toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </time>
            </div>
          ))}
        </div>
      ) : (
        <EmptyState
          title={
            state.mode === "local"
              ? "Detailed activity lives in your backend"
              : "Your activity will appear here"
          }
        >
          <p>
            {state.mode === "local"
              ? "This UI build shows backend health and report approvals. The full activity feed is a later API integration."
              : "Connect example tools and prepare a sample draft to explore the flow."}
          </p>
        </EmptyState>
      )}
    </>
  );
}
export function Diagnostics({
  state,
  onCopy,
  onRefresh,
  busy,
}: {
  state: Workspace;
  onCopy: () => void;
  onRefresh: () => void;
  busy: boolean;
}) {
  return (
    <>
      <div className="page-intro">
        <h1>Clarity when you need it.</h1>
        <p>
          Connection details you can share with support. Credentials stay
          private.
        </p>
      </div>
      <div className="diagnostic-table">
        {[
          ["Desktop version", state.version],
          [
            "Workspace mode",
            state.mode === "preview"
              ? "Preview — sample data only"
              : "Existing local backend",
          ],
          [
            "Local connection",
            state.health === "ready"
              ? "Responding"
              : state.health === "preview"
                ? "Not connected in preview"
                : "Unavailable",
          ],
          [
            "Last checked",
            state.lastRefresh
              ? new Date(state.lastRefresh).toLocaleString()
              : "Not checked yet",
          ],
          ["Hosted onboarding", "Not configured in this build"],
          ["Automatic delivery", "Not controlled by this desktop MVP"],
        ].map(([label, value]) => (
          <div key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
      <div className="row-actions">
        <Button onClick={onRefresh} disabled={busy}>
          <RefreshCw size={16} />
          Check again
        </Button>
        <Button onClick={onCopy}>
          <Copy size={16} />
          Copy safe diagnostics
        </Button>
      </div>
    </>
  );
}
export function Settings({
  state,
  act,
  onConnect,
  busy,
}: {
  state: Workspace;
  act: (a: Action) => void;
  onConnect: (port: number) => void;
  busy: boolean;
}) {
  const [port, setPort] = useState("3000");
  return (
    <>
      <div className="page-intro">
        <h1>Comfortably in control.</h1>
        <p>Make space for your work. Keep the final say.</p>
      </div>
      <div className="setting-row">
        <ShieldCheck />
        <div>
          <h3>Approval comes first</h3>
          <p>Automatic replies cannot be enabled from this desktop MVP.</p>
        </div>
        <span className="connected">Default</span>
      </div>
      <div className="setting-row">
        <ActivityIcon />
        <div>
          <h3>Pause the preview</h3>
          <p>
            {state.mode === "local"
              ? "Use Slack’s pause control to stop the existing workflow."
              : "Stop sample generation without losing your setup."}
          </p>
        </div>
        <button
          className={`switch ${state.paused ? "on" : ""}`}
          role="switch"
          aria-checked={state.paused}
          aria-label="Pause preview"
          disabled={state.mode === "local" || busy}
          onClick={() => act({ type: "pause", value: !state.paused })}
        >
          <span />
        </button>
      </div>
      <details className="advanced">
        <summary>
          <Monitor size={19} />
          Developer / Advanced
          <ChevronRight size={17} />
        </summary>
        <p>
          Connect an already-running backend on this Mac. Choose its private
          data folder using the file picker. The access key is read by the
          desktop process and protected with system encryption; it is never
          shown in this interface.
        </p>
        <label className="field-label" htmlFor="port">
          Local backend port
        </label>
        <div className="local-connect">
          <input
            id="port"
            inputMode="numeric"
            value={port}
            onChange={(e) => setPort(e.target.value)}
            placeholder="3000"
          />
          <Button
            disabled={busy || !/^\d+$/.test(port)}
            onClick={() => onConnect(Number(port))}
          >
            <Link2 size={16} />
            Choose data folder
          </Button>
        </div>
        <p className="fine-print">
          Your existing service stays in charge. This UI does not restart it,
          change its credentials, or alter Slack permissions.
        </p>
        {state.mode === "local" && (
          <Button variant="ghost" onClick={() => act({ type: "preview" })}>
            Disconnect desktop & return to preview
          </Button>
        )}
      </details>
    </>
  );
}
