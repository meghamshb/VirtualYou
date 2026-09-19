import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import CustomerApp from "./CustomerApp";
import { bridge } from "./bridge";
import "./styles.css";

function Entry() {
  const [screen, setScreen] = useState<"loading" | "local" | "customer">(
    window.virtualYouCustomer ? "loading" : "local",
  );
  useEffect(() => {
    if (!window.virtualYouCustomer) return;
    let active = true;
    void bridge.snapshot().then(
      (state) => {
        if (active) setScreen(state.mode === "local" ? "local" : "customer");
      },
      () => {
        if (active) setScreen("customer");
      },
    );
    return () => {
      active = false;
    };
  }, []);
  if (screen === "loading") return <p role="status">Opening your workspace…</p>;
  return screen === "local" ? <App /> : <CustomerApp />;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Entry />
  </React.StrictMode>,
);
