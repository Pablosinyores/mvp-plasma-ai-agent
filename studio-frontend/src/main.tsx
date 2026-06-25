import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { StudioProvider } from "./store";
import { WalletProvider } from "./wallet/WalletContext";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <WalletProvider>
      <StudioProvider>
        <App />
      </StudioProvider>
    </WalletProvider>
  </StrictMode>,
);
