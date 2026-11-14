import { createRoot } from "react-dom/client";
import { App } from "./interface/App";
import "./interface/styles.css";

// No StrictMode: the engine owns a WebGL context and must be created exactly once.
createRoot(document.getElementById("root")!).render(<App />);
