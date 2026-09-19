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
import { collectionLabel, timestamp } from "../../shared/activity";
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
export function ActivityView({
  state,
  onRefresh,
  onCollect,
  busy,
  onReview,
}: {
  state: Workspace;
  onRefresh: () => void;
  onCollect: () => void;
  busy: boolean;
  onReview: () => void;
}) {
  const [kind, setKind] = useState("all");
  const [project, setProject] = useState("all");
  const collection = state.collection;
  const items = state.activity.filter(
    (item) =>
      (kind === "all" || item.kind === kind) &&
      (project === "all" || item.projectId === project),
  );
  return (
    <>
      <div className="page-intro">
        <h1>A clear view of what’s happening.</h1>
        <p>Collected work, drafts, and review decisions from your backend.</p>
        <div className="row-actions">
          <Button
            variant="primary"
            onClick={onCollect}
            disabled={busy || state.mode !== "local"}
          >
            <RefreshCw size={17} /> {busy ? "Checking…" : "Check activity now"}
          </Button>
          <Button variant="ghost" onClick={onRefresh} disabled={busy}>
            Refresh view
          </Button>
        </div>
        {state.mode === "local" && (
          <p className="fine-print">
            Checks only your configured work sources. This does not draft or
            send a message.
          </p>
        )}
      </div>
      {collection && (
        <>
          <div className="collection-summary">
            <div>
              <small>Collection</small>
              <strong>{collectionLabel(collection)}</strong>
              <span>
                {collection.enabled
                  ? "Background refresh enabled"
                  : "Manual refresh"}
              </span>
            </div>
            <div>
              <small>Indexed activities</small>
              <strong>{collection.record_count}</strong>
              <span>
                {collection.changed} changed · {collection.unchanged} unchanged
                on last check
              </span>
            </div>
            <div>
              <small>Last successful check</small>
              <strong>{timestamp(collection.last_success_at)}</strong>
              <span>Last attempt: {timestamp(collection.last_attempt_at)}</span>
            </div>
          </div>
          {!collection.configured && (
            <div className="notice" role="status">
              No collector or feed is configured. Imported activity can still
              appear below; add work sources through your local backend
              configuration.
            </div>
          )}
          {collection.error_count > 0 && (
            <div className="notice error collection-errors" role="alert">
              <strong>
                {collection.error_count} collection issue
                {collection.error_count === 1 ? "" : "s"}. Existing records
                remain visible.
              </strong>
              <ul>
                {collection.errors.map((error, i) => (
                  <li key={`${error.code}-${i}`}>
                    {error.source}: {error.message}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
      <div className="activity-filters">
        <label>
          Show
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="all">All activity</option>
            <option value="activity">Collected work</option>
            <option value="draft_event">Drafts & decisions</option>
          </select>
        </label>
        <label>
          Project
          <select value={project} onChange={(e) => setProject(e.target.value)}>
            <option value="all">All projects</option>
            {state.projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <span>
          {items.length} recent events
          {state.activityHasMore ? " · latest 50 shown" : ""}
        </span>
      </div>
      {items.length ? (
        <div className="timeline">
          {items.map((item) => (
            <details className="activity-event" key={item.id}>
              <summary className="timeline-row">
                <span className="timeline-icon">
                  <ActivityIcon size={18} />
                </span>
                <span className="event-heading">
                  <strong>{item.title}</strong>
                  <small>
                    {item.source}
                    {item.projectId ? ` · ${item.projectId}` : ""}
                  </small>
                </span>
                <time dateTime={item.at}>{timestamp(item.at)}</time>
                <ChevronRight size={16} />
              </summary>
              <div className="event-body">
                {item.summary && <p>{item.summary}</p>}
                {item.kind === "activity" && (
                  <p className="fine-print">
                    {item.filesChanged ?? 0} files changed ·{" "}
                    {item.toolCalls ?? 0} tool calls. Summary reflects recorded
                    work, not independent verification.
                  </p>
                )}
                {item.draftId && (
                  <Button variant="ghost" onClick={onReview}>
                    Open approvals <ChevronRight size={15} />
                  </Button>
                )}
              </div>
            </details>
          ))}
        </div>
      ) : (
        <EmptyState
          title={
            state.activity.length
              ? "No events match these filters"
              : "No activity recorded yet"
          }
        >
          <p>
            {state.mode === "local"
              ? "Check your configured sources, or create a draft in Slack. Collected work and review decisions will appear here."
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
          ["Activity collection", collectionLabel(state.collection)],
          ["Indexed activities", String(state.collection?.record_count ?? 0)],
          ["Text generation", state.provider || "Sample preview"],
          [
            "Report delivery",
            state.deliveryMode === "live"
              ? "Live — approval required"
              : "Simulation — no report sent",
          ],
          [
            "Slack workflow",
            state.workflowAvailable
              ? state.paused
                ? "Paused"
                : "Running"
              : "Not available on this backend",
          ],
          ["Hosted onboarding", "Separate from this local connection"],
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
  const [port, setPort] = useState(String(state.backendPort || 8000));
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
          <h3>
            {state.mode === "local"
              ? "Pause Slack workflow"
              : "Pause the preview"}
          </h3>
          <p>
            {state.mode === "local"
              ? state.workflowAvailable
                ? "Pause Slack drafting and replies. Activity collection continues; already-sent messages are unaffected."
                : "Connect to the Slack backend to control its workflow."
              : "Stop sample generation without losing your setup."}
          </p>
        </div>
        <button
          className={`switch ${state.paused ? "on" : ""}`}
          role="switch"
          aria-checked={state.paused}
          aria-label={
            state.mode === "local" ? "Pause Slack workflow" : "Pause preview"
          }
          disabled={
            busy ||
            (state.mode === "local" &&
              (!state.workflowAvailable || state.health !== "ready"))
          }
          onClick={() => act({ type: "pause", value: !state.paused })}
        >
          <span />
        </button>
      </div>
      <details className="advanced" open={state.mode === "preview"}>
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
            placeholder="8000"
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
