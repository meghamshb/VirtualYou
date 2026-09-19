import { useState } from "react";
import { Check, X, FileText, ArrowUpRight, ShieldCheck } from "lucide-react";
import type { Workspace, Draft } from "../../shared/model";
import { Button, EmptyState } from "../components/ui";
export function Approvals({
  state,
  onSample,
  onDecision,
  busy,
}: {
  state: Workspace;
  onSample: () => void;
  onDecision: (d: Draft, approve: boolean) => void;
  busy: boolean;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const draft = state.drafts.find((d) => d.id === selected) || state.drafts[0];
  return (
    <>
      <div className="page-intro">
        <h1>A final word. Yours.</h1>
        <p>
          Review the message and the evidence behind it before anything is sent.
        </p>
      </div>
      {!draft ? (
        <EmptyState title="Nothing waiting for your approval">
          <p>New drafts will appear here with the sources that support them.</p>
          {state.mode === "preview" && (
            <Button variant="primary" onClick={onSample} disabled={busy}>
              Prepare a sample reply <ArrowUpRight size={17} />
            </Button>
          )}
        </EmptyState>
      ) : (
        <div className="approval-layout">
          <div className="draft-list">
            {state.drafts.map((d) => (
              <button
                key={d.id}
                className={`draft-list-item ${d.id === draft.id ? "chosen" : ""}`}
                onClick={() => setSelected(d.id)}
              >
                <span className="avatar">
                  {d.recipient.slice(0, 1).toUpperCase()}
                </span>
                <span>
                  <strong>{d.recipient}</strong>
                  <small>
                    {d.status === "pending"
                      ? "Ready for review"
                      : d.status === "simulated"
                        ? "Approved in preview"
                        : d.status}
                  </small>
                </span>
              </button>
            ))}
          </div>
          <article className="draft-detail">
            <div className="draft-heading">
              <span className="status-dot" />
              <span>
                {state.mode === "preview"
                  ? "Example Slack reply"
                  : "Backend report draft"}
              </span>
              <span className="draft-revision">Revision {draft.revision}</span>
            </div>
            <h2>Reply to {draft.recipient}</h2>
            <p className="destination">Destination: {draft.target}</p>
            <div className="draft-text">{draft.text}</div>
            <details open>
              <summary>
                <FileText size={17} /> Evidence behind this reply{" "}
                <span>{draft.evidence.length} sources</span>
              </summary>
              <div className="evidence-list">
                {draft.evidence.map((e, i) => (
                  <div className="evidence" key={`${e.id}-${i}`}>
                    <span>
                      {String(i + 1).padStart(2, "0")} · {e.source}
                    </span>
                    <blockquote>{e.text}</blockquote>
                  </div>
                ))}
              </div>
            </details>
            <div className="privacy-note">
              <ShieldCheck size={17} />
              <span>
                Source references help you review. They do not guarantee every
                claim is correct.
              </span>
            </div>
            <div className="approval-actions">
              {draft.status === "pending" ? (
                <>
                  <Button
                    variant="ghost"
                    disabled={busy}
                    onClick={() => onDecision(draft, false)}
                  >
                    <X size={17} /> Reject
                  </Button>
                  <Button
                    variant="primary"
                    disabled={busy || state.paused}
                    onClick={() => onDecision(draft, true)}
                  >
                    <Check size={17} />
                    {state.mode === "preview"
                      ? "Approve sample"
                      : "Approve & send"}
                  </Button>
                </>
              ) : (
                <span className="resolved">
                  {draft.status === "simulated"
                    ? "Approved in preview. No message was sent."
                    : `This draft is ${draft.status}.`}
                </span>
              )}
            </div>
          </article>
        </div>
      )}
    </>
  );
}
