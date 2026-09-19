import { contextBridge, ipcRenderer } from "electron";
import type { Action, Bridge } from "../shared/model";
const api: Bridge = {
  snapshot: () => ipcRenderer.invoke("vy:snapshot"),
  act: (action: Action) => ipcRenderer.invoke("vy:act", action),
  connectLocal: (port: number) => ipcRenderer.invoke("vy:connect-local", port),
  diagnostics: () => ipcRenderer.invoke("vy:diagnostics"),
};
contextBridge.exposeInMainWorld("virtualYou", api);

contextBridge.exposeInMainWorld("virtualYouCustomer", {
  act: (action: unknown) => ipcRenderer.invoke("vy:customer", action),
});
