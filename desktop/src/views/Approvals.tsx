import { useState } from "react";
import {
  Check,
  X,
  FileText,
  ArrowUpRight,
  ShieldCheck,
  RefreshCw,
  ChevronDown,
} from "lucide-react";
import type { Workspace, Draft } from "../../shared/model";
import { approvalCopy, reviewUnavailable } from "../../shared/review";
import { Button, EmptyState } from "../components/ui";

function draftStatus(draft: Draft, state: Workspace) {
  if (draft.status === "pending") return "Ready for review";
  if (draft.status === "simulated")
    return state.mode === "preview"
      ? "Approved in preview"
      : "Delivery simulated";
  return draft.status;
}

function Evidence({ draft }: { draft: Draft }) {
  const [expanded, setExpanded] = useState<string[]>([]);
  const keys = draft.evidence.map((e, i) => `${e.id}-${i}`);
  return (
    <details className="evidence-section">
      <summary>
        <FileText size={17} /> Evidence behind this reply
        <span>{draft.evidence.length} sources</span>
        <ChevronDown size={16} />
      </summary>
      {keys.length > 0 && (
        <div className="disclosure-tools">
          <Button variant="ghost" onClick={() => setExpanded(keys)}>
            Expand all sources
          </Button>
          <Button variant="ghost" onClick={() => setExpanded([])}>
            Collapse all sources
          </Button>
        </div>
      )}
      <div className="evidence-list">
        {draft.evidence.map((e, i) => {
          const key = keys[i];
          const open = expanded.includes(key);
          return (
            <section className="evidence" key={key}>
              <button
                className="evidence-toggle"
                aria-expanded={open}
                aria-controls={`source-${draft.id}-${i}`}
                onClick={() =>
                  setExpanded((current) =>
                    open ? current.filter((k) => k !== key) : [...current, key],
                  )
                }
              >
                <span>
                  {String(i + 1).padStart(2, "0")} · {e.source}
                </span>
                <ChevronDown size={16} className={open ? "rotated" : ""} />
              </button>
              {open && (
                <blockquote id={`source-${draft.id}-${i}`}>{e.text}</blockquote>
              )}
            </section>
          );
        })}
      </div>
    </details>
  );
}

export function Approvals({
  state,
  onSample,
  onDecision,
  onRefresh,
  busy,
}: {
  state: Workspace;
  onSample: () => void;
  onDecision: (d: Draft, approve: boolean) => void;
  onRefresh: () => void;
  busy: boolean;
}) {
  // Independent rows allow comparing several drafts without forcing one open.
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [showResolved, setShowResolved] = useState(true);
  const initial =
    state.drafts.find((d) => d.status === "pending") || state.drafts[0];
  const drafts = state.drafts.filter(
    (d) => showResolved || d.status === "pending",
  );
  const unavailable = reviewUnavailable(state);
  return (
    <>
      <div className="page-intro">
        <h1>A final word. Yours.</h1>
        <p>
          Review the message and the evidence behind it before anything is sent.
        </p>
        {state.mode === "local" && (
          <Button variant="ghost" onClick={onRefresh} disabled={busy}>
            <RefreshCw size={17} /> Refresh drafts
          </Button>
        )}
      </div>
      {state.drafts.length > 0 && (
        <div className="approval-toolbar">
          <div className="disclosure-tools">
            <Button
              variant="ghost"
              onClick={() =>
                setExpanded(
                  Object.fromEntries(state.drafts.map((d) => [d.id, true])),
                )
              }
            >
              Expand all drafts
            </Button>
            <Button
              variant="ghost"
              onClick={() =>
                setExpanded(
                  Object.fromEntries(state.drafts.map((d) => [d.id, false])),
                )
              }
            >
              Collapse all drafts
            </Button>
          </div>
          <label className="resolved-filter">
            <input
              type="checkbox"
              checked={showResolved}
              onChange={(e) => setShowResolved(e.target.checked)}
            />{" "}
            Show resolved
          </label>
        </div>
      )}
      {!drafts.length ? (
        <EmptyState title="Nothing waiting for your approval">
          <p>New drafts will appear here with the sources that support them.</p>
          {state.mode === "preview" && (
            <Button variant="primary" onClick={onSample} disabled={busy}>
              Prepare a sample reply <ArrowUpRight size={17} />
            </Button>
          )}
        </EmptyState>
      ) : (
        <div className="approval-stack">
          {drafts.map((draft) => {
            const open = expanded[draft.id] ?? draft.id === initial?.id;
            return (
              <article className="draft-card" key={draft.id}>
                <button
                  className="draft-card-toggle"
                  aria-expanded={open}
                  aria-controls={`draft-${draft.id}`}
                  onClick={() =>
                    setExpanded((current) => ({
                      ...current,
                      [draft.id]: !open,
                    }))
                  }
                >
                  <span className="avatar">
                    {draft.recipient.slice(0, 1).toUpperCase()}
                  </span>
                  <span className="draft-card-title">
                    <strong>{draft.recipient}</strong>
                    <small>
                      {draft.target} · Revision {draft.revision}
                    </small>
                  </span>
                  <span
                    className={`draft-status ${draft.status === "pending" ? "pending" : ""}`}
                  >
                    {draftStatus(draft, state)}
                  </span>
                  <ChevronDown size={19} className={open ? "rotated" : ""} />
                </button>
                {open && (
                  <div className="draft-detail" id={`draft-${draft.id}`}>
                    <div className="draft-heading">
                      <span className="status-dot" />
                      <span>
                        {state.mode === "preview"
                          ? "Example Slack reply"
                          : "Backend report draft"}
                      </span>
                    </div>
                    <h2>Reply to {draft.recipient}</h2>
                    <div className="draft-text">{draft.text}</div>
                    <Evidence
                      key={`${draft.id}-${draft.revision}`}
                      draft={draft}
                    />
                    <div className="privacy-note">
                      <ShieldCheck size={17} />
                      <span>
                        Source references help you review. They do not guarantee
                        every claim is correct.
                      </span>
                    </div>
                    <div className="approval-actions">
                      {draft.status === "pending" ? (
                        <>
                          <Button
                            variant="ghost"
                            disabled={busy || unavailable}
                            onClick={() => onDecision(draft, false)}
                          >
                            <X size={17} /> Reject
                          </Button>
                          <Button
                            variant="primary"
                            disabled={busy || state.paused || unavailable}
                            onClick={() => onDecision(draft, true)}
                          >
                            <Check size={17} />
                            {approvalCopy(state).label}
                          </Button>
                        </>
                      ) : (
                        <span className="resolved">
                          {draft.status === "simulated"
                            ? state.mode === "preview"
                              ? "Approved in preview. No message was sent."
                              : "Delivery simulated by your backend. No message was sent."
                            : `This draft is ${draft.status}.`}
                        </span>
                      )}
                    </div>
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}
    </>
  );
}
