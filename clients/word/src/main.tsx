import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

// Office.onReady fires inside Word's webview with the host info.
// We also handle running in a plain browser (for dev preview) where Office is undefined.
const mount = () => {
  const el = document.getElementById("root");
  if (!el) throw new Error("#root not found");
  createRoot(el).render(<App />);
};

if (typeof Office !== "undefined") {
  Office.onReady(() => mount());
} else {
  mount();
}
