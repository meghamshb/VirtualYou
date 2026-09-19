import { app, BrowserWindow, dialog, ipcMain, safeStorage } from "electron";
import { readFile, writeFile, mkdir, unlink } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { z } from "zod";
import { actionSchema, type Workspace } from "../shared/model";
import { freshWorkspace, transition, diagnosticText } from "../shared/preview";
import { LocalBackend, localOrigin } from "./backend";
import { applyLocalAction, refreshLocalSnapshot } from "./local-session";
import { CredentialVault, trustedFrame } from "./security";
import { Customer } from "./customer";
if (!app.isPackaged && process.env.VIRTUAL_YOU_DESKTOP_TEST_DATA) {
  app.setPath(
    "userData",
    path.resolve(process.env.VIRTUAL_YOU_DESKTOP_TEST_DATA),
  );
}
let window: BrowserWindow | null = null;
let state: Workspace = freshWorkspace();
let backend: LocalBackend | null = null;
const page = pathToFileURL(path.join(__dirname, "../dist/index.html")).href;
function authorize(event: Electron.IpcMainInvokeEvent) {
  if (
    !window ||
    event.sender !== window.webContents ||
    !event.senderFrame ||
    !trustedFrame(
      event.senderFrame.url,
      page,
      event.senderFrame === window.webContents.mainFrame,
    )
  )
    throw new Error("This window is not authorized.");
}
async function persist() {
  if (state.mode === "preview")
    await writeFile(
      path.join(app.getPath("userData"), "preview.json"),
      JSON.stringify(state),
      { mode: 0o600 },
    );
}
async function disconnect() {
  backend = null;
  await unlink(path.join(app.getPath("userData"), "connection.enc")).catch(
    () => {},
  );
}
async function main() {
  await app.whenReady();
  await mkdir(app.getPath("userData"), { recursive: true, mode: 0o700 });
  let serviceUrl = "";
  try {
    serviceUrl =
      JSON.parse(
        await readFile(path.join(app.getAppPath(), "service.json"), "utf8"),
      ).baseUrl || "";
  } catch {
    /* undeployed operator build */
  }
  const customerVault = new CredentialVault(
    {
      available: () =>
        safeStorage.isEncryptionAvailable() &&
        !(
          process.platform === "linux" &&
          safeStorage.getSelectedStorageBackend() === "basic_text"
        ),
      encrypt: (v) => safeStorage.encryptString(v),
      decrypt: (v) => safeStorage.decryptString(v),
    },
    {
      read: () => readFile(path.join(app.getPath("userData"), "customer.enc")),
      write: (value) =>
        writeFile(path.join(app.getPath("userData"), "customer.enc"), value, {
          mode: 0o600,
        }),
    },
  );
  const customer = new Customer(serviceUrl, customerVault);
  // Developer builds without a hosted address use the local backend adapter.
  if (serviceUrl) await customer.init();
  app.on("before-quit", () => customer.stop());
  ipcMain.handle("vy:customer", async (event, action) => {
    authorize(event);
    return customer.act(action);
  });
  try {
    const saved = JSON.parse(
      await readFile(
        path.join(app.getPath("userData"), "preview.json"),
        "utf8",
      ),
    );
    if (saved.mode === "preview") state = saved;
  } catch {
    /* First launch */
  }
  const vault = new CredentialVault(
    {
      available: () =>
        safeStorage.isEncryptionAvailable() &&
        !(
          process.platform === "linux" &&
          safeStorage.getSelectedStorageBackend() === "basic_text"
        ),
      encrypt: (v) => safeStorage.encryptString(v),
      decrypt: (v) => safeStorage.decryptString(v),
    },
    {
      read: () =>
        readFile(path.join(app.getPath("userData"), "connection.enc")),
      write: (v) =>
        writeFile(path.join(app.getPath("userData"), "connection.enc"), v, {
          mode: 0o600,
        }),
    },
  );
  try {
    if (safeStorage.isEncryptionAvailable()) {
      const saved = z
        .object({ port: z.number(), key: z.string() })
        .parse(await vault.load());
      backend = new LocalBackend(saved.port, saved.key);
    }
  } catch {
    backend = null;
  }
  if (backend) state = await refreshLocalSnapshot(backend, state);
  ipcMain.handle("vy:snapshot", async (event) => {
    authorize(event);
    if (backend) state = await refreshLocalSnapshot(backend, state);
    return state;
  });
  ipcMain.handle("vy:act", async (event, raw) => {
    authorize(event);
    const action = actionSchema.parse(raw);
    if (action.type === "preview") {
      await disconnect();
      state = { ...freshWorkspace(), welcomed: true };
    } else if (backend) {
      const result = await applyLocalAction(backend, action, state);
      state = result.state;
      if (!result.ok) throw result.error;
    } else {
      state = transition(state, action);
    }
    await persist();
    return state;
  });
  ipcMain.handle("vy:connect-local", async (event, raw) => {
    authorize(event);
    const port = z.number().int().min(1024).max(65535).parse(raw);
    localOrigin(port);
    if (
      !safeStorage.isEncryptionAvailable() ||
      (process.platform === "linux" &&
        safeStorage.getSelectedStorageBackend() === "basic_text")
    )
      throw new Error(
        "Secure system storage is unavailable. Enable your system keychain and reopen VirtualYou.",
      );
    const chosen = await dialog.showOpenDialog(window!, {
      title: "Choose the existing VirtualYou private data folder",
      properties: ["openDirectory", "showHiddenFiles"],
    });
    if (chosen.canceled || !chosen.filePaths[0])
      throw new Error("Connection cancelled. Your current workspace was kept.");
    const key = await readFile(
      path.join(chosen.filePaths[0], "admin.key"),
      "utf8",
    ).catch(() => {
      throw new Error(
        "Choose the private data folder containing admin.key. No key needs to be copied.",
      );
    });
    const candidate = new LocalBackend(port, key.trim());
    const snapshot = await candidate.snapshot();
    await vault.save({ port, key: key.trim() });
    backend = candidate;
    state = snapshot;
    return state;
  });
  ipcMain.handle("vy:diagnostics", (event) => {
    authorize(event);
    return diagnosticText(state);
  });
  window = new BrowserWindow({
    width: 1360,
    height: 930,
    minWidth: 780,
    minHeight: 620,
    title: "VirtualYou",
    backgroundColor: "#ffffff",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event, url) => {
    if (url.split("#")[0] !== page) event.preventDefault();
  });
  window.webContents.session.setPermissionRequestHandler(
    (_wc, _permission, callback) => callback(false),
  );
  await window.loadURL(page);
}
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    window?.show();
    window?.focus();
  });
  main().catch(() => {
    dialog.showErrorBox(
      "VirtualYou could not start",
      "Reopen the application. If this continues, contact your administrator.",
    );
    app.quit();
  });
}
app.on("window-all-closed", () => app.quit());
