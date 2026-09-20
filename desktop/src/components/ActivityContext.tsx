import { useEffect, useState } from "react";
import type { ActivityDetail, SourceCount } from "../../shared/model";
import { sourceLabel, sourceScanLabel, timestamp } from "../../shared/activity";
import { Button } from "./ui";

export function SourceBreakdown({ sources }: { sources: SourceCount[] }) {
  const primary = ["claude", "codex", "git"];
  const ordered = sources
    .filter(
      (source) =>
        primary.includes(source.source) ||
        source.configured ||
        source.count > 0,
    )
    .sort((a, b) => {
      const rank = (value: string) =>
        primary.includes(value) ? primary.indexOf(value) : primary.length;
      return (
        rank(a.source) - rank(b.source) || a.source.localeCompare(b.source)
      );
    });
  return (
    <section
      className="source-breakdown"
      aria-label="Work source collection status"
    >
      <h2>Work sources</h2>
      <p className="fine-print">
        Counts cover all indexed records. Each check reads only configured
        sources; session dates reflect when the work happened.
      </p>
      <div className="source-grid">
        {ordered.map((source) => (
          <article className="source-card" key={source.source}>
            <div className="context-heading">
              <h3>{sourceLabel(source.source)}</h3>
              <span
                className="source-state"
                data-state={
                  source.configured ? source.scan_state : "unconfigured"
                }
              >
                {sourceScanLabel(source)}
              </span>
            </div>
            <strong className="source-count">
              {source.count}{" "}
              <small>indexed {source.count === 1 ? "record" : "records"}</small>
            </strong>
            <dl>
              <div>
                <dt>Last source check</dt>
                <dd>{timestamp(source.last_scan_at)}</dd>
              </div>
              <div>
                <dt>Latest session</dt>
                <dd>
                  {source.latest_at
                    ? timestamp(source.latest_at)
                    : "No indexed session"}
                </dd>
              </div>
            </dl>
            {!source.configured && source.count > 0 && (
              <p className="fine-print">
                Previously indexed records remain available.
              </p>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}

export function RecordedContext({ detail }: { detail: ActivityDetail }) {
  return (
    <section className="recorded-context" aria-label="Recorded source context">
      <div className="context-heading">
        <h3>Recorded context</h3>
        <span className="indexed-badge">
          {sourceLabel(detail.source)} · redacted
        </span>
      </div>
      <p className="fine-print">
        Read from the indexed activity record. This shows what ingestion
        captured; it does not prove that an AI reply used every item.
      </p>
      <dl className="context-metadata">
        <div>
          <dt>Project</dt>
          <dd>{detail.project_id || "Not assigned"}</dd>
        </div>
        <div>
          <dt>Session</dt>
          <dd>{detail.session_id}</dd>
        </div>
        <div>
          <dt>Session started</dt>
          <dd>{timestamp(detail.started_at)}</dd>
        </div>
        <div>
          <dt>Session ended</dt>
          <dd>{timestamp(detail.ended_at)}</dd>
        </div>
        <div>
          <dt>Last indexed</dt>
          <dd>{timestamp(detail.ingested_at)}</dd>
        </div>
      </dl>
      {detail.summary && <p>{detail.summary}</p>}
      {detail.start_state && (
        <details className="context-section">
          <summary>Starting context</summary>
          <blockquote>{detail.start_state}</blockquote>
        </details>
      )}
      {detail.end_state && detail.end_state !== detail.summary && (
        <details className="context-section">
          <summary>Recorded outcome</summary>
          <blockquote>{detail.end_state}</blockquote>
        </details>
      )}
      <details className="context-section" open>
        <summary>
          Prompts <span>{detail.prompts.length}</span>
        </summary>
        {detail.prompts.length ? (
          detail.prompts.map((prompt, index) => (
            <blockquote key={index}>{prompt}</blockquote>
          ))
        ) : (
          <p className="fine-print">
            No prompts were recorded for this activity.
          </p>
        )}
      </details>
      {detail.reasoning_summary && (
        <details className="context-section">
          <summary>Recorded work summary</summary>
          <blockquote>{detail.reasoning_summary}</blockquote>
        </details>
      )}
      <details className="context-section">
        <summary>
          File changes <span>{detail.files_changed.length}</span>
        </summary>
        {detail.files_changed.length ? (
          <ul className="context-files">
            {detail.files_changed.map((file, index) => (
              <li key={index}>
                <code>{file.path}</code>
                <span>{file.operation}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="fine-print">No file changes were recorded.</p>
        )}
      </details>
      <details className="context-section">
        <summary>
          Tool activity <span>{detail.tool_calls.length}</span>
        </summary>
        {detail.tool_calls.length ? (
          detail.tool_calls.map((tool, index) => (
            <div className="context-tool" key={index}>
              <div className="context-heading">
                <strong>{tool.name}</strong>
                <span>{tool.status}</span>
              </div>
              {tool.timestamp && <small>{timestamp(tool.timestamp)}</small>}
              {tool.input_summary && (
                <>
                  <h4>Input</h4>
                  <pre>{tool.input_summary}</pre>
                </>
              )}
              {tool.result_summary && (
                <>
                  <h4>Recorded result</h4>
                  <pre>{tool.result_summary}</pre>
                </>
              )}
            </div>
          ))
        ) : (
          <p className="fine-print">No tool events were recorded.</p>
        )}
      </details>
      {detail.diffs.length > 0 && (
        <details className="context-section">
          <summary>
            Diff excerpts <span>{detail.diffs.length}</span>
          </summary>
          {detail.diffs.map((diff, index) => (
            <pre key={index}>{diff}</pre>
          ))}
        </details>
      )}
      {detail.truncated && (
        <p className="fine-print">
          This is a bounded excerpt. Some recorded context is omitted.
        </p>
      )}
      <p className="fine-print">
        Secrets and private filesystem paths are hidden from this view. Raw
        session files are not shown.
      </p>
    </section>
  );
}

export function ActivityContext({
  activityId,
  load,
}: {
  activityId: string;
  load: (activityId: string) => Promise<ActivityDetail>;
}) {
  const [detail, setDetail] = useState<ActivityDetail | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    void load(activityId)
      .then((value) => {
        if (active) setDetail(value);
      })
      .catch((failure: unknown) => {
        if (active)
          setError(
            failure instanceof Error
              ? failure.message
              : "Recorded context could not be loaded.",
          );
      });
    return () => {
      active = false;
    };
  }, [activityId, load, attempt]);
  if (error)
    return (
      <div className="notice error" role="alert">
        <span>{error}</span>
        <Button
          variant="ghost"
          onClick={() => {
            setError("");
            setAttempt((value) => value + 1);
          }}
        >
          Try again
        </Button>
      </div>
    );
  if (!detail)
    return (
      <p role="status" className="fine-print">
        Loading recorded context…
      </p>
    );
  return <RecordedContext detail={detail} />;
}
