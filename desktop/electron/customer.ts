import { app, dialog, shell } from "electron";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { z } from "zod";
import type { CredentialVault } from "./security";
import type {
  SetupState,
  Pair,
  PollResult,
  Collection,
} from "../shared/customer";

const provider = z.enum(["slack", "github", "jira", "drive"]);
export const customerAction = z.discriminatedUnion("type", [
  z.object({ type: z.literal("status") }).strict(),
  z.object({ type: z.literal("pair") }).strict(),
  z.object({ type: z.literal("poll") }).strict(),
  z.object({ type: z.literal("connect"), provider }).strict(),
  z.object({ type: z.literal("disconnect"), provider }).strict(),
  z
    .object({
      type: z.literal("resources"),
      provider,
      parent: z.string().max(100).optional(),
    })
    .strict(),
  z.object({ type: z.literal("add-project") }).strict(),
  z
    .object({
      type: z.literal("select"),
      resources: z
        .array(
          z
            .object({
              provider,
              resource: z.string().max(200),
              project: z.string().max(80),
            })
            .strict(),
        )
        .max(30),
      people: z.array(z.string().max(40)).max(30),
    })
    .strict(),
  z.object({ type: z.literal("collect") }).strict(),
  z
    .object({ type: z.literal("question"), text: z.string().min(1).max(1000) })
    .strict(),
  z
    .object({
      type: z.literal("decision"),
      id: z.string().min(1).max(100),
      revision: z.number().int().positive(),
      approve: z.boolean(),
    })
    .strict(),
  z.object({ type: z.literal("complete") }).strict(),
  z.object({ type: z.literal("pause"), paused: z.boolean() }).strict(),
  z
    .object({
      type: z.literal("enable-person"),
      id: z.string().min(1).max(40),
      version: z.number().int().positive(),
    })
    .strict(),
  z.object({ type: z.literal("forget") }).strict(),
]);
export type CustomerAction = z.infer<typeof customerAction>;
interface Project {
  id: string;
  name: string;
  path: string;
}
export class Customer {
  private credential = "";
  private pending: {
    device_code: string;
    user_code: string;
    verification_uri: string;
    expires_at: number;
  } | null = null;
  private projects: Project[] = [];
  private child: ChildProcessWithoutNullStreams | null = null;
  private busy = false;
  private timer: ReturnType<typeof setInterval> | null = null;
  private serviceError = "";
  private paused = true;
  private collectorReady = false;
  constructor(
    private base: string,
    private vault: CredentialVault,
  ) {}
  async init() {
    if (this.base) {
      const u = new URL(this.base);
      if (
        u.protocol !== "https:" ||
        u.username ||
        u.password ||
        u.pathname !== "/" ||
        u.search ||
        u.hash
      )
        throw Error("The packaged service address is invalid.");
      this.base = u.origin;
    }
    try {
      const saved = z
        .object({ credential: z.string(), pending: z.any().nullable() })
        .parse(await this.vault.load());
      this.credential = saved.credential;
      this.pending = saved.pending;
    } catch {
      /* first launch */
    }
    try {
      this.projects = z
        .array(z.object({ id: z.string(), name: z.string(), path: z.string() }))
        .parse(
          JSON.parse(
            await readFile(
              path.join(app.getPath("userData"), "projects.json"),
              "utf8",
            ),
          ),
        );
    } catch {
      /* first launch */
    }
    try {
      await this.collector({ op: "check" });
      this.collectorReady = true;
    } catch {
      this.serviceError =
        "The collector is missing or could not start. Install the complete customer package.";
    }
    this.timer = setInterval(() => {
      if (this.credential && !this.paused && !this.busy)
        void this.collect().catch(() => {
          this.serviceError =
            "Evidence refresh failed. Check your connection and project permissions.";
        });
    }, 60000);
  }
  async save() {
    await this.vault.save({
      credential: this.credential,
      pending: this.pending,
    });
  }
  async request<T = unknown>(
    route: string,
    body?: unknown,
    method?: string,
  ): Promise<T> {
    if (!this.base)
      throw Error(
        "The customer service is not deployed in this build. Contact the VirtualYou operator.",
      );
    const result = await fetch(this.base + route, {
      method: method || (body === undefined ? "GET" : "POST"),
      headers: {
        "Content-Type": "application/json",
        ...(this.credential
          ? { Authorization: `Bearer ${this.credential}` }
          : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      redirect: "error",
      signal: AbortSignal.timeout(120000),
    }).catch(() => {
      throw Error(
        "Cannot reach VirtualYou. Check your connection and try again.",
      );
    });
    if (!result.ok) {
      if (result.status === 401) {
        this.credential = "";
        await this.save();
        throw Error("This device authorization expired. Reconnect Slack.");
      }
      const codes: Record<string, string> = {
        service_not_configured:
          "This integration is not enabled by the service operator yet.",
        authorization_failed_reconnect:
          "Your connection could not be verified. Reconnect this integration.",
        expired_reconnect:
          "Your authorization expired. Reconnect this integration.",
        not_connected: "Connect this integration before selecting resources.",
        nothing_to_report:
          "No matching evidence is available yet. Collect evidence or select another project.",
        stale_evidence:
          "The evidence changed. Refresh and prepare a new draft before sending.",
        missing_user_scopes:
          "Reconnect Slack and approve the requested personal-DM permissions.",
        missing_bot_scopes: "Reconnect Slack and approve the app permissions.",
        missing_scopes: "Reconnect and grant the requested permissions.",
      };
      const detail = (await result.json().catch(() => null)) as {
        error?: string;
      } | null;
      throw Error(
        (detail?.error && codes[detail.error]) ||
          `VirtualYou could not finish this step (${result.status}). Check your selections and try again.`,
      );
    }
    return result.json();
  }
  async open(url: string) {
    const u = new URL(url);
    if (
      u.origin !== this.base ||
      !["/pair/", "/oauth/launch/"].some((p) => u.pathname.startsWith(p))
    )
      throw Error("The authorization address was rejected.");
    await shell.openExternal(url);
  }
  async collector(input: unknown): Promise<Collection> {
    if (this.busy) throw Error("Evidence collection is already running.");
    this.busy = true;
    try {
      const executable = app.isPackaged
        ? path.join(process.resourcesPath, "collector", "virtual-you-collector")
        : path.join(
            app.getAppPath(),
            "collector-dist",
            "virtual-you-collector",
            "virtual-you-collector",
          );
      const child = spawn(executable, [], {
        stdio: ["pipe", "pipe", "pipe"],
        env: {
          HOME: app.getPath("home"),
          PATH: "/usr/bin:/bin",
          LANG: "en_US.UTF-8",
        },
      });
      this.child = child;
      return await new Promise<Collection>((resolve, reject) => {
        let output = "";
        let settled = false;
        const finish = (err?: Error, value?: Collection) => {
          if (settled) return;
          settled = true;
          clearTimeout(timeout);
          child.kill();
          this.child = null;
          if (err) reject(err);
          else resolve(value!);
        };
        const timeout = setTimeout(
          () =>
            finish(
              Error(
                "Collection timed out. Select a smaller project and try again.",
              ),
            ),
          120000,
        );
        child.on("error", () =>
          finish(
            Error(
              "The bundled collector is unavailable. Install the complete VirtualYou package.",
            ),
          ),
        );
        child.on("exit", () => {
          if (!settled)
            finish(Error("The collector stopped. Restart VirtualYou."));
        });
        child.stderr.on("data", () => {}); // Raw library exceptions never become diagnostics.
        child.stdout.on("data", (chunk: Buffer) => {
          output += chunk.toString();
          if (output.length > 4000000)
            return finish(
              Error("Collected evidence exceeds the upload limit."),
            );
          if (output.includes("\n")) {
            try {
              const result = JSON.parse(output.split("\n")[0]);
              if (!result.ok) throw Error();
              finish(undefined, result.result);
            } catch {
              finish(
                Error("Could not collect this project. Check folder access."),
              );
            }
          }
        });
        child.stdin.end(JSON.stringify(input) + "\n");
      });
    } finally {
      this.busy = false;
    }
  }
  async collect() {
    const result = await this.collector({
      op: "collect",
      projects: this.projects,
      data_dir: path.join(app.getPath("userData"), "collector-data"),
    });
    for (const batch of result.batches) {
      for (let i = 0; i < batch.records.length; i += 30)
        await this.request("/v1/activities", {
          project: batch.project,
          records: batch.records.slice(i, i + 30),
        });
    }
    await this.request("/v1/refresh", {});
    this.serviceError = result.errors.length
      ? "Some local sources could not be read."
      : "";
    return { accepted: true, errors: result.errors };
  }
  async act(raw: unknown): Promise<unknown> {
    const a = customerAction.parse(raw);
    switch (a.type) {
      case "status": {
        let setup: SetupState | null = null;
        if (this.credential) {
          setup = await this.request<SetupState>("/v1/setup");
          this.paused = setup.paused;
        }
        return {
          configured: !!this.base,
          system: {
            supported: process.platform === "darwin",
            collectorReady: this.collectorReady,
            privateStorage: this.vault.available(),
          },
          connected: !!this.credential,
          setup,
          projects: this.projects.map(({ id, name }) => ({ id, name })),
          pairing: this.pending
            ? {
                user_code: this.pending.user_code,
                expires_at: this.pending.expires_at,
              }
            : null,
          error: this.serviceError,
          collecting: this.busy,
          drafts: setup?.projects?.length
            ? await this.request("/v1/drafts")
            : [],
          people: setup?.people?.length ? await this.request("/v1/people") : [],
        };
      }
      case "pair": {
        this.serviceError = "";
        this.pending = await this.request<Pair>("/v1/devices/pair", {});
        await this.save();
        await this.open(this.pending!.verification_uri);
        return {
          user_code: this.pending!.user_code,
          expires_at: this.pending!.expires_at,
        };
      }
      case "poll": {
        if (!this.pending) return { state: "expired" };
        const result = await this.request<PollResult>("/v1/devices/poll", {
          device_code: this.pending.device_code,
        });
        if (result.state === "connected") {
          if (!result.credential)
            throw Error("Authorization returned no device credential.");
          this.credential = result.credential;
          this.pending = null;
          this.serviceError = "";
          await this.save();
        } else if (result.state !== "pending") {
          this.serviceError = result.state === "expired" ? "The device code expired. Connect Slack again." : "Authorization was cancelled or not granted. Connect Slack again.";
          this.pending = null;
          await this.save();
        }
        return { state: result.state };
      }
      case "connect": {
        const result = await this.request<{ url: string }>(
          `/v1/integrations/${a.provider}/authorize`,
          {},
        );
        await this.open(result.url);
        return { opened: true };
      }
      case "disconnect":
        return this.request(
          `/v1/integrations/${a.provider}`,
          undefined,
          "DELETE",
        );
      case "resources":
        return this.request(
          `/v1/resources/${a.provider}?parent=${encodeURIComponent(a.parent || "")}`,
        );
      case "add-project": {
        const chosen = await dialog.showOpenDialog({
          title: "Choose a project to share",
          properties: ["openDirectory"],
        });
        if (chosen.canceled) return { cancelled: true };
        const root = chosen.filePaths[0];
        await readFile(path.join(root, ".git", "HEAD")).catch(async () => {
          const { stat } = await import("node:fs/promises");
          await stat(path.join(root, ".git"));
        });
        const id =
          "p-" + createHash("sha256").update(root).digest("hex").slice(0, 16);
        if (!this.projects.some((p) => p.id === id))
          this.projects.push({ id, name: path.basename(root), path: root });
        await writeFile(
          path.join(app.getPath("userData"), "projects.json"),
          JSON.stringify(this.projects),
          { mode: 0o600 },
        );
        return { added: true };
      }
      case "select":
        return this.request("/v1/setup/projects", {
          projects: this.projects.map((p) => p.id),
          resources: a.resources,
          people: a.people,
        });
      case "collect":
        return this.collect();
      case "question":
        return this.request("/v1/questions", { text: a.text });
      case "decision":
        return this.request(`/v1/drafts/${encodeURIComponent(a.id)}/decision`, {
          revision: a.revision,
          approve: a.approve,
        });
      case "enable-person":
        return this.request(`/v1/people/${encodeURIComponent(a.id)}/enable`, {
          version: a.version,
        });
      case "complete": {
        const result = await this.request("/v1/setup/complete", {});
        this.paused = false;
        return result;
      }
      case "pause": {
        this.paused = a.paused;
        return this.request("/v1/pause", { paused: a.paused });
      }
      case "forget": {
        await this.request("/v1/device", undefined, "DELETE");
        this.credential = "";
        this.pending = null;
        this.paused = true;
        await this.save();
        return { disconnected: true };
      }
    }
  }
  stop() {
    if (this.timer) clearInterval(this.timer);
    this.child?.kill();
  }
}
