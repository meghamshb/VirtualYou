import { describe, it, expect } from "vitest";
import { CredentialVault, trustedFrame } from "../electron/security";
describe("desktop trust boundary", () => {
  it("accepts only the exact packaged main frame", () => {
    const url = "file:///app/dist/index.html";
    expect(trustedFrame(url, url, true)).toBe(true);
    expect(trustedFrame(url + "#settings", url, true)).toBe(true);
    expect(trustedFrame(url, url, false)).toBe(false);
    expect(trustedFrame("https://evil.example", url, true)).toBe(false);
    expect(trustedFrame(url + "?spoof", url, true)).toBe(false);
  });
  it("uses an injectable credential vault and never falls back to plaintext", async () => {
    let stored: Buffer | null = null;
    let clear = "";
    const fakeCipher = {
      available: () => true,
      encrypt: (value: string) => {
        clear = value;
        return Buffer.from("opaque-encrypted-bytes");
      },
      decrypt: () => clear,
    };
    const storage = {
      write: async (value: Buffer) => {
        stored = value;
      },
      read: async () => stored!,
    };
    const vault = new CredentialVault(fakeCipher, storage);
    await vault.save({ port: 3000, key: "private-key" });
    expect(stored!.toString()).not.toContain("private-key");
    expect(await vault.load()).toEqual({ port: 3000, key: "private-key" });
    const unavailable = new CredentialVault(
      { ...fakeCipher, available: () => false },
      storage,
    );
    await expect(
      unavailable.save({ port: 3000, key: "private-key" }),
    ).rejects.toThrow("unavailable");
  });
});
