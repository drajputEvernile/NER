import React from "react";
import ReactDOM from "react-dom/client";
import Chooser from "./Chooser";
import "./styles.css";
import "./kv.css";
import "./chooser.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Chooser />
  </React.StrictMode>,
);
