import { describe, it, expect, vi, afterEach } from "vitest";
vi.mock("electron", () => ({
  app: { isPackaged: false, getPath: () => "/tmp", getAppPath: () => "/tmp" },
  dialog: {},
  shell: { openExternal: vi.fn() },
}));
import { Customer, customerAction } from "../electron/customer";
import { CredentialVault } from "../electron/security";
afterEach(() => vi.unstubAllGlobals());
function client() {
  const vault = new CredentialVault(
    {
      available: () => true,
      encrypt: (v) => Buffer.from(v),
      decrypt: (v) => v.toString(),
    },
    { read: async () => Buffer.from("{}"), write: async () => {} },
  );
  const result = new Customer("https://relay.test", vault);
  Object.assign(result, {collectorReady: true});
  return result;
}
describe("customer IPC and device boundary", () => {
  it("rejects extra paths, arbitrary URLs, and executable commands", () => {
    expect(
      customerAction.safeParse({ type: "add-project", path: "/private" })
        .success,
    ).toBe(false);
    expect(
      customerAction.safeParse({
        type: "connect",
        provider: "github",
        url: "https://evil.test",
      }).success,
    ).toBe(false);
    expect(
      customerAction.safeParse({ type: "execute", command: "anything" })
        .success,
    ).toBe(false);
  });
  it("never returns a granted credential to the renderer", async () => {
    const c = client();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        Response.json(
          url.endsWith("/pair")
            ? {
                device_code: "device-code",
                user_code: "ABCD1234",
                verification_uri: "https://relay.test/pair/one",
                expires_at: 10,
              }
            : { state: "connected", credential: "private-grant" },
        ),
      ),
    );
    const pairing = await c.act({ type: "pair" });
    expect(JSON.stringify(pairing)).not.toContain("device-code");
    expect(await c.act({ type: "poll" })).toEqual({ state: "connected" });
  });
  it("will not launch an authorization URL on another origin", async () => {
    const c = client();
    vi.stubGlobal("fetch", async () =>
      Response.json({
        device_code: "device-code",
        user_code: "ABCD1234",
        verification_uri: "https://evil.test/pair/one",
        expires_at: 10,
      }),
    );
    await expect(c.act({ type: "pair" })).rejects.toThrow("rejected");
  });
  it("does not retry an uncertain live delivery", async () => {
    const fetcher = vi.fn(async () => {
      throw Error("timeout");
    });
    vi.stubGlobal("fetch", fetcher);
    await expect(
      client().act({ type: "decision", id: "one", revision: 1, approve: true }),
    ).rejects.toThrow();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe("customer readiness", () => {
  it("does not present an unhealthy service as ready", async () => {
    vi.stubGlobal("fetch", async () => Response.json({ready: false}, {status: 503}));
    const state = await client().act({type: "status"}) as {configured: boolean; serviceReady: boolean; error: string};
    expect(state.configured).toBe(true);
    expect(state.serviceReady).toBe(false);
    expect(state.error).toContain("temporarily unavailable");
  });
  it("refuses pairing when the bundled collector cannot run", async () => {
    const c = client();
    Object.assign(c, {collectorReady: false});
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    await expect(c.act({type: "pair"})).rejects.toThrow("complete customer app");
    expect(fetcher).not.toHaveBeenCalled();
  });
});
