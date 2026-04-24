import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles/index.css";
// Initialize i18n before the first App render so useTranslation has a
// ready i18next instance. The import side-effect runs `init()` with the
// active language pulled from localStorage / navigator / default.
import "./i18n/index.js";
import { App } from "./App.js";

const root = document.getElementById("root");
if (!root) throw new Error("No #root element found");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
