// Shared formatting helpers used across the React islands (Week, Today, …).
//
// The server localizes timestamps to the user's timezone and serializes the
// wall-clock time into the ISO string, so we read time straight off the string
// (chars 11–16) rather than constructing a Date (which would re-interpret it in
// the browser's timezone and could drift).

export function timeOf(iso) {
  return iso ? iso.slice(11, 16) : "";
}

export function isClockTime(iso) {
  const t = timeOf(iso);
  return t && t !== "00:00";
}

// Date from just the YYYY-MM-DD part (local midnight) — safe for formatting
// weekday/month names without timezone surprises.
export function dateOnly(iso) {
  const s = iso.slice(0, 10);
  return new Date(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10));
}

export function weekdayAbbr(iso) {
  return dateOnly(iso).toLocaleDateString(undefined, { weekday: "short" });
}

export function dayLabel(dateStr) {
  return dateOnly(dateStr).toLocaleDateString(undefined, {
    weekday: "long",
    month: "short",
    day: "numeric",
  });
}

// ISO "2026-06-15T09:00:00-04:00" → "2026-06-15T09:00" for datetime-local.
export function toLocalInput(iso) {
  return iso ? iso.slice(0, 16) : "";
}

// --- reminders ------------------------------------------------------------
// Reminder offset (minutes-before) → human label.
export function alertLabel(m) {
  if (m === 0) return "At time";
  if (m < 60) return `${m} min before`;
  if (m === 60) return "1 hour before";
  if (m < 1440) return `${m / 60} hours before`;
  if (m === 1440) return "1 day before";
  if (m < 10080) return `${m / 1440} days before`;
  if (m === 10080) return "1 week before";
  return `${m} min before`;
}

// Dedupe, clamp (0…4 weeks), sort descending.
export function normalizeAlerts(list) {
  const seen = new Set();
  return list
    .map(Number)
    .filter((n) => !Number.isNaN(n) && n >= 0 && n <= 40320)
    .filter((n) => (seen.has(n) ? false : (seen.add(n), true)))
    .sort((a, b) => b - a);
}

export const ALERT_PRESETS = [
  [0, "At time of event"],
  [5, "5 min before"],
  [15, "15 min before"],
  [30, "30 min before"],
  [60, "1 hour before"],
  [120, "2 hours before"],
  [1440, "1 day before"],
  [2880, "2 days before"],
  [10080, "1 week before"],
];
