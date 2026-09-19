export interface Cipher {
  available(): boolean;
  encrypt(value: string): Buffer;
  decrypt(value: Buffer): string;
}
export interface PrivateStorage {
  read(): Promise<Buffer>;
  write(value: Buffer): Promise<void>;
}
export class CredentialVault {
  constructor(
    private cipher: Cipher,
    private storage: PrivateStorage,
  ) {}
  available() {
    return this.cipher.available();
  }
  async save(value: unknown) {
    if (!this.cipher.available())
      throw new Error("Secure system storage is unavailable.");
    await this.storage.write(this.cipher.encrypt(JSON.stringify(value)));
  }
  async load(): Promise<unknown> {
    if (!this.cipher.available())
      throw new Error("Secure system storage is unavailable.");
    return JSON.parse(this.cipher.decrypt(await this.storage.read()));
  }
}
export function trustedFrame(
  actual: string,
  expected: string,
  isMainFrame: boolean,
): boolean {
  return isMainFrame && actual.split("#")[0] === expected;
}
