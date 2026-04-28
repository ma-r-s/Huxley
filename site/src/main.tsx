import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Landing } from "./Landing.js";
import "./styles/index.css";
import "./i18n/index.js"; // initializes i18next; safe-effect import

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Landing />
  </StrictMode>,
);
