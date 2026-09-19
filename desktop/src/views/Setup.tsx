import {
  LockKeyhole,
  Check,
  ShieldCheck,
  ArrowLeft,
  FolderGit2,
  Sparkles,
} from "lucide-react";
import type { Workspace, Action, ProviderId } from "../../shared/model";
import { Button, Stepper, Arrow } from "../components/ui";
import { IntegrationList } from "../components/IntegrationList";
export function Projects({
  state,
  act,
  busy,
}: {
  state: Workspace;
  act: (a: Action) => void;
  busy: boolean;
}) {
  return (
    <div className="project-list">
      {state.projects.map((p) => (
        <label
          className={`project-row ${p.selected ? "selected" : ""}`}
          key={p.id}
        >
          <span className="project-icon">
            <FolderGit2 size={23} />
          </span>
          <span className="project-copy">
            <strong>{p.name}</strong>
            <small>{p.detail}</small>
          </span>
          {state.mode === "local" ? (
            <span className="indexed-badge">Indexed</span>
          ) : (
            <input
              type="checkbox"
              checked={p.selected}
              disabled={busy}
              onChange={(e) =>
                act({ type: "project", id: p.id, selected: e.target.checked })
              }
              aria-label={`Include ${p.name}`}
            />
          )}
        </label>
      ))}
    </div>
  );
}
export function Setup({
  state,
  act,
  onConnect,
  goApprovals,
  busy,
}: {
  state: Workspace;
  act: (a: Action) => void;
  onConnect: (p: ProviderId) => void;
  goApprovals: () => void;
  busy: boolean;
}) {
  if (state.step === "complete")
    return (
      <div className="completion">
        <span className="completion-symbol">
          <Check size={32} />
        </span>
        <h1>Your workspace is taking shape.</h1>
        <p>
          {state.mode === "preview"
            ? "You’ve completed the preview. Try reviewing a sample reply next."
            : "Your local evidence is available to review."}
        </p>
        <div className="completion-summary">
          <div>
            <strong>
              {state.integrations.filter((i) => i.state === "connected").length}
            </strong>
            <span>tools connected</span>
          </div>
          <div>
            <strong>{state.projects.filter((p) => p.selected).length}</strong>
            <span>projects selected</span>
          </div>
          <div>
            <ShieldCheck />
            <span>Approval required</span>
          </div>
        </div>
        <Button variant="primary" onClick={goApprovals}>
          Try a sample approval <Arrow />
        </Button>
        <p className="fine-print">
          Preview actions never contact your colleagues.
        </p>
      </div>
    );
  return (
    <>
      <div className="page-intro">
        <h1>Make room for your real work.</h1>
        <p>Connect your tools. Choose what to share. Stay in control.</p>
      </div>
      <Stepper step={state.step} />
      <section className="setup-body">
        {state.step === "connect" && (
          <>
            <h2>Your work, connected</h2>
            <p className="section-description">
              Choose the tools you use. You can add more later.
            </p>
            <IntegrationList
              items={state.integrations}
              onConnect={onConnect}
              busy={busy}
            />
            <div className="privacy-note">
              <LockKeyhole size={19} />
              <span>
                You approve every reply. Your raw editor history stays on this
                device.
              </span>
            </div>
          </>
        )}
        {state.step === "projects" && (
          <>
            <h2>A little context. The right context.</h2>
            <p className="section-description">
              Choose which projects VirtualYou can use. You stay in charge of
              who sees them.
            </p>
            <Projects state={state} act={act} busy={busy} />
            <div className="privacy-note">
              <ShieldCheck size={19} />
              <span>
                Project selection does not give every colleague access. Set each
                audience’s permissions in Slack.
              </span>
            </div>
          </>
        )}
        {state.step === "review" && (
          <>
            <h2>Set up to keep you in control.</h2>
            <p className="section-description">
              These are your starting defaults. No surprises, no background
              sends.
            </p>
            <div className="review-rows">
              <div>
                <ShieldCheck />
                <span>
                  <strong>Every reply comes to you first</strong>
                  <small>Read, edit, or reject a draft before it leaves.</small>
                </span>
                <Check />
              </div>
              <div>
                <FolderGit2 />
                <span>
                  <strong>
                    {state.projects
                      .filter((p) => p.selected)
                      .map((p) => p.name)
                      .join(", ")}
                  </strong>
                  <small>
                    Only selected projects are included in this preview.
                  </small>
                </span>
                <Check />
              </div>
              <div>
                <LockKeyhole />
                <span>
                  <strong>Automatic replies are off</strong>
                  <small>
                    This desktop MVP does not change your existing Slack
                    delivery settings.
                  </small>
                </span>
                <Check />
              </div>
            </div>
            <div className="preview-callout">
              <Sparkles size={18} />
              <span>
                This is a guided preview. Production account pairing and hosted
                OAuth will be connected in the next milestone.
              </span>
            </div>
          </>
        )}
      </section>
      <footer className="wizard-footer">
        <div>
          {state.step !== "connect" ? (
            <Button
              variant="ghost"
              onClick={() => act({ type: "back" })}
              disabled={busy}
            >
              <ArrowLeft size={16} />
              Back
            </Button>
          ) : (
            <span>Step 1 of 3</span>
          )}
        </div>
        <Button
          variant="primary"
          onClick={() => act({ type: "next" })}
          disabled={busy}
        >
          {state.step === "review" ? "Finish preview" : "Continue"}
          <Arrow />
        </Button>
      </footer>
    </>
  );
}
