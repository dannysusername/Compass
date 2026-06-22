// Entry point for the Week view's React island.
//
// Jinja renders the page shell (month header + nav, imported-calendars legend)
// and leaves a <div id="week-root" data-month="YYYY-MM">. react-dom mounts
// WeekApp into that div, which renders the entire month grid + its modals.
import { createRoot } from "react-dom/client";
import WeekApp from "./WeekApp.jsx";

const el = document.getElementById("week-root");
if (el) {
  createRoot(el).render(<WeekApp month={el.dataset.month} />);
}
