import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import AppRoutes from "./routes/AppRoutes";
import "./styles.css";

// Survives minify so a rebuild mints a new hashed /assets/*.js after CDN poison.
if (import.meta.env.PROD) {
  (window as unknown as { __MDA_BUILD__?: string }).__MDA_BUILD__ =
    "2026-08-01T10-no-tips";
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
