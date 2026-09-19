import { Check, ExternalLink } from "lucide-react";
import { Brand, Button } from "./ui";
import type { Integration, ProviderId } from "../../shared/model";
export const providers: Record<
  ProviderId,
  { name: string; description: string; permission: string }
> = {
  slack: {
    name: "Slack",
    description: "Draft replies where your team already works.",
    permission:
      "Read selected conversations and send replies only through your approval settings.",
  },
  github: {
    name: "GitHub",
    description: "Bring pull requests and code changes into context.",
    permission:
      "Read repository changes and pull requests for the projects you select.",
  },
  jira: {
    name: "Jira",
    description: "Keep tasks and blockers in the picture.",
    permission:
      "Read issues and project updates from your selected Jira projects.",
  },
  drive: {
    name: "Google Drive",
    description: "Bring selected folder metadata into context.",
    permission:
      "Read metadata from the chosen folder. Document contents and your entire Drive are not imported.",
  },
};
export function IntegrationList({
  items,
  onConnect,
  onDisconnect,
  busy,
}: {
  items: Integration[];
  onConnect: (id: ProviderId) => void;
  onDisconnect?: (id: ProviderId) => void;
  busy: boolean;
}) {
  return (
    <div className="integration-list">
      {items.map((item) => {
        const provider = providers[item.id];
        return (
          <div className="integration-row" key={item.id}>
            <Brand provider={item.id} />
            <div className="integration-copy">
              <h3>{provider.name}</h3>
              <p>{provider.description}</p>
              {item.account && <small>{item.account}</small>}
            </div>
            {item.state === "connected" || item.state === "configured" ? (
              <div className="connected-actions">
                <span className="connected">
                  <Check size={15} />{" "}
                  {item.state === "configured" ? "Configured" : "Connected"}
                </span>
                {onDisconnect && (
                  <Button
                    variant="ghost"
                    disabled={busy}
                    onClick={() => onDisconnect(item.id)}
                    aria-label={`Disconnect ${provider.name}`}
                  >
                    Disconnect
                  </Button>
                )}
              </div>
            ) : (
              <Button
                disabled={busy}
                onClick={() => onConnect(item.id)}
                aria-label={
                  item.state === "unavailable"
                    ? `View ${provider.name} setup`
                    : `Connect ${provider.name}`
                }
              >
                {item.state === "unavailable" ? "Details" : "Connect"}
                {item.state === "unavailable" && <ExternalLink size={14} />}
              </Button>
            )}
          </div>
        );
      })}
    </div>
  );
}
