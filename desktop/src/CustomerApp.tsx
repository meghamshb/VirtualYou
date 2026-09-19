import { useEffect, useState, useRef } from "react";
import {
  Diamond,
  ArrowRight,
  Check,
  FolderPlus,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import type { CustomerAction } from "../electron/customer";
import { Button, Modal } from "./components/ui";
import App from "./App";
import type { CustomerStatus, CustomerDraft } from "../shared/customer";

declare global {
  interface Window {
    virtualYouCustomer?: { act: (a: CustomerAction) => Promise<unknown> };
  }
}
type Option = { id: string; name: string };
export default function CustomerApp() {
  const [preview, setPreview] = useState(false);
  const [status, setStatus] = useState<CustomerStatus | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [options, setOptions] = useState<Record<string, Option[]>>({});
  const [resources, setResources] = useState<
    {
      provider: "github" | "jira" | "drive";
      resource: string;
      project: string;
    }[]
  >([]);
  const [people, setPeople] = useState<string[]>([]);
  const [question, setQuestion] = useState(
    "What changed in my selected projects? Include progress and blockers.",
  );
  const [confirm, setConfirm] = useState<CustomerDraft | null>(null);
  const api = async (a: CustomerAction) => {
    if (!window.virtualYouCustomer)
      throw Error("Use the installed desktop app for customer onboarding.");
    return window.virtualYouCustomer.act(a);
  };
  const hydrated = useRef(false);
  const load = async () => {
    const s = (await api({ type: "status" })) as CustomerStatus;
    setStatus(s);
    if (!hydrated.current && s.setup) {
      setResources(
        s.setup.resources.filter(
          (r) => r.provider !== "slack",
        ) as typeof resources,
      );
      setPeople(s.setup.people);
      hydrated.current = true;
    }
  };
  const run = async (a: CustomerAction) => {
    setBusy(true);
    setError("");
    try {
      const r = await api(a);
      await load();
      return r;
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "This step failed. Please try again.",
      );
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    void load().catch((e) => setError(e.message));
    const timer = setInterval(() => {
      void api({ type: "poll" })
        .then(load)
        .catch(() => {});
    }, 5000);
    return () => clearInterval(timer);
  }, []);
  async function list(
    provider: "slack" | "github" | "jira" | "drive",
    parent = "",
  ) {
    setBusy(true);
    try {
      const items = (await api({
        type: "resources",
        provider,
        parent,
      })) as Option[];
      setOptions((o) => ({ ...o, [provider]: items }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  if (preview) return <App />;
  const setup = status?.setup;
  return (
    <div className="customer-shell">
      <header className="customer-header">
        <strong>
          <Diamond size={24} /> VirtualYou
        </strong>
        <span>Your work. Your voice. Your control.</span>
        <Button variant="ghost" onClick={() => setPreview(true)}>
          Developer / preview
        </Button>
      </header>
      <main className="customer-main">
        <h1>
          {setup?.step === "complete"
            ? "Your VirtualYou is connected."
            : "Make room for your real work."}
        </h1>
        <p className="customer-intro">
          Connect your accounts once. Choose what to share. Approve every reply.
        </p>
        {status?.system && (
          <div className="privacy-note">
            <ShieldCheck size={18} />
            <span>
              {status.system.supported
                ? "macOS supported"
                : "This build supports macOS"}{" "}
              ·{" "}
              {status.system.collectorReady
                ? "Local collector ready"
                : "Collector needs attention"}{" "}
              ·{" "}
              {status.system.privateStorage
                ? "Private device storage"
                : "Unlock secure device storage"}
            </span>
          </div>
        )}
        {error && (
          <div role="alert" className="notice error">
            {error}
          </div>
        )}
        {status?.error && (
          <div role="status" className="notice">
            {status.error}
          </div>
        )}
        {!status?.configured && (
          <section>
            <h2>Customer service is not available yet</h2>
            <p>
              This build needs the operator’s hosted service address. You don’t
              need to create developer apps, edit files, or supply secrets.
            </p>
            <Button
              onClick={() => void load().catch((e) => setError(e.message))}
            >
              Check again
            </Button>
          </section>
        )}
        {status?.configured && !status.connected && (
          <section>
            <h2>1. Connect your Slack account</h2>
            <p>
              We’ll open your browser. Slack will show the workspace and
              permissions before you authorize.
            </p>
            <Button
              variant="primary"
              disabled={busy || !status.serviceReady || !status.system.collectorReady || !status.system.privateStorage}
              onClick={() => void run({ type: "pair" })}
            >
              Connect Slack <ArrowRight size={16} />
            </Button>
            {status.pairing && (
              <div className="pair-code">
                <p>Enter this code in the browser you just opened:</p>
                <strong>{status.pairing.user_code}</strong>
                <p>Waiting for your authorization…</p>
              </div>
            )}
          </section>
        )}
        {status?.connected && (
          <>
            <section>
              <h2>1. Your connected accounts</h2>
              {setup?.last_refresh && (
                <p>
                  Last evidence refresh:{" "}
                  {new Date(setup.last_refresh * 1000).toLocaleString()}
                </p>
              )}
              {setup?.refresh_results
                ?.filter((r) => !r.ok)
                .map((r) => (
                  <p role="status" key={r.provider}>
                    {r.provider} refresh failed. Check the connection and try
                    Refresh evidence again.
                  </p>
                ))}
              <div className="integration-list">
                {setup?.integrations.map((i) => (
                  <div className="integration-row" key={i.id}>
                    <div>
                      <strong>
                        {
                          (
                            {
                              slack: "Slack",
                              github: "GitHub",
                              jira: "Jira",
                              drive: "Google Drive",
                            } as const
                          )[i.id]
                        }
                      </strong>
                      <p>
                        {i.connected ? i.label : i.error || "Not connected"}
                      </p>
                    </div>
                    <div className="customer-actions">
                      {i.connected && (
                        <Button disabled={busy} onClick={() => void list(i.id)}>
                          Choose resources
                        </Button>
                      )}
                      <Button
                        disabled={busy || !i.configured}
                        onClick={() =>
                          void run({ type: "connect", provider: i.id })
                        }
                      >
                        {i.connected ? "Reconnect" : "Connect"}
                      </Button>
                      {i.connected && (
                        <Button
                          variant="ghost"
                          onClick={() =>
                            void run({ type: "disconnect", provider: i.id })
                          }
                        >
                          Disconnect
                        </Button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </section>
            <section>
              <h2>2. Choose projects and colleagues</h2>
              <p>
                Only selected local projects are collected. Raw editor logs and
                code patches stay on this device.
              </p>
              <Button
                onClick={() => void run({ type: "add-project" })}
                disabled={busy}
              >
                <FolderPlus size={18} /> Choose local project
              </Button>
              <ul>
                {status!.projects.map((p: Option) => (
                  <li key={p.id}>{p.name}</li>
                ))}
              </ul>
              {Object.entries(options).map(([provider, items]) => (
                <div key={provider}>
                  <h3>
                    {provider === "slack"
                      ? "Colleagues"
                      : provider + " resources"}
                  </h3>
                  {items.length === 0 && (
                    <p>No accessible resources were returned.</p>
                  )}
                  {items.map((item) =>
                    provider === "slack" ? (
                      <label className="customer-choice" key={item.id}>
                        <input
                          type="checkbox"
                          checked={people.includes(item.id)}
                          onChange={(e) =>
                            setPeople((old) =>
                              e.target.checked
                                ? [...old, item.id]
                                : old.filter((x) => x !== item.id),
                            )
                          }
                        />
                        {item.name}
                      </label>
                    ) : provider === "jira" && !item.id.includes(":") ? (
                      <Button
                        key={item.id}
                        onClick={() => void list("jira", item.id)}
                      >
                        {item.name}
                      </Button>
                    ) : (
                      <label className="customer-choice" key={item.id}>
                        <span>{item.name}</span>
                        <select
                          aria-label={`Project for ${item.name}`}
                          value={
                            resources.find(
                              (r) =>
                                r.provider === provider &&
                                r.resource === item.id,
                            )?.project || ""
                          }
                          onChange={(e) =>
                            setResources((old) => [
                              ...old.filter(
                                (r) =>
                                  !(
                                    r.provider === provider &&
                                    r.resource === item.id
                                  ),
                              ),
                              ...(e.target.value
                                ? [
                                    {
                                      provider: provider as
                                        "github" | "jira" | "drive",
                                      resource: item.id,
                                      project: e.target.value,
                                    },
                                  ]
                                : []),
                            ])
                          }
                        >
                          <option value="">Don’t share</option>
                          {status!.projects.map((p: Option) => (
                            <option key={p.id} value={p.id}>
                              {p.name}
                            </option>
                          ))}
                        </select>
                      </label>
                    ),
                  )}
                </div>
              ))}
              <Button
                variant="primary"
                disabled={busy || !status?.projects.length}
                onClick={() => void run({ type: "select", resources, people })}
              >
                Save project access
              </Button>
            </section>
            {(setup?.projects?.length || 0) > 0 && (
              <>
                <section>
                  <h2>3. Verify evidence and your first reply</h2>
                  <p>
                    This test creates a draft for your own Slack app
                    conversation. It sends only after you approve.
                  </p>
                  <div className="customer-actions">
                    <Button
                      disabled={busy}
                      onClick={() => void run({ type: "collect" })}
                    >
                      <RefreshCw size={17} /> Collect and refresh evidence
                    </Button>
                  </div>
                  <label className="customer-question">
                    Ask about your projects
                    <textarea
                      value={question}
                      onChange={(e) => setQuestion(e.target.value)}
                      rows={3}
                    />
                  </label>
                  <Button
                    disabled={busy || !setup?.model_ready}
                    onClick={() =>
                      void run({ type: "question", text: question })
                    }
                  >
                    Prepare test draft
                  </Button>
                  {!setup?.model_ready && (
                    <p>The operator needs to enable the model service.</p>
                  )}
                  {(status?.drafts || []).map((d) => (
                    <article className="customer-draft" key={d.id}>
                      <h3>
                        {d.recipient_id || "Setup reply"} · {d.status}
                      </h3>
                      <p className="draft-text">{d.text}</p>
                      <details>
                        <summary>
                          {d.evidence?.length || 0} evidence references
                        </summary>
                        {d.evidence?.map((e) => (
                          <blockquote key={e.evidence_id}>
                            {e.source}: {e.text}
                          </blockquote>
                        ))}
                      </details>
                      {d.status === "pending" && (
                        <div className="customer-actions">
                          <Button
                            onClick={() =>
                              void run({
                                type: "decision",
                                id: d.id,
                                revision: d.revision,
                                approve: false,
                              })
                            }
                          >
                            Reject
                          </Button>
                          <Button
                            variant="primary"
                            onClick={() => setConfirm(d)}
                          >
                            Review & send
                          </Button>
                        </div>
                      )}
                    </article>
                  ))}
                </section>
                <section>
                  <h2>4. Review communication styles</h2>
                  <p>
                    Colleague profiles are independent. Enable each person after
                    reviewing their inferred style. Automatic sending stays off.
                  </p>
                  {status?.people?.map((p) => (
                    <article key={p.id} className="customer-draft">
                      <h3>{p.name}</h3>
                      {p.preparing ? (
                        <p>Preparing from your Slack history…</p>
                      ) : (
                        <>
                          <p>{p.style.tone}</p>
                          <p>Formality: {p.style.formality}</p>
                          <Button
                            disabled={p.enabled || busy}
                            onClick={() =>
                              void run({
                                type: "enable-person",
                                id: p.id,
                                version: p.version,
                              })
                            }
                          >
                            {p.enabled
                              ? "Enabled — approval required"
                              : "Approve style & enable drafts"}
                          </Button>
                        </>
                      )}
                    </article>
                  ))}
                </section>
                <section>
                  <h2>Stay in control</h2>
                  <p>
                    <ShieldCheck size={18} /> Approval is required. Pausing
                    stops collection and new replies.
                  </p>
                  <div className="customer-actions">
                    <Button
                      variant="primary"
                      disabled={busy || !setup?.model_ready}
                      onClick={() => void run({ type: "complete" })}
                    >
                      <Check size={18} /> Finish setup
                    </Button>
                    <Button
                      disabled={busy}
                      onClick={() =>
                        void run({ type: "pause", paused: !setup?.paused })
                      }
                    >
                      {setup?.paused ? "Resume" : "Pause"}
                    </Button>
                    <Button
                      variant="ghost"
                      onClick={() => void run({ type: "forget" })}
                    >
                      Disconnect this device
                    </Button>
                  </div>
                </section>
              </>
            )}
          </>
        )}
      </main>
      {confirm && (
        <Modal
          title="Send this reviewed reply?"
          onClose={() => setConfirm(null)}
        >
          <p>
            Destination: {confirm.destination?.target}. This will send a real
            Slack message using the identity shown by the backend.
          </p>
          <div className="draft-text">{confirm.text}</div>
          <Button
            variant="primary"
            disabled={busy}
            onClick={() => {
              void run({
                type: "decision",
                id: confirm.id,
                revision: confirm.revision,
                approve: true,
              });
              setConfirm(null);
            }}
          >
            Approve & send
          </Button>
        </Modal>
      )}
      <footer className="customer-footer">
        {busy
          ? "Working…"
          : "Your accounts and project access stay under your control."}
      </footer>
    </div>
  );
}
