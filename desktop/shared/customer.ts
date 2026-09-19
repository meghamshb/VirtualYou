export type Provider = "slack" | "github" | "jira" | "drive";
export interface Choice {
  id: string;
  name: string;
}
export interface CustomerDraft {
  id: string;
  revision: number;
  recipient_id?: string;
  text: string;
  status: string;
  destination: { target: string };
  evidence: { evidence_id: string; source: string; text: string }[];
}
export interface CustomerPerson {
  id: string;
  name: string;
  preparing?: boolean;
  style: { tone: string; formality: string };
  version: number;
  enabled: boolean;
}
export interface SetupState {
  step: string;
  paused: boolean;
  projects: string[];
  people: string[];
  model_ready: boolean;
  last_refresh?: number;
  refresh_results?: { provider: Provider; ok: boolean }[];
  resources: { provider: Provider; resource: string; project: string }[];
  integrations: {
    id: Provider;
    connected: boolean;
    configured: boolean;
    label: string;
    error?: string;
  }[];
}
export interface CustomerStatus {
  system: {
    supported: boolean;
    collectorReady: boolean;
    privateStorage: boolean;
  };
  configured: boolean;
  connected: boolean;
  setup: SetupState | null;
  projects: Choice[];
  pairing: { user_code: string; expires_at: number } | null;
  error: string;
  collecting: boolean;
  drafts: CustomerDraft[];
  people: CustomerPerson[];
}
export interface Pair {
  device_code: string;
  user_code: string;
  verification_uri: string;
  expires_at: number;
}
export interface PollResult {
  state: string;
  credential?: string;
}
export interface Collection {
  batches: { project: string; records: unknown[] }[];
  errors: unknown[];
}
