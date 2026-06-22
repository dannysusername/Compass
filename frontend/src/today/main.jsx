// Entry point for the Today list's React island. Mounted by today.html,
// home.html, and the class page into <div id="today-root">. The class page sets
// data-default-class-id so its add-task form pre-selects the current class.
import { createRoot } from "react-dom/client";
import TodayApp from "./TodayApp.jsx";

const el = document.getElementById("today-root");
if (el) {
  const defaultClassId = el.dataset.defaultClassId || null;
  createRoot(el).render(<TodayApp defaultClassId={defaultClassId} />);
}
